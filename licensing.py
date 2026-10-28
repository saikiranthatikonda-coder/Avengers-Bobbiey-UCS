"""Licensing — one paid plan, pay-to-use, collected over UPI with zero fees.

* OWNER mode (BUCS_OWNER=1 in .env — the Commander's own machine): everything
  unlocked, and the dashboard can ISSUE licence keys.
* CUSTOMER installs are locked until a licence key is activated. The customer
  pays over UPI (website QR), emails the UTR + their install ID, and the owner
  issues a key from the dashboard.

Keys are Ed25519-signed by the owner's private key (license_signing.key —
gitignored, never leaves the owner's machine). Every install verifies them
offline against PUBLIC_KEY below: no licence server, no fees, no network.
A key carries the plan, an expiry, and optionally the install ID it is bound to.

Key format:  BUCS-<base64url(json payload)>.<base64url(signature)>
"""

import base64
import hashlib
import json
import math
import os
import platform
import secrets
import time
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from billing import EDITION_RANK, ENTITLEMENTS

HERE = Path(__file__).parent
FILE = HERE / "license.json"
SIGNING_KEY = HERE / "license_signing.key"
ISSUED = HERE / "licences_issued.json"
# The owner's verification key. Changing it invalidates every key ever issued.
PUBLIC_KEY = "dOo9mgbEs8Eof37z6VDZMVbPpiGMMfwS78Iz8ooP9K0"
PREFIX = "BUCS-"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def install_id() -> str:
    """Stable per-machine ID the customer quotes when paying (binds their key)."""
    raw = f"{platform.node()}|{uuid.getnode()}".encode()
    h = hashlib.sha256(raw).hexdigest()[:10].upper()
    return f"{h[:5]}-{h[5:]}"


def verify_key(key: str, public_key: str = PUBLIC_KEY) -> dict:
    """Returns {"ok": True, "payload": {...}} or {"ok": False, "error": ...}."""
    key = (key or "").strip().replace("\n", "").replace(" ", "")
    if not key.startswith(PREFIX) or "." not in key:
        return {"ok": False, "error": "that isn't a BUCS licence key"}
    body, _, sig = key[len(PREFIX):].partition(".")
    try:
        Ed25519PublicKey.from_public_bytes(_b64d(public_key)).verify(_b64d(sig), body.encode())
        payload = json.loads(_b64d(body))
    except (InvalidSignature, ValueError):
        return {"ok": False, "error": "licence key signature is invalid"}
    return {"ok": True, "payload": payload}


class Licensing:
    def __init__(self, path: Path = FILE, public_key: str = PUBLIC_KEY,
                 signing_key: Path = SIGNING_KEY, issued: Path = ISSUED) -> None:
        self.path = Path(path)
        self.public_key = public_key
        self.signing_key = Path(signing_key)
        self.issued_path = Path(issued)
        self.owner = os.getenv("BUCS_OWNER", "0") == "1"
        self.install_id = install_id()
        self.data: dict = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    # ── state ─────────────────────────────────────────────────────
    def _payload(self) -> dict | None:
        key = self.data.get("key")
        if not key:
            return None
        v = verify_key(key, self.public_key)
        return v["payload"] if v["ok"] else None

    @property
    def edition(self) -> str:
        if self.owner:
            return "pro"
        p = self._payload()
        if not p or p.get("exp", 0) < time.time():
            return "community"
        if p.get("iid") and p["iid"] != self.install_id:
            return "community"
        return p.get("ed", "pro") if p.get("ed") in EDITION_RANK else "pro"

    @property
    def paid(self) -> bool:
        return self.owner or self.edition != "community"

    def allows(self, feature: str) -> bool:
        need = ENTITLEMENTS.get(feature, "pro")
        return EDITION_RANK.get(self.edition, 0) >= EDITION_RANK.get(need, 1)

    def snapshot(self) -> dict:
        p = self._payload() or {}
        key = self.data.get("key", "")
        exp = p.get("exp")
        return {"owner": self.owner, "edition": self.edition, "paid": self.paid,
                "licensed": bool(p) and self.edition != "community",
                "key": (key[:9] + "…" + key[-4:]) if key else "",
                "customer": p.get("to"), "expires_at": exp,
                "days_left": max(0, math.ceil((exp - time.time()) / 86400)) if exp else None,
                "expired": bool(exp and exp < time.time()),
                "install_id": self.install_id,
                "can_issue": self.owner and self.signing_key.exists(),
                "entitlements": {f: self.allows(f) for f in ENTITLEMENTS}}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ── customer: activate / remove ───────────────────────────────
    def activate(self, key: str) -> dict:
        v = verify_key(key, self.public_key)
        if not v["ok"]:
            return v
        p = v["payload"]
        if p.get("exp", 0) < time.time():
            return {"ok": False, "error": "this licence key has expired — renew to get a new one"}
        if p.get("iid") and p["iid"] != self.install_id:
            return {"ok": False, "error": f"this key belongs to another install (this one is {self.install_id})"}
        self.data = {"key": key.strip().replace("\n", "").replace(" ", ""), "activated_at": time.time()}
        self._save()
        return {"ok": True, **self.snapshot()}

    def deactivate(self) -> dict:
        self.data = {}
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": True, **self.snapshot()}

    # ── owner: issue keys ─────────────────────────────────────────
    def issue(self, customer: str, days: int = 30, iid: str | None = None,
              payment_ref: str | None = None, edition: str = "pro") -> dict:
        if not self.owner:
            return {"ok": False, "error": "only the owner's machine can issue licences"}
        if not self.signing_key.exists():
            return {"ok": False, "error": "license_signing.key is missing on this machine"}
        customer = (customer or "").strip()[:80]
        if not customer:
            return {"ok": False, "error": "customer email or name is required"}
        days = max(1, min(int(days or 30), 3660))
        now = int(time.time())
        payload = {"v": 1, "id": secrets.token_hex(4), "to": customer, "ed": edition,
                   "iat": now, "exp": now + days * 86400}
        iid = (iid or "").strip().upper()
        if iid:
            payload["iid"] = iid
        body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
        sk = serialization.load_pem_private_key(self.signing_key.read_bytes(), password=None)
        assert isinstance(sk, Ed25519PrivateKey)
        key = PREFIX + body + "." + _b64e(sk.sign(body.encode()))
        rec = {**payload, "payment_ref": (payment_ref or "").strip()[:40]}
        log = self.issued()
        log.insert(0, rec)
        tmp = self.issued_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(log[:2000], indent=1), encoding="utf-8")
        tmp.replace(self.issued_path)
        return {"ok": True, "key": key, "record": rec}

    def issued(self) -> list:
        try:
            return json.loads(self.issued_path.read_text(encoding="utf-8"))
        except Exception:
            return []
