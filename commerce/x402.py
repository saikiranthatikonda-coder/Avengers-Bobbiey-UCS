"""x402 seller side — agents pay per request in USDC over HTTP 402.

Flow for every paid call (settle-after-delivery):
  1. no payment header           → 402 with the payment terms (v2 in the PAYMENT-REQUIRED header,
                                   v1 in the JSON body, so both client generations can pay)
  2. payment header present       → facilitator /verify (signature, amount, payTo, network, expiry)
  3. verified                     → run the service
  4. service succeeded            → facilitator /settle (this is what moves the money on-chain)
  5. settled                      → 200 + result + PAYMENT-RESPONSE (tx hash); recorded in the ledger
  A failed service is never settled, so the agent is never charged for an error.

Disabled until the owner configures it (.env):
  X402_PAY_TO           the owner's wallet address that RECEIVES USDC (nothing here can send funds)
  X402_NETWORK          eip155:84532 (Base Sepolia, test — default) or eip155:8453 (Base mainnet, real money)
  X402_FACILITATOR_URL  default https://x402.org/facilitator (testnet); mainnet needs a mainnet facilitator
  X402_PUBLIC_BASE_URL  the public https address agents reach (needed for discovery listings)
"""

import base64
import json
import os
import re

import httpx

USDC = {"eip155:8453": ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USD Coin"),
        "eip155:84532": ("0x036CbD53842c5426634e7929541eC2318f3dCF7e", "USDC")}
V1_NAMES = {"eip155:8453": "base", "eip155:84532": "base-sepolia"}


class X402Config:
    def __init__(self) -> None:
        self.pay_to = os.getenv("X402_PAY_TO", "").strip()
        self.network = os.getenv("X402_NETWORK", "eip155:84532").strip()
        self.facilitator = os.getenv("X402_FACILITATOR_URL", "https://x402.org/facilitator").strip().rstrip("/")
        self.public_base = os.getenv("X402_PUBLIC_BASE_URL", "").strip().rstrip("/")
        wallets = lambda k: {w.strip().lower() for w in os.getenv(k, "").split(",") if w.strip()}   # noqa: E731
        self.internal = wallets("X402_INTERNAL_WALLETS")          # the owner's own test buyers
        self.marketplace = wallets("X402_MARKETPLACE_WALLETS")    # directories' verification purchases

    def classify(self, payer: str, labels: dict | None = None) -> str:
        """external = a genuine customer. Everything else is never counted as demand or a customer."""
        p = (payer or "").lower()
        if not p:
            return "unidentified"
        if p == self.pay_to.lower():
            return "self_transfer"
        if p in self.internal:
            return "internal_test"
        if p in self.marketplace:
            return "marketplace_test"
        return (labels or {}).get(p, "external")

    @property
    def enabled(self) -> bool:
        return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", self.pay_to)) and self.network in USDC

    @property
    def mode(self) -> str:
        return "live" if self.network == "eip155:8453" else "test"

    def status(self) -> dict:
        return {"enabled": self.enabled, "mode": self.mode if self.enabled else None, "network": self.network,
                "pay_to": (self.pay_to[:6] + "…" + self.pay_to[-4:]) if self.pay_to else None,
                "facilitator": self.facilitator, "public_base_url": self.public_base or None}

    def requirements(self, resource: str, price_usd: float, description: str, output_schema: dict | None = None) -> dict:
        asset, name = USDC[self.network]
        atomic = str(round(price_usd * 1_000_000))
        return {"v2": {"scheme": "exact", "network": self.network, "amount": atomic, "asset": asset, "payTo": self.pay_to,
                       "maxTimeoutSeconds": 60, "extra": {"name": name, "version": "2"}},
                "v1": {"scheme": "exact", "network": V1_NAMES[self.network], "maxAmountRequired": atomic, "resource": resource,
                       "description": description, "mimeType": "application/json", "payTo": self.pay_to,
                       "maxTimeoutSeconds": 60, "asset": asset, "extra": {"name": name, "version": "2"},
                       "outputSchema": output_schema or {}},
                "resource": {"url": resource, "description": description, "mimeType": "application/json"},
                "atomic": int(atomic)}


def b64json(obj) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()


def challenge(req: dict, error: str = "payment required") -> tuple[dict, dict]:
    """(body, headers) for a 402: v1 terms in the body, v2 terms in PAYMENT-REQUIRED."""
    v2 = {"x402Version": 2, "error": error, "resource": req["resource"], "accepts": [req["v2"]]}
    return ({"x402Version": 1, "error": error, "accepts": [req["v1"]]},
            {"PAYMENT-REQUIRED": b64json(v2)})


def read_payment(headers) -> tuple[int, dict] | None:
    raw = headers.get("payment-signature") or headers.get("x-payment")
    if not raw:
        return None
    try:
        payload = json.loads(base64.b64decode(raw))
    except Exception:
        raise ValueError("payment header is not valid base64 JSON")
    version = 2 if headers.get("payment-signature") else int(payload.get("x402Version", 1))
    return version, payload


async def facilitator(cfg: X402Config, action: str, version: int, payload: dict, req: dict) -> dict:
    body = {"x402Version": version, "paymentPayload": payload,
            "paymentRequirements": req["v2"] if version >= 2 else req["v1"]}
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=6.0)) as c:
        r = await c.post(f"{cfg.facilitator}/{action}", json=body)
    try:
        return r.json()
    except Exception:
        return {"isValid": False, "success": False, "invalidReason": f"facilitator HTTP {r.status_code}"}
