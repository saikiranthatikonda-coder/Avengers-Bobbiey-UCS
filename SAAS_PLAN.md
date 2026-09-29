# Bobbiey UCS — Commercialization Plan

Grounded in the code as of **2026-09-29**. Supersedes the June draft (3 tiers,
$29 "Commander"). The published pricing is now the 4 editions in `billing.py`,
and the website must always match them (`tests/test_site.py` enforces this).

## 1. Positioning

**A JARVIS-class AI command center that runs on the customer's own machine.**
Competitors are cloud dashboards. The moat is local-first privacy (telemetry,
camera and voice never leave the device), a cinematic operator experience, and
a real multi-agent layer that reasons over live, measured data. We sell
**operational awareness + AI delegation**, not "a dashboard".

Beachhead: technical solo operators and small ops teams (founders, SREs, IT
admins, security-minded power users) who already run their own machines and
value privacy. Enterprise (fleet, audit, on-prem AI) is the expansion path.

## 2. What is actually sellable today

| Capability | Code state | Sellable? | Notes |
|---|---|---|---|
| HUD dashboard, live telemetry, threat intel, 8 agents | IMPLEMENTED | ✅ Community core | Strongest demo moment |
| Claude cloud brain | IMPLEMENTED via the **operator's own Claude CLI login** | ⚠️ | See §5: a paid tier can't rely on the customer's personal CLI subscription |
| Local AI (Ollama) | IMPLEMENTED | ✅ | Needs RAM: a 3B model adds ~2.6 GB |
| Voice (wake word → STT → agent → TTS) | IMPLEMENTED, live-mic test pending | ⚠️ | Extra ~300 MB of deps plus a model download. Must become one-click |
| Vision presence / zones | IMPLEMENTED (browser) | ✅ | Identity-free is a selling point |
| Google Calendar + Gmail | IMPLEMENTED, BYO OAuth client | ❌ for customers | Each user would need their own Google Cloud project. Needs a verified shared OAuth app |
| News | IMPLEMENTED, BYO NewsAPI key | ⚠️ | NewsAPI's free tier isn't licensed for commercial production use. Needs a paid feed or an RSS alternative |
| Orchestrator / team memory / knowledge hub | IMPLEMENTED (tick crash fixed 2026-09-29) | ✅ | |
| Decisions (propose → simulate → execute) | IMPLEMENTED, supervised | ✅ Team+ | |
| Fleet, remote auth, audit, RBAC, export | IMPLEMENTED | ✅ Team/Enterprise | |
| SSO / SAML | **PLANNED** (listed under Enterprise) | ❌ | Listed on the pricing page but not built. Sell as "on roadmap" only |
| Editions, credits, entitlements | **MOCK (display only)** | ❌ | §3 |
| Web3 | OPTIONAL, real chain reads, demo token | Not a revenue line yet | §4 |

## 3. Billing: current architecture and the gap to real money

**Today (`billing.py`, demo):**
- `EDITIONS`: community (free, 100 credits), pro ($19/mo, 2,000), team ($49/user/mo, 10,000), enterprise (custom).
- `ENTITLEMENTS`: feature → minimum edition (e.g. `cloud_brain: pro`, `fleet: team`, `sso: enterprise`).
- State persists in `billing.json`. API: `GET /api/billing`, `POST /api/billing/edition` (commander only, audited, demo switch).
- **Nothing is enforced.** `use_credits()` is never called, and no endpoint checks
  an entitlement. Every feature works on Community. The dashboard shows the
  edition, but switching it changes nothing else.

**Minimum architecture for real payments** (don't build before the paid beta):
1. `BILLING_MODE=demo|live` in `.env`. Demo stays the default, and the two
   code paths never mix (demo can't mint a paid license).
2. **License, not accounts:** a signed license key (Ed25519, verified offline
   with a public key in the app, 7-day grace when offline). The edition comes
   from the license. `set_edition` stays demo-only.
3. **Provider adapter:** LemonSqueezy or Paddle (merchant of record, handles
   GST/VAT, easiest from India). A webhook → our tiny license service → key
   emailed. The local app never sees card data.
4. **One enforcement seam:** `billing.allows(feature)` checked in the few
   endpoints that map to paid entitlements, returning an honest "Pro feature" state.
5. **Credits = real cost:** meter in `brain.think` only for cloud-AI calls, since
   that's where the per-call cost is. Local AI is never metered.

## 4. Web3: real vs demo

| | State |
|---|---|
| Chain status (block, gas, RPC latency, health) | **REAL**: public JSON-RPC, no key (`web3mod/blockchain.py`) |
| Wallet connect (EIP-6963) and on-chain balances | **REAL** when a wallet connects |
| Governance votes, marketplace installs, treasury | **REAL (local)**, persisted per install, not on-chain |
| Token supply / symbol / price | **DEMO**. `token.contract` is empty. Live ERC-20 reads switch on only with a deployed contract. Market data needs a listing |
| Staking | **PLANNED** (needs contracts) |
| Module | **OFF by default** and never a requirement |

Recommendation: keep Web3 as an optional community layer. Don't launch a
token before there's paying revenue from subscriptions. It adds regulatory
and reputational risk with no product upside yet.

## 5. Critical commercial risks (found in code review)

1. **Claude access model.** The "Claude cloud brain" shells out to the operator's
   own Claude Code CLI login. That works for the founder's machine. For paying
   customers, either (a) they bring their own Claude access (then don't charge
   for it), or (b) paid tiers call the **Claude API with our key**, metered by
   AI credits. (b) is the real product. Check Anthropic's commercial terms
   before selling any Claude-backed tier.
2. **Integrations are BYO-credentials.** Google and news need per-user keys. A
   verified Google OAuth app and a commercially licensed news source are
   launch blockers for Pro.
3. **Install friction.** It needs Python 3.10+, a manual venv, optional voice
   deps and Ollama. A one-click installer (PyInstaller/Inno Setup bundle) is the
   #1 conversion lever.
4. **Resource footprint.** UCS + Ollama 3B + Whisper pushed a 16 GB laptop to
   94–98% RAM. Ship "Lite" defaults (no local LLM or voice until enabled) and
   document requirements.
5. **Reliability.** Two production bugs (billing 500, orchestrator crash with no
   calendar) were found only once tests existed. Keep growing `tests/`.

## 6. Pricing (published, matches code)

| Edition | Price | Credits | Positioning |
|---|---|---|---|
| Community | Free forever, 1 operator | 100 | Full local command center + local AI |
| Pro | $19/mo | 2,000 | Cloud AI brain, vision AI, briefings, Google intelligence |
| Team | $49/user/mo | 10,000 | Multi-operator, fleet (≤10 nodes), shared memory, remote access |
| Enterprise | Custom annual | — | On-prem AI clusters, unlimited nodes, compliance, SLA, services |

## 7. Path to revenue

**Now → 30 days (private alpha, free)**
- [x] Public site + waitlist (Netlify Forms) + pricing aligned with code
- [x] Test suite + CI-ready (`python -m unittest discover -s tests`)
- [ ] Connect Netlify to GitHub for continuous deploys (operator, 2 min)
- [ ] One-click Windows installer, Lite defaults
- [ ] Onboard 5–10 waitlist users. In-app feedback → `/api/feedback`

**30–90 days (paid beta)**
- [ ] Claude API-backed cloud brain + credit metering (§5.1)
- [ ] Verified Google OAuth app. Licensed news source
- [ ] License keys + LemonSqueezy/Paddle + `BILLING_MODE=live` (§3)
- [ ] Entitlement enforcement seam. Auto-update channel
- [ ] Privacy policy + ToS on the site (say loudly: data stays local)

**90–180 days (teams)**
- [ ] SSO/SAML, multi-operator polish, fleet onboarding
- [ ] Design partners → Enterprise pilots (on-prem AI, compliance export)

## 8. Metrics that matter

Waitlist → install rate · day-7 active operators · voice commands / day ·
agent questions / day · insight engagement · beta → paid conversion · churn ·
AI-credit cost per paying user (must stay well under price).
