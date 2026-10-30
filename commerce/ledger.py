"""The books. SQLite, one database per mode (test / live) so test traffic can
never appear in real revenue. Every state change also appends to `events`, a
hash chain (each row hashes the previous one) — editing or deleting history
breaks the chain and verify_chain() reports where.

Money is stored as integer paise (₹1 = 100 paise); never floats.
"""

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, service_id TEXT NOT NULL, customer_id TEXT, input_json TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK (amount_paise > 0), currency TEXT NOT NULL DEFAULT 'INR',
  status TEXT NOT NULL, gateway TEXT, gateway_order_id TEXT UNIQUE, idempotency_key TEXT UNIQUE,
  access_hash TEXT NOT NULL, result_json TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), amount_paise INTEGER NOT NULL,
  fee_paise INTEGER NOT NULL DEFAULT 0, tax_paise INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL,
  status TEXT NOT NULL, method TEXT, refunded_paise INTEGER NOT NULL DEFAULT 0,
  settlement_id TEXT, settled_at REAL, captured_at REAL, raw_json TEXT);
CREATE TABLE IF NOT EXISTS refunds (
  id TEXT PRIMARY KEY, payment_id TEXT NOT NULL REFERENCES payments(id), amount_paise INTEGER NOT NULL,
  status TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS customers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('human','agent')),
  key_hash TEXT UNIQUE, per_txn_limit_paise INTEGER, daily_limit_paise INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS webhook_events (id TEXT PRIMARY KEY, received_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, ref TEXT,
  data_json TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
"""


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Ledger:
    def __init__(self, mode: str, path: Path | None = None) -> None:
        assert mode in ("test", "live")
        self.mode = mode
        self.path = Path(path) if path else HERE / f"commerce_{mode}.db"
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    # ── low level ────────────────────────────────────────────────
    def _append_event(self, kind: str, ref: str | None, data: dict) -> None:
        last = self.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else "genesis"
        ts = time.time()
        body = json.dumps(data, sort_keys=True, separators=(",", ":"))
        h = sha(f"{prev}|{ts:.6f}|{kind}|{ref or ''}|{body}")
        self.db.execute("INSERT INTO events (ts, kind, ref, data_json, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                        (ts, kind, ref, body, prev, h))

    def tx(self):
        """BEGIN IMMEDIATE … COMMIT under a process lock: state change + event are atomic."""
        ledger = self

        class _Tx:
            def __enter__(self_):
                ledger._lock.acquire(); ledger.db.execute("BEGIN IMMEDIATE"); return ledger

            def __exit__(self_, et, e, tb):
                try:
                    ledger.db.execute("ROLLBACK" if et else "COMMIT")
                finally:
                    ledger._lock.release()
                return False
        return _Tx()

    def verify_chain(self) -> dict:
        prev, n = "genesis", 0
        for r in self.db.execute("SELECT * FROM events ORDER BY seq"):
            want = sha(f"{prev}|{r['ts']:.6f}|{r['kind']}|{r['ref'] or ''}|{r['data_json']}")
            if r["prev_hash"] != prev or r["hash"] != want:
                return {"ok": False, "broken_at": r["seq"], "checked": n}
            prev, n = r["hash"], n + 1
        return {"ok": True, "checked": n}

    # ── settings ─────────────────────────────────────────────────
    def get_setting(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value) -> None:
        with self.tx():
            self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value)))
            self._append_event("setting", key, {"value": value})

    # ── customers / agents ───────────────────────────────────────
    def add_customer(self, name: str, kind: str, per_txn_paise: int | None, daily_paise: int | None) -> tuple[str, str]:
        cid = "cus_" + secrets.token_hex(6)
        key = f"bk_{self.mode}_" + secrets.token_urlsafe(24)       # shown once, stored hashed
        with self.tx():
            self.db.execute("INSERT INTO customers VALUES (?,?,?,?,?,?,1,?)",
                            (cid, name[:80], kind, sha(key), per_txn_paise, daily_paise, time.time()))
            self._append_event("customer.created", cid, {"name": name[:80], "kind": kind,
                                                         "per_txn": per_txn_paise, "daily": daily_paise})
        return cid, key

    def customer_by_key(self, key: str):
        if not key or not key.startswith(f"bk_{self.mode}_"):
            return None
        return self.db.execute("SELECT * FROM customers WHERE key_hash=? AND enabled=1", (sha(key),)).fetchone()

    def set_customer_enabled(self, cid: str, enabled: bool) -> None:
        with self.tx():
            self.db.execute("UPDATE customers SET enabled=? WHERE id=?", (1 if enabled else 0, cid))
            self._append_event("customer.enabled" if enabled else "customer.disabled", cid, {})

    def spent_today(self, customer_id: str) -> int:
        start = time.time() - 24 * 3600
        r = self.db.execute("SELECT COALESCE(SUM(amount_paise),0) s FROM orders WHERE customer_id=? AND created_at>? "
                            "AND status IN ('awaiting_payment','paid','executing','delivered')", (customer_id, start)).fetchone()
        return int(r["s"])

    # ── orders ───────────────────────────────────────────────────
    def create_order(self, service_id: str, customer_id: str | None, inp: dict, amount_paise: int,
                     idem: str | None) -> tuple[dict, str, bool]:
        """Returns (order, access_token, created). Same idempotency key → same order, no duplicate."""
        if idem:
            r = self.db.execute("SELECT * FROM orders WHERE idempotency_key=?", (idem,)).fetchone()
            if r:
                return dict(r), "", False
        oid = "ord_" + secrets.token_hex(8)
        token = "ot_" + secrets.token_urlsafe(24)                    # lets the buyer fetch the result
        now = time.time()
        with self.tx():
            self.db.execute("INSERT INTO orders (id, service_id, customer_id, input_json, amount_paise, currency, status, "
                            "idempotency_key, access_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (oid, service_id, customer_id, json.dumps(inp), amount_paise, "INR", "created",
                             idem, sha(token), now, now))
            self._append_event("order.created", oid, {"service": service_id, "customer": customer_id, "amount_paise": amount_paise})
        return self.order(oid), token, True

    def order(self, oid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        return dict(r) if r else None

    def order_by_gateway(self, goid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM orders WHERE gateway_order_id=?", (goid,)).fetchone()
        return dict(r) if r else None

    def check_access(self, oid: str, token: str) -> bool:
        import hmac
        o = self.order(oid)
        return bool(o and token and hmac.compare_digest(o["access_hash"], sha(token)))

    def set_order(self, oid: str, kind: str, **fields) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.tx():
            self.db.execute(f"UPDATE orders SET {cols} WHERE id=?", (*fields.values(), oid))
            self._append_event(kind, oid, {k: v for k, v in fields.items() if k not in ("result_json", "updated_at")})

    # ── payments (only what the gateway confirmed) ───────────────
    def record_payment(self, p: dict, order_id: str) -> bool:
        """Insert or update a gateway-confirmed payment. Returns True if newly captured."""
        with self.tx():
            ex = self.db.execute("SELECT status FROM payments WHERE id=?", (p["id"],)).fetchone()
            if ex:
                self.db.execute("UPDATE payments SET status=?, fee_paise=?, tax_paise=?, raw_json=? WHERE id=?",
                                (p["status"], p.get("fee", 0) or 0, p.get("tax", 0) or 0, json.dumps(p), p["id"]))
                newly = ex["status"] != "captured" and p["status"] == "captured"
            else:
                self.db.execute("INSERT INTO payments (id, order_id, amount_paise, fee_paise, tax_paise, currency, status, "
                                "method, captured_at, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (p["id"], order_id, int(p["amount"]), int(p.get("fee") or 0), int(p.get("tax") or 0),
                                 p.get("currency", "INR"), p["status"], p.get("method"),
                                 time.time() if p["status"] == "captured" else None, json.dumps(p)))
                newly = p["status"] == "captured"
            self._append_event("payment." + p["status"], p["id"], {"order": order_id, "amount_paise": int(p["amount"]),
                                                                    "fee_paise": int(p.get("fee") or 0)})
            return newly

    def record_refund(self, r: dict, payment_id: str, reason: str = "") -> None:
        with self.tx():
            if not self.db.execute("SELECT 1 FROM refunds WHERE id=?", (r["id"],)).fetchone():
                self.db.execute("INSERT INTO refunds VALUES (?,?,?,?,?,?)",
                                (r["id"], payment_id, int(r["amount"]), r.get("status", "processed"), reason[:200], time.time()))
            self.db.execute("UPDATE refunds SET status=? WHERE id=?", (r.get("status", "processed"), r["id"]))
            tot = self.db.execute("SELECT COALESCE(SUM(amount_paise),0) s FROM refunds WHERE payment_id=? AND status!='failed'",
                                  (payment_id,)).fetchone()["s"]
            self.db.execute("UPDATE payments SET refunded_paise=? WHERE id=?", (tot, payment_id))
            self._append_event("refund." + r.get("status", "processed"), r["id"], {"payment": payment_id, "amount_paise": int(r["amount"])})

    def mark_settled(self, payment_id: str, settlement_id: str, settled_at: float) -> bool:
        with self.tx():
            cur = self.db.execute("UPDATE payments SET settlement_id=?, settled_at=? WHERE id=? AND settlement_id IS NULL",
                                  (settlement_id, settled_at, payment_id))
            if cur.rowcount:
                self._append_event("payment.settled", payment_id, {"settlement": settlement_id})
            return bool(cur.rowcount)

    def seen_webhook(self, event_id: str) -> bool:
        """True if this webhook event was already processed (idempotency)."""
        with self.tx():
            if self.db.execute("SELECT 1 FROM webhook_events WHERE id=?", (event_id,)).fetchone():
                return True
            self.db.execute("INSERT INTO webhook_events VALUES (?,?)", (event_id, time.time()))
            return False

    # ── owner analytics: computed only from gateway-confirmed rows ──
    def overview(self) -> dict:
        q = lambda sql, *a: self.db.execute(sql, a).fetchone()          # noqa: E731
        cap = q("SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) gross, COALESCE(SUM(fee_paise),0) fee, "
                "COALESCE(SUM(tax_paise),0) tax, COALESCE(SUM(refunded_paise),0) refunded FROM payments "
                "WHERE status IN ('captured','refunded')")
        pend = q("SELECT COALESCE(SUM(amount_paise - fee_paise - tax_paise - refunded_paise),0) s, COUNT(*) n "
                 "FROM payments WHERE status IN ('captured','refunded') AND settlement_id IS NULL")
        settled = q("SELECT COALESCE(SUM(amount_paise - fee_paise - tax_paise - refunded_paise),0) s FROM payments "
                    "WHERE settlement_id IS NOT NULL")
        failed = q("SELECT COUNT(*) n FROM payments WHERE status='failed'")["n"]
        gross, fees, refunded = cap["gross"], cap["fee"] + cap["tax"], cap["refunded"]

        def rows(sql):
            return [dict(r) for r in self.db.execute(sql)]
        by_service = rows("SELECT o.service_id key, COUNT(*) n, SUM(p.amount_paise) gross FROM payments p JOIN orders o "
                          "ON o.id=p.order_id WHERE p.status IN ('captured','refunded') GROUP BY o.service_id ORDER BY gross DESC")
        by_customer = rows("SELECT COALESCE(c.name, 'anonymous') key, COALESCE(c.kind,'human') kind, COUNT(*) n, "
                           "SUM(p.amount_paise) gross FROM payments p JOIN orders o ON o.id=p.order_id LEFT JOIN customers c "
                           "ON c.id=o.customer_id WHERE p.status IN ('captured','refunded') GROUP BY key, kind ORDER BY gross DESC LIMIT 20")

        def series(fmt, n, span):
            out = {}
            for r in self.db.execute("SELECT captured_at, amount_paise FROM payments WHERE status IN ('captured','refunded') "
                                     "AND captured_at > ?", (time.time() - n * span,)):
                k = time.strftime(fmt, time.localtime(r["captured_at"]))
                out[k] = out.get(k, 0) + r["amount_paise"]
            keys = [time.strftime(fmt, time.localtime(time.time() - i * span)) for i in range(n - 1, -1, -1)]
            seen, ordered = set(), []
            for k in keys:
                if k not in seen:
                    seen.add(k); ordered.append({"key": k, "gross": out.get(k, 0)})
            return ordered
        recent = rows("SELECT p.id, p.order_id, o.service_id, p.amount_paise, p.fee_paise + p.tax_paise fees, p.status, "
                      "p.refunded_paise, p.settlement_id, p.captured_at, p.method FROM payments p JOIN orders o ON o.id=p.order_id "
                      "ORDER BY COALESCE(p.captured_at, 0) DESC LIMIT 25")
        orders = q("SELECT COUNT(*) n, SUM(status='delivered') delivered, SUM(status='failed') failed, "
                   "SUM(status IN ('created','awaiting_payment')) open FROM orders")
        customers = rows("SELECT id, name, kind, per_txn_limit_paise, daily_limit_paise, enabled, created_at FROM customers "
                         "ORDER BY created_at DESC LIMIT 50")
        return {
            "mode": self.mode, "currency": "INR",
            "gross_paise": gross, "fees_paise": fees, "refunds_paise": refunded,
            "net_paise": gross - fees - refunded, "transactions": cap["n"],
            "avg_paise": round(gross / cap["n"]) if cap["n"] else 0,
            "pending_settlement_paise": pend["s"], "pending_settlement_count": pend["n"],
            "settled_paise": settled["s"], "failed_payments": failed,
            "orders": {k: (orders[k] or 0) for k in ("n", "delivered", "failed", "open")},
            "by_service": by_service, "by_customer": by_customer,
            "daily": series("%d %b", 30, 86400), "weekly": series("W%V", 12, 7 * 86400), "monthly": series("%b %Y", 12, 30 * 86400),
            "recent": recent, "customers": customers, "chain": self.verify_chain(),
        }
