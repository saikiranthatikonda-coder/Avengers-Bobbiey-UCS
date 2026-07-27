"""AnalyticsService — portfolio history + on-chain event log.

Portfolio value is sampled from REAL wallet balances each time the wallet
refreshes, so the history curve is genuine observed data (it starts empty and
fills as the platform runs — nothing is back-filled or invented).
"""

import json
import time
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "web3_analytics.json"
MAX_POINTS = 240


class AnalyticsService:
    def __init__(self) -> None:
        self.portfolio: list[dict] = []
        self.events: list[dict] = []
        try:
            d = json.loads(FILE.read_text(encoding="utf-8"))
            self.portfolio = d.get("portfolio") or []
            self.events = d.get("events") or []
        except Exception:
            pass

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps({"portfolio": self.portfolio[-MAX_POINTS:],
                                        "events": self.events[:60]}, indent=0),
                            encoding="utf-8")
        except Exception:
            pass

    def sample_portfolio(self, wallet: dict | None) -> None:
        """Record a real balance observation (max 1 per 60s)."""
        if not wallet:
            return
        now = time.time()
        if self.portfolio and now - self.portfolio[-1]["ts"] < 60:
            return
        self.portfolio.append({
            "ts": now,
            "native": round(float(wallet.get("native_balance") or 0), 6),
            "token": round(float(wallet.get("token_balance") or 0), 4),
            "chain_id": wallet.get("chain_id"),
        })
        self.portfolio = self.portfolio[-MAX_POINTS:]
        self._save()

    def log(self, kind: str, text: str) -> dict:
        e = {"ts": time.time(), "kind": kind[:24], "text": (text or "")[:160]}
        self.events.insert(0, e)
        self.events = self.events[:60]
        self._save()
        return e

    def snapshot(self, wallet: dict | None = None) -> dict:
        series = self.portfolio[-60:]
        first = series[0]["native"] if series else 0
        last = series[-1]["native"] if series else 0
        change = round(((last - first) / first * 100), 2) if first else 0.0
        holdings = []
        if wallet:
            if wallet.get("native_balance") is not None:
                holdings.append({"asset": wallet.get("native_symbol", "ETH"),
                                 "amount": round(wallet["native_balance"], 6),
                                 "kind": "native"})
            if wallet.get("token_balance") is not None:
                holdings.append({"asset": wallet.get("token_symbol", "TOKEN"),
                                 "amount": round(wallet["token_balance"], 4),
                                 "kind": "platform"})
        return {
            "series": series,
            "points": len(series),
            "change_pct": change,
            "holdings": holdings,
            "events": self.events[:8],
        }
