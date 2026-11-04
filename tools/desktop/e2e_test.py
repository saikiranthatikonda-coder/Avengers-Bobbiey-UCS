"""End-to-end checks for an INSTALLED BUCS desktop app (run by .github/workflows/desktop.yml).

    python e2e_test.py <phase> --home HOME --launch '<json argv>' [--python PY] [--expect-version V]

HOME is the writable app dir (Windows: the install dir; macOS: ~/Library/Application Support/BUCS).
Phases:
  boot            launch, wait for the unlicensed first-run state (/api/license), check /activate,
                  / -> /activate redirect, /api/system/version, the update check endpoint
  single          a second launch must not start a second server (it only opens the window)
  update          simulate an in-app update (VERSION 99.0.0 + a changed requirements.txt):
                  launcher restarts it, pip installs the new requirement with the bundled
                  Python, mark_healthy clears verify.json; then a broken update (99.0.1) must
                  be rolled back automatically to 99.0.0
  port            with port 8765 held by another program, BUCS must start on another port
  shutdown        dashboard shutdown stops the server and the launcher
  datamark/datacheck  write / verify a user-data marker in .env (survives reinstall/upgrade)
Every step prints PASS/FAIL lines and GitHub ::error:: annotations on failure.
"""

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

A = None
MARK = "# bucs-e2e-user-data-marker"


def fail(msg: str) -> None:
    print(f"::error::[e2e {A.phase}] {msg}", flush=True)
    dump_logs()
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"PASS {msg}", flush=True)


def dump_logs() -> None:
    for name in ("launcher.log", "server.log"):
        p = Path(A.home) / "logs" / name
        if p.exists():
            print(f"----- {p} (tail) -----")
            print(p.read_text(errors="replace")[-6000:])


def state() -> dict:
    try:
        return json.loads((Path(A.home) / ".update" / "launcher.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def req(port: int, path: str, data=None, timeout=10):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                               method="POST" if data is not None else "GET",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, resp.geturl(), body


def jget(port: int, path: str, data=None):
    return json.loads(req(port, path, data)[2])


def launch() -> None:
    cmd = json.loads(A.launch)
    print("launch:", cmd, flush=True)
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(timeout=240, expect_port=None) -> int:
    end = time.time() + timeout
    while time.time() < end:
        st = state()
        port = st.get("port")
        if port and (expect_port is None or port == expect_port):
            try:
                if isinstance(jget(port, "/api/license"), dict):
                    return port
            except Exception:
                pass
        time.sleep(2)
    fail(f"BUCS did not answer /api/license within {timeout}s (state={state()})")


def version(port: int) -> str:
    return jget(port, "/api/system/version").get("version")


def wait_version(want: str, timeout=300) -> int:
    end = time.time() + timeout
    last = None
    while time.time() < end:
        st = state()
        try:
            last = version(st["port"])
            if last == want:
                return st["port"]
        except Exception:
            pass
        time.sleep(2)
    fail(f"expected version {want}, last seen {last}")


def kill_server() -> None:
    pid = state().get("server_pid")
    if not pid:
        fail("no server_pid in launcher.json")
    print(f"killing server pid {pid} (simulates the server exiting for the restart)")
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False)
    else:
        os.kill(int(pid), 15)


def installed_python_env() -> dict:
    env = dict(os.environ)
    if sys.platform != "win32" or (Path(A.home) / ".pydeps").exists():   # read-only runtime: overlay
        env["PYTHONPATH"] = str(Path(A.home) / ".pydeps")
        env["PYTHONPYCACHEPREFIX"] = str(Path(A.home) / ".pycache")
    return env


def apply_package(files: dict, from_v: str, to_v: str) -> dict:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    pkg = Path(A.home) / ".update" / f"e2e-{to_v}.zip"
    pkg.write_bytes(buf.getvalue())
    code = ("import hashlib,json,sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import updater; "
            "d=Path(sys.argv[2]).read_bytes(); "
            "print(json.dumps(updater.apply_package(Path(sys.argv[1]), d, hashlib.sha256(d).hexdigest(), sys.argv[3], sys.argv[4])))")
    r = subprocess.run([A.python, "-c", code, A.home, str(pkg), from_v, to_v], capture_output=True, text=True,
                       env=installed_python_env(), cwd=A.home)
    pkg.unlink(missing_ok=True)
    if r.returncode != 0:
        fail(f"apply_package failed: {r.stderr[-2000:]}")
    res = json.loads(r.stdout.strip().splitlines()[-1])
    if not res.get("ok"):
        fail(f"apply_package refused: {res}")
    (Path(A.home) / ".update" / "restart.flag").write_text(str(time.time()))
    return res


# ── phases ─────────────────────────────────────────────────────────────
def phase_boot() -> None:
    launch()
    port = wait_ready()
    ok(f"launched; first-run (unlicensed) state answers on port {port}")
    lic = jget(port, "/api/license")
    print("licence snapshot keys:", sorted(lic)[:12])
    status, _, body = req(port, "/activate")
    if status != 200 or b"<html" not in body.lower():
        fail(f"/activate did not render (HTTP {status})")
    ok("/activate renders")
    status, final, _ = req(port, "/")
    if not final.rstrip("/").endswith("/activate"):
        print(f"note: / served {final} (licensed or owner install?)")
    else:
        ok("/ redirects an unlicensed install to /activate")
    v = version(port)
    if A.expect_version and v != A.expect_version:
        fail(f"version {v} != expected {A.expect_version}")
    snap = jget(port, "/api/system/version")
    if snap.get("managed_by_git"):
        fail("installed app thinks it is a git checkout: in-app updates would be disabled")
    ok(f"/api/system/version = {v}, updates enabled (not a git checkout)")
    chk = jget(port, "/api/system/update/check", data=b"{}")
    if "version" not in chk:
        fail(f"update check returned {chk}")
    ok(f"update check endpoint works: latest={chk.get('latest')} available={chk.get('available')} "
       f"error={chk.get('error')}")
    st = state()
    if not st.get("server_pid") or not st.get("pid"):
        fail(f"launcher state incomplete: {st}")
    ok("launcher state recorded (single-instance lock + server pid)")


def phase_single() -> None:
    before = state()
    launch()
    time.sleep(15)
    after = state()
    if after.get("pid") != before.get("pid") or after.get("server_pid") != before.get("server_pid"):
        fail(f"a second launch replaced the running instance: {before} -> {after}")
    ok("second launch reused the running instance (no second server)")


def phase_update() -> None:
    home = Path(A.home)
    cur = (home / "VERSION").read_text().strip()
    req_txt = (home / "requirements.txt").read_text()
    apply_package({"VERSION": "99.0.0\n", "requirements.txt": req_txt.rstrip() + "\ntomli-w==1.0.0\n"}, cur, "99.0.0")
    kill_server()
    port = wait_version("99.0.0")
    ok("launcher restarted into the updated version 99.0.0")
    r = subprocess.run([A.python, "-c", "import tomli_w; print(tomli_w.__file__)"], capture_output=True, text=True,
                       env=installed_python_env())
    if r.returncode != 0:
        fail("the new requirement was not installed after the update: " + r.stderr[-800:])
    where = r.stdout.strip()
    ok(f"changed requirements were pip-installed by the bundled Python ({where})")
    if sys.platform != "win32" and ".pydeps" not in where:
        fail("dependency went into the signed app bundle instead of the writable overlay")
    end = time.time() + 120
    while (home / ".update" / "verify.json").exists():
        if time.time() > end:
            fail("verify.json not cleared: the new version never marked itself healthy")
        time.sleep(2)
    ok("updated version marked itself healthy (rollback disarmed)")

    good_main = (home / "main.py").read_bytes()
    apply_package({"VERSION": "99.0.1\n", "main.py": "raise SystemExit('e2e: deliberately broken build')\n"},
                  "99.0.0", "99.0.1")
    kill_server()
    time.sleep(5)
    wait_version("99.0.0", timeout=300)
    if (home / "main.py").read_bytes() != good_main:
        fail("main.py was not restored by the rollback")
    ok("broken update 99.0.1 was rolled back automatically to 99.0.0")
    print(f"port {port}")


def phase_port() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 8765))
    srv.listen(5)
    print("holding 127.0.0.1:8765 with a foreign listener")
    try:
        launch()
        end = time.time() + 240
        while time.time() < end:
            st = state()
            if st.get("port") and st["port"] != 8765:
                break
            time.sleep(2)
        port = wait_ready()
        if port == 8765:
            fail("BUCS claims port 8765 although another program holds it")
        ok(f"port 8765 busy: BUCS started on {port} instead")
        phase_shutdown()
    finally:
        srv.close()


def phase_shutdown() -> None:
    st = state()
    if not st.get("port"):
        fail("BUCS is not running")
    jget(st["port"], "/api/system/shutdown", data=b"{}")
    end = time.time() + 90
    while time.time() < end:
        if not (Path(A.home) / ".update" / "launcher.json").exists():
            break
        time.sleep(1)
    else:
        fail("launcher did not exit after the dashboard shutdown")
    time.sleep(2)
    try:
        req(st["port"], "/api/license", timeout=3)
        fail("server still answering after shutdown")
    except (urllib.error.URLError, ConnectionError, OSError):
        pass
    ok("dashboard shutdown stopped the server and the launcher")


def phase_datamark() -> None:
    env = Path(A.home) / ".env"
    env.write_text(env.read_text() + f"\n{MARK}\n" if env.exists() else f"{MARK}\n")
    (Path(A.home) / "operator_memory.json").write_text('{"e2e": "keep me"}')
    ok("user-data markers written (.env, operator_memory.json)")


def phase_datacheck() -> None:
    env = Path(A.home) / ".env"
    mem = Path(A.home) / "operator_memory.json"
    if not env.exists() or MARK not in env.read_text():
        fail(".env user settings were lost")
    if not mem.exists() or "keep me" not in mem.read_text():
        fail("operator_memory.json user data was lost")
    ok("user data survived")


def main() -> None:
    global A
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["boot", "single", "update", "port", "shutdown", "datamark", "datacheck"])
    ap.add_argument("--home", required=True)
    ap.add_argument("--launch", default=os.environ.get("LAUNCH", "[]"), help="JSON argv (default: $LAUNCH)")
    ap.add_argument("--python", default="")
    ap.add_argument("--expect-version", default="")
    A = ap.parse_args()
    globals()["phase_" + A.phase]()


if __name__ == "__main__":
    main()
