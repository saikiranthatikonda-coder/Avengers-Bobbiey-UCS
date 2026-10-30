"""Revenue opportunity pipeline (owner-only): save a market opportunity with its
evidence, compare competitors, model unit economics, approve/reject, convert an
approved one into a DRAFT BUCS service (never live until it has a tested
executor), and track directory listings.

Estimates are always labelled as estimates; actual money only ever comes from
the ledger's gateway-confirmed payments.

Schema changes are additive (CREATE TABLE IF NOT EXISTS) and the database file
is backed up before the first migration, so they are reversible.
"""

import json
import secrets
import shutil
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
BACKUPS = HERE / ".update" / "backups"

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT, status TEXT NOT NULL DEFAULT 'saved',
  evidence_json TEXT, competitors_json TEXT, price_usd REAL, cost_usd REAL, monthly_requests INTEGER,
  fixed_monthly_usd REAL, notes TEXT, decision_note TEXT, service_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY, directory TEXT NOT NULL, service_id TEXT, listing_url TEXT, status TEXT NOT NULL,
  detail_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
"""


def migrate(ledger) -> None:
    db = ledger.db
    have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {"opportunities", "listings"} <= have:
        return
    if ledger.path.exists() and ledger.path.stat().st_size:
        BACKUPS.mkdir(parents=True, exist_ok=True)
        db.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(ledger.path, BACKUPS / f"{ledger.path.stem}-{time.strftime('%Y%m%d-%H%M%S')}-pre-pipeline.db")
    db.executescript(SCHEMA)


def economics(o: dict) -> dict:
    """Unit economics — an ESTIMATE from the owner's inputs, never actual revenue."""
    if o.get("price_usd") is None or o.get("cost_usd") is None:      # unknown inputs → unknown economics, never 100%
        return {"kind": "estimate", "revenue_usd": None, "variable_cost_usd": None, "fixed_cost_usd": None,
                "profit_usd": None, "unit_margin_pct": None, "break_even_requests": None, "missing": "price and cost per request"}
    price, cost = float(o["price_usd"]), float(o["cost_usd"])
    n, fixed = int(o.get("monthly_requests") or 0), float(o.get("fixed_monthly_usd") or 0)
    rev, var = price * n, cost * n
    profit = rev - var - fixed
    return {"kind": "estimate", "revenue_usd": round(rev, 2), "variable_cost_usd": round(var, 2),
            "fixed_cost_usd": round(fixed, 2), "profit_usd": round(profit, 2),
            "unit_margin_pct": round((price - cost) / price * 100, 1) if price else None,
            "break_even_requests": (int(fixed / (price - cost)) + 1) if price > cost else None}


def _row(r) -> dict:
    d = dict(r)
    for k in ("evidence_json", "competitors_json", "detail_json"):
        if k in d:
            d[k[:-5]] = json.loads(d.pop(k) or "null")
    if "price_usd" in d:
        d["economics"] = economics(d)
    return d


def save(ledger, o: dict) -> dict:
    oid = "opp_" + secrets.token_hex(5)
    now = time.time()
    with ledger.tx():
        ledger.db.execute("INSERT INTO opportunities (id, title, category, status, evidence_json, competitors_json, price_usd, cost_usd, "
                          "monthly_requests, fixed_monthly_usd, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (oid, str(o["title"])[:120], o.get("category"), "saved", json.dumps(o.get("evidence") or {}),
                           json.dumps(o.get("competitors") or []), o.get("price_usd"), o.get("cost_usd"),
                           o.get("monthly_requests"), o.get("fixed_monthly_usd"), str(o.get("notes") or "")[:2000], now, now))
        ledger._append_event("opportunity.saved", oid, {"title": str(o["title"])[:120], "category": o.get("category")})
    return get(ledger, oid)


def update(ledger, oid: str, fields: dict) -> dict | None:
    allowed = {"title", "category", "price_usd", "cost_usd", "monthly_requests", "fixed_monthly_usd", "notes"}
    f = {k: v for k, v in fields.items() if k in allowed}
    if not f:
        return get(ledger, oid)
    f["updated_at"] = time.time()
    with ledger.tx():
        ledger.db.execute(f"UPDATE opportunities SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), oid))
        ledger._append_event("opportunity.updated", oid, {k: v for k, v in f.items() if k != "updated_at"})
    return get(ledger, oid)


def decide(ledger, oid: str, decision: str, note: str = "") -> dict | None:
    o = get(ledger, oid)
    if not o or decision not in ("approved", "rejected") or o["status"] == "converted":
        return None
    with ledger.tx():
        ledger.db.execute("UPDATE opportunities SET status=?, decision_note=?, updated_at=? WHERE id=?",
                          (decision, note[:500], time.time(), oid))
        ledger._append_event(f"opportunity.{decision}", oid, {"note": note[:500]})
    return get(ledger, oid)


def convert(ledger, oid: str, service_id: str) -> dict | None:
    """Approved → a DRAFT service entry (not sellable until an executor exists and passes tests)."""
    o = get(ledger, oid)
    if not o or o["status"] != "approved":
        return None
    drafts = ledger.get_setting("draft_services", {})
    drafts[service_id] = {"from_opportunity": oid, "title": o["title"], "category": o.get("category"),
                          "price_usd": o.get("price_usd"), "status": "draft: needs executor + tests before going live",
                          "created_at": time.time()}
    ledger.set_setting("draft_services", drafts)
    with ledger.tx():
        ledger.db.execute("UPDATE opportunities SET status='converted', service_id=?, updated_at=? WHERE id=?",
                          (service_id, time.time(), oid))
        ledger._append_event("opportunity.converted", oid, {"service_id": service_id})
    return get(ledger, oid)


def get(ledger, oid: str) -> dict | None:
    r = ledger.db.execute("SELECT * FROM opportunities WHERE id=?", (oid,)).fetchone()
    return _row(r) if r else None


def all_(ledger) -> list[dict]:
    return [_row(r) for r in ledger.db.execute("SELECT * FROM opportunities ORDER BY updated_at DESC LIMIT 200")]


def add_listing(ledger, directory: str, service_id: str, url: str, status: str, detail: dict | None = None) -> dict:
    lid = "lst_" + secrets.token_hex(5)
    now = time.time()
    with ledger.tx():
        ledger.db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)",
                          (lid, directory[:60], service_id, url[:500], status[:40], json.dumps(detail or {}), now, now))
        ledger._append_event("listing." + status[:40], lid, {"directory": directory[:60], "service": service_id})
    return _row(ledger.db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone())


def listings(ledger) -> list[dict]:
    return [_row(r) for r in ledger.db.execute("SELECT * FROM listings ORDER BY updated_at DESC")]
