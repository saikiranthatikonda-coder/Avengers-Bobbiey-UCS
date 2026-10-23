"""Commander Memory — long-form, private context about the operator.

The operator pastes or uploads a full profile document (identity, career,
projects, rules — tens of KB). It is stored LOCALLY in commander_memory.md
(gitignored, never committed, protected from engineering-mode edits), split
into sections, and for every agent request the most relevant sections are
retrieved and added to the prompt — plus a short always-on core (who the
operator is, how they want to be addressed). Agents get real context without
stuffing the whole document into every call.

Retrieval is dependency-free: tokenised keyword overlap weighted by inverse
document frequency (BM25-lite), with a boost for section titles.

Before 2026-10-23 operator memory only held 40 one-line facts (UI 200 chars,
server 240), so a profile document was silently truncated.
"""

import math
import re
import time
from pathlib import Path

FILE = Path(__file__).parent / "commander_memory.md"
MAX_CHARS = 300_000
CHUNK_CHARS = 1400
CORE_CHARS = 700

_HEADING = re.compile(
    r"^\s*(#{1,6}\s+.+|PART\s+[IVXLC]+\b.*|\d{1,2}\.\s+[A-Z][^.]{2,80}|[A-Z][A-Za-z0-9 &/’'()\-—]{3,60})\s*$")
_RULES = re.compile(r"NON-NEGOTIABLE|\bnever\b|\bmust not\b|\brequire[sd]? authori[sz]ation", re.I)
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]{1,}")
_STOP = set("""a an the and or of to in on for with by is are was were be been it its this that these those
as at from into about over under than then so not no do does did can could should would will shall may
might must have has had i me my we our you your he she they them their his her what which who whom when
where why how all any each more most other some such only own same too very just also if but per via""".split())


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2]


class CommanderMemory:
    def __init__(self, path: Path = FILE) -> None:
        self.path = Path(path)
        self.text = ""
        self.sections: list[dict] = []
        self.updated: float | None = None
        self._idf: dict[str, float] = {}
        self.load()

    # ── storage ───────────────────────────────────────────────────
    def load(self) -> None:
        try:
            self.text = self.path.read_text(encoding="utf-8")
            self.updated = self.path.stat().st_mtime
        except FileNotFoundError:
            self.text, self.updated = "", None
        self._index()

    def save(self, content: str, mode: str = "replace") -> dict:
        content = (content or "").replace("\r\n", "\n").strip()
        if not content:
            return {"ok": False, "error": "nothing to save"}
        new = content if mode == "replace" or not self.text else self.text.rstrip() + "\n\n" + content
        if len(new) > MAX_CHARS:
            return {"ok": False, "error": f"too large ({len(new):,} chars; limit {MAX_CHARS:,})"}
        tmp = self.path.with_suffix(".md.tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(self.path)
        self.text, self.updated = new, time.time()
        self._index()
        return {"ok": True, **self.meta()}

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass
        self.text, self.updated, self.sections = "", None, []
        self._idf = {}

    # ── sectioning ────────────────────────────────────────────────
    def _index(self) -> None:
        self.sections = []
        if not self.text:
            self._idf = {}
            return
        title, buf, part = "Overview", [], ""
        for line in self.text.split("\n"):
            m = _HEADING.match(line)
            if m and len(line.strip()) <= 90 and not line.strip().endswith((".", ":", ",")):
                if buf:
                    self._add(part, title, "\n".join(buf))
                head = line.strip().lstrip("#").strip()
                if re.match(r"PART\s+[IVXLC]+", head):
                    part, title = head, head
                else:
                    title = head
                buf = []
            else:
                buf.append(line)
        if buf:
            self._add(part, title, "\n".join(buf))
        n = max(1, len(self.sections))
        df: dict[str, int] = {}
        for s in self.sections:
            for t in set(s["tokens"]):
                df[t] = df.get(t, 0) + 1
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}

    def _add(self, part: str, title: str, body: str) -> None:
        body = body.strip()
        if not body:
            return
        # long sections are split on paragraph boundaries
        paras, cur = re.split(r"\n\s*\n", body), ""
        chunks = []
        for p in paras:
            if cur and len(cur) + len(p) > CHUNK_CHARS:
                chunks.append(cur); cur = p
            else:
                cur = f"{cur}\n\n{p}" if cur else p
        if cur:
            chunks.append(cur)
        for i, c in enumerate(chunks):
            label = title if len(chunks) == 1 else f"{title} ({i + 1}/{len(chunks)})"
            # only the FIRST piece of a split section carries the title, so a
            # title match points at where the section's substance starts
            head = title + " " if i == 0 else ""
            self.sections.append({"part": part, "title": label, "text": c,
                                  "rules": bool(_RULES.search(c)),
                                  "tokens": _tokens(head + c),
                                  "title_tokens": set(_tokens(title + " " + part)) if i == 0 else set()})

    # ── retrieval ─────────────────────────────────────────────────
    def core(self) -> str:
        """Always-on identity: the first section(s) up to CORE_CHARS."""
        out = ""
        for s in self.sections[:3]:
            if len(out) >= CORE_CHARS:
                break
            out += f"{s['title']}: {s['text']}\n"
        return out[:CORE_CHARS].strip()

    def search(self, query: str, k: int = 3) -> list[dict]:
        q = _tokens(query)
        if not q or not self.sections:
            return []
        scored = []
        for i, s in enumerate(self.sections):
            tf: dict[str, int] = {}
            for t in s["tokens"]:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for t in set(q):
                if t in tf:
                    score += self._idf.get(t, 0) * (1 + math.log(tf[t])) * (2.0 if t in s["title_tokens"] else 1.0)
            if score > 0:
                if s.get("rules"):          # the operator's rules never lose to descriptive text
                    score *= 2.0
                scored.append((score / math.sqrt(len(s["tokens"]) + 20), i))
        scored.sort(key=lambda x: (-x[0], x[1]))          # ties → earlier section
        return [dict(self.sections[i], score=round(sc, 3)) for sc, i in scored[:k]]

    def context_for(self, prompt: str, budget: int = 2600) -> str:
        """Prompt block: core identity + the sections relevant to this request."""
        if not self.sections:
            return ""
        parts = [self.core()]
        used = len(parts[0])
        for s in self.search(prompt, k=4):
            if s["text"] in parts[0]:
                continue
            block = f"[{s['title']}] {s['text']}"
            if used + len(block) > budget:
                block = block[:max(0, budget - used)]
            if block:
                parts.append(block); used += len(block)
            if used >= budget:
                break
        return ("[COMMANDER CONTEXT — private profile supplied by the operator. Use it when relevant; "
                "don't recite it; treat project/employment details as historical unless confirmed.]\n"
                + "\n".join(parts) + "\n[END COMMANDER CONTEXT]\n")

    def meta(self) -> dict:
        titles, seen = [], set()
        for s in self.sections:
            t = re.sub(r" \(\d+/\d+\)$", "", s["title"])
            if t not in seen:
                seen.add(t); titles.append(t)
        return {"chars": len(self.text), "sections": len(self.sections), "titles": titles[:60],
                "updated": self.updated, "stored_at": self.path.name}
