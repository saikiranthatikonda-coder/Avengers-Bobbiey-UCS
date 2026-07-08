"""Enterprise Audit Trail — Roadmap Phase 4.

Append-only JSONL log (audit.log) of every command action taken on the
platform: who did it (operator + role), what, and when. This is the
enterprise-deployment primitive — nothing is silently mutable.

  · append-only file, never rewritten
  · in-memory tail for the dashboard feed
  · every entry also broadcast as {type: "audit", ...} for live display
"""

import json
import time
from collections import deque
from pathlib import Path

FILE = Path(__file__).parent / "audit.log"


class Audit:
    def __init__(self, hub=None) -> None:
        self.hub = hub
        self.tail: deque = deque(maxlen=120)
        self.entries_total = 0
        try:
            lines = FILE.read_text(encoding="utf-8").strip().splitlines()
            self.entries_total = len(lines)
            for ln in lines[-120:]:
                try:
                    self.tail.append(json.loads(ln))
                except Exception:
                    continue
        except Exception:
            pass

    async def log(self, action: str, detail: str = "",
                  operator: str = "operator", role: str = "commander") -> None:
        entry = {
            "ts": time.time(),
            "operator": operator[:40],
            "role": role,
            "action": action[:60],
            "detail": (detail or "")[:200],
        }
        self.tail.append(entry)
        self.entries_total += 1
        try:
            with open(FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if self.hub:
            await self.hub.broadcast({"type": "audit", **entry})

    def recent(self, n: int = 40) -> list[dict]:
        return list(self.tail)[-n:][::-1]

    def snapshot(self) -> dict:
        return {"entries_total": self.entries_total, "recent": self.recent(20)}
