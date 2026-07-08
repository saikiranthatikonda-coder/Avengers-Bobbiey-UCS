"""Cross-Agent Shared Memory — Roadmap Phase 2 (final feature).

One persistent memory stream every agent reads from and writes to
(team_memory.json). This is what turns eight solo agents into a team:

  WRITES · agent replies worth keeping (real Q&A, not ticks)
         · orchestrator directive completions and preemptions
         · agent-to-agent consult conclusions
         · critical insights and incident escalations

  READS  · every agent's reply prompt is prefixed with the freshest team
           entries from OTHER agents, so Stark knows what Widow just found
         · the Knowledge Hub indexes it as a first-class source

Entries are compact (one line each) to keep prompt overhead tiny.
"""

import json
import time
from collections import deque
from pathlib import Path

FILE = Path(__file__).parent / "team_memory.json"
MAX_ENTRIES = 200


class TeamMemory:
    def __init__(self, hub=None) -> None:
        self.hub = hub
        self.entries: deque = deque(maxlen=MAX_ENTRIES)
        self.writes_total = 0
        try:
            for e in json.loads(FILE.read_text(encoding="utf-8")):
                self.entries.append(e)
        except Exception:
            pass

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps(list(self.entries), ensure_ascii=False,
                                       indent=0), encoding="utf-8")
        except Exception:
            pass

    async def write(self, agent: str, kind: str, text: str) -> None:
        """kind: reply | directive | consult | insight | incident"""
        text = (text or "").strip()
        if len(text) < 12:
            return
        entry = {"ts": time.time(), "agent": agent, "kind": kind,
                 "text": text[:220]}
        self.entries.append(entry)
        self.writes_total += 1
        self._save()
        if self.hub:
            await self.hub.broadcast({"type": "team-memory", **entry})

    def recent(self, n: int = 8, exclude_agent: str | None = None) -> list[dict]:
        out = [e for e in reversed(self.entries)
               if e.get("agent") != exclude_agent]
        return out[:n]

    def context_block(self, for_agent: str, n: int = 5) -> str:
        """Prompt prefix giving an agent its teammates' freshest knowledge."""
        items = self.recent(n=n, exclude_agent=for_agent)
        if not items:
            return ""
        lines = "\n".join(
            f"- [{e['agent'].upper()} · {e['kind']}] {e['text']}" for e in items)
        return ("TEAM MEMORY (what other agents recently learned/did — use "
                "only if relevant):\n" + lines + "\n\n")

    def snapshot(self) -> dict:
        return {"count": len(self.entries), "writes_total": self.writes_total,
                "recent": self.recent(n=10)}
