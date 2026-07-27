"""MarketplaceService — agent / plugin / workflow listings.

Functional foundation: listings are browsable, installs are REAL and persisted
(marketplace.json), install counts increment, and operators can publish their
own listings. Actual code execution of third-party agents is deliberately NOT
implemented — running untrusted code needs a sandbox + review pipeline, which
is a security decision, not a UI one. Installed items are recorded so the
platform can activate them once that pipeline exists.
"""

import json
import time
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "marketplace.json"

CATALOG = [
    {"slug": "sentinel", "name": "Sentinel — network anomaly agent", "type": "agent",
     "price": 120, "author": "core", "desc": "Watches interface traffic for anomalous patterns and raises HUD alerts."},
    {"slug": "standup-synth", "name": "Standup Synth — meeting summarizer", "type": "plugin",
     "price": 0, "author": "core", "desc": "Turns calendar events + notes into a spoken standup summary."},
    {"slug": "ops-weekly", "name": "Ops Weekly — automation template", "type": "workflow",
     "price": 40, "author": "community", "desc": "Weekly operational digest across telemetry, threats and calendar."},
    {"slug": "tactical-hud", "name": "Tactical HUD — dashboard theme", "type": "dashboard",
     "price": 0, "author": "community", "desc": "High-contrast amber command theme for low-light operations."},
    {"slug": "fleet-watch", "name": "Fleet Watch — node health agent", "type": "agent",
     "price": 80, "author": "core", "desc": "Per-node thresholds with escalation into the decision engine."},
    {"slug": "brief-export", "name": "Brief Export — PDF briefings", "type": "plugin",
     "price": 25, "author": "community", "desc": "Exports the executive briefing as a formatted PDF."},
]


class MarketplaceService:
    def __init__(self, config, hub=None) -> None:
        self.config = config
        self.hub = hub
        self.installed: dict[str, dict] = {}
        self.published: list[dict] = []
        self.counts: dict[str, int] = {}
        try:
            d = json.loads(FILE.read_text(encoding="utf-8"))
            self.installed = d.get("installed") or {}
            self.published = d.get("published") or []
            self.counts = d.get("counts") or {}
        except Exception:
            pass

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps({"installed": self.installed,
                                        "published": self.published,
                                        "counts": self.counts}, indent=1),
                            encoding="utf-8")
        except Exception:
            pass

    def _all(self) -> list[dict]:
        return CATALOG + self.published

    async def install(self, slug: str) -> dict:
        item = next((i for i in self._all() if i["slug"] == slug), None)
        if not item:
            return {"ok": False, "error": "unknown listing"}
        if slug in self.installed:
            return {"ok": False, "error": "already installed"}
        self.installed[slug] = {"slug": slug, "name": item["name"], "ts": time.time()}
        self.counts[slug] = self.counts.get(slug, 0) + 1
        self._save()
        if self.hub:
            await self.hub.broadcast({
                "type": "alert", "severity": "info",
                "title": "Marketplace install",
                "detail": f"{item['name']} installed — activation pending the extension sandbox.",
                "source": "web3 · marketplace", "action": "Manage it in the Marketplace widget.",
            })
        return {"ok": True, "installed": self.installed[slug]}

    async def uninstall(self, slug: str) -> dict:
        if slug not in self.installed:
            return {"ok": False, "error": "not installed"}
        del self.installed[slug]
        self._save()
        return {"ok": True}

    async def publish(self, name: str, kind: str, desc: str = "", price: float = 0,
                      author: str = "operator") -> dict:
        name = (name or "").strip()
        if len(name) < 4:
            return {"ok": False, "error": "name must be at least 4 characters"}
        slug = "".join(ch for ch in name.lower().replace(" ", "-") if ch.isalnum() or ch == "-")[:32]
        if any(i["slug"] == slug for i in self._all()):
            return {"ok": False, "error": "a listing with that name exists"}
        item = {"slug": slug, "name": name[:60],
                "type": kind if kind in ("agent", "plugin", "workflow", "dashboard") else "plugin",
                "price": max(0, float(price or 0)), "author": (author or "operator")[:42],
                "desc": (desc or "")[:160], "ts": time.time(), "review": "pending"}
        self.published.insert(0, item)
        self._save()
        if self.hub:
            await self.hub.broadcast({
                "type": "alert", "severity": "info",
                "title": "Marketplace submission",
                "detail": f"{item['name']} published — awaiting community review.",
                "source": "web3 · marketplace", "action": "Track it in the Marketplace widget.",
            })
        return {"ok": True, "listing": item}

    def snapshot(self, symbol: str = "TOKEN") -> dict:
        items = []
        for i in self._all():
            items.append({
                **i,
                "price_label": "Free" if not i.get("price") else f"{i['price']:g} {symbol}",
                "installs": self.counts.get(i["slug"], 0),
                "is_installed": i["slug"] in self.installed,
                "status": i.get("review", "available"),
            })
        return {
            "listings": items,
            "installed_count": len(self.installed),
            "published_count": len(self.published),
            "installed": list(self.installed.values()),
        }
