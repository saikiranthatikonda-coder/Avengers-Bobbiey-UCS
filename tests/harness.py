"""Isolated UCS server for tests.

Copies the application code (no .env, no state files, no credentials) into a
temp dir and boots it with uvicorn on a free loopback port. Every state file a
module writes lands in that temp copy, so tests can never touch the operator's
real data. Claude CLI, local LLM, TTS and voice are forced off so runs are
deterministic and need no credentials or network.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

# env the app reads — scrubbed so the operator's shell config can't leak in
_APP_ENV_PREFIXES = ("JARVIS_", "NEWSAPI", "LOCAL_LLM", "CLAUDE_BIN")


def copy_app(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for py in ROOT.glob("*.py"):
        shutil.copy2(py, dst / py.name)
    for d in ("static", "web3mod"):
        shutil.copytree(ROOT / d, dst / d, ignore=ignore)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class IsolatedServer:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="ucs-test-"))
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.log_path = self.dir / "server.log"

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(_APP_ENV_PREFIXES)}
        env.update({
            "CLAUDE_BIN": "__ucs_test_no_claude__",
            "LOCAL_LLM_URL": "http://127.0.0.1:9/v1",   # discard port → instant refusal
            "JARVIS_TTS": "0",
            "JARVIS_VOICE": "0",
            "JARVIS_HOST": "127.0.0.1",
            "PYTHONIOENCODING": "utf-8",
        })
        return env

    def start(self, timeout: float = 90) -> "IsolatedServer":
        copy_app(self.dir)
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
             "--port", str(self.port)],
            cwd=self.dir, env=self.env(), stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("server exited during boot:\n" + self.log())
            try:
                if self.get("/api/status", timeout=3):
                    return self
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        raise RuntimeError("server did not become ready:\n" + self.log())

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_log", None):
            self._log.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def log(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")[-4000:]
        except OSError:
            return ""

    def _req(self, method: str, path: str, body=None, timeout: float = 30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
            return json.loads(raw) if "json" in ctype else raw.decode(errors="replace")

    def get(self, path: str, timeout: float = 30):
        return self._req("GET", path, timeout=timeout)

    def post(self, path: str, body: dict, timeout: float = 60):
        return self._req("POST", path, body, timeout=timeout)
