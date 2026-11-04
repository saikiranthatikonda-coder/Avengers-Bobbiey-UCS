"""Windows-native actions for the real-time engine — an explicit allow-list.

Nothing here runs arbitrary commands: every action is a named, reviewed
operation (launch a known app, open a known folder, a media/volume key, lock
the workstation). The LLM never reaches this module; only the deterministic
intent router does, and every call is audited by the engine.

Each action returns {"ok": bool, "say": str, ...}. On macOS a native
equivalent is used where one exists (`open -a` for mapped apps, `open` for the
standard home folders, AppleScript for volume/mute); everything else, and every
action on other hosts, returns ok=False with an honest reason. No Windows API
(ctypes.windll, os.startfile, CREATE_NO_WINDOW) is touched off Windows.
"""

import ctypes
import os
import platform
import subprocess
from pathlib import Path

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# spoken/typed name → (label, launch target)
APPS: dict[str, tuple[str, list[str]]] = {
    "notepad":        ("Notepad",         ["notepad.exe"]),
    "calculator":     ("Calculator",      ["calc.exe"]),
    "calc":           ("Calculator",      ["calc.exe"]),
    "paint":          ("Paint",           ["mspaint.exe"]),
    "file explorer":  ("File Explorer",   ["explorer.exe"]),
    "explorer":       ("File Explorer",   ["explorer.exe"]),
    "task manager":   ("Task Manager",    ["taskmgr.exe"]),
    "settings":       ("Settings",        ["explorer.exe", "ms-settings:"]),
    "control panel":  ("Control Panel",   ["control.exe"]),
    "snipping tool":  ("Snipping Tool",   ["explorer.exe", "ms-screenclip:"]),
    "camera":         ("Camera",          ["explorer.exe", "microsoft.windows.camera:"]),
    "terminal":       ("Windows Terminal", ["wt.exe"]),
}

# macOS equivalents of the allow-listed names above (`open -a <App>`). A name
# with no honest macOS counterpart (Paint, Control Panel, Camera) is left out
# and returns an "unsupported" answer instead of launching something else.
MAC_APPS: dict[str, tuple[str, str]] = {
    "notepad":        ("TextEdit",         "TextEdit"),
    "calculator":     ("Calculator",       "Calculator"),
    "calc":           ("Calculator",       "Calculator"),
    "file explorer":  ("Finder",           "Finder"),
    "explorer":       ("Finder",           "Finder"),
    "task manager":   ("Activity Monitor", "Activity Monitor"),
    "settings":       ("System Settings",  "System Settings"),
    "snipping tool":  ("Screenshot",       "Screenshot"),
    "terminal":       ("Terminal",         "Terminal"),
}

FOLDERS: dict[str, str] = {
    "downloads": "Downloads", "documents": "Documents", "desktop": "Desktop",
    "pictures": "Pictures", "music": "Music", "videos": "Videos",
}

# virtual-key codes for media/volume keys (sent with keybd_event)
VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
      "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}


def _unsupported(what: str) -> dict:
    where = "on Windows" if not IS_MAC else "on Windows, not on macOS"
    return {"ok": False, "say": f"{what} is only available {where}, sir.", "supported": False}


def _flags() -> int:
    # creationflags must be 0 on POSIX (any other value raises ValueError)
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0


def _mac_run(args: list[str], timeout: float = 5) -> tuple[bool, str]:
    """Run a fixed macOS command (never a shell). (ok, error text)."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()[:200]
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def open_app(name: str) -> dict:
    key = name.lower().strip()
    if IS_MAC:
        if key not in APPS:
            return {"ok": False, "say": f"{name} isn't on my approved app list, sir."}
        if key not in MAC_APPS:
            return {"ok": False, "supported": False,
                    "say": f"{APPS[key][0]} is a Windows app with no macOS equivalent on my list, sir."}
        label, app = MAC_APPS[key]
        ok, err = _mac_run(["open", "-a", app])
        if ok:
            return {"ok": True, "say": f"Opening {label}.", "action": "open_app", "target": label}
        return {"ok": False, "say": f"Couldn't open {label}: {err or 'open failed'}"}
    if not IS_WIN:
        return _unsupported("Launching apps")
    if key not in APPS:
        return {"ok": False, "say": f"{name} isn't on my approved app list, sir."}
    label, cmd = APPS[key]
    try:
        subprocess.Popen(cmd, close_fds=True, creationflags=_flags())
        return {"ok": True, "say": f"Opening {label}.", "action": "open_app", "target": label}
    except Exception as e:
        return {"ok": False, "say": f"Couldn't open {label}: {e}"}


def open_folder(name: str) -> dict:
    if not (IS_WIN or IS_MAC):
        return _unsupported("Opening folders")
    sub = FOLDERS.get(name.lower().strip())
    if not sub:
        return {"ok": False, "say": f"I don't have a folder called {name}, sir."}
    path = Path.home() / sub
    if not path.exists():
        return {"ok": False, "say": f"Your {sub} folder doesn't exist on this machine."}
    if IS_MAC:
        ok, err = _mac_run(["open", str(path)])
        if ok:
            return {"ok": True, "say": f"Opening {sub}.", "action": "open_folder", "target": str(path)}
        return {"ok": False, "say": f"Couldn't open {sub}: {err or 'open failed'}"}
    try:
        os.startfile(str(path))                       # type: ignore[attr-defined]
        return {"ok": True, "say": f"Opening {sub}.", "action": "open_folder", "target": str(path)}
    except Exception as e:
        return {"ok": False, "say": f"Couldn't open {sub}: {e}"}


# macOS: volume/mute through AppleScript (no key injection, no extra permission).
# Play/pause/next/previous have no app-neutral equivalent, so they stay unsupported.
_MAC_VOLUME = {
    "volume_up":   "set volume output volume ((output volume of (get volume settings)) + {n})",
    "volume_down": "set volume output volume ((output volume of (get volume settings)) - {n})",
    "mute":        "set volume output muted (not (output muted of (get volume settings)))",
}


def media_key(name: str, presses: int = 1) -> dict:
    if IS_MAC:
        if name not in VK:
            return {"ok": False, "say": "Unknown media control."}
        script = _MAC_VOLUME.get(name)
        if not script:
            return {"ok": False, "supported": False,
                    "say": "Track controls aren't available on macOS yet, sir."}
        step = 2 * max(1, min(int(presses or 1), 25))          # one Windows key press ~ 2%
        ok, err = _mac_run(["osascript", "-e", script.format(n=step)])
        words = {"volume_up": "Volume up", "volume_down": "Volume down", "mute": "Toggled mute"}
        if ok:
            return {"ok": True, "say": f"{words[name]}.", "action": "media", "target": name}
        return {"ok": False, "say": f"Media control failed: {err or 'osascript failed'}"}
    if not IS_WIN:
        return _unsupported("Media keys")
    code = VK.get(name)
    if code is None:
        return {"ok": False, "say": "Unknown media control."}
    try:
        user32 = ctypes.windll.user32                 # type: ignore[attr-defined]
        for _ in range(max(1, min(presses, 25))):
            user32.keybd_event(code, 0, 0, 0)
            user32.keybd_event(code, 0, 2, 0)         # KEYEVENTF_KEYUP
        words = {"volume_up": "Volume up", "volume_down": "Volume down", "mute": "Toggled mute",
                 "play_pause": "Play / pause", "next": "Next track", "previous": "Previous track"}
        return {"ok": True, "say": f"{words[name]}.", "action": "media", "target": name}
    except Exception as e:
        return {"ok": False, "say": f"Media key failed: {e}"}


def lock_screen() -> dict:
    if not IS_WIN:
        return _unsupported("Locking the screen")
    try:
        ok = bool(ctypes.windll.user32.LockWorkStation())   # type: ignore[attr-defined]
        return {"ok": ok, "say": "Locking the workstation." if ok else "Windows refused the lock request.",
                "action": "lock"}
    except Exception as e:
        return {"ok": False, "say": f"Lock failed: {e}"}


def catalog() -> dict:
    """What the action layer can do — shown in the dashboard / used by tests."""
    if IS_MAC:
        return {"platform": platform.system(), "apps": sorted({v[0] for v in MAC_APPS.values()}),
                "folders": sorted(FOLDERS.values()), "media": sorted(_MAC_VOLUME), "lock": False}
    if not IS_WIN:
        return {"platform": platform.system(), "apps": [], "folders": [], "media": [], "lock": False,
                "note": "Desktop actions are only available on Windows and macOS."}
    return {"platform": platform.system(), "apps": sorted({v[0] for v in APPS.values()}),
            "folders": sorted(FOLDERS.values()), "media": sorted(VK), "lock": IS_WIN}
