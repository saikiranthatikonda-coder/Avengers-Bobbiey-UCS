"""Monetization engine — editions, AI credits, entitlements.

A single paid plan (BUCS Pro). Customers pay over UPI and activate a licence
key; nothing runs on a customer install without one.

The edition itself comes from licensing.py (a signed licence key). This module
holds the plan definition and a local AI-credit counter (billing.json,
gitignored).
"""

import json
import time
from pathlib import Path
from atomicio import atomic_write_text

FILE = Path(__file__).parent / "billing.json"

# ── the plan ──────────────────────────────────────────────────────
# One paid plan. Customers pay over UPI (docs/payments.json) and receive a
# signed licence key (licensing.py). Unlicensed installs are locked.
EDITIONS = [
    {
        "id": "pro", "name": "BUCS Pro", "price": "₹149", "price_num": 149,
        "period": "/month", "credits": None,
        # the only two plans sold (must match docs/payments.json "prices")
        "plans": [{"months": 1, "term": "1 month", "price": "₹149", "price_num": 149, "days": 31},
                  {"months": 12, "term": "12 months", "price": "₹1,499", "price_num": 1499, "days": 366}],
        "tagline": "The complete command center, on your own machine.",
        "features": [
            "Command dashboard, HUD and 8-agent AI roster",
            "Voice control, conversation mode and webcam vision",
            "LLM switch: Claude, OpenAI, Gemini, Groq, local Ollama, any model",
            "Google Calendar, Gmail and Tasks intelligence",
            "Threat intelligence, decision support and live telemetry",
            "JARVIS engineering mode, fleet nodes and audit trail",
        ],
    },
]
# "community" = no licence (locked on customer installs)
EDITION_RANK = {"community": 0, "pro": 1}

# feature → minimum edition that unlocks it
ENTITLEMENTS = {f: "pro" for f in (
    "local_ai", "agents", "telemetry", "threat_intel", "cloud_brain", "vision_ai",
    "briefings", "knowledge_hub", "google_sync", "engineering", "multi_operator",
    "fleet", "team_memory", "remote_access")}

MONETIZATION_PATHS: list[str] = []


class Billing:
    def __init__(self, hub=None) -> None:
        self.hub = hub
        self.state = {
            "edition": "pro",
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
            atomic_write_text(FILE, json.dumps(self.state, indent=1), encoding="utf-8")
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
