"""Stage a self-contained BUCS desktop build (bundled CPython 3.11 + dependencies + app files).

    python tools/desktop/build.py --target windows-x64|macos-arm64|macos-x64|linux-x64 [--out build/desktop]

Produces <out>/<target>/stage/:
    python/       relocatable CPython from astral-sh/python-build-standalone (pinned, SHA-256 checked)
                  with requirements.txt installed into its site-packages
    launcher/     bucs_launcher.py (the desktop launcher; not touched by in-app updates)
    app/          the customer app files: exactly what tools/release.py ships (git archive of
                  HEAD minus release.EXCLUDE), so in-app updates apply over the same file set
    build.json    version / build id / target

The installers (windows/bucs.iss, macos/build_app.sh, linux/build_appimage.sh) package that stage directory.
Must run on the target OS (pip resolves native wheels for the running platform).
"""

import argparse
import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

PBS_TAG = "20261021"
PBS_PY = "3.11.17"
PBS = {   # target -> (triple, sha256 from the release's SHA256SUMS)
    "windows-x64": ("x86_64-pc-windows-msvc", "136bbb2e50fe037fd5e32126e70e95b35d1fa5dac5bd04a55b8088415ee99540"),
    "macos-arm64": ("aarch64-apple-darwin", "b5199d66966141fb6d5447512d2b2fe0398d417da37b1949f58db3792d314eac"),
    "macos-x64": ("x86_64-apple-darwin", "c647c272312c03301e399f0e0fdb2b876fed213ee6985cd2d232d588a316483f"),
    "linux-x64": ("x86_64-unknown-linux-gnu", "7cc8d9dce9ea23c31267e7f244005c8979da04794b7737a3a30e62b03abeb120"),
}
# stdlib parts a server never needs (saves ~40 MB)
STRIP = ("test", "idlelib", "turtledemo", "tkinter", "lib2to3/tests", "unittest/test", "ensurepip/_bundled")


def release_exclude() -> list:
    tree = ast.parse((ROOT / "tools" / "release.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "EXCLUDE" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("EXCLUDE not found in tools/release.py")


def fetch_python(target: str, cache: Path) -> Path:
    triple, sha = PBS[target]
    name = f"cpython-{PBS_PY}+{PBS_TAG}-{triple}-install_only.tar.gz"
    path = cache / name
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/{name.replace('+', '%2B')}"
        print(f"[build] downloading {url}")
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=300) as r:
            data = r.read()
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            raise SystemExit(f"python download checksum mismatch: {got} != {sha}")
        path.write_bytes(data)
    return path


def run(cmd: list, **kw) -> None:
    print("[build] $ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, choices=sorted(PBS))
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "desktop")
    ap.add_argument("--build-id", default=os.getenv("GITHUB_SHA", "local")[:10] + "-" + os.getenv("GITHUB_RUN_NUMBER", "0"))
    a = ap.parse_args()

    work = a.out / a.target
    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    # 1) bundled Python
    tgz = fetch_python(a.target, a.out / "cache")
    with tarfile.open(tgz) as t:
        t.extractall(stage, filter="tar") if hasattr(tarfile, "data_filter") else t.extractall(stage)
    py_dir = stage / "python"
    py = py_dir / ("python.exe" if a.target.startswith("windows") else "bin/python3")
    lib = py_dir / ("Lib" if a.target.startswith("windows") else f"lib/python{PBS_PY.rsplit('.', 1)[0]}")
    for rel in STRIP:
        shutil.rmtree(lib / rel, ignore_errors=True)
    shutil.rmtree(py_dir / "tcl", ignore_errors=True)            # Tk runtime (Windows), unused
    for pdb in py_dir.rglob("*.pdb"):                            # Windows debug symbols (~80 MB)
        pdb.unlink()

    # 2) dependencies into the bundled interpreter
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONNOUSERSITE": "1"}
    run([py, "-m", "pip", "install", "--no-warn-script-location", "--upgrade", "pip"], env=env)
    run([py, "-m", "pip", "install", "--no-warn-script-location", "-r", ROOT / "requirements.txt"], env=env)
    run([py, "-c", "import fastapi, uvicorn, httpx, psutil, apscheduler, dotenv, pydantic, cryptography, "
                   "googleapiclient, google_auth_oauthlib; print('[build] dependency import check ok')"], env=env)
    for cache_dir in py_dir.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)       # compiled on the user's machine instead

    # 3) app files: identical set to the release zip (git archive HEAD minus EXCLUDE)
    spec = ["."] + [f":!{e}" for e in release_exclude()]
    tar_bytes = subprocess.run(["git", "archive", "--format=tar", "HEAD", "--", *spec], cwd=ROOT,
                               check=True, capture_output=True).stdout
    app = stage / "app"
    app.mkdir()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as t:
        t.extractall(app, filter="tar") if hasattr(tarfile, "data_filter") else t.extractall(app)
    if not (app / "main.py").exists():
        raise SystemExit("app files missing main.py")

    # 4) launcher + build info
    (stage / "launcher").mkdir()
    shutil.copy2(HERE / "bucs_launcher.py", stage / "launcher" / "bucs_launcher.py")
    version = (app / "VERSION").read_text(encoding="utf-8").strip()
    info = {"version": version, "build": f"{version}+{a.build_id}", "target": a.target,
            "python": PBS_PY, "pbs": PBS_TAG}
    (stage / "build.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    (work / "version.txt").write_text(version, encoding="utf-8")
    size = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file())
    print(f"[build] staged BUCS {version} for {a.target}: {size / 1e6:.0f} MB in {stage}")


if __name__ == "__main__":
    sys.exit(main())
