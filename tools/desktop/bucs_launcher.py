"""BUCS desktop launcher (Windows installer + macOS BUCS.app). Standard library only.

Runs under the Python runtime bundled with the desktop app and owns the server's life:

* single instance: a second launch only opens the dashboard window of the running one;
* port handling: JARVIS_PORT (env, then .env, default 8765); if that port is taken by
  something else, the next free port in 8771-8799 is used and recorded;
* restart / update / rollback loop with exactly run.ps1's semantics:
    .update/restart.flag   -> relaunch (after `pip install -r requirements.txt` when the
                              applied update changed requirements, see verify.json)
    .update/shutdown.flag  -> stop for good (exit code 99)
    .update/verify.json    -> still present after the server exited = the new version
                              failed before mark_healthy(): restore the backup, relaunch
* logs: <home>/logs/launcher.log and <home>/logs/server.log (rotated);
* opens the dashboard as an app window (Edge/Chrome --app=, else the default browser),
  unless BUCS_NO_BROWSER=1.

macOS / Linux AppImage: the app bundle is read-only in spirit (it is code-signed), so the pristine app files
in BUCS.app/Contents/Resources/app are copied to ~/Library/Application Support/BUCS on first
run and whenever the bundle carries a newer version (user data is never overwritten), and
packages an update adds are installed into <home>/.pydeps (on PYTHONPATH), never into the
bundle. Linux: <home> is ~/.local/share/BUCS, the AppImage's app/ is the bundle.
Windows: <home> is the per-user install dir itself (%LOCALAPPDATA%\\Programs\\BUCS).

Usage:  bucs_launcher.py [--home DIR] [--bundle DIR] [--open] [--stop] [--no-browser]
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
HERE = Path(__file__).resolve().parent
EXIT_SHUTDOWN = 99
ALT_PORTS = range(8771, 8800)
# user data an app-file sync must never overwrite (mirror of updater.PRESERVE)
PRESERVE = {
    ".env", "license.json", "license_signing.key", "licences_issued.json", "providers.json",
    "commander_memory.md", "operator_memory.json", "token.json", "credentials.json", "auth.json",
    "fleet_token.txt", "pair_code.txt", "node_id.txt", "governance.json", "audio_pref.json",
    "audit.log", "model_pref.json", "billing.json", "productivity.json", "team_memory.json",
}

log_file = None


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    try:
        if log_file:
            log_file.write(line + "\n")
            log_file.flush()
    except Exception:
        pass
    try:
        if sys.stdout:
            print(line, flush=True)
    except Exception:
        pass


def rotate(path: Path, limit: int = 5 * 1024 * 1024) -> None:
    try:
        if path.exists() and path.stat().st_size > limit:
            old = path.with_suffix(path.suffix + ".1")
            old.unlink(missing_ok=True)
            path.rename(old)
    except OSError:
        pass


def vtuple(v: str) -> tuple:
    out = []
    for part in str(v).strip().lstrip("v").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def read_text(p: Path, default: str = "") -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return default


# ── paths ──────────────────────────────────────────────────────────────
def default_home() -> Path:
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "BUCS"
    if not IS_WIN:                          # Linux AppImage: XDG data dir
        return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "BUCS"
    return HERE.parent                      # Windows: <install>\launcher\bucs_launcher.py


def server_python() -> str:
    exe = Path(sys.executable)
    if IS_WIN and exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")    # pythonw has no stdio; CREATE_NO_WINDOW hides python.exe
        if console.exists():
            return str(console)
    return str(exe)


# ── single instance (OS file lock held for the launcher's lifetime) ────
class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path, self.fh = path, None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if IS_WIN:
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False


def http_json(url: str, data: bytes | None = None, timeout: float = 3):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def bucs_alive(port: int) -> bool:
    try:
        d = http_json(f"http://127.0.0.1:{port}/api/license", timeout=2)
        return isinstance(d, dict)
    except Exception:
        return False


def port_busy(host: str, port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1" if host in ("0.0.0.0", "") else host, port), timeout=0.5):
            return True
    except OSError:
        pass
    with socket.socket() as s:                      # nothing answers, but can we bind it?
        try:
            s.bind((host if host else "127.0.0.1", port))
            return False
        except OSError:
            return True


def read_dotenv(home: Path) -> dict:
    out = {}
    for line in read_text(home / ".env").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# ── dashboard window ───────────────────────────────────────────────────
def _app_browser() -> str | None:
    if IS_WIN:
        roots = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")]
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            for r in roots:
                if r and Path(r, rel).exists():
                    return str(Path(r, rel))
    elif IS_MAC:
        for rel in ("Google Chrome.app/Contents/MacOS/Google Chrome",
                    "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                    "Brave Browser.app/Contents/MacOS/Brave Browser",
                    "Chromium.app/Contents/MacOS/Chromium"):
            for d in (Path("/Applications"), Path.home() / "Applications"):
                if (d / rel).exists():
                    return str(d / rel)
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                     "microsoft-edge", "brave-browser"):
            if shutil.which(name):
                return shutil.which(name)
    return None


def open_window(url: str) -> None:
    if os.environ.get("BUCS_NO_BROWSER") == "1":
        log(f"dashboard ready at {url} (window not opened: BUCS_NO_BROWSER=1)")
        return
    exe = _app_browser()
    try:
        if exe:
            kw = {"close_fds": True, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL}
            if IS_WIN:
                kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:
                kw["start_new_session"] = True
            subprocess.Popen([exe, f"--app={url}", "--new-window"], **kw)
            log(f"opened dashboard window ({Path(exe).name}) at {url}")
            return
    except OSError as e:
        log(f"app window failed ({e}); using the default browser")
    if IS_WIN:
        os.startfile(url)                                     # noqa: S606 — fixed loopback URL
    elif IS_MAC:
        subprocess.run(["open", url], check=False)
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        import webbrowser
        webbrowser.open(url, new=2)
    log(f"opened dashboard in the default browser at {url}")


def message_box(text: str) -> None:
    """Visible error for a windowless launcher (the macOS app shows its own alert)."""
    if IS_WIN and os.environ.get("BUCS_NO_BROWSER") != "1":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "BUCS", 0x10)
        except Exception:
            pass


# ── macOS: sync pristine app files from the bundle into the writable home ──
def sync_from_bundle(bundle: Path, home: Path) -> None:
    info = {}
    try:
        info = json.loads((bundle.parent / "build.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    marker = home / ".update" / "bundle.json"
    try:
        seen = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        seen = {}
    b_ver, h_ver = read_text(bundle / "VERSION", "0.0.0"), read_text(home / "VERSION", "")
    build = info.get("build", b_ver)
    if h_ver and vtuple(b_ver) < vtuple(h_ver):
        log(f"installed files are v{h_ver} (updated in-app); bundle v{b_ver} is older: kept")
        return
    if h_ver and vtuple(b_ver) == vtuple(h_ver) and seen.get("build") == build:
        return
    log(f"installing app files v{b_ver} (build {build}) into {home} (was {h_ver or 'none'})")
    home.mkdir(parents=True, exist_ok=True)
    for src in bundle.rglob("*"):
        rel = src.relative_to(bundle)
        dst = home / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if rel.as_posix() in PRESERVE and dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o644 | (0o111 if src.suffix in (".sh", ".command") else 0))
    shutil.rmtree(home / ".pydeps", ignore_errors=True)       # the new bundle carries its own deps
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"build": build, "version": b_ver, "at": time.time()}), encoding="utf-8")


# ── the launcher ───────────────────────────────────────────────────────
class Launcher:
    def __init__(self, home: Path, bundle: Path | None) -> None:
        self.home, self.bundle = home, bundle
        self.upd = home / ".update"
        self.state_file = self.upd / "launcher.json"
        self.overlay = home / ".pydeps"
        self.proc: subprocess.Popen | None = None
        self.stopping = False
        self.port = 8765
        self.host = "127.0.0.1"

    # state shared with --open / --stop and the tests
    def write_state(self, **extra) -> None:
        d = {"pid": os.getpid(), "port": self.port, "host": self.host,
             "server_pid": self.proc.pid if self.proc else None, "home": str(self.home), **extra}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def env(self) -> dict:
        env = dict(os.environ)
        env.update({"BUCS_LAUNCHER": "1", "BUCS_DESKTOP": "1", "PYTHONIOENCODING": "utf-8",
                    "PYTHONUNBUFFERED": "1", "JARVIS_HOST": self.host, "JARVIS_PORT": str(self.port)})
        if self.bundle:                      # read-only runtime (macOS app / Linux AppImage)
            env["PYTHONPATH"] = str(self.overlay) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            env.setdefault("PYTHONPYCACHEPREFIX", str(self.home / ".pycache"))
        return env

    def pick_port(self) -> None:
        de = read_dotenv(self.home)
        self.host = os.environ.get("JARVIS_HOST") or de.get("JARVIS_HOST") or "127.0.0.1"
        want = os.environ.get("JARVIS_PORT") or de.get("JARVIS_PORT") or "8765"
        try:
            self.port = int(want)
        except ValueError:
            self.port = 8765
        if port_busy(self.host, self.port):
            for p in ALT_PORTS:
                if not port_busy(self.host, p):
                    log(f"port {self.port} is in use by another program; using {p}")
                    self.port = p
                    return
            raise SystemExit(f"no free port (tried {want} and {ALT_PORTS.start}-{ALT_PORTS.stop - 1})")

    def pip_install(self) -> None:
        req = self.home / "requirements.txt"
        cmd = [server_python(), "-m", "pip", "install", "-q", "--disable-pip-version-check",
               "--no-warn-script-location", "-r", str(req)]
        if self.bundle:
            self.overlay.mkdir(parents=True, exist_ok=True)
            cmd[4:4] = ["--upgrade", "--target", str(self.overlay)]
        log("requirements changed: " + " ".join(cmd[1:]))
        with open(self.home / "logs" / "server.log", "ab") as out:
            r = subprocess.run(cmd, cwd=self.home, env=self.env(), stdout=out, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0)
        log(f"pip install finished with exit code {r.returncode}")

    def rollback(self, verify: Path) -> None:
        v = json.loads(verify.read_text(encoding="utf-8"))
        log(f"version {v.get('to')} failed to start: rolling back to {v.get('from')}")
        backup = Path(v["backup"])
        if backup.exists():
            for src in backup.rglob("*"):
                if src.is_file():
                    dst = self.home / src.relative_to(backup)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
        for rel in v.get("created", []):
            (self.home / rel).unlink(missing_ok=True)
        verify.unlink(missing_ok=True)

    def opener(self) -> None:
        url = f"http://127.0.0.1:{self.port}/"
        for _ in range(450):
            if self.stopping or (self.proc and self.proc.poll() is not None and not self.stopping and False):
                return
            if bucs_alive(self.port):
                self.write_state(ready=True)
                open_window(url)
                return
            time.sleep(1)
        log("server did not answer within 7.5 minutes; see logs/server.log")

    def stop_server(self, *_args) -> None:
        self.stopping = True
        p = self.proc
        if p and p.poll() is None:
            log("stopping the server")
            try:
                if IS_WIN:
                    p.terminate()
                else:
                    p.send_signal(signal.SIGINT)            # graceful lifespan shutdown, like Ctrl+C
                p.wait(timeout=20)
            except Exception:
                p.kill()

    def run(self) -> int:
        self.upd.mkdir(parents=True, exist_ok=True)
        (self.home / "logs").mkdir(parents=True, exist_ok=True)
        if not (self.home / ".env").exists() and (self.home / ".env.example").exists():
            shutil.copy2(self.home / ".env.example", self.home / ".env")
            log(".env created from the template")
        self.pick_port()
        if not IS_WIN:
            signal.signal(signal.SIGTERM, self.stop_server)
            signal.signal(signal.SIGINT, self.stop_server)
            signal.signal(signal.SIGHUP, self.stop_server)
        log(f"BUCS v{read_text(self.home / 'VERSION', '?')} starting on http://{self.host}:{self.port} "
            f"(home {self.home}, python {sys.version.split()[0]})")
        opened = False
        crashes: list[float] = []
        code = 0
        while True:
            server_log = self.home / "logs" / "server.log"
            rotate(server_log)
            with open(server_log, "ab") as out:
                started = time.time()
                self.proc = subprocess.Popen(
                    [server_python(), "-m", "uvicorn", "main:app", "--host", self.host, "--port", str(self.port)],
                    cwd=self.home, env=self.env(), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0)
                self.write_state(ready=False)
                if not opened:
                    opened = True
                    threading.Thread(target=self.opener, daemon=True).start()
                rc = self.proc.wait()
            log(f"server exited with code {rc} after {time.time() - started:.0f}s")
            restart, shutdown, verify = (self.upd / "restart.flag", self.upd / "shutdown.flag",
                                         self.upd / "verify.json")
            if restart.exists():
                restart.unlink(missing_ok=True)
                shutdown.unlink(missing_ok=True)          # an update restart wins over a stale stop
                log("restarting into the updated version")
                try:
                    if verify.exists() and json.loads(verify.read_text(encoding="utf-8")).get("requirements_changed"):
                        self.pip_install()
                except Exception as e:
                    log(f"dependency install failed: {e}")
                continue
            if shutdown.exists():
                shutdown.unlink(missing_ok=True)
                log("shut down from the dashboard")
                code = EXIT_SHUTDOWN
                break
            if verify.exists():
                try:
                    self.rollback(verify)
                except Exception as e:
                    log(f"rollback failed: {e}")
                    verify.unlink(missing_ok=True)
                continue
            if self.stopping:
                log("stopped")
                break
            # unexpected exit: relaunch unless it is crash-looping
            crashes = [t for t in crashes if time.time() - t < 300] + [time.time()]
            if len(crashes) <= 3 and time.time() - started > 20:
                log("unexpected exit: relaunching")
                time.sleep(2)
                continue
            log("BUCS stopped unexpectedly; see logs/server.log")
            message_box(f"BUCS stopped unexpectedly.\n\nDetails: {server_log}")
            code = 1
            break
        self.state_file.unlink(missing_ok=True)
        return code


def running_instance(home: Path) -> dict | None:
    try:
        st = json.loads((home / ".update" / "launcher.json").read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else None
    except Exception:
        return None


def pid_alive(pid) -> bool:
    if not pid:
        return False
    if IS_WIN:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                        # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def stop_running(home: Path, lock: InstanceLock) -> int:
    st = running_instance(home)
    if not st:
        log("BUCS is not running")
        return 0
    try:
        http_json(f"http://127.0.0.1:{st['port']}/api/system/shutdown", data=b"{}", timeout=5)
        log("asked BUCS to shut down")
    except Exception as e:
        log(f"shutdown request failed ({e})")
    for _ in range(60):
        if lock.acquire():
            return 0
        time.sleep(0.5)
    for pid in (st.get("server_pid"), st.get("pid")):          # last resort
        if pid_alive(pid):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
    return 0


def main() -> int:
    global log_file
    ap = argparse.ArgumentParser(description="BUCS desktop launcher")
    ap.add_argument("--home", type=Path, default=None, help="writable app directory")
    ap.add_argument("--bundle", type=Path, default=None, help="(macOS) pristine app files to install from")
    ap.add_argument("--open", action="store_true", help="only open the window of a running BUCS")
    ap.add_argument("--stop", action="store_true", help="shut down a running BUCS")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.no_browser:
        os.environ["BUCS_NO_BROWSER"] = "1"
    home = (a.home or default_home()).resolve()
    (home / "logs").mkdir(parents=True, exist_ok=True)
    rotate(home / "logs" / "launcher.log", 1024 * 1024)
    log_file = open(home / "logs" / "launcher.log", "a", encoding="utf-8")

    lock = InstanceLock(home / ".update" / "launcher.lock")
    if a.stop:
        if lock.acquire():
            log("BUCS is not running")
            return 0
        return stop_running(home, lock)
    if not lock.acquire():
        st = running_instance(home)
        log("BUCS is already running: opening its window")
        if st and st.get("port"):
            for _ in range(120):                       # it may still be booting
                if bucs_alive(int(st["port"])):
                    open_window(f"http://127.0.0.1:{st['port']}/")
                    break
                time.sleep(1)
        return 0
    if a.open:
        log("--open: BUCS is not running; starting it")
    try:
        if a.bundle:
            sync_from_bundle(a.bundle.resolve(), home)
        if not (home / "main.py").exists():
            raise SystemExit(f"BUCS app files are missing in {home}; reinstall BUCS")
        return Launcher(home, a.bundle).run()
    except SystemExit as e:
        if e.code not in (None, 0):
            log(f"fatal: {e.code}")
            message_box(f"BUCS could not start:\n\n{e.code}")
            return 1
        raise
    except Exception as e:
        log(f"fatal: {type(e).__name__}: {e}")
        message_box(f"BUCS could not start:\n\n{type(e).__name__}: {e}\n\nLog: {home / 'logs'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
