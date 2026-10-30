# PROJECT TRILLION — Agent Commerce & Microtransaction Revenue Engine

Private, owner-only extension of BUCS (`commerce/`). External AI agents and human
customers buy individual BUCS services; a service runs **only after the backend
verifies the payment with the gateway itself**. Nothing is simulated: every figure
in the owner dashboard comes from a ledger of gateway-confirmed payments.

## Architecture (as built, v7.7.0)

| Part | File | What it does |
|---|---|---|
| Catalogue | `commerce/catalog.py` | 7 services with id, description, input/output JSON schema, price, auth, limits. Only services with a working executor are `available`; the rest are listed as **planned** and cannot be bought. |
| Executors | `commerce/executors.py` | `website.extract` (live): fetches a public page, returns title/description/headings/links/social/emails/OG. SSRF-safe (public IPs only, each redirect re-checked, 2 MB / 10 s caps). |
| Ledger | `commerce/ledger.py` | SQLite, **separate `commerce_test.db` and `commerce_live.db`** (test money never mixes with real). Integer paise. Idempotency keys, unique gateway ids (no duplicate payments), refunds, settlements, customers/agents with hashed API keys, and an append-only **hash-chained event log** (`verify_chain()` detects any edit). |
| Gateway | `commerce/gateways.py` | Abstraction + **Razorpay**: orders, client signature check, **server-side payment fetch** (source of truth), capture, webhook HMAC verification, refunds, settlement reconciliation. Keys only from server-side `.env`. |
| API | `commerce/api.py` | Public: catalog, create order, hosted checkout page, verify, order status/result (per-order access token), webhook, history (API key). Owner admin: overview, pricing, pause/resume, limits, API keys, refunds (typed confirmation), settlement sync. |
| Dashboard | `static/app.js` | **PROJECT TRILLION · AGENT COMMERCE** card — visible only when the owner admin API answers (owner install + host machine + commander). |

### Added in v7.8.0
| Part | File | What it does |
|---|---|---|
| x402 seller | `commerce/x402.py`, `/x402/v1/{service}` | Agents pay per request in USDC (x402 v1 + v2). Verify → deliver → **settle only after successful delivery**. OFF until `X402_PAY_TO` is set; Base Sepolia by default. |
| Discovery | `/commerce/mcp`, `/.well-known/x402` | Minimal MCP server + manifest so agents find the services and their payment URL. |
| Payer classes | `ledger.buyer_stats()` | external (customer) vs internal_test / marketplace_test / self_transfer, classified at read time. |
| Market radar | `commerce/radar.py` | Read-only market intelligence (CDP Bazaar, agenteconomy, nohumans, x402-list, PayAPI, Base on-chain) with source, time, coverage and verification per figure; listing-farm detection. |
| Opportunity pipeline | `commerce/pipeline.py` | Save → economics (estimates) → approve/reject → draft service → listings. |
| Market report | `TRILLION_MARKET_REPORT.md` | Top opportunities, top 20 competitors, first 3 services, acquisition plan, path to the first genuine paying agent. |

### Money rules (enforced in code and tests)
- Delivery requires: valid client signature **and** the gateway's own record showing the right order, exact amount, INR, `captured`.
- Webhooks are signature-verified, de-duplicated by event id, and the payment is **re-fetched** from the gateway rather than trusted from the webhook body.
- A failure after payment is flagged `failed` for the owner; refunds are never automatic (human approval, typed `REFUND`).
- No endpoint can move money out, withdraw, or change the payout account (that lives in the Razorpay dashboard only).
- Limits: per-transaction max, daily cap per customer/agent, allowlist mode (registered API keys only), per-IP rate limit.
- Gross, fees (gateway fee + tax), refunds, net, pending settlement and settled amounts are shown separately, in INR, from the ledger only.

## Launch phases

| Phase | Status |
|---|---|
| 1. Owner dashboard + service catalogue | **Done** (v7.7.0) |
| 2. One gateway in test mode | **Built, waiting for keys**: add Razorpay *test* keys + webhook secret to `.env` |
| 3. One real paid service end-to-end | **Built** (`website.extract`), proven with a fake gateway in `tests/test_commerce.py`; verify with Razorpay test mode once keys are in |
| 4. Production merchant onboarding + real payments | Needs Razorpay KYC → live keys; then one real ₹1–10 purchase to confirm settlement into your bank |
| 5. Publish catalogue + agent-readable API docs | Needs a **public HTTPS address** (see decision 1) |
| 6. Agent payment protocols (x402, MPP, AP2) | Designed for (gateway abstraction); added only when legally and technically available for INR/India |

## Decisions only the owner can make

1. **Public hosting.** BUCS listens on `127.0.0.1`, so agents on the internet and Razorpay webhooks can't reach it. Options:
   - **Cloudflare Tunnel** from this PC (free, quick) — but sales stop when the PC is off.
   - **Small cloud server** (always on) running the `commerce/` module + the executors — recommended for real revenue.
2. **Gateway account.** Razorpay (recommended: test keys are instant after signup; live needs KYC — individuals/unregistered businesses can apply) or Cashfree. Configure payouts to your bank inside the gateway dashboard.
3. **Which services next.** Company/Startup/Market data need licensed data sources; Document Intelligence and Business Analytics need a **commercial AI API key** — a personal Claude subscription must not be resold to third parties.

## Owner checklist to reach Phase 3 in test mode
1. Sign up at razorpay.com → Settings → API Keys → generate **test** keys.
2. Settings → Webhooks → add `https://<your public address>/commerce/v1/webhooks/razorpay`, events `payment.captured`, `payment.failed`, `refund.processed`; copy the secret.
3. Put `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` in `.env`; restart BUCS.
4. The dashboard banner switches to **TEST MODE**; buy Website Extraction with a Razorpay test card/UPI; watch it appear in the ledger.
