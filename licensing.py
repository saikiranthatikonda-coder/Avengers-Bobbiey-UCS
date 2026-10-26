"""Licensing — real paid access for customer installs, never a lockout for the owner.

* OWNER mode (BUCS_OWNER=1 in .env — the Commander's own machine): every
  feature unlocked, no licence needed.
* CUSTOMER installs start on Community. A purchase on the website produces a
  licence key (LemonSqueezy issues them automatically at checkout); activating
  it here unlocks the paid edition. Verification uses LemonSqueezy's public
  License API (activate / validate) — no secret key lives in the app.
  Re-validated every 24 h; 7-day offline grace; deactivate frees the seat.

Edition comes from the product/variant name on the licence ("Pro", "Team",
"Enterprise"). billing.ENTITLEMENTS maps features → minimum edition, and the
API enforces them at a few clear seams (cloud LLM providers, Google sync,
engineering mode). Nothing here is simulated: no key → no paid edition.
"""

import json
import os
import platform
import time
from pathlib import Path

import httpx

from billing import EDITION_RANK, ENTITLEMENTS

FILE = Path(__file__).parent / "license.json"
LS_API = "https://api.lemonsqueezy.com/v1/licenses"
REVALIDATE_S = 24 * 3600
GRACE_S = 7 * 24 * 3600


def edition_from_names(*names: str) -> str:
    low = " ".join(n or "" for n in names).lower()
    for ed in ("enterprise", "team", "pro"):
        if ed in low:
            return ed
    return "pro"                      # any paid product without a tier name → Pro


class Licensing:
    def __init__(self, path: Path = FILE) -> None:
        self.path = Path(path)
        self.owner = os.getenv("BUCS_OWNER", "0") == "1"
        self.data: dict = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    # ── state ─────────────────────────────────────────────────────
    @property
    def edition(self) -> str:
        if self.owner:
            return "enterprise"
        d = self.data
        if not d.get("key") or d.get("status") not in ("active",):
            return "community"
        if time.time() - d.get("validated_at", 0) > REVALIDATE_S + GRACE_S:
            return "community"            # offline too long — fall back honestly
        return d.get("edition", "community")

    def allows(self, feature: str) -> bool:
        need = ENTITLEMENTS.get(feature, "community")
        return EDITION_RANK.get(self.edition, 0) >= EDITION_RANK.get(need, 0)

    def snapshot(self) -> dict:
        d = self.data
        key = d.get("key", "")
        return {"owner": self.owner, "edition": self.edition,
                "licensed": bool(key) and d.get("status") == "active",
                "key": (key[:4] + "…" + key[-4:]) if key else "",
                "product": d.get("product"), "variant": d.get("variant"),
                "status": d.get("status"), "validated_at": d.get("validated_at"),
                "customer": d.get("customer_email_masked"),
                "entitlements": {f: self.allows(f) for f in ENTITLEMENTS}}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ── LemonSqueezy License API ──────────────────────────────────
    async def _post(self, action: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{LS_API}/{action}", data=payload, headers={"Accept": "application/json"})
            try:
                return r.json()
            except Exception:
                return {"error": f"HTTP {r.status_code}"}

    def _absorb(self, res: dict, key: str) -> None:
        meta, lk = res.get("meta") or {}, res.get("license_key") or {}
        email = meta.get("customer_email") or ""
        self.data.update({
            "key": key, "status": lk.get("status") or ("active" if res.get("valid") or res.get("activated") else "invalid"),
            "product": meta.get("product_name"), "variant": meta.get("variant_name"),
            "edition": edition_from_names(meta.get("product_name"), meta.get("variant_name")),
            "validated_at": time.time(),
            "customer_email_masked": (email[:2] + "…@" + email.split("@")[-1]) if "@" in email else None,
        })

    async def activate(self, key: str) -> dict:
        key = (key or "").strip()
        if len(key) < 8:
            return {"ok": False, "error": "that doesn't look like a licence key"}
        res = await self._post("activate", {"license_key": key,
                                            "instance_name": f"BUCS · {platform.node()}"[:60]})
        if not res.get("activated"):
            return {"ok": False, "error": res.get("error") or "activation refused"}
        self._absorb(res, key)
        self.data["instance_id"] = (res.get("instance") or {}).get("id")
        self._save()
        return {"ok": True, **self.snapshot()}

    async def validate(self) -> dict:
        d = self.data
        if not d.get("key"):
            return {"ok": False, "error": "no licence"}
        try:
            res = await self._post("validate", {"license_key": d["key"],
                                                "instance_id": d.get("instance_id") or ""})
        except Exception as e:                      # offline → grace period applies
            return {"ok": False, "error": f"offline: {e}", **self.snapshot()}
        if res.get("valid"):
            self._absorb(res, d["key"])
        else:
            self.data["status"] = (res.get("license_key") or {}).get("status") or "invalid"
        self._save()
        return {"ok": bool(res.get("valid")), **self.snapshot()}

    async def deactivate(self) -> dict:
        d = self.data
        if d.get("key") and d.get("instance_id"):
            try:
                await self._post("deactivate", {"license_key": d["key"], "instance_id": d["instance_id"]})
            except Exception:
                pass
        self.data = {}
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": True, **self.snapshot()}
