"""Authenticated Remote Access — Roadmap Phase 4 (enterprise authentication).

The security perimeter that makes JARVIS_HOST=0.0.0.0 safe:

  · loopback (the host machine) is ALWAYS trusted — the operator at the
    console never sees a login
  · remote access is DENIED BY DEFAULT: until a password is set from the
    host, no remote request gets past the gate
  · remote operators log in at /login → HMAC-signed, HttpOnly session
    cookie (12 h, revocable, invalidated on restart)
  · programmatic access via API bearer tokens (stored as SHA-256 hashes,
    shown in full exactly once)
  · per-IP rate limiting: 5 failed logins → 5-minute lockout
  · everything persisted to auth.json (gitignored); passwords hashed with
    PBKDF2-HMAC-SHA256 (200k iterations) — stdlib only, no dependencies
"""

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

FILE = Path(__file__).parent / "auth.json"

PBKDF2_ITERS = 200_000
SESSION_TTL = 12 * 3600
MAX_FAILURES = 5
LOCKOUT_SEC = 300


class AuthManager:
    def __init__(self) -> None:
        self.data = {"password": None, "secret": None, "tokens": []}
        try:
            saved = json.loads(FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.data.update(saved)
        except Exception:
            pass
        if not self.data.get("secret"):
            self.data["secret"] = secrets.token_hex(32)
            self._save()
        self.sessions: dict[str, dict] = {}      # sid → {ip, ua, created, exp}
        self._failures: dict[str, list[float]] = {}  # ip → [ts, ...]

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        except Exception:
            pass

    # ── password ──────────────────────────────────────────────────
    @property
    def password_set(self) -> bool:
        return bool(self.data.get("password"))

    def set_password(self, password: str) -> bool:
        password = (password or "").strip()
        if len(password) < 8:
            return False
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERS).hex()
        self.data["password"] = {"salt": salt, "hash": digest,
                                 "iters": PBKDF2_ITERS, "set_at": time.time()}
        self._save()
        return True

    def verify_password(self, password: str) -> bool:
        rec = self.data.get("password")
        if not rec:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode(),
            bytes.fromhex(rec["salt"]), rec.get("iters", PBKDF2_ITERS)).hex()
        return hmac.compare_digest(digest, rec["hash"])

    # ── login rate limiting (per IP) ──────────────────────────────
    def is_locked(self, ip: str) -> float:
        """0 if allowed, else seconds remaining in the lockout."""
        now = time.time()
        fails = [t for t in self._failures.get(ip, []) if now - t < LOCKOUT_SEC]
        self._failures[ip] = fails
        if len(fails) >= MAX_FAILURES:
            return round(LOCKOUT_SEC - (now - fails[0]))
        return 0

    def record_failure(self, ip: str) -> int:
        self._failures.setdefault(ip, []).append(time.time())
        return len(self._failures[ip])

    def clear_failures(self, ip: str) -> None:
        self._failures.pop(ip, None)

    # ── sessions (HMAC-signed cookie value) ───────────────────────
    def _sign(self, payload: str) -> str:
        return hmac.new(bytes.fromhex(self.data["secret"]),
                        payload.encode(), hashlib.sha256).hexdigest()[:32]

    def create_session(self, ip: str, ua: str = "") -> str:
        sid = secrets.token_hex(16)
        exp = int(time.time() + SESSION_TTL)
        self.sessions[sid] = {"ip": ip, "ua": ua[:80], "created": time.time(),
                              "exp": exp}
        payload = f"{sid}.{exp}"
        return f"{payload}.{self._sign(payload)}"

    def verify_session(self, cookie: str) -> bool:
        try:
            sid, exp_s, sig = (cookie or "").split(".")
            if not hmac.compare_digest(sig, self._sign(f"{sid}.{exp_s}")):
                return False
            if int(exp_s) < time.time():
                self.sessions.pop(sid, None)
                return False
            return sid in self.sessions
        except Exception:
            return False

    def revoke_session(self, sid_prefix: str) -> bool:
        for sid in list(self.sessions):
            if sid.startswith(sid_prefix):
                del self.sessions[sid]
                return True
        return False

    def session_list(self) -> list[dict]:
        now = time.time()
        for sid in list(self.sessions):
            if self.sessions[sid]["exp"] < now:
                del self.sessions[sid]
        return [{"sid": sid[:8], "ip": s["ip"], "ua": s["ua"],
                 "age_min": round((now - s["created"]) / 60)}
                for sid, s in self.sessions.items()]

    # ── API bearer tokens ─────────────────────────────────────────
    def create_api_token(self, label: str) -> str:
        raw = "bucs_" + secrets.token_hex(20)
        self.data.setdefault("tokens", []).append({
            "label": (label or "token").strip()[:32],
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "prefix": raw[:12],
            "created": time.time(),
        })
        self._save()
        return raw

    def verify_api_token(self, token: str) -> bool:
        if not token:
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        return any(hmac.compare_digest(digest, t["sha256"])
                   for t in self.data.get("tokens", []))

    def revoke_api_token(self, prefix: str) -> bool:
        before = len(self.data.get("tokens", []))
        self.data["tokens"] = [t for t in self.data.get("tokens", [])
                               if t.get("prefix") != prefix]
        if len(self.data["tokens"]) != before:
            self._save()
            return True
        return False

    # ── panel snapshot (never exposes secrets) ────────────────────
    def snapshot(self) -> dict:
        return {
            "password_set": self.password_set,
            "sessions": self.session_list(),
            "tokens": [{"label": t["label"], "prefix": t["prefix"],
                        "created": t["created"]}
                       for t in self.data.get("tokens", [])],
        }
