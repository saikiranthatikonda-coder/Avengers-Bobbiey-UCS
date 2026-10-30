"""Payment gateway abstraction + Razorpay.

Gateway contract (any rail — Razorpay today; x402 / MPP / AP2 later — must
implement it; they are NOT interchangeable, each adds its own verification):

  configured / mode                 keys present? "test" or "live"
  create_order(amount, receipt, notes)   → gateway order the buyer pays
  checkout(order, gateway_order)    → what the client needs to pay
  verify_client_signature(...)      → cheap first check of the client's claim
  fetch_payment(payment_id)         → the gateway's own record (the source of truth)
  capture(payment_id, amount)       → capture an authorized payment
  verify_webhook(raw_body, sig)     → authenticity of a webhook
  refund(payment_id, amount)        → refund entity
  settlement_recon(day)             → which payments settled into which payout

Credentials come only from server-side environment variables (.env):
  RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
The payout bank account is configured inside the Razorpay dashboard; BUCS has
no API that can change where money goes or move money out.
"""

import hashlib
import hmac
import os
import time

import httpx

RZP_API = "https://api.razorpay.com/v1"


class GatewayError(Exception):
    pass


class Razorpay:
    name = "razorpay"

    def __init__(self) -> None:
        self.key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
        self.key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
        self.webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    @property
    def mode(self) -> str:
        # Razorpay keys carry their mode in the prefix; anything else is treated as test
        return "live" if self.key_id.startswith("rzp_live_") else "test"

    def status(self) -> dict:
        return {"gateway": self.name, "configured": self.configured, "mode": self.mode if self.configured else None,
                "key_id": (self.key_id[:12] + "…") if self.key_id else None,
                "webhook_secret_set": bool(self.webhook_secret)}

    async def _call(self, method: str, path: str, **kw) -> dict:
        if not self.configured:
            raise GatewayError("Razorpay is not configured: add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env")
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=6.0), auth=(self.key_id, self.key_secret)) as c:
            r = await c.request(method, RZP_API + path, **kw)
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code >= 400:
            msg = (data.get("error") or {}).get("description") or f"HTTP {r.status_code}"
            raise GatewayError(f"Razorpay: {msg}")
        return data

    async def create_order(self, amount_paise: int, receipt: str, notes: dict) -> dict:
        return await self._call("POST", "/orders", json={"amount": int(amount_paise), "currency": "INR",
                                                         "receipt": receipt[:40], "notes": notes})

    def checkout(self, order: dict, gorder: dict) -> dict:
        return {"gateway": self.name, "key_id": self.key_id, "order_id": gorder["id"], "amount": gorder["amount"],
                "currency": gorder.get("currency", "INR"), "script": "https://checkout.razorpay.com/v1/checkout.js"}

    def verify_client_signature(self, gateway_order_id: str, payment_id: str, signature: str) -> bool:
        # documented: HMAC_SHA256(order_id + "|" + payment_id, key_secret)
        want = hmac.new(self.key_secret.encode(), f"{gateway_order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return bool(signature) and hmac.compare_digest(want, signature)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        if not self.webhook_secret or not signature:
            return False
        want = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, signature)

    async def fetch_payment(self, payment_id: str) -> dict:
        return await self._call("GET", f"/payments/{payment_id}")

    async def capture(self, payment_id: str, amount_paise: int) -> dict:
        return await self._call("POST", f"/payments/{payment_id}/capture", json={"amount": int(amount_paise), "currency": "INR"})

    async def refund(self, payment_id: str, amount_paise: int, note: str = "") -> dict:
        return await self._call("POST", f"/payments/{payment_id}/refund",
                                json={"amount": int(amount_paise), "notes": {"reason": note[:200]}})

    async def settlement_recon(self, day: float | None = None) -> list[dict]:
        t = time.localtime(day or time.time())
        data = await self._call("GET", "/settlements/recon/combined",
                                params={"year": t.tm_year, "month": t.tm_mon, "day": t.tm_mday, "count": 1000})
        return data.get("items", [])


def gateway():
    """The configured gateway (Razorpay for the India launch)."""
    return Razorpay()
