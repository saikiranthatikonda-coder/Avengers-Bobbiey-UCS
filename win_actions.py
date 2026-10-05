"""Windows-native actions for the real-time engine — an explicit allow-list.

Nothing here runs arbitrary commands: every action is a named, reviewed
operation (launch a known app, open a known folder, a media/volume key, lock
the workstation). The LLM never reaches this module; only the deterministic
intent router does, and every call is audited by the engine.

Each action returns {"ok": bool, "say": str, ...}. On non-Windows hosts every
action returns ok=False with an honest reason.
"""

import ctypes
import os
import platform
import subprocess
from pathlib import Path

IS_WIN = platform.system() == "Windows"

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

FOLDERS: dict[str, str] = {
    "downloads": "Downloads", "documents": "Documents", "desktop": "Desktop",
    "pictures": "Pictures", "music": "Music", "videos": "Videos",
}

# virtual-key codes for media/volume keys (sent with keybd_event)
VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
      "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}


def _unsupported(what: str) -> dict:
    return {"ok": False, "say": f"{what} is only available on Windows, sir."}


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def open_app(name: str) -> dict:
    if not IS_WIN:
        return _unsupported("Launching apps")
    key = name.lower().strip()
    if key not in APPS:
        return {"ok": False, "say": f"{name} isn't on my approved app list, sir."}
    label, cmd = APPS[key]
    try:
        subprocess.Popen(cmd, close_fds=True, creationflags=_flags())
        return {"ok": True, "say": f"Opening {label}.", "action": "open_app", "target": label}
    except Exception as e:
        return {"ok": False, "say": f"Couldn't open {label}: {e}"}


def open_folder(name: str) -> dict:
    if not IS_WIN:
        return _unsupported("Opening folders")
    sub = FOLDERS.get(name.lower().strip())
    if not sub:
        return {"ok": False, "say": f"I don't have a folder called {name}, sir."}
    path = Path.home() / sub
    if not path.exists():
        return {"ok": False, "say": f"Your {sub} folder doesn't exist on this machine."}
    try:
        os.startfile(str(path))                       # type: ignore[attr-defined]
        return {"ok": True, "say": f"Opening {sub}.", "action": "open_folder", "target": str(path)}
    except Exception as e:
        return {"ok": False, "say": f"Couldn't open {sub}: {e}"}


def media_key(name: str, presses: int = 1) -> dict:
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
    return {"platform": platform.system(), "apps": sorted({v[0] for v in APPS.values()}),
            "folders": sorted(FOLDERS.values()), "media": sorted(VK), "lock": IS_WIN}
