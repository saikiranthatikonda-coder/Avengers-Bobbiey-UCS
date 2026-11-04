"""PHONE AGENT · Cloudflare quick tunnel (cloudflared) lifecycle.

BUCS binds 127.0.0.1, so the telephony provider cannot reach the webhooks directly.
`cloudflared tunnel --url http://127.0.0.1:<port>` publishes a random
https://<words>.trycloudflare.com URL that forwards to this machine. Only
/phone/twilio/* is usable through it: origin_guard refuses every other path for a
non-local Host header, and every phone webhook is signature-checked.

The process runs in its own process group; stop() kills the whole tree.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]{0,62}\.trycloudflare\.com")
CANDIDATES = [r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
              r"C:\Program Files\cloudflared\cloudflared.exe",
              "/usr/local/bin/cloudflared", "/opt/homebrew/bin/cloudflared", "/usr/bin/cloudflared"]


def find_cloudflared() -> str | None:
    b = shutil.which("cloudflared")
    if b:
        return b
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    return None


def parse_url(line: str) -> str | None:
    m = URL_RE.search(line or "")
    if m and m.group(0) != "https://api.trycloudflare.com":
        return m.group(0)
    return None


class Tunnel:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None
        self.port: int | None = None
        self.started: float | None = None
        self.error: str = ""
        self.lines: deque[str] = deque(maxlen=40)
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def snapshot(self) -> dict:
        return {"installed": bool(find_cloudflared()), "running": self.running, "url": self.url if self.running else None,
                "port": self.port, "started": self.started, "error": self.error,
                "pid": self.proc.pid if self.running else None}

    def _reader(self, proc: subprocess.Popen) -> None:
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                self.lines.append(line[:300])
                u = parse_url(line)
                if u and not self.url:
                    self.url = u
        except Exception:
            pass

    def start(self, port: int, wait_s: float = 25.0) -> dict:
        """Blocking (run it off the event loop). Returns the snapshot once the URL is known or on timeout."""
        with self._lock:
            if self.running:
                return self.snapshot()
            b = find_cloudflared()
            if not b:
                self.error = "cloudflared is not installed (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)"
                return self.snapshot()
            self.url, self.error, self.port = None, "", int(port)
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(
                [b, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{int(port)}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=flags)
            self.started = time.time()
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        deadline = time.time() + wait_s
        while time.time() < deadline and self.running and not self.url:
            time.sleep(0.25)
        if not self.running:
            self.error = "cloudflared exited: " + (" | ".join(list(self.lines)[-3:]) or "no output")[:300]
        elif not self.url:
            self.error = "tunnel started but no trycloudflare URL yet — check again in a few seconds"
        return self.snapshot()

    def stop(self) -> dict:
        with self._lock:
            p = self.proc
            if p is not None and p.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                else:
                    p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
            self.proc, self.url, self.started = None, None, None
        return self.snapshot()
