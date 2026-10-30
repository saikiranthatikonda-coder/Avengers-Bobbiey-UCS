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


class X402Seller(unittest.TestCase):
    """x402: settle only after successful delivery; USDC kept apart from INR; off until configured."""

    PAY_TO = "0x1111111111111111111111111111111111111111"

    def setUp(self):
        import base64, os
        self.b64 = lambda o: base64.b64encode(json.dumps(o).encode()).decode()
        self.tmp = tempfile.TemporaryDirectory()
        self.L = Ledger("test", Path(self.tmp.name) / "c.db")
        self.calls = {"verify": 0, "settle": 0}
        self.valid = True

        async def fac(cfg, action, version, payload, req):
            self.calls[action] += 1
            if action == "verify":
                return {"isValid": self.valid, "payer": "0xAbCd000000000000000000000000000000000001", "invalidReason": None if self.valid else "invalid_signature"}
            return {"success": True, "transaction": f"0xtx{self.calls['settle']}", "network": "eip155:84532", "payer": "0xabcd"}

        async def ex(inp):
            if inp["url"].endswith("/blocked"):
                from commerce.executors import ExecutionError
                raise ExecutionError("the site answered HTTP 403", "SITE_REFUSED", 403)
            return {"schema": "bucs.website.extract/v1", "url": inp["url"], "title": "T"}
        self.env = mock.patch.dict(os.environ, {"X402_PAY_TO": self.PAY_TO, "X402_NETWORK": "eip155:84532"})
        self.p = [self.env, mock.patch.object(capi.X, "facilitator", fac), mock.patch.dict(capi._ledgers, {"test": self.L}, clear=True),
                  mock.patch.dict(capi.EXECUTORS, {"website.extract": ex}), mock.patch.object(capi, "_rate", {})]
        for x in self.p:
            x.start()
        app = FastAPI()

        async def audit(k, d): pass
        app.include_router(capi.build_router({"licensing": mock.Mock(owner=True)}, lambda r: True, audit))
        self.c = TestClient(app)

    def tearDown(self):
        for x in self.p:
            x.stop()
        self.L.db.close(); self.tmp.cleanup()

    def pay(self, url):
        return self.c.get("/x402/v1/website.extract", params={"url": url}, headers={"X-PAYMENT": self.b64({"x402Version": 1, "payload": {}})})

    def test_unpaid_call_gets_both_versions_of_the_terms(self):
        r = self.c.get("/x402/v1/website.extract", params={"url": "https://example.com"})
        self.assertEqual(r.status_code, 402)
        v1 = r.json()["accepts"][0]
        self.assertEqual((v1["network"], v1["maxAmountRequired"], v1["payTo"]), ("base-sepolia", "10000", self.PAY_TO))
        import base64
        v2 = json.loads(base64.b64decode(r.headers["payment-required"]))
        self.assertEqual((v2["x402Version"], v2["accepts"][0]["network"], v2["accepts"][0]["amount"]), (2, "eip155:84532", "10000"))
        self.assertEqual(self.calls, {"verify": 0, "settle": 0})

    def test_invalid_payment_gets_nothing(self):
        self.valid = False
        r = self.pay("https://example.com")
        self.assertEqual(r.status_code, 402); self.assertEqual(self.calls["settle"], 0)

    def test_failed_delivery_is_never_settled(self):
        r = self.pay("https://example.com/blocked")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["charged"], False)
        self.assertEqual(r.json()["error"]["code"], "SITE_REFUSED")
        self.assertEqual(self.calls, {"verify": 1, "settle": 0})               # verified, but no money taken
        self.assertEqual(self.L.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)

    def test_paid_delivery_settles_once_and_books_usdc_separately(self):
        r = self.pay("https://example.com")
        self.assertEqual(r.status_code, 200); self.assertEqual(r.json()["title"], "T")
        self.assertIn("payment-response", r.headers)
        self.assertEqual(self.calls, {"verify": 1, "settle": 1})
        usdc, inr = self.L.overview("USDC"), self.L.overview("INR")
        self.assertEqual((usdc["gross_paise"], usdc["transactions"], usdc["identified_buyers"]), (10000, 1, 1))
        self.assertEqual((inr["gross_paise"], inr["transactions"]), (0, 0))      # never mixed into rupees
        self.pay("https://example.com"); self.assertEqual(self.L.overview("USDC")["repeat_buyers"], 1)

    def test_owner_price_change_reaches_the_402_terms(self):
        self.assertEqual(self.c.post("/api/commerce/admin/x402/price", json={"service_id": "website.extract", "price_usd": 0.002}).status_code, 200)
        r = self.c.get("/x402/v1/website.extract", params={"url": "https://example.com"})
        self.assertEqual(r.json()["accepts"][0]["maxAmountRequired"], "2000")
        self.assertEqual(self.c.post("/api/commerce/admin/x402/price", json={"service_id": "website.extract", "price_usd": 0}).status_code, 422)

    def test_test_purchases_are_never_counted_as_customers(self):
        import os
        payer = "0xabcd000000000000000000000000000000000001"
        ov = lambda: self.c.get("/api/commerce/admin/overview").json()["usdc"]["by_class"]          # noqa: E731
        with mock.patch.dict(os.environ, {"X402_INTERNAL_WALLETS": payer.upper().replace("0X", "0x")}):
            self.assertEqual(self.pay("https://example.com").status_code, 200)
            c = ov()
        self.assertEqual((c["internal_test"]["payments"], c["external"]["buyers"]), (1, 0))   # owner's own test buy
        self.assertEqual(ov()["external"]["buyers"], 1)                                          # same wallet, unlabelled → external
        r = self.c.post("/api/commerce/admin/x402/wallet-label", json={"address": payer, "label": "marketplace_test"})
        self.assertTrue(r.json()["ok"])
        c = ov()
        self.assertEqual((c["marketplace_test"]["payments"], c["external"]["buyers"]), (1, 0))   # a directory's verification buy
        self.assertEqual(self.L.overview("USDC")["transactions"], 1)                             # the payment itself is untouched
        self.assertEqual(self.c.post("/api/commerce/admin/x402/wallet-label", json={"address": "nope", "label": "x"}).status_code, 422)

    def test_discovery_mcp_and_manifest(self):
        m = self.c.get("/.well-known/x402").json()
        self.assertEqual(m["services"][0]["x402"]["price_usd"], 0.01)
        init = self.c.post("/commerce/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).json()
        self.assertEqual(init["result"]["serverInfo"]["name"], "bucs-trillion")
        tools = self.c.post("/commerce/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"list_services", "website_extract"})
        call = self.c.post("/commerce/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                                 "params": {"name": "website_extract", "arguments": {"url": "https://a.com"}}}).json()
        body = json.loads(call["result"]["content"][0]["text"])
        self.assertTrue(body["payment_required"]); self.assertIn("/x402/v1/website.extract?url=https%3A%2F%2Fa.com", body["pay_and_call"])

    def test_off_until_the_owner_configures_a_wallet(self):
        import os
        with mock.patch.dict(os.environ, {"X402_PAY_TO": ""}):
            self.assertEqual(self.c.get("/x402/v1/website.extract", params={"url": "https://a.com"}).status_code, 503)


class WebsiteExtractionSafety(unittest.TestCase):
    def test_refuses_private_and_local_addresses(self):
        import asyncio
        from commerce.executors import ExecutionError, website_extract
        for url in ("http://127.0.0.1:8765/api/export", "http://localhost/", "http://169.254.169.254/latest/meta-data",
                    "http://10.0.0.1/", "file:///C:/Windows/win.ini", "ftp://example.com"):
            with self.assertRaises(ExecutionError, msg=url):
                asyncio.run(website_extract({"url": url}))

    def test_dns_rebinding_is_refused_at_connect_time(self):
        """The first lookup says public, the connect-time lookup says loopback: no connection is made."""
        import asyncio
        from commerce import executors as ex
        answers = iter([[(2, 1, 6, "", ("93.184.215.14", 0))]])

        def lookup(host, *a, **k):
            return next(answers, [(2, 1, 6, "", ("127.0.0.1", 0))])
        with mock.patch.object(ex.socket, "getaddrinfo", lookup):
            with self.assertRaises(ex.ExecutionError) as e:
                asyncio.run(ex.website_extract({"url": "http://rebind.test/"}))
        self.assertEqual(e.exception.code, "NOT_PUBLIC")


class RadarAndPipeline(unittest.TestCase):
    """Market radar is analysis only; the pipeline keeps estimates apart from money and never deploys."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.L = Ledger("test", Path(self.tmp.name) / "c.db")
        self.owner = {"v": True}

        async def audit(k, d): pass
        app = FastAPI()
        app.include_router(capi.build_router({"licensing": mock.Mock(owner=True)}, lambda r: self.owner["v"], audit))
        self.p = [mock.patch.object(capi, "gateway", lambda: FakeGateway()), mock.patch.dict(capi._ledgers, {"test": self.L}, clear=True),
                  mock.patch.object(capi.P, "BACKUPS", Path(self.tmp.name) / "bk")]
        for x in self.p:
            x.start()
        self.c = TestClient(app)

    def tearDown(self):
        for x in self.p:
            x.stop()
        self.L.db.close(); self.tmp.cleanup()

    def test_pipeline_lifecycle_is_owner_only_and_estimates_only(self):
        self.L.set_setting("seed", 1)                                     # existing data → migration backs it up first
        self.owner["v"] = False
        self.assertEqual(self.c.get("/api/commerce/admin/pipeline").status_code, 403)
        self.owner["v"] = True
        o = self.c.post("/api/commerce/admin/pipeline", json={"title": "Website extraction", "category": "website_extraction",
                        "price_usd": 0.01, "cost_usd": 0.0002, "monthly_requests": 1000, "fixed_monthly_usd": 5}).json()["opportunity"]
        self.assertTrue(list((Path(self.tmp.name) / "bk").glob("*-pre-pipeline.db")))
        e = o["economics"]
        self.assertEqual((e["kind"], e["revenue_usd"], e["profit_usd"], e["unit_margin_pct"]), ("estimate", 10.0, 4.8, 98.0))
        self.assertEqual(self.c.post(f"/api/commerce/admin/pipeline/{o['id']}/convert", json={"service_id": "x"}).status_code, 422)
        self.c.post(f"/api/commerce/admin/pipeline/{o['id']}/decide", json={"decision": "approved", "note": "demand proven"})
        c = self.c.post(f"/api/commerce/admin/pipeline/{o['id']}/convert", json={"service_id": "web.extract.v2"}).json()
        self.assertEqual(c["opportunity"]["status"], "converted")
        got = self.c.get("/api/commerce/admin/pipeline").json()
        self.assertTrue(got["drafts"]["web.extract.v2"]["status"].startswith("draft"))
        self.assertNotIn("web.extract.v2", {x["id"] for x in capi.catalog.public(self.L)})        # never auto-deployed
        self.assertEqual(self.L.overview("INR")["gross_paise"], 0)                               # no money from estimates
        self.assertTrue(self.L.verify_chain()["ok"])

    def test_radar_is_owner_only_and_refresh_is_rate_limited(self):
        self.owner["v"] = False
        self.assertEqual(self.c.get("/api/commerce/admin/radar").status_code, 403)
        self.owner["v"] = True
        with mock.patch.object(capi.RADAR, "latest", lambda: {"generated_at": 1}), \
             mock.patch.object(capi.RADAR.Radar, "collect", mock.AsyncMock(return_value={})), \
             mock.patch.object(capi.RADAR, "save_snapshot", lambda raw, r: None):
            self.assertEqual(self.c.get("/api/commerce/admin/radar").json()["radar"], {"generated_at": 1})
            self.assertEqual(self.c.post("/api/commerce/admin/radar/refresh").status_code, 200)
            self.assertEqual(self.c.post("/api/commerce/admin/radar/refresh").status_code, 429)

    def test_analysis_dedups_endpoints_and_labels_directory_purchases(self):
        from commerce import radar
        ep = lambda res, calls, payers, price=0.01: {"key": radar.endpoint_key(res), "resource": res, "service": "S", "description": "",  # noqa: E731
                                                     "tags": [], "network": "base", "pay_to": "0xseller", "price_usd": price,
                                                     "calls_30d": calls, "payers_30d": payers, "last_called": None,
                                                     "category": radar.categorize("scrape website")}
        raw = {"bazaar": [ep("https://a.io/x", 100, 10), ep("https://a.io/x/", 40, 4), ep("https://b.io/y", 0, 0)],
               "payapi": [{"base_url": "https://a.io", "payment_verified": True}], "nohumans": {}, "agenteconomy": {}}
        r = radar.analyse(raw)
        self.assertEqual((r["endpoints"]["listed_mainnet"], r["endpoints"]["active_30d"], r["endpoints"]["calls_30d_sum"]), (2, 1, 100))
        top = r["top_payers"][0]
        self.assertEqual((top["payers_30d"], top["repeat_ratio"], top["est_rev_30d_usd"]), (10, 10.0, 1.0))
        self.assertIn("not demand", top["evidence"]["payapi"]["note"])

if __name__ == "__main__":
    unittest.main()
