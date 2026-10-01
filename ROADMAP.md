# Bobbiey UCS — Roadmap

What is actually built, what is partial, and what comes next.
**The codebase is the source of truth.** Update this file when status changes.

Last verified: **2026-09-28** on the Windows dev machine (`C:\Users\Saikiran`).

## How to read this file

The dashboard's **Product Evolution** panel (`roadmap.py`, `/api/roadmap`)
currently reports **v5.8 · 96% (47/49 features)**. Its probes check that a feature
is **built** (the code path exists and loads). They do not check that it is
**usable on this machine right now**. Example: "Cloud AI brain (Claude CLI)"
probes as done even though the Claude CLI isn't installed here. This file
tracks both.

| Label | Meaning |
|---|---|
| **IMPLEMENTED** | Working code path, usable with no extra setup |
| **PARTIAL** | Works with real limits, or only part of the intended scope exists |
| **MOCK** | Demo values, deliberately not real |
| **EXPERIMENTAL** | Works but is new or lightly exercised |
| **OPTIONAL** | Off by default or needs operator setup (keys, installs, OAuth) |
| **PLANNED** | Not built |

"Here" = current state on this machine.

---

## Phase 1 — AI Command Dashboard

| Feature | Status | Here |
|---|---|---|
| Live telemetry (CPU/mem/disk/net/proc, 2 s) | IMPLEMENTED | ✅ |
| Cinematic HUD dashboard | IMPLEMENTED | ✅ |
| 8-agent roster | IMPLEMENTED | ✅ Claude-backed replies citing live telemetry |
| AI brain: Claude CLI → local LLM → rule templates | IMPLEMENTED | ✅ Claude CLI live (`brain_mode: llm`), templates as fallback |
| Local AI models (Ollama / OpenAI-compatible) | OPTIONAL | ✅ `llama3.2:3b` powers insights + Claude failover (Claude-first routing) |
| Insights engine + executive briefing | IMPLEMENTED | ✅ (AI wording depends on brain) |
| Threat intelligence + emergency alerts | IMPLEMENTED | ✅ |
| TTS (System.Speech / `say` / `spd-say`) | IMPLEMENTED | ✅ (muted by default) |
| Voice STT + wake words | IMPLEMENTED | ⚠️ `base` + beam 5, adaptive trigger, auto-gain, 12 s conversation mode. 5/5 on synthesized commands (normal + quiet). Live spoken test pending |
| Double-clap → JARVIS pop-up + spoken reply | IMPLEMENTED | ⚠️ detector unit-tested. Live clap test pending (mic echo-cancels speaker playback) |
| Vision: identity-free presence (browser frame diff) | IMPLEMENTED | ✅ with camera |
| Vision: AI scene description | IMPLEMENTED | ⚠️ Claude CLI available. Not yet re-verified with a camera here |
| Operator memory (description-based, not biometric) | IMPLEMENTED | ✅ |
| Google Calendar + Gmail + Tasks (read-only, one sign-in) | OPTIONAL | ❌ awaiting operator's `credentials.json` + one G sign-in. Code: all calendars, join links, RSVP, tasks, re-sync without re-login (tested with fake API) |
| News feed | IMPLEMENTED | ✅ keyless open feeds (BBC, The Hindu, Al Jazeera, The Hacker News, HN) every 10 min. NewsAPI optional |
| Internet speed / latency / loss | IMPLEMENTED | ✅ measured (~500↓ / 40–240↑ Mbps, ~13 ms on T-Hub Wi-Fi) |
| Header tiles + orb readouts | IMPLEMENTED | ✅ all bound to live data (were hardcoded until 2026-09-30) |
| Weather (Open-Meteo) | IMPLEMENTED | ✅ |

## Phase 2 — Multi-Agent Intelligence

| Feature | Status |
|---|---|
| Jarvis-led orchestrator, directives, delegation | IMPLEMENTED (tick crashed on every cycle without a calendar, fixed 2026-09-29) |
| Shared blackboard, priority preemption, agent consults | IMPLEMENTED |
| Persistent cross-agent team memory | IMPLEMENTED |
| Command recommendations, knowledge hub search | IMPLEMENTED |
| Productivity intelligence (focus, tasks, score) | IMPLEMENTED |
| LLM-backed agent reasoning | IMPLEMENTED: Claude CLI, tool-less, fed live telemetry + team memory |
| LangGraph-based delegation | PLANNED (see `ARCHITECTURE.md` target stack) |

## Phase 3 — Computer Vision Operations

| Feature | Status |
|---|---|
| Multi-camera enumeration + hot-switch | IMPLEMENTED (browser) |
| 3×3 zone monitoring + away-intrusion alerts | IMPLEMENTED |
| Activity analytics timeline | IMPLEMENTED |
| OpenCV / MediaPipe server-side vision | PLANNED |

## Phase 4 — Enterprise Command Platform

| Feature | Status |
|---|---|
| Append-only audit trail, compliance export | IMPLEMENTED |
| Commander/observer RBAC (server-side, optional PIN) | IMPLEMENTED |
| Operator profiles | IMPLEMENTED |
| Multi-site fleet (node agents, pairing codes) | IMPLEMENTED |
| Authenticated remote access (PBKDF2, HMAC sessions, API tokens, lockout) | IMPLEMENTED |
| On-prem AI cluster routing to fleet Ollama nodes | IMPLEMENTED, needs nodes running Ollama |
| PostgreSQL / Redis / Next.js / multi-operator SSO | PLANNED |

## Phase 5 — Autonomous Decision Support

| Feature | Status |
|---|---|
| Propose → simulate → execute under human authority | IMPLEMENTED |
| Supervised-autonomy whitelist (low-risk, audited) | IMPLEMENTED |
| Fleet-wide operations (non-destructive) | IMPLEMENTED |

## Phase 6 — Ecosystem & Monetization (active)

| Feature | Status |
|---|---|
| Editions (Community → Enterprise), AI credits, entitlements | **MOCK**: demo ledger in `billing.json`, no payments, nothing enforced. Real-payments design in `SAAS_PLAN.md` §3 |
| Real payment provider (LemonSqueezy/Stripe), license keys | PLANNED |
| Web3 module (off by default) | OPTIONAL |
| Web3: chain status, gas, RPC health, on-chain balances | IMPLEMENTED (real JSON-RPC, when enabled) |
| Web3: EIP-6963 wallet connect, governance voting, marketplace, treasury | IMPLEMENTED (persisted locally) |
| Web3: token supply/symbol figures | MOCK until `token.contract` is set |
| Web3: live token market data | PLANNED (needs a listing) |
| Web3: on-chain staking | PLANNED (needs contracts) |
| Public site + waitlist | IMPLEMENTED. `docs/` canonical, root `netlify.toml`. Live Netlify still shows the older release until redeployed or linked |

## Engineering foundation

| Item | Status |
|---|---|
| Claude Code operating manual (`CLAUDE.md`) | IMPLEMENTED |
| Automated tests | IMPLEMENTED (foundation): 32 tests. `test_brain.py` (provider chain) + `test_smoke.py` (isolated real server: startup, WS, agents, orchestrator, decisions, billing, Web3-off, Google/news absent, remote-auth gate, AuthManager). Gaps: voice, vision, fleet ingestion, frontend JS execution |
| Live dev loop (browser pane + `.claude/launch.json`) | IMPLEMENTED |
| One-click installer, auto-update | PLANNED (`SAAS_PLAN.md`) |

---

## Next up (this development cycle)

In order. Each needs operator input only where noted.

1. ~~**Claude CLI foundation**~~ ✅ done 2026-09-28.
2. ~~**Ollama / local AI**~~ ✅ done 2026-09-29 (`llama3.2:3b`).
3. ~~**AI provider verification**~~ ✅ all three tiers verified in the live app + tests.
4. **Voice** ✅ installed + enabled, pipeline verified. *(operator: one live spoken test)*
5. **Google** ✅ code upgraded (all calendars, meeting details, Tasks, sign-in-once). *(operator: credentials.json + one sign-in)*
6. ~~**News**~~ ✅ keyless open feeds, 2026-09-30.
7. ~~**Automated tests**~~ ✅ foundation shipped 2026-09-28 (found and fixed a billing crash).
8. ~~**Docs/path cleanup**~~ ✅ done 2026-09-28. Open: `site/.netlify/netlify.toml` still points at the old machine (deploy config, needs operator decision).
9. **Visual UX audit** 🔨 done: fake tiles → live, ring overlap, phone width (587→370 px), reduced motion. Open: 60% of text < 9 px (legibility pass), 43 targets < 24 px.
10. **Next product evolution**: the unified command experience (speak → delegate → approve → act).
11. **Commercialization**: see `SAAS_PLAN.md` §7. Next build items: one-click installer + Lite defaults, Claude API-backed metered brain, license keys + entitlement seam.
