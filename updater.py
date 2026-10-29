"""In-app updates — signed releases published on the BUCS website.

Release (owner):  tools/release.py builds a package from a committed version,
                  hashes it and signs the manifest with the owner key
                  (license_signing.key), then it is pushed to GitHub Pages.
Install (users):  BUCS checks releases.json, verifies the Ed25519 signature
                  against licensing.PUBLIC_KEY and the package SHA-256, backs up
                  every file it will replace, extracts the package (never
                  touching user data: licence, settings, memory, tokens), and
                  restarts. run.ps1 rolls back automatically if the new version
                  exits before it is marked healthy.

A git checkout (the owner's machine) is never modified — it updates via git.
"""

import asyncio
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

import licensing

HERE = Path(__file__).parent
SITE = "https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/"
RELEASES_URL = SITE + "releases.json"
MAX_PACKAGE = 50 * 1024 * 1024

# never written by an update, even if a package contained them
PRESERVE = {
    ".env", "license.json", "license_signing.key", "licences_issued.json", "providers.json",
    "commander_memory.md", "operator_memory.json", "token.json", "credentials.json", "auth.json",
    "fleet_token.txt", "pair_code.txt", "node_id.txt", "governance.json", "audio_pref.json",
    "audit.log", "model_pref.json", "billing.json", "productivity.json", "team_memory.json",
}
SKIP_DIRS = (".venv/", ".git/", ".update/", "__pycache__/")


def read_version(root: Path = HERE) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


def vtuple(v: str) -> tuple:
    out = []
    for part in str(v).strip().lstrip("v").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def _canonical(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_manifest(manifest: dict, key_path: Path) -> dict:
    sk = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    assert isinstance(sk, Ed25519PrivateKey)
    return {**manifest, "sig": licensing._b64e(sk.sign(_canonical(manifest)))}


def verify_manifest(manifest: dict, public_key: str = licensing.PUBLIC_KEY) -> bool:
    if not isinstance(manifest, dict) or manifest.get("kind") != "bucs-release":
        return False
    try:
        Ed25519PublicKey.from_public_bytes(licensing._b64d(public_key)).verify(
            licensing._b64d(manifest.get("sig", "")), _canonical(manifest))
        return True
    except (InvalidSignature, ValueError):
        return False


def _safe_member(name: str) -> str | None:
    """Relative POSIX path inside the install, or None if the entry must be skipped."""
    n = name.replace("\\", "/")
    if n.endswith("/") or not n:
        return None
    if n.startswith("/") or ":" in n.split("/")[0] or any(p == ".." for p in n.split("/")):
        raise ValueError(f"unsafe path in package: {name}")
    if any(n.startswith(d) or f"/{d}" in n for d in SKIP_DIRS):
        return None
    if n in PRESERVE:
        return None
    return n


def apply_package(root: Path, data: bytes, sha256: str, from_v: str, to_v: str) -> dict:
    """Verify + back up + extract. Returns the verify record (also written to .update/verify.json)."""
    root = Path(root)
    if (root / ".git").exists():
        return {"ok": False, "error": "this install is a git checkout — update it with git pull"}
    if hashlib.sha256(data).hexdigest() != sha256:
        return {"ok": False, "error": "package checksum mismatch — update refused"}
    zf = zipfile.ZipFile(io.BytesIO(data))
    members = []
    for info in zf.infolist():
        rel = _safe_member(info.filename)          # raises on path traversal
        if rel:
            members.append((info, rel))
    upd = root / ".update"
    backup = upd / f"backup-{from_v}"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)
    created, replaced = [], []
    for _, rel in members:
        target = root / rel
        if target.exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup / rel)
            replaced.append(rel)
        else:
            created.append(rel)
    req_before = (root / "requirements.txt").read_bytes() if (root / "requirements.txt").exists() else b""
    for info, rel in members:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    record = {"from": from_v, "to": to_v, "backup": str(backup), "created": created,
              "replaced": len(replaced), "applied_at": time.time(),
              "requirements_changed": (root / "requirements.txt").read_bytes() != req_before
              if (root / "requirements.txt").exists() else False}
    (upd / "verify.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return {"ok": True, **record}


class Updater:
    def __init__(self, root: Path = HERE, url: str | None = None, public_key: str = licensing.PUBLIC_KEY) -> None:
        # BUCS_UPDATE_URL points an install at another release channel (staging / tests);
        # read here, not at import, because main.py loads .env after importing modules
        self.url = url or os.getenv("BUCS_UPDATE_URL") or RELEASES_URL
        self.root, self.public_key = Path(root), public_key
        self.current = read_version(self.root)
        self.git = (self.root / ".git").exists()
        self.state: dict = {"checked_at": None, "available": False, "latest": None, "notes": [],
                            "error": None, "phase": "idle"}

    def snapshot(self) -> dict:
        return {"version": self.current, "managed_by_git": self.git, **self.state}

    async def check(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                r = await c.get(self.url, params={"t": int(time.time())})
                r.raise_for_status()
                m = r.json()
            if not verify_manifest(m, self.public_key):
                raise ValueError("release manifest signature is invalid — ignored")
            self.manifest = m
            newer = vtuple(m["version"]) > vtuple(self.current)
            self.state.update(checked_at=time.time(), latest=m["version"], notes=m.get("notes", []),
                              date=m.get("date"), size=m.get("size"), available=newer, error=None)
        except httpx.HTTPStatusError as e:
            msg = ("no release has been published yet" if e.response.status_code == 404
                   else f"release server answered HTTP {e.response.status_code}")
            self.state.update(checked_at=time.time(), error=msg)
        except httpx.HTTPError:
            self.state.update(checked_at=time.time(), error="offline: couldn't reach the release server")
        except Exception as e:
            self.state.update(checked_at=time.time(), error=str(e)[:160])
        return self.snapshot()

    async def apply(self) -> dict:
        if self.git:
            return {"ok": False, "error": "this install is a git checkout — update it with git pull"}
        await self.check()
        if not self.state.get("available"):
            return {"ok": False, "error": self.state.get("error") or "already on the latest version"}
        m = self.manifest
        self.state["phase"] = "downloading"
        try:
            url = urljoin(self.url, m["url"])      # package lives next to the manifest
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
                r = await c.get(url)
                r.raise_for_status()
                data = r.content
            if len(data) > MAX_PACKAGE or len(data) != int(m.get("size", len(data))):
                raise ValueError("package size does not match the signed manifest")
            self.state["phase"] = "installing"
            res = await asyncio.to_thread(apply_package, self.root, data, m["sha256"], self.current, m["version"])
            if not res.get("ok"):
                raise ValueError(res.get("error"))
            if res.get("requirements_changed"):
                self.state["phase"] = "installing dependencies"
                py = self.root / ".venv" / "Scripts" / "python.exe"
                py = py if py.exists() else Path(sys.executable)
                await asyncio.to_thread(subprocess.run, [str(py), "-m", "pip", "install", "-q", "-r",
                                                         str(self.root / "requirements.txt")], check=False)
            self.state["phase"] = "restarting"
            return {"ok": True, "from": self.current, "to": m["version"]}
        except Exception as e:
            self.state.update(phase="idle", error=str(e)[:160])
            return {"ok": False, "error": str(e)[:160]}

    def mark_healthy(self) -> None:
        """Called once the (possibly new) version has run cleanly — disarms rollback."""
        v = self.root / ".update" / "verify.json"
        if v.exists():
            try:
                rec = json.loads(v.read_text(encoding="utf-8"))
                self.state["just_updated"] = {"from": rec.get("from"), "to": rec.get("to")}
            except Exception:
                pass
            v.unlink(missing_ok=True)
        # keep only the two most recent backups
        backups = sorted((self.root / ".update").glob("backup-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[2:]:
            shutil.rmtree(old, ignore_errors=True)


def request_exit(root: Path, reason: str) -> None:
    """Ask the launcher to stop (shutdown) or relaunch (restart), then exit cleanly."""
    upd = Path(root) / ".update"
    upd.mkdir(exist_ok=True)
    (upd / f"{reason}.flag").write_text(str(time.time()), encoding="utf-8")
