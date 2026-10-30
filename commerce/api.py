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
            L.set_order(o["id"], "order.failed_after_payment", status="failed", error=str(e)[:300])
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
        return {"ok": True, "gateway": gw.status(), "catalog": catalog.public(L), "limits": L.get_setting("limits", {}),
                "disabled": L.get_setting("disabled_services", []), **L.overview()}

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
