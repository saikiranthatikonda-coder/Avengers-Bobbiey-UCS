"""Knowledge Hub — one searchable memory across everything the platform knows.

Sources (all REAL, all already on this machine):
  · operator memory      facts + AI observations (operator_memory.json)
  · notes                the operator's own notes (notes.json)
  · insights             recent AI insights & briefings
  · agent history        every agent Q/A exchange this session
  · incidents            the threat engine's event feed
  · calendar + inbox     synced Google events and mail snippets
  · news                 current headline buffer

Search is ranked keyword retrieval (term overlap × source weight × recency).
/api/knowledge/ask feeds the top hits to the active brain (respects the
operator's local-model choice) for a synthesized answer with sources.
"""

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent

SOURCE_WEIGHT = {
    "memory-fact": 3.0, "note": 2.6, "observation": 2.0, "insight": 1.8,
    "agent": 1.6, "incident": 1.5, "calendar": 2.2, "email": 1.8, "news": 1.0,
}

_WORD = re.compile(r"[a-z0-9]{2,}")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


class KnowledgeHub:
    def __init__(self, memory=None, insights=None, team=None, threats=None,
                 agenda=None, news=None, brain=None) -> None:
        self.memory = memory
        self.insights = insights
        self.team = team
        self.threats = threats
        self.agenda = agenda
        self.news = news
        self.brain = brain

    # ── corpus assembly (live, cheap) ─────────────────────────────
    def _docs(self) -> list[dict]:
        docs: list[dict] = []
        now = time.time()

        if self.memory is not None:
            try:
                for f in self.memory.data.get("facts", []):
                    docs.append({"source": "memory-fact", "ts": f.get("ts", now),
                                 "text": f.get("text", ""), "ref": "operator memory"})
                for o in self.memory.data.get("observations", [])[-40:]:
                    docs.append({"source": "observation", "ts": o.get("ts", now),
                                 "text": o.get("text", ""), "ref": "vision log"})
            except Exception:
                pass

        try:
            notes = json.loads((ROOT / "notes.json").read_text(encoding="utf-8"))
            for n in notes:
                docs.append({"source": "note", "ts": n.get("ts", now),
                             "text": n.get("text", ""), "ref": "command notes"})
        except Exception:
            pass

        if self.insights is not None:
            for i in self.insights.recent:
                docs.append({"source": "insight", "ts": i.get("ts", now),
                             "text": (i.get("insight", "") + " "
                                      + i.get("recommendation", "")).strip(),
                             "ref": i.get("source", "insights")})

        if self.team:
            for a in self.team.values():
                for h in a.history[-8:]:
                    q, ans = h.get("q", ""), h.get("a", "")
                    if q == "[tick]":
                        continue
                    docs.append({"source": "agent", "ts": h.get("ts", now),
                                 "text": f"{q} — {ans}"[:400],
                                 "ref": f"agent {a.codename}"})

        if self.threats is not None:
            for ev in list(self.threats.events)[:25]:
                docs.append({"source": "incident", "ts": ev.get("ts", now),
                             "text": f"{ev.get('title', '')}. {ev.get('detail', '')}",
                             "ref": f"threat · {ev.get('category', '')}"})

        if self.agenda is not None:
            try:
                snap = self.agenda.snapshot()
                for e in snap.get("events", []):
                    docs.append({"source": "calendar", "ts": e.get("start_ts", now),
                                 "text": f"{e['title']} at {e.get('location') or 'no location'} "
                                         f"with {', '.join(e.get('attendees') or []) or 'no attendees'}",
                                 "ref": "google calendar"})
                for m in snap.get("emails", []):
                    docs.append({"source": "email", "ts": m.get("ts", now),
                                 "text": f"{m.get('subject', '')} — from {m.get('sender', '')}. "
                                         f"{m.get('snippet', '')}",
                                 "ref": "gmail"})
            except Exception:
                pass

        if self.news is not None:
            for a in (self.news.recent or [])[:15]:
                docs.append({"source": "news", "ts": now,
                             "text": a.get("title", ""),
                             "ref": a.get("source", "news feed")})
        return docs

    # ── ranked retrieval ──────────────────────────────────────────
    def search(self, query: str, limit: int = 12) -> dict:
        q = _tokens(query)
        if not q:
            return {"query": query, "results": [], "scanned": 0}
        now = time.time()
        scored = []
        docs = self._docs()
        for d in docs:
            toks = _tokens(d["text"])
            if not toks:
                continue
            exact = len(q & toks)
            # prefix fuzz: "meet" matches "meeting"
            fuzzy = sum(1 for qt in q if qt not in toks
                        and any(t.startswith(qt) for t in toks))
            hits = exact + fuzzy * 0.6
            if hits == 0:
                continue
            coverage = hits / len(q)
            age_h = max(0.0, (now - d.get("ts", now)) / 3600)
            recency = 1.0 / (1.0 + age_h / 24)          # halves every ~day
            score = coverage * SOURCE_WEIGHT.get(d["source"], 1.0) * (0.55 + 0.45 * recency)
            scored.append((score, d))
        scored.sort(key=lambda x: -x[0])
        return {
            "query": query,
            "scanned": len(docs),
            "results": [{
                "source": d["source"], "ref": d["ref"],
                "text": d["text"][:260], "ts": d.get("ts"),
                "score": round(s, 3),
            } for s, d in scored[:limit]],
        }

    def stats(self) -> dict:
        counts: dict[str, int] = {}
        for d in self._docs():
            counts[d["source"]] = counts.get(d["source"], 0) + 1
        return {"sources": counts, "total": sum(counts.values())}

    # ── AI answer over the corpus (uses the ACTIVE brain) ─────────
    async def ask(self, query: str) -> dict:
        hits = self.search(query, limit=8)["results"]
        if not hits:
            return {"answer": "Nothing in the knowledge base matches that yet, sir.",
                    "sources": []}
        context = "\n".join(f"[{h['source']} · {h['ref']}] {h['text']}"
                            for h in hits)
        answer = None
        if self.brain is not None:
            raw = await self.brain.think(
                f"Knowledge-base excerpts:\n{context}\n\n"
                f"Question: {query}\n"
                "Answer in 1-3 sentences using ONLY the excerpts; if they don't "
                "contain the answer, say what IS known that's closest.",
                system="You are JARVIS answering from the operator's own knowledge base. Be precise.",
                agent="vision", fast=True, timeout=60)
            if raw and not raw.lstrip().startswith("[brain"):
                answer = raw.strip()[:500]
        if not answer:
            answer = "Top match — " + hits[0]["text"]
        return {"answer": answer, "sources": hits[:5]}
