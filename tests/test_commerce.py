"""PROJECT TRILLION — purchase-to-delivery with a fake gateway (no network, no money)."""

import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import commerce.api as capi  # noqa: E402
from commerce.ledger import Ledger  # noqa: E402

SECRET, WH = "test_secret_123", "wh_secret_456"


class FakeGateway:
    name, configured, mode, key_id, key_secret, webhook_secret = "razorpay", True, "test", "rzp_test_abc", SECRET, WH

    def __init__(self):
        self.orders, self.payments, self.refunds = {}, {}, []

    def status(self):
        return {"gateway": self.name, "configured": True, "mode": "test"}

    async def create_order(self, amount, receipt, notes):
        gid = f"order_{len(self.orders) + 1}"
        self.orders[gid] = {"id": gid, "amount": amount, "currency": "INR"}
        return self.orders[gid]

    def checkout(self, order, g):
        return {"gateway": "razorpay", "order_id": g["id"], "amount": g["amount"]}

    def verify_client_signature(self, goid, pid, sig):
        want = hmac.new(SECRET.encode(), f"{goid}|{pid}".encode(), hashlib.sha256).hexdigest()
        return bool(sig) and hmac.compare_digest(want, sig)

    def verify_webhook(self, raw, sig):
        return hmac.compare_digest(hmac.new(WH.encode(), raw, hashlib.sha256).hexdigest(), sig or "")

    async def fetch_payment(self, pid):
        return dict(self.payments[pid])

    async def capture(self, pid, amount):
        self.payments[pid]["status"] = "captured"; return dict(self.payments[pid])

    async def refund(self, pid, amount, note=""):
        r = {"id": f"rfnd_{len(self.refunds) + 1}", "amount": amount, "status": "processed", "payment_id": pid}
        self.refunds.append(r); return r

    async def settlement_recon(self, day=None):
        return [{"type": "payment", "entity_id": p, "settlement_id": "setl_1", "settled_at": 1.0}
                for p, v in self.payments.items() if v["status"] == "captured"]


def sign(goid, pid):
    return hmac.new(SECRET.encode(), f"{goid}|{pid}".encode(), hashlib.sha256).hexdigest()


class Commerce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.L = Ledger("test", Path(self.tmp.name) / "c.db")
        self.gw = FakeGateway()
        self.owner = {"v": True}
        self.audits = []

        async def audit(kind, detail):
            self.audits.append(kind)
        state = {"licensing": mock.Mock(owner=True)}
        app = FastAPI()
        app.include_router(capi.build_router(state, lambda req: self.owner["v"], audit))
        self.p = [mock.patch.object(capi, "gateway", lambda: self.gw), mock.patch.dict(capi._ledgers, {"test": self.L}, clear=True),
                  mock.patch.dict(capi.EXECUTORS, {"website.extract": self._exec}), mock.patch.object(capi, "_rate", {})]
        for x in self.p:
            x.start()
        self.c = TestClient(app)
        self.exec_calls = []

    def tearDown(self):
        for x in self.p:
            x.stop()
        self.L.db.close(); self.tmp.cleanup()

    async def _exec(self, inp):
        self.exec_calls.append(inp)
        if inp["url"].endswith("/fail"):
            raise RuntimeError("site unreachable")
        return {"url": inp["url"], "title": "Example"}

    def order(self, url="https://example.com", **extra):
        r = self.c.post("/commerce/v1/orders", json={"service_id": "website.extract", "input": {"url": url}, **extra}).json()
        self.assertTrue(r["ok"], r); return r

    def pay(self, r, status="captured", amount=None, goid=None):
        goid = goid or r["checkout"]["order_id"]
        pid = f"pay_{len(self.gw.payments) + 1}"
        self.gw.payments[pid] = {"id": pid, "order_id": goid, "amount": amount or r["amount_paise"], "currency": "INR",
                                 "status": status, "fee": 24, "tax": 4, "method": "upi"}
        return pid

    # ── catalogue ──
    def test_catalog_sells_only_what_runs(self):
        cat = self.c.get("/commerce/v1/catalog").json()
        avail = [s["id"] for s in cat["services"] if s["available"]]
        self.assertEqual(avail, ["website.extract"])
        planned = [s for s in cat["services"] if not s["available"]]
        self.assertEqual(len(planned), 6)
        self.assertTrue(all("price_paise" not in s for s in planned))
        self.assertEqual(self.c.post("/commerce/v1/orders", json={"service_id": "company.intel", "input": {}}).status_code, 404)

    # ── the core rule: no verified payment → no delivery ──
    def test_purchase_to_delivery_requires_server_verified_payment(self):
        r = self.order()
        tok = {"x-order-token": r["access_token"]}
        pid = self.pay(r)
        bad = self.c.post(f"/commerce/v1/orders/{r['order_id']}/verify", headers=tok, json={"payment_id": pid, "signature": "forged"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.exec_calls, [])
        ok = self.c.post(f"/commerce/v1/orders/{r['order_id']}/verify", headers=tok,
                         json={"payment_id": pid, "signature": sign(r["checkout"]["order_id"], pid)}).json()
        self.assertEqual(ok["order"]["status"], "delivered")
        self.assertEqual(ok["order"]["result"]["title"], "Example")
        self.assertEqual(self.c.get(f"/commerce/v1/orders/{r['order_id']}").status_code, 404)     # no token → nothing
        self.assertEqual(self.c.get(f"/commerce/v1/orders/{r['order_id']}", headers=tok).json()["order"]["status"], "delivered")

    def test_wrong_amount_or_other_orders_payment_is_refused(self):
        r = self.order(); tok = {"x-order-token": r["access_token"]}
        pid = self.pay(r, amount=r["amount_paise"] - 1)                         # underpaid
        v = self.c.post(f"/commerce/v1/orders/{r['order_id']}/verify", headers=tok,
                        json={"payment_id": pid, "signature": sign(r["checkout"]["order_id"], pid)})
        self.assertEqual(v.status_code, 402)
        r2 = self.order("https://example.org")
        pid2 = self.pay(r2)                                                      # paid for a DIFFERENT order
        v2 = self.c.post(f"/commerce/v1/orders/{r['order_id']}/verify", headers=tok,
                         json={"payment_id": pid2, "signature": sign(r["checkout"]["order_id"], pid2)})
        self.assertEqual(v2.status_code, 402)
        self.assertEqual(self.exec_calls, [])

    def test_authorized_payment_is_captured_then_delivered(self):
        r = self.order(); pid = self.pay(r, status="authorized")
        v = self.c.post(f"/commerce/v1/orders/{r['order_id']}/verify", headers={"x-order-token": r["access_token"]},
                        json={"payment_id": pid, "signature": sign(r["checkout"]["order_id"], pid)}).json()
        self.assertEqual(v["order"]["status"], "delivered")
        self.assertEqual(self.gw.payments[pid]["status"], "captured")

    def test_idempotency_and_duplicate_payment_prevention(self):
        r = self.order(idempotency_key="abc-1")
        again = self.c.post("/commerce/v1/orders", json={"service_id": "website.extract", "input": {"url": "https://example.com"},
                                                         "idempotency_key": "abc-1"}).json()
        self.assertTrue(again["duplicate"]); self.assertEqual(len(self.gw.orders), 1)   # no second gateway order
        pid = self.pay(r); body = {"payment_id": pid, "signature": sign(r["checkout"]["order_id"], pid)}
        for _ in range(3):                                                          # replayed verify calls
            self.c.post(f"/commerce/v1/orders/{r['order_id']}/verify", headers={"x-order-token": r["access_token"]}, json=body)
        self.assertEqual(len(self.exec_calls), 1)
        self.assertEqual(self.L.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)

    def test_webhook_is_signature_checked_idempotent_and_refetches_payment(self):
        r = self.order(); pid = self.pay(r)
        body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {"id": pid, "order_id": r["checkout"]["order_id"],
                                                                                          "amount": 1, "status": "captured"}}}}).encode()
        self.assertEqual(self.c.post("/commerce/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": "nope"}).status_code, 400)
        sig = hmac.new(WH.encode(), body, hashlib.sha256).hexdigest()
        h = {"x-razorpay-signature": sig, "x-razorpay-event-id": "evt_1"}
        self.assertTrue(self.c.post("/commerce/v1/webhooks/razorpay", content=body, headers=h).json()["ok"])
        self.assertTrue(self.c.post("/commerce/v1/webhooks/razorpay", content=body, headers=h).json()["duplicate"])
        self.assertEqual(self.L.order(r["order_id"])["status"], "delivered")      # amount came from the gateway, not the webhook
        self.assertEqual(len(self.exec_calls), 1)

    def test_failure_after_payment_is_flagged_and_refund_needs_owner_confirmation(self):
        r = self.order("https://example.com/fail"); pid = self.pay(r)
        v = self.c.post(f"/commerce/v1/orders/{r['order_id']}/verify", headers={"x-order-token": r["access_token"]},
                        json={"payment_id": pid, "signature": sign(r["checkout"]["order_id"], pid)}).json()
        self.assertEqual(v["order"]["status"], "failed")
        self.assertEqual(self.gw.refunds, [])                                      # never refunded automatically
        no = self.c.post("/api/commerce/admin/refund", json={"payment_id": pid, "amount_paise": 1000})
        self.assertEqual(no.status_code, 400); self.assertEqual(self.gw.refunds, [])
        yes = self.c.post("/api/commerce/admin/refund", json={"payment_id": pid, "amount_paise": 1000, "confirm": "REFUND",
                                                              "reason": "service failed"}).json()
        self.assertTrue(yes["ok"])
        ov = self.c.get("/api/commerce/admin/overview").json()
        self.assertEqual((ov["gross_paise"], ov["fees_paise"], ov["refunds_paise"]), (1000, 28, 1000))
        self.assertEqual(ov["net_paise"], 1000 - 28 - 1000)

    def test_overview_is_ledger_only_and_tamper_evident(self):
        ov = self.c.get("/api/commerce/admin/overview").json()
        self.assertEqual((ov["gross_paise"], ov["transactions"], ov["net_paise"]), (0, 0, 0))   # nothing invented
        r = self.order(); pid = self.pay(r)
        self.c.post(f"/commerce/v1/orders/{r['order_id']}/verify", headers={"x-order-token": r["access_token"]},
                    json={"payment_id": pid, "signature": sign(r["checkout"]["order_id"], pid)})
        ov = self.c.get("/api/commerce/admin/overview").json()
        self.assertEqual((ov["gross_paise"], ov["transactions"], ov["pending_settlement_paise"]), (1000, 1, 1000 - 28))
        self.assertTrue(ov["chain"]["ok"])
        self.assertTrue(self.c.post("/api/commerce/admin/settlements/sync?days=1").json()["newly_settled"] == 1)
        ov = self.c.get("/api/commerce/admin/overview").json()
        self.assertEqual((ov["pending_settlement_paise"], ov["settled_paise"]), (0, 1000 - 28))
        self.L.db.execute("UPDATE events SET data_json='{\"amount_paise\": 99999}' WHERE seq=2")   # rewrite history
        self.assertFalse(self.c.get("/api/commerce/admin/overview").json()["chain"]["ok"])

    def test_admin_is_owner_only_and_limits_are_enforced(self):
        self.owner["v"] = False
        self.assertEqual(self.c.get("/api/commerce/admin/overview").status_code, 403)
        self.assertEqual(self.c.post("/api/commerce/admin/price", json={"service_id": "website.extract", "price_paise": 1}).status_code, 403)
        self.owner["v"] = True
        self.c.post("/api/commerce/admin/limits", json={"require_api_key": True, "per_txn_max_paise": 5000})
        self.assertEqual(self.c.post("/commerce/v1/orders", json={"service_id": "website.extract",
                                                                  "input": {"url": "https://example.com"}}).status_code, 401)
        k = self.c.post("/api/commerce/admin/customers", json={"name": "agent-1", "kind": "agent", "daily_paise": 1500}).json()
        hdr = {"x-api-key": k["api_key"]}
        ok = self.c.post("/commerce/v1/orders", headers=hdr, json={"service_id": "website.extract", "input": {"url": "https://a.com"}})
        self.assertEqual(ok.status_code, 200)
        cap = self.c.post("/commerce/v1/orders", headers=hdr, json={"service_id": "website.extract", "input": {"url": "https://b.com"}})
        self.assertEqual(cap.status_code, 403)                                          # 1000 + 1000 > 1500 daily cap
        self.assertEqual(len(self.c.get("/commerce/v1/history", headers=hdr).json()["orders"]), 1)

    def test_not_available_on_customer_installs(self):
        app = FastAPI()
        app.include_router(capi.build_router({"licensing": mock.Mock(owner=False)}, lambda r: True, None))
        self.assertEqual(TestClient(app).get("/commerce/v1/catalog").status_code, 404)


class WebsiteExtractionSafety(unittest.TestCase):
    def test_refuses_private_and_local_addresses(self):
        import asyncio
        from commerce.executors import ExecutionError, website_extract
        for url in ("http://127.0.0.1:8765/api/export", "http://localhost/", "http://169.254.169.254/latest/meta-data",
                    "http://10.0.0.1/", "file:///C:/Windows/win.ini", "ftp://example.com"):
            with self.assertRaises(ExecutionError, msg=url):
                asyncio.run(website_extract({"url": url}))


if __name__ == "__main__":
    unittest.main()
