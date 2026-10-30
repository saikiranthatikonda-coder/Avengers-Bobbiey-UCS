"""HTTP layer for PROJECT TRILLION.

Public (customers + external agents), only on the OWNER's BUCS install:
  GET  /commerce/v1/catalog                      services, prices, schemas, gateway status
  POST /commerce/v1/orders                       {service_id, input, idempotency_key?} [+ X-API-Key]
  GET  /commerce/v1/orders/{id}                  status + result once delivered   [X-Order-Token]
  GET  /commerce/v1/orders/{id}/pay?token=…      hosted checkout page (humans)
  POST /commerce/v1/orders/{id}/verify           {payment_id, signature}          [X-Order-Token]
  POST /commerce/v1/webhooks/razorpay            gateway webhook (signature-verified)
  GET  /commerce/v1/history                      the caller's orders              [X-API-Key]

Owner-only admin (host machine + commander + owner install): /api/commerce/admin/*

A service is executed ONLY after the backend fetched the payment from the
gateway and confirmed: right order, right amount, INR, captured.
"""

import asyncio
import hmac
import json
import time

from fastapi import APIRouter, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import catalog
from .executors import EXECUTORS, ExecutionError
from .gateways import GatewayError, gateway
from .ledger import Ledger
from . import x402 as X
from . import pipeline as P
from . import radar as RADAR

_ledgers: dict[str, Ledger] = {}
_rate: dict[str, list[float]] = {}


def ledger_for(mode: str) -> Ledger:
    if mode not in _ledgers:
        _ledgers[mode] = Ledger(mode)
    return _ledgers[mode]


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


class OrderReq(BaseModel):
    service_id: str
    input: dict
    idempotency_key: str | None = None


class VerifyReq(BaseModel):
    payment_id: str
    signature: str


class PriceReq(BaseModel):
    service_id: str
    price_paise: int


class ServiceReq(BaseModel):
    service_id: str
    enabled: bool


class LimitsReq(BaseModel):
    per_txn_max_paise: int | None = None
    daily_cap_paise: int | None = None
    require_api_key: bool = False


class CustomerReq(BaseModel):
    name: str
    kind: str = "agent"
    per_txn_paise: int | None = None
    daily_paise: int | None = None


class EnableReq(BaseModel):
    enabled: bool


class OpportunityReq(BaseModel):
    title: str
    category: str | None = None
    evidence: dict = {}
    competitors: list = []
    price_usd: float | None = None
    cost_usd: float | None = None
    monthly_requests: int | None = None
    fixed_monthly_usd: float | None = None
    notes: str = ""


class DecisionReq(BaseModel):
    decision: str
    note: str = ""


class ConvertReq(BaseModel):
    service_id: str


class ListingReq(BaseModel):
    directory: str
    service_id: str
    listing_url: str = ""
    status: str = "prepared"
    detail: dict = {}


class WalletLabelReq(BaseModel):
    address: str
    label: str


class RefundReq(BaseModel):
    payment_id: str
    amount_paise: int
    reason: str = ""
    confirm: str = ""


def build_router(state: dict, is_owner_session, audit) -> APIRouter:
    r = APIRouter()

    def owner_install() -> bool:
        lic = state.get("licensing")
        return bool(lic and lic.owner)

    def books():
        gw = gateway()
        return gw, ledger_for(gw.mode if gw.configured else "test")

    # ── fulfilment: verified payment → execute → deliver ─────────
    async def fulfil(L: Ledger, order: dict) -> dict:
        o = L.order(order["id"])
        if o["status"] in ("delivered", "executing"):
            return o
        if o["status"] != "paid":
            return o
        L.set_order(o["id"], "order.executing", status="executing")
        try:
            fn = EXECUTORS.get(o["service_id"])
            if not fn:
                raise ExecutionError("service not available")
            result = await asyncio.wait_for(fn(json.loads(o["input_json"])), 60)
            L.set_order(o["id"], "order.delivered", status="delivered", result_json=json.dumps(result), error=None)
        except Exception as e:
            # paid but not delivered: flagged for the owner to refund (refunds are a human decision)
            msg = f"{e.code}: {e}" if isinstance(e, ExecutionError) else str(e)
            L.set_order(o["id"], "order.failed_after_payment", status="failed", error=msg[:300])
        return L.order(o["id"])

    async def confirm_payment(gw, L: Ledger, order: dict, payment_id: str) -> dict:
        """The gateway's own record decides — never the client's claim."""
        p = await gw.fetch_payment(payment_id)
        if p.get("order_id") != order["gateway_order_id"]:
            raise GatewayError("payment belongs to a different order")
        if int(p.get("amount", -1)) != int(order["amount_paise"]) or p.get("currency") != "INR":
            raise GatewayError("payment amount/currency does not match the order")
        if p.get("status") == "authorized":
            p = await gw.capture(payment_id, order["amount_paise"])
        L.record_payment(p, order["id"])
        if p.get("status") != "captured":
            raise GatewayError(f"payment is {p.get('status')}, not captured")
        if order["status"] in ("created", "awaiting_payment"):
            L.set_order(order["id"], "order.paid", status="paid")
        return p

    def public_order(o: dict) -> dict:
        out = {k: o[k] for k in ("id", "service_id", "status", "amount_paise", "currency", "created_at", "updated_at")}
        if o["status"] == "delivered" and o.get("result_json"):
            out["result"] = json.loads(o["result_json"])
        if o["status"] == "failed":
            out["error"] = o.get("error")
        return out

    # ═══ public ══════════════════════════════════════════════════
    @r.get("/commerce/v1/catalog")
    async def catalog_ep():
        if not owner_install():
            return _err(404, "not found")
        gw, L = books()
        return {"provider": "BUCS · Project Trillion", "currency": "INR", "services": catalog.public(L),
                "payments": {"gateway": gw.name, "ready": gw.configured, "mode": gw.mode if gw.configured else None},
                "how_to_buy": ["POST /commerce/v1/orders {service_id, input}", "pay using the returned checkout",
                               "POST /commerce/v1/orders/{id}/verify with the payment id + signature (or wait for the webhook)",
                               "GET /commerce/v1/orders/{id} with X-Order-Token to collect the result"]}

    @r.post("/commerce/v1/orders")
    async def create_order(req: OrderReq, request: Request, x_api_key: str | None = Header(default=None)):
        if not owner_install():
            return _err(404, "not found")
        gw, L = books()
        if not gw.configured:
            return _err(503, "payments are not configured yet")
        svc = catalog.get(req.service_id)
        pub = next((s for s in catalog.public(L) if s["id"] == req.service_id), None)
        if not svc or not pub or not pub["available"]:
            return _err(404, "that service is not available")
        bad = catalog.validate(svc, req.input)
        if bad:
            return _err(422, bad)
        ip = request.client.host if request.client else "?"
        win = [t for t in _rate.get(ip, []) if time.time() - t < 60]
        if len(win) >= int(svc.get("limits", {}).get("per_minute", 20)):
            return _err(429, "rate limit: try again in a minute")
        _rate[ip] = win + [time.time()]
        limits = L.get_setting("limits", {})
        cust = L.customer_by_key(x_api_key) if x_api_key else None
        if x_api_key and not cust:
            return _err(401, "invalid or disabled API key")
        if limits.get("require_api_key") and not cust:
            return _err(401, "an API key is required (allowlist mode)")
        amount = int(pub["price_paise"])
        caps = [limits.get("per_txn_max_paise"), cust and cust["per_txn_limit_paise"]]
        if any(c and amount > c for c in caps):
            return _err(403, "amount exceeds the per-transaction limit")
        daily = [limits.get("daily_cap_paise"), cust and cust["daily_limit_paise"]]
        if cust and any(c and L.spent_today(cust["id"]) + amount > c for c in daily):
            return _err(403, "daily spending limit reached")
        try:
            order, token, created = L.create_order(svc["id"], cust and cust["id"], req.input, amount, req.idempotency_key)
        except Exception:                      # a concurrent request with the same idempotency key won the race
            row = L.db.execute("SELECT * FROM orders WHERE idempotency_key=?", (req.idempotency_key,)).fetchone() \
                if req.idempotency_key else None
            if not row:
                return _err(409, "duplicate request")
            order, token, created = dict(row), "", False
        if not created:
            return {"ok": True, "duplicate": True, "order": public_order(order),
                    "note": "this idempotency key already created an order; use its original access token"}
        try:
            g = await gw.create_order(amount, order["id"], {"bucs_order": order["id"], "service": svc["id"]})
        except GatewayError as e:
            L.set_order(order["id"], "order.gateway_error", status="failed", error=str(e)[:300])
            return _err(502, str(e))
        L.set_order(order["id"], "order.awaiting_payment", status="awaiting_payment", gateway=gw.name, gateway_order_id=g["id"])
        base = str(request.base_url).rstrip("/")
        return {"ok": True, "order_id": order["id"], "access_token": token, "amount_paise": amount, "currency": "INR",
                "checkout": gw.checkout(order, g), "pay_url": f"{base}/commerce/v1/orders/{order['id']}/pay?token={token}",
                "status_url": f"{base}/commerce/v1/orders/{order['id']}"}

    @r.get("/commerce/v1/orders/{oid}")
    async def get_order(oid: str, token: str | None = None, x_order_token: str | None = Header(default=None)):
        if not owner_install():
            return _err(404, "not found")
        _, L = books()
        if not L.check_access(oid, x_order_token or token or ""):
            return _err(404, "order not found")
        return {"ok": True, "order": public_order(L.order(oid))}

    @r.post("/commerce/v1/orders/{oid}/verify")
    async def verify(oid: str, req: VerifyReq, token: str | None = None, x_order_token: str | None = Header(default=None)):
        if not owner_install():
            return _err(404, "not found")
        gw, L = books()
        if not L.check_access(oid, x_order_token or token or ""):
            return _err(404, "order not found")
        o = L.order(oid)
        if o["status"] == "delivered":
            return {"ok": True, "order": public_order(o)}
        if not gw.verify_client_signature(o["gateway_order_id"] or "", req.payment_id, req.signature):
            return _err(400, "payment signature is invalid")
        try:
            await confirm_payment(gw, L, o, req.payment_id)
        except GatewayError as e:
            return _err(402, str(e))
        return {"ok": True, "order": public_order(await fulfil(L, L.order(oid)))}

    @r.get("/commerce/v1/orders/{oid}/pay", response_class=HTMLResponse)
    async def pay_page(oid: str, token: str = ""):
        if not owner_install():
            return HTMLResponse("not found", 404)
        gw, L = books()
        if not L.check_access(oid, token):
            return HTMLResponse("order not found", 404)
        o = L.order(oid)
        svc = catalog.get(o["service_id"]) or {}
        cfg = json.dumps({"key": gw.key_id, "order_id": o["gateway_order_id"], "amount": o["amount_paise"],
                          "currency": "INR", "name": "BUCS · Project Trillion", "description": svc.get("name", "")})
        return HTMLResponse(PAY_PAGE.replace("__CFG__", cfg).replace("__OID__", oid).replace("__TOKEN__", json.dumps(token))
                            .replace("__AMOUNT__", f"₹{o['amount_paise'] / 100:,.2f}").replace("__SVC__", svc.get("name", "")))

    @r.post("/commerce/v1/webhooks/razorpay")
    async def webhook(request: Request, x_razorpay_signature: str = Header(default=""),
                      x_razorpay_event_id: str = Header(default="")):
        if not owner_install():
            return _err(404, "not found")
        gw = gateway()
        raw = await request.body()
        if not gw.verify_webhook(raw, x_razorpay_signature):
            return _err(400, "invalid signature")
        L = ledger_for(gw.mode)
        ev = json.loads(raw)
        eid = x_razorpay_event_id or f"{ev.get('event')}:{ev.get('created_at')}:{hash(raw)}"
        if L.seen_webhook(eid):
            return {"ok": True, "duplicate": True}
        kind = ev.get("event", "")
        pay = ((ev.get("payload") or {}).get("payment") or {}).get("entity") or {}
        if kind in ("payment.captured", "order.paid", "payment.failed") and pay.get("order_id"):
            o = L.order_by_gateway(pay["order_id"])
            if o:
                try:
                    if kind == "payment.failed":
                        L.record_payment(await gw.fetch_payment(pay["id"]), o["id"])
                    else:
                        await confirm_payment(gw, L, o, pay["id"])        # re-fetched from the gateway, not trusted
                        await fulfil(L, L.order(o["id"]))
                except GatewayError:
                    pass
        if kind.startswith("refund."):
            ref = ((ev.get("payload") or {}).get("refund") or {}).get("entity") or {}
            if ref.get("payment_id"):
                L.record_refund(ref, ref["payment_id"], "webhook")
        return {"ok": True}

    @r.get("/commerce/v1/history")
    async def history(x_api_key: str | None = Header(default=None)):
        if not owner_install():
            return _err(404, "not found")
        _, L = books()
        c = L.customer_by_key(x_api_key or "")
        if not c:
            return _err(401, "API key required")
        rows = L.db.execute("SELECT * FROM orders WHERE customer_id=? ORDER BY created_at DESC LIMIT 100", (c["id"],)).fetchall()
        return {"ok": True, "orders": [public_order(dict(x)) for x in rows]}

    # ═══ x402: agents pay per request in USDC (settle only after successful delivery) ═══
    X402_PRICES_USD = {"website.extract": 0.01}

    def x402_price(L, sid: str) -> float:
        return float((L.get_setting("x402_prices", {}) or {}).get(sid, X402_PRICES_USD.get(sid, 0.01)))

    def resource_url(request: Request, sid: str, cfg) -> str:
        base = cfg.public_base or str(request.base_url).rstrip("/")
        return f"{base}/x402/v1/{sid}"

    @r.api_route("/x402/v1/{sid}", methods=["GET", "POST"])
    async def x402_call(sid: str, request: Request):
        if not owner_install():
            return _err(404, "not found")
        cfg = X.X402Config()
        if not cfg.enabled:
            return _err(503, "x402 payments are not enabled on this server yet")
        L = ledger_for(cfg.mode)
        pub = next((x for x in catalog.public(L) if x["id"] == sid), None)
        svc = catalog.get(sid)
        if not svc or not pub or not pub["available"] or sid not in EXECUTORS:
            return _err(404, "that service is not available")
        inp = dict(request.query_params)
        if request.method == "POST":
            try:
                inp = {**inp, **(await request.json())}
            except Exception:
                return _err(400, "body must be JSON")
        bad = catalog.validate(svc, inp)
        if bad:
            return _err(422, bad)
        price = x402_price(L, sid)
        req = cfg.requirements(resource_url(request, sid, cfg), price, svc["description"], svc.get("output_schema"))
        try:
            got = X.read_payment(request.headers)
        except ValueError as e:
            body, hdr = X.challenge(req, str(e)); return JSONResponse(body, 402, headers=hdr)
        if not got:
            body, hdr = X.challenge(req); return JSONResponse(body, 402, headers=hdr)
        version, payload = got
        v = await X.facilitator(cfg, "verify", version, payload, req)
        if not v.get("isValid"):
            body, hdr = X.challenge(req, "payment not valid: " + str(v.get("invalidReason") or "rejected"))
            return JSONResponse(body, 402, headers=hdr)
        payer = str(v.get("payer") or "").lower()
        try:                                                         # deliver first …
            result = await asyncio.wait_for(EXECUTORS[sid](inp), 60)
        except ExecutionError as e:                                  # … a failure is never settled: no charge
            return JSONResponse({"ok": False, "charged": False, "error": e.as_dict()}, 422)
        except Exception as e:
            return JSONResponse({"ok": False, "charged": False, "error": {"code": "INTERNAL", "message": str(e)[:200]}}, 500)
        st = await X.facilitator(cfg, "settle", version, payload, req)   # … then take the money
        if not st.get("success"):
            body, hdr = X.challenge(req, "settlement failed: " + str(st.get("errorReason") or "unknown"))
            return JSONResponse(body, 402, headers=hdr)
        tx = str(st.get("transaction") or "")
        order, _, _ = L.create_order(sid, f"wallet:{payer}" if payer else None, inp, req["atomic"], f"x402:{tx}", currency="USDC")
        L.set_order(order["id"], "order.awaiting_payment", status="awaiting_payment", gateway="x402", gateway_order_id=f"x402:{tx}")
        L.record_payment({"id": tx, "amount": req["atomic"], "currency": "USDC", "status": "captured", "method": f"x402 {cfg.network}",
                          "fee": 0, "payer": payer, "network": st.get("network") or cfg.network}, order["id"])
        L.set_order(order["id"], "order.paid", status="paid")
        L.set_order(order["id"], "order.delivered", status="delivered", result_json=json.dumps(result))
        resp = {"success": True, "transaction": tx, "network": st.get("network") or cfg.network, "payer": payer}
        return JSONResponse(result, 200, headers={"PAYMENT-RESPONSE": X.b64json(resp), "X-PAYMENT-RESPONSE": X.b64json(resp)})

    # ═══ discovery: MCP server + well-known manifest (agents find BUCS, then pay over x402) ═══
    def manifest(request: Request) -> dict:
        cfg = X.X402Config()
        L = ledger_for(cfg.mode if cfg.enabled else "test")
        items = []
        for svc in catalog.public(L, include_planned=False):
            if svc["id"] in EXECUTORS:
                items.append({"id": svc["id"], "name": svc["name"], "description": svc["description"],
                              "input_schema": svc.get("input_schema"), "output_schema": svc.get("output_schema"),
                              "x402": {"url": resource_url(request, svc["id"], cfg), "methods": ["GET", "POST"],
                                       "price_usd": x402_price(L, svc["id"]), "asset": "USDC", "network": cfg.network,
                                       "enabled": cfg.enabled},
                              "card": {"price": svc.get("price"), "currency": "INR", "order_url": "/commerce/v1/orders"}})
        return {"provider": "BUCS · Project Trillion", "x402Version": 2, "services": items,
                "mcp": (cfg.public_base or str(request.base_url).rstrip("/")) + "/commerce/mcp"}

    @r.get("/.well-known/x402")
    async def well_known(request: Request):
        if not owner_install():
            return _err(404, "not found")
        return manifest(request)

    @r.post("/commerce/mcp")
    async def mcp(request: Request):
        """Minimal MCP (streamable HTTP, JSON responses): discover services and how to pay for them."""
        if not owner_install():
            return _err(404, "not found")
        try:
            msg = await request.json()
        except Exception:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:                                             # notification
            return JSONResponse({}, 202)
        ok = lambda res: JSONResponse({"jsonrpc": "2.0", "id": mid, "result": res})      # noqa: E731
        if method == "initialize":
            return ok({"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "bucs-trillion", "version": "1.0.0"},
                       "instructions": "BUCS sells per-request data services to agents. Call list_services, then pay the "
                                       "service's x402 URL (USDC). Failed requests are never charged."})
        if method == "tools/list":
            return ok({"tools": [
                {"name": "list_services", "description": "Paid BUCS services with schemas, prices and their x402 payment URL.",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "website_extract", "description": "Structured facts and readable text from a public web page "
                 "(title, description, headings, links, social profiles, emails, Open Graph, text). Paid per request over x402; "
                 "this tool returns the exact paid URL and terms to call.",
                 "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string", "format": "uri"}}}}]})
        if method == "tools/call":
            p = msg.get("params") or {}
            m = manifest(request)
            if p.get("name") == "list_services":
                text = json.dumps(m["services"], indent=1)
            elif p.get("name") == "website_extract":
                svc = next((x for x in m["services"] if x["id"] == "website.extract"), None)
                url = ((p.get("arguments") or {}).get("url") or "")
                if not svc:
                    text = json.dumps({"available": False})
                else:
                    from urllib.parse import quote
                    text = json.dumps({"payment_required": True, "enabled": svc["x402"]["enabled"],
                                       "pay_and_call": f"{svc['x402']['url']}?url={quote(url, safe='')}",
                                       "price_usd": svc["x402"]["price_usd"], "asset": "USDC", "network": svc["x402"]["network"],
                                       "how": "GET the URL with an x402 client; it answers 402 with terms, your client pays, "
                                              "the result returns in the same response. Not charged if extraction fails."})
            else:
                return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}})
            return ok({"content": [{"type": "text", "text": text}]})
        return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})

    # ═══ owner-only admin ════════════════════════════════════════
    def admin_guard(request: Request):
        if not owner_install():
            return _err(404, "not found")
        if not is_owner_session(request):
            return _err(403, "owner only: host machine + commander session")
        return None

    @r.get("/api/commerce/admin/overview")
    async def overview(request: Request, mode: str | None = None):
        bad = admin_guard(request)
        if bad:
            return bad
        gw = gateway()
        m = mode if mode in ("test", "live") else (gw.mode if gw.configured else "test")
        L = ledger_for(m)
        xc = X.X402Config()
        UL = ledger_for(xc.mode if xc.enabled else m)
        usdc = UL.overview("USDC")
        labels = UL.get_setting("wallet_labels", {})
        usdc["by_class"] = UL.buyer_stats("USDC", lambda w: xc.classify(w, labels))
        return {"ok": True, "gateway": gw.status(), "x402": {**xc.status(), "prices_usd": {sid: x402_price(UL, sid) for sid in EXECUTORS}},
                "catalog": catalog.public(L), "limits": L.get_setting("limits", {}),
                "disabled": L.get_setting("disabled_services", []), **L.overview("INR"),
                "usdc": {k: usdc[k] for k in ("mode", "gross_paise", "net_paise", "transactions", "identified_buyers", "repeat_buyers",
                                              "avg_paise", "recent", "by_customer", "by_class")}}

    @r.post("/api/commerce/admin/x402/price")
    async def x402_set_price(request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        try:
            b = await request.json()
            sid, usd_ = str(b["service_id"]), float(b["price_usd"])
        except Exception:
            return _err(400, "body: {service_id, price_usd}")
        if sid not in EXECUTORS or not (0.0005 <= usd_ <= 5):
            return _err(422, "price must be $0.0005–$5 for a sellable service")
        xc = X.X402Config()
        L = ledger_for(xc.mode if xc.enabled else "test")
        prices = L.get_setting("x402_prices", {}); prices[sid] = round(usd_, 6)
        L.set_setting("x402_prices", prices)
        await audit("commerce.x402", f"{sid} price ${usd_}")
        return {"ok": True, "x402_prices": prices}

    @r.post("/api/commerce/admin/x402/wallet-label")
    async def wallet_label(req: WalletLabelReq, request: Request):
        """Mark a paying wallet as the owner's own test buyer or a directory's verification purchase
        (or back to external). Read-time only: payments are never rewritten."""
        bad = admin_guard(request)
        if bad:
            return bad
        import re as _re
        if not _re.fullmatch(r"0x[0-9a-fA-F]{40}", req.address) or req.label not in ("external", "internal_test", "marketplace_test"):
            return _err(422, "address: 0x… (40 hex); label: external | internal_test | marketplace_test")
        xc = X.X402Config()
        L = ledger_for(xc.mode if xc.enabled else "test")
        labels = L.get_setting("wallet_labels", {})
        if req.label == "external":
            labels.pop(req.address.lower(), None)
        else:
            labels[req.address.lower()] = req.label
        L.set_setting("wallet_labels", labels)
        await audit("commerce.x402", f"wallet {req.address[:10]}… labelled {req.label}")
        return {"ok": True, "labels": labels}

    @r.post("/api/commerce/admin/price")
    async def set_price(req: PriceReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        if not catalog.get(req.service_id) or not (100 <= req.price_paise <= 10_000_000):
            return _err(422, "price must be between ₹1 and ₹1,00,000 for a known service")
        _, L = books()
        prices = L.get_setting("prices", {}); prices[req.service_id] = int(req.price_paise)
        L.set_setting("prices", prices)
        await audit("commerce.price", f"{req.service_id} → {req.price_paise} paise")
        return {"ok": True, "prices": prices}

    @r.post("/api/commerce/admin/service")
    async def toggle_service(req: ServiceReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        _, L = books()
        dis = set(L.get_setting("disabled_services", []))
        (dis.discard if req.enabled else dis.add)(req.service_id)
        L.set_setting("disabled_services", sorted(dis))
        await audit("commerce.service", f"{req.service_id} {'enabled' if req.enabled else 'paused'}")
        return {"ok": True, "disabled": sorted(dis)}

    @r.post("/api/commerce/admin/limits")
    async def set_limits(req: LimitsReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        _, L = books()
        L.set_setting("limits", req.model_dump())
        await audit("commerce.limits", json.dumps(req.model_dump()))
        return {"ok": True, "limits": req.model_dump()}

    @r.post("/api/commerce/admin/customers")
    async def add_customer(req: CustomerReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        if req.kind not in ("human", "agent") or not req.name.strip():
            return _err(422, "name and kind (human|agent) required")
        _, L = books()
        cid, key = L.add_customer(req.name.strip(), req.kind, req.per_txn_paise, req.daily_paise)
        await audit("commerce.customer", f"{cid} {req.kind} '{req.name[:40]}'")
        return {"ok": True, "id": cid, "api_key": key, "note": "shown once: store it now"}

    @r.post("/api/commerce/admin/customers/{cid}/enabled")
    async def customer_enabled(cid: str, req: EnableReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        _, L = books()
        L.set_customer_enabled(cid, req.enabled)
        await audit("commerce.customer", f"{cid} {'enabled' if req.enabled else 'disabled'}")
        return {"ok": True}

    @r.post("/api/commerce/admin/refund")
    async def refund(req: RefundReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        if req.confirm != "REFUND":
            return _err(400, 'type REFUND to confirm: refunds move real money')
        gw, L = books()
        p = L.db.execute("SELECT * FROM payments WHERE id=?", (req.payment_id,)).fetchone()
        if not p:
            return _err(404, "payment not found in this ledger")
        if not (0 < req.amount_paise <= p["amount_paise"] - p["refunded_paise"]):
            return _err(422, "refund amount exceeds what is refundable")
        try:
            ref = await gw.refund(req.payment_id, req.amount_paise, req.reason)
        except GatewayError as e:
            return _err(502, str(e))
        L.record_refund(ref, req.payment_id, req.reason)
        await audit("commerce.refund", f"{req.payment_id} {req.amount_paise} paise: {req.reason[:80]}")
        return {"ok": True, "refund": {"id": ref.get("id"), "status": ref.get("status"), "amount_paise": ref.get("amount")}}

    @r.post("/api/commerce/admin/settlements/sync")
    async def sync_settlements(request: Request, days: int = 7):
        bad = admin_guard(request)
        if bad:
            return bad
        gw, L = books()
        n = 0
        try:
            for d in range(max(1, min(days, 31))):
                for it in await gw.settlement_recon(time.time() - d * 86400):
                    if it.get("type") == "payment" and it.get("settlement_id") and it.get("entity_id"):
                        n += L.mark_settled(it["entity_id"], it["settlement_id"], float(it.get("settled_at") or time.time()))
        except GatewayError as e:
            return _err(502, str(e))
        await audit("commerce.settlements", f"synced {n} payments")
        return {"ok": True, "newly_settled": n}

    # ═══ market radar (read-only market intelligence; never buys anything) ═══
    radar_job = {"task": None, "started": 0.0, "error": None}

    async def run_radar():
        try:
            prev = RADAR.latest()
            raw = await RADAR.Radar().collect()
            RADAR.save_snapshot(raw, RADAR.analyse(raw, prev))
            radar_job["error"] = None
        except Exception as e:
            radar_job["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    @r.get("/api/commerce/admin/radar")
    async def radar_get(request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        t = radar_job["task"]
        return {"ok": True, "radar": RADAR.latest(), "refreshing": bool(t and not t.done()), "error": radar_job["error"]}

    @r.post("/api/commerce/admin/radar/refresh")
    async def radar_refresh(request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        t = radar_job["task"]
        if t and not t.done():
            return {"ok": True, "refreshing": True}
        if time.time() - radar_job["started"] < 1800:          # be polite to the free sources
            return _err(429, "the radar was refreshed less than 30 minutes ago")
        radar_job["started"] = time.time()
        radar_job["task"] = asyncio.create_task(run_radar())
        await audit("commerce.radar", "market radar refresh started")
        return {"ok": True, "refreshing": True}

    # ═══ revenue opportunity pipeline (estimates only; never touches money) ═══
    def pipe():
        _, L = books()
        P.migrate(L)
        return L

    @r.get("/api/commerce/admin/pipeline")
    async def pipeline_get(request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        L = pipe()
        return {"ok": True, "opportunities": P.all_(L), "listings": P.listings(L),
                "drafts": L.get_setting("draft_services", {}), "note": "economics are owner estimates, not revenue"}

    @r.post("/api/commerce/admin/pipeline")
    async def pipeline_save(req: OpportunityReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        if not req.title.strip():
            return _err(422, "title required")
        o = P.save(pipe(), req.model_dump())
        await audit("commerce.pipeline", f"saved {o['id']} '{req.title[:60]}'")
        return {"ok": True, "opportunity": o}

    @r.post("/api/commerce/admin/pipeline/{oid}/update")
    async def pipeline_update(oid: str, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        try:
            fields = await request.json()
        except Exception:
            return _err(400, "body must be JSON")
        o = P.update(pipe(), oid, fields if isinstance(fields, dict) else {})
        return {"ok": True, "opportunity": o} if o else _err(404, "no such opportunity")

    @r.post("/api/commerce/admin/pipeline/{oid}/decide")
    async def pipeline_decide(oid: str, req: DecisionReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        o = P.decide(pipe(), oid, req.decision, req.note)
        if not o:
            return _err(422, "decision must be approved|rejected on an unconverted opportunity")
        await audit("commerce.pipeline", f"{oid} {req.decision}")
        return {"ok": True, "opportunity": o}

    @r.post("/api/commerce/admin/pipeline/{oid}/convert")
    async def pipeline_convert(oid: str, req: ConvertReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        sid = req.service_id.strip().lower()
        if not sid or len(sid) > 40 or not all(c.isalnum() or c in "._-" for c in sid):
            return _err(422, "service_id: letters, digits, . _ - only")
        o = P.convert(pipe(), oid, sid)
        if not o:
            return _err(422, "only an approved opportunity can become a draft service")
        await audit("commerce.pipeline", f"{oid} → draft service {sid} (not deployed)")
        return {"ok": True, "opportunity": o}

    @r.get("/api/commerce/admin/listings/payapi-draft")
    async def payapi_draft(request: Request, service_id: str = "website.extract", contact_email: str = ""):
        """The exact PayAPI Market submit_listing payload + what still blocks it. Never submits:
        submitting is outward-facing and PayAPI then makes a real paid test call."""
        bad = admin_guard(request)
        if bad:
            return bad
        cfg = X.X402Config()
        L = ledger_for(cfg.mode if cfg.enabled else "test")
        svc = catalog.get(service_id)
        if not svc or service_id not in EXECUTORS:
            return _err(404, "no sellable service with that id")
        base = cfg.public_base
        host = (base.split("//", 1)[-1].split("/", 1)[0]).lower()
        blockers = []
        if not cfg.enabled:
            blockers.append("X402_PAY_TO is not set (a wallet you control; public address only)")
        if cfg.network != "eip155:8453":
            blockers.append("X402_NETWORK must be eip155:8453 (Base mainnet): PayAPI verifies with a real paid call on Base")
        if not base.startswith("https://"):
            blockers.append("X402_PUBLIC_BASE_URL must be your public https address")
        elif any(t in host for t in ("localhost", "127.0.0.1", "trycloudflare.com", "ngrok", "loca.lt")):
            blockers.append("PayAPI refuses localhost and tunnel hosts: use your own domain or a server")
        if "@" not in contact_email:
            blockers.append("contact_email is required (pass ?contact_email=…)")
        if cfg.network == "eip155:8453" and "x402.org" in cfg.facilitator:
            blockers.append("set X402_FACILITATOR_URL to a mainnet facilitator (x402.org's is testnet only)")
        payload = {"name": "BUCS " + svc["name"], "provider_name": "BUCS · Bobbiey", "category": "Data",
                   "description": svc["description"] + " Structured JSON with a quality score; failed extractions are never charged.",
                   "base_url": base or "<X402_PUBLIC_BASE_URL>", "paid_route_url": resource_url(request, service_id, cfg) + "?url=https://example.com",
                   "price": x402_price(L, service_id), "networks": ["base"], "payTo": cfg.pay_to or "<X402_PAY_TO>",
                   "contact_email": contact_email or "<your email>", "mcp_endpoint": (base + "/commerce/mcp") if base else None}
        return {"ok": True, "ready": not blockers, "blockers": blockers, "payload": payload,
                "how_to_submit": "Owner only, after approving: call PayAPI's MCP tool submit_listing with this payload (https://payapi.market/mcp). "
                                 "Then record it here with POST /api/commerce/admin/listings (directory=payapi, status=submitted) and label "
                                 "PayAPI's test wallet as marketplace_test so its verification purchase is never counted as a customer.",
                "note": "BUCS never submits listings itself."}

    @r.post("/api/commerce/admin/listings")
    async def listing_add(req: ListingReq, request: Request):
        bad = admin_guard(request)
        if bad:
            return bad
        if req.status not in ("prepared", "submitted", "live", "rejected", "removed"):
            return _err(422, "status: prepared|submitted|live|rejected|removed")
        lst = P.add_listing(pipe(), req.directory, req.service_id, req.listing_url, req.status, req.detail)
        await audit("commerce.listing", f"{req.directory} {req.service_id} {req.status}")
        return {"ok": True, "listing": lst}

    return r


PAY_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pay · BUCS</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#02060d;color:#c8e6ff;font-family:system-ui,sans-serif;padding:16px}
.c{max-width:420px;width:100%;border:1px solid #0d3a5a;border-radius:12px;padding:1.6rem;background:#061220}
h1{font-size:1.1rem;margin:0 0 .4rem}.a{font-size:2rem;font-weight:800;margin:.6rem 0}.m{color:#6b94b4;font-size:.9rem;line-height:1.5}
button{width:100%;padding:.8rem;border:0;border-radius:8px;background:#00d9ff;color:#02141c;font-weight:700;font-size:1rem;cursor:pointer;margin-top:1rem}
#s{margin-top:1rem;font-family:monospace;font-size:.85rem;white-space:pre-wrap}</style></head><body><div class="c">
<h1>__SVC__</h1><div class="m">Order __OID__</div><div class="a">__AMOUNT__</div>
<div class="m">Payment is processed by Razorpay. The service runs only after the payment is verified by our server.</div>
<button id="b">Pay securely</button><div id="s"></div></div>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script><script>
const cfg = __CFG__, token = __TOKEN__, s = document.getElementById("s");
async function verify(resp) {
  s.textContent = "Verifying payment with the gateway…";
  const r = await fetch("/commerce/v1/orders/__OID__/verify", {method:"POST", headers:{"content-type":"application/json","x-order-token":token},
    body: JSON.stringify({payment_id: resp.razorpay_payment_id, signature: resp.razorpay_signature})});
  const d = await r.json();
  s.textContent = d.ok ? ("Status: " + d.order.status + (d.order.result ? "\\n\\n" + JSON.stringify(d.order.result, null, 2) : "")) : ("Not completed: " + d.error);
}
document.getElementById("b").onclick = () => new Razorpay({...cfg, handler: verify,
  modal: {ondismiss: () => { s.textContent = "Payment window closed."; }}}).open();
</script></body></html>"""
