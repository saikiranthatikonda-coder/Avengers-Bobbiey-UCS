"""Publish a BUCS version upgrade to every install (owner machine only).

    1. bump VERSION, commit your changes (tests green)
    2. python tools/release.py --notes "What changed" --notes "Another change"
    3. git add docs site && git commit -m "release vX.Y.Z" && git push

The package is built from the committed HEAD (git archive), hashed, and the
manifest is signed with license_signing.key, so installs accept only releases
you signed. Installs see "Update available" within 12 hours (or on CHECK NOW).
"""

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import updater  # noqa: E402

# app files only; the website, tests and owner tooling don't ship in updates
EXCLUDE = ["docs", "site", "tests", "tools", "desktop.png", ".claude", ".github"]
KEEP = 2   # packages kept on the site (the latest is all an install needs)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", action="append", default=[], help="release note line (repeat)")
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()

    key = ROOT / "license_signing.key"
    if not key.exists():
        sys.exit("license_signing.key not found: releases can only be signed on the owner's machine")
    dirty = [l for l in git("status", "--porcelain").splitlines()
             if not l[3:].startswith(("docs/", "site/"))]
    if dirty and not a.allow_dirty:
        sys.exit("commit your changes first (uncommitted: " + ", ".join(l[3:] for l in dirty[:5]) + ")")
    version = git("show", "HEAD:VERSION").strip()
    if not a.notes:
        sys.exit("add at least one --notes line: customers see these in the update prompt")

    pkg_dir = ROOT / "docs" / "releases"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg = pkg_dir / f"bucs-{version}.zip"
    spec = ["."] + [f":!{e}" for e in EXCLUDE]
    subprocess.run(["git", "archive", "--format=zip", "-o", str(pkg), "HEAD", "--", *spec], cwd=ROOT, check=True)
    data = pkg.read_bytes()
    manifest = {
        "kind": "bucs-release", "version": version,
        "date": datetime.date.today().isoformat(), "commit": git("rev-parse", "--short", "HEAD"),
        "notes": a.notes, "url": f"releases/{pkg.name}",
        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
    }
    signed = updater.sign_manifest(manifest, key)
    assert updater.verify_manifest(signed), "signature self-check failed"
    (ROOT / "docs" / "releases.json").write_text(json.dumps(signed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    old = sorted(pkg_dir.glob("bucs-*.zip"), key=lambda p: updater.vtuple(p.stem[5:]), reverse=True)
    for stale in old[KEEP:]:
        stale.unlink()
    # mirror for the Netlify copy of the site
    shutil.copy2(ROOT / "docs" / "releases.json", ROOT / "site" / "releases.json")
    if (ROOT / "site" / "releases").exists():
        shutil.rmtree(ROOT / "site" / "releases")
    shutil.copytree(pkg_dir, ROOT / "site" / "releases")
    print(f"release v{version} ({manifest['commit']}) · {len(data) / 1024:.0f} KB · sha256 {manifest['sha256'][:12]}…")
    print("now: git add docs site && git commit -m \"release v%s\" && git push" % version)


if __name__ == "__main__":
    main()
