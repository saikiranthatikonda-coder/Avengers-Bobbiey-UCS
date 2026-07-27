"""TreasuryService — reserve allocation, movements, optional live balance.

Reserves are derived from the configured Treasury/DAO token allocation and
split into the four operating reserves. If `treasury.address` is configured,
the real native balance of that address is read over RPC and shown alongside.
Movements are recorded whenever the platform books an allocation change, so
the ledger is real even before a token exists.
"""

import json
import time
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "treasury.json"


class TreasuryService:
    def __init__(self, config, chain_svc, hub=None) -> None:
        self.config = config
        self.chain = chain_svc
        self.hub = hub
        self.movements: list[dict] = []
        try:
            self.movements = json.loads(FILE.read_text(encoding="utf-8")).get("movements", [])
        except Exception:
            pass

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps({"movements": self.movements[:60]}, indent=1),
                            encoding="utf-8")
        except Exception:
            pass

    async def record(self, reserve: str, delta: float, note: str = "") -> dict:
        entry = {"ts": time.time(), "reserve": reserve[:40],
                 "delta": round(float(delta), 4), "note": (note or "")[:120]}
        self.movements.insert(0, entry)
        self.movements = self.movements[:60]
        self._save()
        if self.hub:
            await self.hub.broadcast({
                "type": "alert", "severity": "info",
                "title": "Treasury updated",
                "detail": f"{reserve}: {'+' if delta >= 0 else ''}{delta:,.0f} — {note}"[:180],
                "source": "web3 · treasury", "action": "Review the Treasury widget.",
            })
        return {"ok": True, "movement": entry}

    async def snapshot(self, treasury_tokens: int = 0, symbol: str = "TOKEN") -> dict:
        cfg = self.config.data["treasury"]
        reserves = []
        for r in cfg.get("reserves", []):
            tokens = round(treasury_tokens * r.get("pct", 0) / 100)
            reserves.append({**r, "tokens": tokens, "symbol": symbol})

        live_native = None
        addr = (cfg.get("address") or "").strip()
        if addr:
            live_native = await self.chain.native_balance(addr)

        return {
            "address": addr or None,
            "live_native_balance": live_native,
            "native_symbol": self.config.chain()["symbol"],
            "total_tokens": treasury_tokens,
            "symbol": symbol,
            "reserves": reserves,
            "movements": self.movements[:8],
        }
