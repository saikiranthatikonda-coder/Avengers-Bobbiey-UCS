"""AI CORE · retrieval-augmented memory.

SQLite store (.update/agentos/memory.db, gitignored, never touched by the updater) of
facts, episodes (conversation summaries), notes and documents (chunked). Retrieval is
hybrid:
  BM25        pure-python, always available
  embeddings  when an embedding model is reachable (Ollama nomic-embed-text by default;
              cloud embedding models only if the operator allows it — memory text would
              leave the machine)
Results carry ids so prompts cite them as [M<id>]; the context block has a size budget.

Legacy memories are IMPORTED by copy (operator_memory facts, team memory, notes.json) —
idempotent through source keys, originals are never modified. The commander profile
(commander_memory.md, private) is NOT copied: it is searched live as a linked source.
"""

from __future__ import annotations

import array
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path

KINDS = ("fact", "episode", "note", "document")
CHUNK = 1200
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]{1,}")
_STOP = set("""a an the and or of to in on for with by is are was were be been it its this that these those as at
from into about over under than then so not no do does did can could should would will shall may might must have
has had i me my we our you your he she they them their his her what which who whom when where why how all any each
more most other some such only own same too very just also if but per via""".split())


def tokens(text: str) -> list[str]:
    return [w.strip(".-") for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1]


def _vec(b: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(b)
    return list(a)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class BM25:
    def __init__(self, docs: dict[int, list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs, self.k1, self.b = docs, k1, b
        self.n = max(1, len(docs))
        self.avg = (sum(len(t) for t in docs.values()) / self.n) if docs else 1.0
        df: dict[str, int] = {}
        for t in docs.values():
            for w in set(t):
                df[w] = df.get(w, 0) + 1
        self.idf = {w: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = {i: {} for i in docs}
        for i, t in docs.items():
            d = self.tf[i]
            for w in t:
                d[w] = d.get(w, 0) + 1

    def scores(self, query: list[str]) -> dict[int, float]:
        out: dict[int, float] = {}
        q = set(query)
        for i, tf in self.tf.items():
            dl = len(self.docs[i]) or 1
            s = 0.0
            for w in q:
                f = tf.get(w)
                if not f:
                    # prefix fuzz: "meet" → "meeting" (half weight)
                    f2 = sum(c for t, c in tf.items() if len(w) >= 4 and t.startswith(w))
                    if not f2:
                        continue
                    f, wt = f2, 0.5
                else:
                    wt = 1.0
                idf = self.idf.get(w, math.log(1 + self.n))
                s += wt * idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
            if s > 0:
                out[i] = s
        return out


class MemoryStore:
    def __init__(self, path: Path, embedder=None, commander=None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder              # async (texts) -> (model_name, vectors) | None ; or None
        self.commander = commander            # CommanderMemory (linked, read-only)
        self._lock = threading.Lock()
        self._bm25: BM25 | None = None
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT, text TEXT NOT NULL,
          source TEXT, source_key TEXT UNIQUE, scope TEXT DEFAULT 'global', parent TEXT,
          pinned INTEGER DEFAULT 0, created REAL, updated REAL, meta TEXT);
        CREATE TABLE IF NOT EXISTS vectors (item_id INTEGER PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB);
        CREATE INDEX IF NOT EXISTS ix_items_kind ON items(kind);
        CREATE INDEX IF NOT EXISTS ix_items_parent ON items(parent);
        """)
        self.db.commit()
        self.embed_model: str | None = None

    # ── writes ─────────────────────────────────────────────────
    def _touch(self) -> None:
        self._bm25 = None

    def add(self, text: str, *, kind: str = "fact", title: str = "", source: str = "manual",
            source_key: str | None = None, scope: str = "global", parent: str | None = None,
            pinned: bool = False, meta: dict | None = None, ts: float | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        if kind not in KINDS:
            return {"ok": False, "error": "bad kind"}
        now = float(ts) if isinstance(ts, (int, float)) and 0 < ts <= time.time() + 60 else time.time()
        with self._lock:
            try:
                cur = self.db.execute(
                    "INSERT INTO items(kind,title,text,source,source_key,scope,parent,pinned,created,updated,meta)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (kind, (title or "")[:200], text[:20000], source[:80], source_key, scope[:60], parent,
                     int(bool(pinned)), now, now, json.dumps(meta or {})))
                self.db.commit()
                iid = cur.lastrowid
            except sqlite3.IntegrityError:
                if ts is not None and source_key:      # re-import: keep the ORIGINAL time on untouched copies
                    self.db.execute("UPDATE items SET created=?, updated=? WHERE source_key=? AND created=updated",
                                    (now, now, source_key))
                    self.db.commit()
                return {"ok": True, "duplicate": True}
        self._touch()
        return {"ok": True, "id": iid}

    def add_document(self, title: str, text: str, source: str = "operator", scope: str = "global") -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty document"}
        parent = "doc:" + hashlib.sha256((title + text).encode()).hexdigest()[:16]
        paras, cur, chunks = re.split(r"\n\s*\n", text), "", []
        for p in paras:
            while len(p) > CHUNK:
                chunks.append(p[:CHUNK]); p = p[CHUNK:]
            if cur and len(cur) + len(p) > CHUNK:
                chunks.append(cur); cur = p
            else:
                cur = f"{cur}\n\n{p}" if cur else p
        if cur:
            chunks.append(cur)
        ids = []
        for i, c in enumerate(chunks):
            r = self.add(c, kind="document", title=f"{title[:150]} ({i + 1}/{len(chunks)})" if len(chunks) > 1 else title[:150],
                         source=source, source_key=f"{parent}:{i}", scope=scope, parent=parent)
            if r.get("id"):
                ids.append(r["id"])
        return {"ok": True, "parent": parent, "chunks": len(chunks), "ids": ids}

    def update(self, iid: int, *, text: str | None = None, title: str | None = None, pinned: bool | None = None) -> dict:
        sets, vals = [], []
        if text is not None:
            if not text.strip():
                return {"ok": False, "error": "empty"}
            sets.append("text=?"); vals.append(text.strip()[:20000])
        if title is not None:
            sets.append("title=?"); vals.append(title[:200])
        if pinned is not None:
            sets.append("pinned=?"); vals.append(int(bool(pinned)))
        if not sets:
            return {"ok": False, "error": "nothing to change"}
        sets.append("updated=?"); vals.append(time.time())
        with self._lock:
            n = self.db.execute(f"UPDATE items SET {','.join(sets)} WHERE id=?", (*vals, int(iid))).rowcount
            if text is not None:
                self.db.execute("DELETE FROM vectors WHERE item_id=?", (int(iid),))
            self.db.commit()
        self._touch()
        return {"ok": bool(n)}

    def forget(self, iid: int) -> dict:
        with self._lock:
            row = self.db.execute("SELECT parent FROM items WHERE id=?", (int(iid),)).fetchone()
            if row is None:
                return {"ok": False, "error": "not found"}
            if row["parent"]:                               # forgetting a doc chunk forgets the document
                ids = [r[0] for r in self.db.execute("SELECT id FROM items WHERE parent=?", (row["parent"],))]
            else:
                ids = [int(iid)]
            self.db.executemany("DELETE FROM vectors WHERE item_id=?", [(i,) for i in ids])
            self.db.executemany("DELETE FROM items WHERE id=?", [(i,) for i in ids])
            self.db.commit()
        self._touch()
        return {"ok": True, "removed": len(ids)}

    # ── reads ──────────────────────────────────────────────────
    def _row(self, r) -> dict:
        return {"id": r["id"], "kind": r["kind"], "title": r["title"] or "", "text": r["text"], "source": r["source"],
                "scope": r["scope"], "pinned": bool(r["pinned"]), "created": r["created"], "updated": r["updated"],
                "parent": r["parent"]}

    def get(self, iid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM items WHERE id=?", (int(iid),)).fetchone()
        return self._row(r) if r else None

    def list(self, kind: str | None = None, limit: int = 50, offset: int = 0, pinned: bool | None = None) -> list[dict]:
        q, v = "SELECT * FROM items WHERE 1=1", []
        if kind in KINDS:
            q += " AND kind=?"; v.append(kind)
        if pinned is not None:
            q += " AND pinned=?"; v.append(int(pinned))
        q += " ORDER BY pinned DESC, updated DESC LIMIT ? OFFSET ?"
        v += [int(limit), int(offset)]
        return [self._row(r) for r in self.db.execute(q, v)]

    def stats(self) -> dict:
        counts = {k: 0 for k in KINDS}
        for r in self.db.execute("SELECT kind, COUNT(*) c FROM items GROUP BY kind"):
            counts[r["kind"]] = r["c"]
        nv = self.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        pinned = self.db.execute("SELECT COUNT(*) FROM items WHERE pinned=1").fetchone()[0]
        return {"counts": counts, "total": sum(counts.values()), "pinned": pinned, "vectors": nv,
                "embed_model": self.embed_model, "retrieval": "hybrid (BM25 + embeddings)" if nv else "BM25 (no embedding model)",
                "commander_linked": bool(self.commander and getattr(self.commander, "sections", None))}

    def _index(self) -> BM25:
        if self._bm25 is None:
            docs = {r["id"]: tokens((r["title"] or "") + " " + r["text"])
                    for r in self.db.execute("SELECT id,title,text FROM items")}
            self._bm25 = BM25(docs)
        return self._bm25

    def search(self, query: str, limit: int = 8, scope: str | None = None, kinds: tuple | None = None,
               qvec: list[float] | None = None) -> list[dict]:
        q = tokens(query)
        bm = self._index().scores(q) if q else {}
        top = max(bm.values()) if bm else 0.0
        scores: dict[int, float] = {i: 0.6 * s / top for i, s in bm.items()} if top else {}
        if qvec:
            for r in self.db.execute("SELECT item_id, vec FROM vectors WHERE dim=?", (len(qvec),)):
                c = cosine(qvec, _vec(r["vec"]))
                if c > 0.35:
                    scores[r["item_id"]] = scores.get(r["item_id"], 0.0) + 0.6 * c
        if not scores:
            return []
        ids = sorted(scores, key=lambda i: -scores[i])[: limit * 4]
        rows = {r["id"]: r for r in self.db.execute(
            f"SELECT * FROM items WHERE id IN ({','.join('?' * len(ids))})", ids)}
        out = []
        now = time.time()
        for i in ids:
            r = rows.get(i)
            if r is None:
                continue
            if scope and r["scope"] not in ("global", scope):
                continue
            if kinds and r["kind"] not in kinds:
                continue
            s = scores[i] + (0.15 if r["pinned"] else 0) + 0.05 / (1 + (now - (r["updated"] or now)) / 86400 / 30)
            out.append(dict(self._row(r), score=round(s, 4)))
        out.sort(key=lambda x: -x["score"])
        return out[:limit]

    async def asearch(self, query: str, limit: int = 8, scope: str | None = None, kinds: tuple | None = None) -> list[dict]:
        qvec = None
        if self.embedder is not None:
            try:
                got = await self.embedder([query])
                if got:
                    self.embed_model, vecs = got
                    qvec = vecs[0]
            except Exception:
                qvec = None
        return self.search(query, limit, scope, kinds, qvec)

    async def embed_pending(self, batch: int = 32) -> int:
        """Embed items without a vector for the current model. Returns how many were embedded."""
        if self.embedder is None:
            return 0
        rows = list(self.db.execute(
            "SELECT i.id, i.title, i.text FROM items i LEFT JOIN vectors v ON v.item_id=i.id WHERE v.item_id IS NULL LIMIT ?",
            (batch,)))
        if not rows:
            return 0
        got = await self.embedder([((r["title"] or "") + "\n" + r["text"])[:4000] for r in rows])
        if not got:
            return 0
        model, vecs = got
        self.embed_model = model
        with self._lock:
            for r, v in zip(rows, vecs):
                a = array.array("f", v)
                self.db.execute("INSERT OR REPLACE INTO vectors(item_id,model,dim,vec) VALUES(?,?,?,?)",
                                (r["id"], model, len(v), a.tobytes()))
            self.db.commit()
        return len(rows)

    async def context_block(self, query: str, budget: int = 1800, scope: str | None = None,
                            include_commander: bool = True) -> tuple[str, list[dict]]:
        hits = await self.asearch(query, limit=6, scope=scope)
        pinned = [p for p in self.list(pinned=True, limit=5) if p["id"] not in {h["id"] for h in hits}]
        lines, cites, used = [], [], 0
        for h in pinned + hits:
            line = f"[M{h['id']}] ({h['kind']}{' · ' + h['title'] if h['title'] else ''}) {h['text']}"
            if used + len(line) > budget:
                line = line[:max(0, budget - used)]
            if not line:
                break
            lines.append(line); used += len(line)
            cites.append({"id": f"M{h['id']}", "kind": h["kind"], "title": h["title"], "preview": h["text"][:160]})
            if used >= budget:
                break
        if include_commander and self.commander is not None and used < budget:
            try:
                for s in self.commander.search(query, k=2):
                    line = f"[P:{s['title'][:40]}] {s['text']}"[: max(0, budget - used)]
                    if not line:
                        break
                    lines.append(line); used += len(line)
                    cites.append({"id": f"P:{s['title'][:40]}", "kind": "commander-profile", "title": s["title"],
                                  "preview": ""})            # private profile text never goes back to the browser here
            except Exception:
                pass
        if not lines:
            return "", []
        return ("[LONG-TERM MEMORY — retrieved for this request. Use only if relevant; cite as [M#] when you rely "
                "on an item.]\n" + "\n".join(lines) + "\n[END MEMORY]\n"), cites

    # ── import (copy, idempotent, originals untouched) ────────
    def import_legacy(self, operator_memory=None, team_memory=None, notes_path: Path | None = None) -> dict:
        n = {"facts": 0, "team": 0, "notes": 0, "profile": 0}
        if operator_memory is not None:
            data = getattr(operator_memory, "data", {}) or {}
            for f in data.get("facts", []):
                key = "opmem:" + hashlib.sha256(f.get("text", "").encode()).hexdigest()[:20]
                r = self.add(f.get("text", ""), kind="fact", source="operator memory · " + str(f.get("source", "")),
                             source_key=key, ts=f.get("ts"))
                n["facts"] += bool(r.get("id"))
            name = (data.get("profile") or {}).get("name")
            if name:
                r = self.add(f"The operator's name is {name}.", kind="fact", source="operator memory · profile",
                             source_key="opmem:profile:name")
                n["profile"] += bool(r.get("id"))
        if team_memory is not None:
            for e in list(getattr(team_memory, "entries", [])):
                key = f"team:{e.get('ts')}:{e.get('agent')}"
                r = self.add(e.get("text", ""), kind="episode", title=f"{str(e.get('agent', '?')).upper()} · {e.get('kind', '')}",
                             source="team memory", source_key=key, ts=e.get("ts"))
                n["team"] += bool(r.get("id"))
        if notes_path is not None:
            try:
                for note in json.loads(Path(notes_path).read_text(encoding="utf-8")):
                    t = note.get("text", "") if isinstance(note, dict) else str(note)
                    key = "note:" + hashlib.sha256(t.encode()).hexdigest()[:20]
                    r = self.add(t, kind="note", source="command notes", source_key=key,
                                 ts=note.get("ts") if isinstance(note, dict) else None)
                    n["notes"] += bool(r.get("id"))
            except Exception:
                pass
        return {"ok": True, "imported": n}

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
