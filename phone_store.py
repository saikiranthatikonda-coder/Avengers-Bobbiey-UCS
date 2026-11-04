"""PHONE AGENT · storage (.update/phone.db, gitignored).

contacts   name, E.164 number, notes, timezone, consent (flag + timestamp + source), DNC, blocked
calls      provider call id, direction, from/to, status, timing, duration, cost, purpose, summary
events     status timeline per call (from the provider's StatusCallback)
turns      turn-by-turn transcript (who, text, speech confidence)
followups  actions extracted from the post-call summary (open / done)
outbound   one row per outbound attempt (for rate limits)

All values here come from real calls. Nothing is seeded.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, number TEXT NOT NULL UNIQUE, notes TEXT DEFAULT '', tz TEXT DEFAULT '',
  consent INTEGER DEFAULT 0, consent_ts REAL, consent_source TEXT DEFAULT '',
  dnc INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sid TEXT UNIQUE, provider TEXT, direction TEXT, from_number TEXT, to_number TEXT,
  contact_id INTEGER, status TEXT, purpose TEXT DEFAULT '', created REAL, answered REAL, ended REAL,
  duration INTEGER, price TEXT, price_unit TEXT, end_reason TEXT DEFAULT '',
  summary TEXT, summary_engine TEXT, summary_ts REAL, recorded INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id INTEGER, ts REAL, status TEXT, detail TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id INTEGER, ts REAL, who TEXT, text TEXT, confidence REAL);
CREATE TABLE IF NOT EXISTS followups (
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id INTEGER, text TEXT, due TEXT DEFAULT '',
  status TEXT DEFAULT 'open', created REAL, done_ts REAL);
CREATE TABLE IF NOT EXISTS outbound (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, contact_id INTEGER, number TEXT);
CREATE INDEX IF NOT EXISTS turns_call ON turns(call_id);
CREATE INDEX IF NOT EXISTS events_call ON events(call_id);
"""

CONTACT_FIELDS = ("id", "name", "number", "notes", "tz", "consent", "consent_ts", "consent_source",
                  "dnc", "blocked", "created", "updated")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _q(self, sql: str, args: tuple = (), one: bool = False):
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
        rows = [dict(r) for r in rows]
        return (rows[0] if rows else None) if one else rows

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.lastrowid

    # ── contacts ──
    def contacts(self) -> list[dict]:
        return self._q("SELECT * FROM contacts ORDER BY name COLLATE NOCASE")

    def contact(self, cid: int) -> dict | None:
        return self._q("SELECT * FROM contacts WHERE id=?", (cid,), one=True)

    def contact_by_number(self, number: str) -> dict | None:
        return self._q("SELECT * FROM contacts WHERE number=?", (number,), one=True)

    def upsert_contact(self, c: dict) -> dict:
        now = time.time()
        old = self.contact(c["id"]) if c.get("id") else self.contact_by_number(c["number"])
        if old and c.get("id") is None and old["number"] == c["number"]:
            c["id"] = old["id"]
        consent = 1 if c.get("consent") else 0
        if old:
            # consent timestamp/source change only when consent itself changes (audit trail)
            if consent and not old["consent"]:
                cts, csrc = now, c.get("consent_source") or "recorded in BUCS"
            elif consent:
                cts, csrc = old["consent_ts"], c.get("consent_source") or old["consent_source"]
            else:
                cts, csrc = None, ""
            self._x("UPDATE contacts SET name=?, number=?, notes=?, tz=?, consent=?, consent_ts=?, "
                    "consent_source=?, dnc=?, blocked=?, updated=? WHERE id=?",
                    (c["name"], c["number"], c.get("notes", ""), c.get("tz", ""), consent, cts, csrc,
                     1 if c.get("dnc") else 0, 1 if c.get("blocked") else 0, now, old["id"]))
            return self.contact(old["id"])
        cid = self._x("INSERT INTO contacts (name, number, notes, tz, consent, consent_ts, consent_source, "
                      "dnc, blocked, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (c["name"], c["number"], c.get("notes", ""), c.get("tz", ""), consent,
                       now if consent else None, (c.get("consent_source") or "recorded in BUCS") if consent else "",
                       1 if c.get("dnc") else 0, 1 if c.get("blocked") else 0, now, now))
        return self.contact(cid)

    def delete_contact(self, cid: int) -> bool:
        self._x("DELETE FROM contacts WHERE id=?", (cid,))
        return True

    # ── calls ──
    def create_call(self, **kw) -> int:
        kw.setdefault("created", time.time())
        cols = ",".join(kw)
        return self._x(f"INSERT INTO calls ({cols}) VALUES ({','.join('?' * len(kw))})", tuple(kw.values()))

    def update_call(self, call_id: int, **kw) -> None:
        if not kw:
            return
        sets = ",".join(f"{k}=?" for k in kw)
        self._x(f"UPDATE calls SET {sets} WHERE id=?", tuple(kw.values()) + (call_id,))

    def call(self, call_id: int) -> dict | None:
        return self._q("SELECT * FROM calls WHERE id=?", (call_id,), one=True)

    def call_by_sid(self, sid: str) -> dict | None:
        return self._q("SELECT * FROM calls WHERE sid=?", (sid,), one=True)

    def calls(self, limit: int = 50) -> list[dict]:
        return self._q("SELECT c.*, (SELECT COUNT(*) FROM turns t WHERE t.call_id=c.id) AS turns, "
                       "ct.name AS contact_name FROM calls c LEFT JOIN contacts ct ON ct.id=c.contact_id "
                       "ORDER BY c.created DESC LIMIT ?", (int(limit),))

    def add_event(self, call_id: int, status: str, detail: str = "") -> None:
        self._x("INSERT INTO events (call_id, ts, status, detail) VALUES (?,?,?,?)",
                (call_id, time.time(), status, detail[:300]))

    def events(self, call_id: int) -> list[dict]:
        return self._q("SELECT ts, status, detail FROM events WHERE call_id=? ORDER BY id", (call_id,))

    def add_turn(self, call_id: int, who: str, text: str, confidence: float | None = None) -> dict:
        ts = time.time()
        tid = self._x("INSERT INTO turns (call_id, ts, who, text, confidence) VALUES (?,?,?,?,?)",
                      (call_id, ts, who, text[:2000], confidence))
        return {"id": tid, "ts": ts, "who": who, "text": text[:2000], "confidence": confidence}

    def turns(self, call_id: int) -> list[dict]:
        return self._q("SELECT id, ts, who, text, confidence FROM turns WHERE call_id=? ORDER BY id", (call_id,))

    # ── follow-ups ──
    def add_followup(self, call_id: int, text: str, due: str = "") -> int:
        return self._x("INSERT INTO followups (call_id, text, due, status, created) VALUES (?,?,?,?,?)",
                       (call_id, text[:400], due[:60], "open", time.time()))

    def followups(self, status: str | None = None, call_id: int | None = None) -> list[dict]:
        sql, args = "SELECT f.*, c.to_number, c.from_number, c.direction FROM followups f " \
                    "LEFT JOIN calls c ON c.id=f.call_id WHERE 1=1", []
        if status:
            sql += " AND f.status=?"
            args.append(status)
        if call_id is not None:
            sql += " AND f.call_id=?"
            args.append(call_id)
        return self._q(sql + " ORDER BY f.created DESC LIMIT 200", tuple(args))

    def set_followup(self, fid: int, status: str) -> None:
        self._x("UPDATE followups SET status=?, done_ts=? WHERE id=?",
                (status, time.time() if status == "done" else None, fid))

    # ── outbound attempts (rate limits) ──
    def log_outbound(self, contact_id: int, number: str) -> None:
        self._x("INSERT INTO outbound (ts, contact_id, number) VALUES (?,?,?)", (time.time(), contact_id, number))

    def outbound_since(self, since: float, contact_id: int | None = None) -> int:
        if contact_id is None:
            r = self._q("SELECT COUNT(*) AS n FROM outbound WHERE ts>=?", (since,), one=True)
        else:
            r = self._q("SELECT COUNT(*) AS n FROM outbound WHERE ts>=? AND contact_id=?", (since, contact_id), one=True)
        return int((r or {}).get("n") or 0)

    # ── analytics ──
    def analytics(self) -> dict:
        tot = self._q("SELECT COUNT(*) AS n FROM calls", one=True)["n"]
        by_status = {r["status"] or "unknown": r["n"] for r in
                     self._q("SELECT status, COUNT(*) AS n FROM calls GROUP BY status")}
        by_dir = {r["direction"] or "unknown": r["n"] for r in
                  self._q("SELECT direction, COUNT(*) AS n FROM calls GROUP BY direction")}
        avg = self._q("SELECT AVG(duration) AS a FROM calls WHERE duration > 0", one=True)["a"]
        out_tot = self._q("SELECT COUNT(*) AS n FROM calls WHERE direction='outbound' AND "
                          "status IN ('completed','busy','no-answer','failed','canceled')", one=True)["n"]
        out_ans = self._q("SELECT COUNT(*) AS n FROM calls WHERE direction='outbound' AND answered IS NOT NULL",
                          one=True)["n"]
        tpc = self._q("SELECT AVG(n) AS a FROM (SELECT COUNT(t.id) AS n FROM calls c JOIN turns t "
                      "ON t.call_id=c.id GROUP BY c.id)", one=True)["a"]
        open_f = self._q("SELECT COUNT(*) AS n FROM followups WHERE status='open'", one=True)["n"]
        return {"total": tot, "by_status": by_status, "by_direction": by_dir,
                "avg_duration_s": round(avg, 1) if avg is not None else None,
                "answer_rate": round(out_ans / out_tot, 3) if out_tot else None,
                "outbound_finished": out_tot,
                "turns_per_call": round(tpc, 1) if tpc is not None else None,
                "open_followups": open_f}


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False)
