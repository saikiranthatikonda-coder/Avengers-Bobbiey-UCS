import os
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_browser() -> tuple[str | None, str | None]:
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
    """Open a URL in a real browser window. Chrome/Edge preferred for fullscreen + app mode."""
    kind, exe = _find_browser()
    if not exe:
        webbrowser.open(url, new=2)
        return {"opened": True, "kind": "default", "fullscreen": False, "app_mode": False}

    args: list[str] = [exe, "--new-window"]
    if fullscreen:
        args.append("--start-fullscreen")
    if app_mode:
        args.append(f"--app={url}")
    else:
        args.append(url)

    flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, shell=False, creationflags=flags, close_fds=True)
    return {"opened": True, "kind": kind, "fullscreen": fullscreen, "app_mode": app_mode}


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
