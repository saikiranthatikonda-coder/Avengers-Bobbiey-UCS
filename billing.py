"""Monetization engine — editions, AI credits, entitlements.

One of SEVERAL monetization paths (subscriptions, AI credits, plugin
marketplace, enterprise licensing, optional token). The platform is fully
usable on the free Community edition; paid tiers unlock more.

Demo/mock billing: the current edition + credit ledger persist to
billing.json (gitignored). No real payment processing — upgrade actions here
just switch the demo edition so the UI/entitlements can be exercised.
"""

import json
import time
from pathlib import Path

FILE = Path(__file__).parent / "billing.json"

# ── editions ──────────────────────────────────────────────────────
EDITIONS = [
    {
        "id": "community", "name": "Community", "price": "Free",
        "price_num": 0, "period": "forever", "credits": 100,
        "tagline": "The full command center for one operator, on your machine.",
        "features": [
            "Unified command dashboard + HUD",
            "8-agent AI roster + orchestration",
            "Local AI models (Ollama)",
            "System telemetry + threat intelligence",
            "1 operator · single node",
            "Community support",
        ],
    },
    {
        "id": "pro", "name": "Pro", "price": "$19", "price_num": 19,
        "period": "/mo", "credits": 2000,
        "tagline": "Cloud AI brain, vision, and priority intelligence.",
        "features": [
            "Everything in Community",
            "Claude cloud brain + webcam vision AI",
            "Executive briefings + priority insights",
            "Google Calendar + Gmail intelligence",
            "Knowledge Hub + productivity engine",
            "2,000 AI credits / month · email support",
        ],
    },
    {
        "id": "team", "name": "Team", "price": "$49", "price_num": 49,
        "period": "/user/mo", "credits": 10000,
        "tagline": "Multi-operator command with a shared fleet.",
        "features": [
            "Everything in Pro",
            "Multi-operator + role-based command",
            "Multi-site fleet (up to 10 nodes)",
            "Shared team memory + audit trail",
            "Authenticated remote access",
            "10,000 pooled AI credits / month",
        ],
    },
    {
        "id": "enterprise", "name": "Enterprise", "price": "Custom",
        "price_num": None, "period": "", "credits": None,
        "tagline": "Unlimited scale, on-prem AI, and compliance.",
        "features": [
            "Everything in Team",
            "Unlimited nodes + on-prem AI clusters",
            "SSO / SAML · compliance export",
            "Autonomous decision support at scale",
            "Dedicated support + SLA",
            "Custom integrations + professional services",
        ],
    },
]
EDITION_RANK = {"community": 0, "pro": 1, "team": 2, "enterprise": 3}

# feature → minimum edition that unlocks it (drives the entitlement view)
ENTITLEMENTS = {
    "local_ai": "community", "agents": "community", "telemetry": "community",
    "threat_intel": "community", "cloud_brain": "pro", "vision_ai": "pro",
    "briefings": "pro", "knowledge_hub": "pro", "google_sync": "pro",
    "multi_operator": "team", "fleet": "team", "team_memory": "team",
    "remote_access": "team", "on_prem_clusters": "enterprise",
    "sso": "enterprise", "compliance_export": "enterprise",
}

# other monetization paths shown on the pricing page (coexist with editions)
MONETIZATION_PATHS = [
    "Free Community Edition", "Pro Subscription", "Team Subscription",
    "Enterprise Licensing", "On-Prem Deployment", "AI Credits",
    "Plugin Marketplace", "Professional Services", "Optional Token Ecosystem",
]


class Billing:
    def __init__(self, hub=None) -> None:
        self.hub = hub
        self.state = {
            "edition": "community",
            "credits_used_cycle": 0,
            "cycle_start": time.time(),
        }
        try:
            saved = json.loads(FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.state.update(saved)
        except Exception:
            pass

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _edition(self, eid=None) -> dict:
        eid = eid or self.state["edition"]
        return next((e for e in EDITIONS if e["id"] == eid), EDITIONS[0])

    def current(self) -> dict:
        ed = self._edition()
        allot = ed.get("credits")
        used = self.state.get("credits_used_cycle", 0)
        return {
            "edition": ed["id"], "name": ed["name"], "price": ed["price"],
            "period": ed["period"],
            "credits_allotment": allot,
            "credits_used": used,
            "credits_remaining": (None if allot is None else max(0, allot - used)),
            "credits_unlimited": allot is None,
        }

    def entitlements(self) -> dict:
        rank = EDITION_RANK.get(self.state["edition"], 0)
        return {feat: EDITION_RANK.get(mined, 0) <= rank
                for feat, mined in ENTITLEMENTS.items()}

    async def set_edition(self, eid: str) -> dict:
        if eid not in EDITION_RANK:
            return {"ok": False, "error": "unknown edition"}
        self.state["edition"] = eid
        self.state["credits_used_cycle"] = 0
        self.state["cycle_start"] = time.time()
        self._save()
        if self.hub:
            await self.hub.broadcast({"type": "log", "level": "info",
                                      "msg": f"license switched → {self._edition().name} edition (demo)"})
        return {"ok": True, **self.current()}

    async def use_credits(self, n: int = 1) -> dict:
        self.state["credits_used_cycle"] = self.state.get("credits_used_cycle", 0) + max(0, n)
        self._save()
        return self.current()

    def snapshot(self) -> dict:
        return {
            "editions": EDITIONS,
            "current": self.current(),
            "entitlements": self.entitlements(),
            "monetization_paths": MONETIZATION_PATHS,
        }
