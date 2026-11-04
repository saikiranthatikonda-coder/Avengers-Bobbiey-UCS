"""AUTONOMOUS EVOLUTION · sandbox — validate a candidate change in an ISOLATED COPY.

Used by devagent.DevAgent.sandbox(): the engineering CLI edits a throwaway copy of
the repository (never the real working tree), then:

  diff        file-level compare copy ↔ repo (added / modified / deleted, unified diff text)
  tests       a unit-test subset (tests for the changed modules + the security suite) runs
              INSIDE the copy with a scrubbed environment (no .env, no credentials)
  boot        the changed copy boots under uvicorn on a free loopback port (like
              tests/harness.IsolatedServer: Claude/LLM/TTS/voice forced off) and a few
              endpoints must answer 200 with no tracebacks in the log

Nothing here writes to the real repository.
"""

import asyncio
import difflib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

MAX_FILE = 2 * 1024 * 1024
SKIP_DIRS = {".git", ".venv", "__pycache__", ".update", "node_modules", ".claude"}
SKIP_PREFIXES = ("docs/releases/", "site/releases/")
_APP_ENV_PREFIXES = ("JARVIS_", "NEWSAPI", "LOCAL_LLM", "CLAUDE_BIN", "BUCS_")
BOOT_PATHS = ("/api/status", "/api/health", "/api/roadmap", "/api/evolution")
ALWAYS_TESTS = ("test_security",)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def sha(b: bytes | None) -> str | None:
    return None if b is None else hashlib.sha256(b).hexdigest()


def _git_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root,
                           capture_output=True, timeout=30, creationflags=NO_WINDOW)
        if r.returncode != 0:
            return None
        return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]
    except Exception:
        return None


def source_files(root: Path, protected: list[str]) -> list[str]:
    """Code + docs the app needs. Never secrets (PROTECTED) and never gitignored state files."""
    root = Path(root)
    files = _git_files(root)
    if files is None:                                   # no git: the app's own code only
        files = [p.name for p in root.glob("*.py")] + [n for n in ("VERSION", "CLAUDE.md", "requirements.txt")
                                                       if (root / n).exists()]
        for d in ("static", "web3mod", "tests"):
            if (root / d).is_dir():
                files += [str(p.relative_to(root)).replace("\\", "/") for p in (root / d).rglob("*") if p.is_file()]
    prot = {p.lower() for p in protected}
    out = []
    for f in files:
        f = f.replace("\\", "/")
        parts = f.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]) or f.startswith(SKIP_PREFIXES):
            continue
        if parts[-1].lower() in prot or f.lower() in prot or parts[-1].endswith((".pyc", ".key", ".db")):
            continue
        p = root / f
        try:
            if p.is_file() and p.stat().st_size <= MAX_FILE:
                out.append(f)
        except OSError:
            pass
    return sorted(set(out))


def make_copy(root: Path, protected: list[str]) -> tuple[Path, list[str]]:
    box = Path(tempfile.mkdtemp(prefix="bucs-sandbox-"))
    files = source_files(root, protected)
    for f in files:
        dst = box / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(root) / f, dst)
    return box, files


def _walk(box: Path) -> set[str]:
    out = set()
    for p in box.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(box)).replace("\\", "/")
        parts = rel.split("/")
        if any(x in SKIP_DIRS for x in parts[:-1]) or rel.endswith(".pyc") or rel in ("server.log",):
            continue
        out.add(rel)
    return out


def diff(root: Path, box: Path, files: list[str]) -> dict:
    """What the candidate change did, relative to the copied baseline."""
    root, box = Path(root), Path(box)
    now = _walk(box)
    changes, text = {}, []
    for f in sorted(set(files) | now):
        orig = (root / f).read_bytes() if (root / f).is_file() and f in files else None
        new = (box / f).read_bytes() if (box / f).is_file() else None
        if f not in files and new is None:
            continue
        if f not in files:
            orig = None
        if orig == new:
            continue
        changes[f] = {"orig_sha": sha(orig), "new": new, "op": "added" if orig is None else "deleted" if new is None else "modified"}
        try:
            a = (orig or b"").decode("utf-8").splitlines(keepends=True)
            b = (new or b"").decode("utf-8").splitlines(keepends=True)
            text.extend(difflib.unified_diff(a, b, fromfile="a/" + f, tofile="b/" + f, n=2))
        except UnicodeDecodeError:
            text.append(f"Binary file {f} {changes[f]['op']}\n")
    added = sum(1 for l in text if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in text if l.startswith("-") and not l.startswith("---"))
    return {"changes": changes, "diff": "".join(text)[:60000], "added": added, "removed": removed}


def related_tests(box: Path, changed: list[str]) -> list[str]:
    tests_dir = Path(box) / "tests"
    if not tests_dir.is_dir():
        return []
    mods = []
    for f in changed:
        stem = Path(f).stem
        if "/" in f.strip("/") and not f.startswith("static/"):
            stem = f.split("/")[0]
        for cand in (f"test_{stem}", f"test_{stem.split('_')[0]}"):
            if (tests_dir / f"{cand}.py").exists() and cand not in mods:
                mods.append(cand)
        if f.startswith("static/") and (tests_dir / "test_smoke.py").exists() and "test_smoke" not in mods:
            mods.append("test_smoke")
    for t in ALWAYS_TESTS:
        if (tests_dir / f"{t}.py").exists() and t not in mods:
            mods.append(t)
    return mods


def scrubbed_env(owner: bool = True) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_APP_ENV_PREFIXES)}
    env.update({"CLAUDE_BIN": "__ucs_sandbox_no_claude__", "LOCAL_LLM_URL": "http://127.0.0.1:9/v1",
                "JARVIS_TTS": "0", "JARVIS_VOICE": "0", "JARVIS_HOST": "127.0.0.1", "JARVIS_SPEEDTEST_MIN": "0",
                "PYTHONIOENCODING": "utf-8", "BUCS_OWNER": "1" if owner else "0", "BUCS_SANDBOX": "1"})
    return env


def python_exe(root: Path) -> str:
    py = Path(root) / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(py) if py.exists() else sys.executable


def kill_tree(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           creationflags=NO_WINDOW)
        else:
            proc.kill()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def parse_unittest(out: str) -> dict:
    import re
    ran = re.findall(r"^Ran (\d+) tests? in ([\d.]+)s", out, re.M)
    failing = []
    for kind, name, where in re.findall(r"^(FAIL|ERROR): (\S+) \(([\w.]+)\)", out, re.M):
        failing.append(f"{where}.{name}" if not where.endswith(name) else where)
    m = re.search(r"^(OK|FAILED)\b(.*)$", out, re.M)
    skipped = re.search(r"skipped=(\d+)", m.group(2)) if m else None
    return {"ran": int(ran[-1][0]) if ran else 0, "seconds": float(ran[-1][1]) if ran else None,
            "failing": sorted(set(failing)), "ok_line": (m.group(0).strip() if m else ""),
            "skipped_n": int(skipped.group(1)) if skipped else 0}


def run_tests_sync(box: Path, modules: list[str], python: str, timeout: float = 600) -> dict:
    if not modules:
        return {"ok": None, "skipped": "no tests/ in this install (customer build)", "modules": []}
    t0 = time.time()
    proc = subprocess.Popen([python, "-m", "unittest", *[f"tests.{m}" for m in modules]], cwd=box,
                            env=scrubbed_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=NO_WINDOW)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        return {"ok": False, "summary": f"tests timed out after {timeout:.0f} s", "modules": modules}
    text = out.decode("utf-8", "replace")
    from evolution_core import scrub
    p = parse_unittest(text)
    tail = [l for l in text.strip().splitlines()[-6:] if l.strip()]
    return {"ok": proc.returncode == 0, "modules": modules, "ran": p["ran"], "failing": p["failing"],
            "seconds": round(time.time() - t0, 1), "summary": scrub(" | ".join(tail), 400)}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def boot_check_sync(box: Path, python: str, timeout: float = 90) -> dict:
    """Boot the changed copy and hit a few endpoints. The copy's state files stay in the copy."""
    port = _free_port()
    log_path = Path(box) / "server.log"
    t0 = time.time()
    with open(log_path, "wb") as log:
        proc = subprocess.Popen([python, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=box, env=scrubbed_env(), stdout=log, stderr=subprocess.STDOUT,
                                creationflags=NO_WINDOW)
        try:
            ready = None
            while time.time() - t0 < timeout:
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as r:
                        if r.status == 200:
                            ready = time.time() - t0
                            break
                except Exception:
                    time.sleep(0.5)
            probes = []
            if ready is not None:
                for path in BOOT_PATHS:
                    t = time.perf_counter()
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as r:
                            body = r.read()
                            ok = r.status == 200
                            try:
                                json.loads(body)
                            except Exception:
                                ok = False
                            probes.append({"path": path, "status": r.status, "ok": ok,
                                           "ms": round((time.perf_counter() - t) * 1000)})
                    except Exception as e:
                        probes.append({"path": path, "status": getattr(e, "code", None), "ok": False,
                                       "ms": round((time.perf_counter() - t) * 1000), "error": type(e).__name__})
        finally:
            kill_tree(proc)
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        text = ""
    from evolution_core import scrub
    tracebacks = text.count("Traceback (most recent call last)")
    not_loaded = [l.strip() for l in text.splitlines() if "[features]" in l and "not loaded" in l]
    ok = ready is not None and all(p["ok"] for p in probes) and tracebacks == 0
    return {"ok": ok, "boot_s": round(ready, 1) if ready is not None else None, "probes": probes,
            "tracebacks": tracebacks, "modules_not_loaded": [scrub(l, 160) for l in not_loaded][:6],
            "log_tail": scrub(" | ".join(text.strip().splitlines()[-4:]), 400) if not ok else ""}


async def run_tests(box: Path, modules: list[str], python: str) -> dict:
    return await asyncio.to_thread(run_tests_sync, box, modules, python)


async def boot_check(box: Path, python: str) -> dict:
    return await asyncio.to_thread(boot_check_sync, box, python)


def cleanup(box: Path | None) -> None:
    if box:
        shutil.rmtree(box, ignore_errors=True)
