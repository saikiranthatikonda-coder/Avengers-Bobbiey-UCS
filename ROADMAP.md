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
| Voice STT + wake words | OPTIONAL | ❌ `faster-whisper`, `sounddevice`, `numpy` not installed |
| Vision: identity-free presence (browser frame diff) | IMPLEMENTED | ✅ with camera |
| Vision: AI scene description | IMPLEMENTED | ⚠️ Claude CLI available. Not yet re-verified with a camera here |
| Operator memory (description-based, not biometric) | IMPLEMENTED | ✅ |
| Google Calendar + Gmail (read-only) | OPTIONAL | ❌ `credentials.json` / `token.json` absent |
| News feed (NewsAPI) | OPTIONAL | ❌ `NEWSAPI_KEY` empty, so feed is empty (not mocked) |
| Weather (Open-Meteo) | IMPLEMENTED | ✅ |

## Phase 2 — Multi-Agent Intelligence

| Feature | Status |
|---|---|
| Jarvis-led orchestrator, directives, delegation | IMPLEMENTED |
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
| Editions (Community → Enterprise), AI credits, entitlements | **MOCK**: demo ledger in `billing.json`, no payments |
| Real payment provider (LemonSqueezy/Stripe), license keys | PLANNED |
| Web3 module (off by default) | OPTIONAL |
| Web3: chain status, gas, RPC health, on-chain balances | IMPLEMENTED (real JSON-RPC, when enabled) |
| Web3: EIP-6963 wallet connect, governance voting, marketplace, treasury | IMPLEMENTED (persisted locally) |
| Web3: token supply/symbol figures | MOCK until `token.contract` is set |
| Web3: live token market data | PLANNED (needs a listing) |
| Web3: on-chain staking | PLANNED (needs contracts) |
| Public site + waitlist (Netlify, GitHub Pages copy) | IMPLEMENTED |

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
4. **Voice**: install optional STT deps into the existing venv, verify mic → STT → routing → TTS.
5. **Google**: restore `credentials.json`, operator runs OAuth. *(operator action)*
6. **News**: configure `NEWSAPI_KEY`, fix the stale "mock feed" docs. *(operator provides key)*
7. ~~**Automated tests**~~ ✅ foundation shipped 2026-09-28 (found and fixed a billing crash).
8. ~~**Docs/path cleanup**~~ ✅ done 2026-09-28. Open: `site/.netlify/netlify.toml` still points at the old machine (deploy config, needs operator decision).
9. **Visual UX audit**: refine the HUD without losing its identity.
10. **Next product evolution**: the unified command experience (speak → delegate → approve → act).
