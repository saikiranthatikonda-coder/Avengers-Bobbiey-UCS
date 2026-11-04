import os
import platform
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path

OS = platform.system()   # "Windows" | "Darwin" | "Linux"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# macOS: Chromium-family app bundles (all accept --new-window / --app= / --start-fullscreen),
# looked up in /Applications and ~/Applications.
MAC_BROWSERS = [
    ("chrome",   "Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("edge",     "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("chromium", "Chromium.app/Contents/MacOS/Chromium"),
    ("brave",    "Brave Browser.app/Contents/MacOS/Brave Browser"),
]


def _mac_dirs() -> list[Path]:
    return [Path("/Applications"), Path.home() / "Applications"]


def _find_browser() -> tuple[str | None, str | None]:
    if OS == "Darwin":
        for kind, rel in MAC_BROWSERS:
            for d in _mac_dirs():
                exe = d / rel
                if exe.exists():
                    return (kind, str(exe))
        return (None, None)
    if OS != "Windows":
        for cand in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                     "microsoft-edge", "brave-browser"):
            w = shutil.which(cand)
            if w:
                return (cand, w)
        return (None, None)
    for p in CHROME_PATHS:
        if p and Path(p).exists():
            return ("chrome", p)
    for p in EDGE_PATHS:
        if p and Path(p).exists():
            return ("edge", p)
    for cand in ("chrome", "msedge"):
        w = shutil.which(cand)
        if w:
            return (cand, w)
    return (None, None)


def open_url(url: str, fullscreen: bool = False, app_mode: bool = True) -> dict:
    """Open a URL in a real browser window. Chrome/Edge preferred for fullscreen + app mode.
    Only http(s) web URLs: never a local path, custom protocol or a browser switch."""
    import re
    from urllib.parse import urlsplit
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I) or not urlsplit(url).netloc or any(c in url for c in '\r\n"'):
        return {"opened": False, "error": "only http:// or https:// web addresses can be opened"}
    kind, exe = _find_browser()
    if not exe:
        return _open_default(url)

    args: list[str] = [exe, "--new-window"]
    if fullscreen:
        args.append("--start-fullscreen")
    if app_mode:
        args.append(f"--app={url}")
    else:
        args += ["--", url]                     # "--": the URL can never be read as a switch

    kw: dict = {"shell": False, "close_fds": True}
    if OS == "Windows":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True           # detach from the server's process group
        kw["stdin"] = kw["stdout"] = kw["stderr"] = subprocess.DEVNULL
    try:
        subprocess.Popen(args, **kw)
    except OSError:
        return _open_default(url)
    return {"opened": True, "kind": kind, "fullscreen": fullscreen, "app_mode": app_mode}


def _open_default(url: str) -> dict:
    """No Chromium-family browser found: hand the URL to the OS default browser."""
    if OS == "Darwin":
        try:
            r = subprocess.run(["open", url], capture_output=True, timeout=10)
            if r.returncode == 0:
                return {"opened": True, "kind": "default", "fullscreen": False, "app_mode": False}
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        ok = bool(webbrowser.open(url, new=2))
    except Exception:
        ok = False
    if not ok:
        return {"opened": False, "error": "no browser could be found on this machine"}
    return {"opened": True, "kind": "default", "fullscreen": False, "app_mode": False}


# ── intent parsing ──────────────────────────────────────────────────
_VERBS = re.compile(r"\b(open|launch|show|pull\s*up|navigate(?:\s+to)?|load|go\s+to|bring\s+up|fire\s+up)\b", re.I)
_FS = re.compile(r"\bfull[\s-]?screen\b|\bmaximi[sz]e\b|\bkiosk\b", re.I)
_URL = re.compile(r"https?://\S+", re.I)
_DOMAIN = re.compile(r"\b([a-z0-9][a-z0-9-]*\.(?:com|app|org|net|io|ai|dev|so|gov|edu|co|uk))\b", re.I)


def parse_browser_intent(prompt: str) -> dict | None:
    """Return {url, fullscreen, name} if the prompt looks like a browser command, else None."""
    if not prompt:
        return None
    p = prompt.strip()
    low = p.lower()
    has_verb = bool(_VERBS.search(p))
    fullscreen = bool(_FS.search(p))
    mentions_worldmonitor = "worldmonitor" in low or "world monitor" in low

    if mentions_worldmonitor and (has_verb or fullscreen):
        return {
            "url": "https://www.worldmonitor.app",
            "fullscreen": fullscreen,
            "name": "WorldMonitor",
        }

    if has_verb:
        m = _URL.search(p)
        if m:
            return {
                "url": m.group(0).rstrip(".,;:)]"),
                "fullscreen": fullscreen,
                "name": m.group(0),
            }
        m = _DOMAIN.search(p)
        if m:
            return {
                "url": f"https://{m.group(1)}",
                "fullscreen": fullscreen,
                "name": m.group(1),
            }
    return None
