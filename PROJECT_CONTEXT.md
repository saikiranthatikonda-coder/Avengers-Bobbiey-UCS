# 🧠 Bobbiey UCS — Project Context & Engineering Notes

The durable record of **how this platform is built and why**, plus the
non-obvious traps that cost real debugging time. Read this first when picking
the project up on a new machine, after a break, or in a fresh AI session.

> Companion docs: `MIGRATION.md` (moving machines) · `ARCHITECTURE.md` (module
> map) · `FLEET.md` (multi-node) · `SAAS_PLAN.md` (commercial) · `README.md`.

---

## 1. What this is

A **local-first AI operational command platform** — a real-world JARVIS. It runs
entirely on the operator's own machine: a FastAPI server streaming live
telemetry over WebSockets into a cinematic HUD dashboard, driven by 8 named AI
agents, with voice, vision, calendar/mail intelligence, threat monitoring, a
multi-node fleet, and an optional Web3 layer.

**Design commitments** (these have shaped every decision):
- **Local-first & privacy-first** — telemetry, camera frames and voice are
  processed on-device. Camera presence is motion/description-based, **never
  facial recognition or biometrics**.
- **Real data only** — no mock/placeholder values in the dashboard. If something
  can't be measured, it says so honestly rather than inventing a number. This
  rule has been enforced repeatedly and is the single most important product
  value here.
- **Loopback-first** — binds `127.0.0.1` unless explicitly exposed.
- **Optional modules never become dependencies** — Web3 and monetization are
  off/free by default; the platform is fully usable without them.

---

## 2. Current state

| | |
|---|---|
| Roadmap | **v5.8 · 96% built** — Phases 1–5 complete, Phase 6 active (8/10) |
| Repo | `saikiranthatikonda-coder/Avengers-Bobbiey-UCS` |
| Site | `avengers-bobbiey.netlify.app` (mirrored in `/docs` for GitHub Pages) |
| Server | FastAPI + uvicorn, single asyncio process, port 8765 |
| Dashboard cache-bust | `?v=5.0` on `app.js` / `style.css` |

### Phase status
1. **AI Command Dashboard** ✅ — telemetry, 8 agents, voice, vision, Google
   Calendar + Gmail, local AI, threat intelligence, emergency alerts
2. **Multi-Agent Intelligence** ✅ — Jarvis-led orchestrator, directive
   delegation, shared blackboard, priority preemption, agent-to-agent consults,
   persistent cross-agent team memory
3. **Computer Vision Operations** ✅ — multi-camera hot-switch, 3×3 zone
   monitoring with away-intrusion alerts, activity analytics
4. **Enterprise Command Platform** ✅ — append-only audit trail,
   commander/observer RBAC enforced server-side, operator profiles, compliance
   export, multi-site fleet, authenticated remote access, on-prem AI clusters
5. **Autonomous Decision Support** ✅ — propose → simulate → execute under human
   authority, supervised-autonomy whitelist, fleet-wide operations
6. **Ecosystem & Monetization** 🔨 — editions/credits/entitlements + a working
   Web3 subsystem. Remaining: live token market data, on-chain staking.

---

## 3. Module map

```
main.py            FastAPI app, lifespan wiring, ALL endpoints, auth middleware
hub.py             WebSocket pub/sub bus — every module broadcasts through this
agents.py          the 8 Avengers (status/task/confidence/history/team memory)
brain.py           LLM chain: Claude CLI → local Ollama → rule templates
llm_local.py       OpenAI-compatible local client (Ollama/LM Studio)
local_brain.py     rule-based fallback so the platform never goes mute
insights.py        insight engine + executive briefing + recommendations
orchestrator.py    P2: directives, delegation, blackboard, preemption, consults
shared_memory.py   P2: persistent cross-agent TeamMemory
knowledge.py       P2: ranked search across every data source + AI answers
productivity.py    P2: focus timer, tasks, context switches, AI score
threats.py         threat engine: 6-domain matrix, SOC, incidents, trend, queue
agenda.py          calendar/inbox intelligence (real Google data only)
google_sync.py     Google OAuth + Calendar + Gmail (readonly)
memory.py          operator memory — description-based recognition, NOT biometric
vision            (in main.py) webcam frames → Claude vision
audit.py           P4: append-only audit trail
auth.py            P4: PBKDF2 password, HMAC sessions, API tokens, lockout
fleet.py           P4: node registry (online/stale/offline, aggregates)
node_probe.py      P4: shared telemetry collector (psutil + stdlib only)
node_agent.py      P4: standalone agent any laptop runs to join the fleet
decisions.py       P5: propose → simulate → execute, supervised autonomy
billing.py         P6: editions, AI credits, entitlements
web3mod/           P6: optional Web3 subsystem (7 modular services)
roadmap.py         live Product Evolution — probes the RUNNING system
routines.py        APScheduler: agent ticks, syncs, orchestration, decisions
services.py        SystemMonitor + NewsService
connectivity.py    wifi/bluetooth/ping/active-uplink detection
weather.py         Open-Meteo client
voice.py / tts.py  faster-whisper STT + wake words / cross-platform TTS
static/            index.html · app.js · style.css · login.html · landing.html
docs/              GitHub Pages mirror of the website
```

---

## 4. Key architectural decisions (and why)

**Roadmap probes test capability, not configuration.** `roadmap.py` verifies a
feature is *built and functional*, not that the operator finished optional setup
(connecting Google, starting Ollama, enrolling a face). Earlier it checked live
connection state, which made the version *regress* when Google disconnected.
Live connection status belongs in the Service Grid instead.

**Brain routing is explicit and persisted.** Selecting a local model sets
`force_local` in `model_pref.json`, which survives restarts and actually
redirects replies. Auto-preferring local only happens when it has *proven* fast
(<4s) — on a GPU-less machine local inference is slower than Claude.
**Vision always uses Claude** (local models here are text-only).

**Fleet nodes push, the host never polls.** Each node runs `node_agent.py` and
POSTs telemetry with a token. Nodes need only `pip install psutil` — everything
else is stdlib, so any laptop can join. The host registers *itself* as a node so
the fleet is never empty.

**The auth perimeter trusts loopback, denies remote by default.** The operator at
the console never logs in. Remote is *blocked entirely* until a password is set,
then needs a session cookie or API bearer token. Node pairing/reporting is
allowlisted so machine-to-machine traffic isn't broken by human auth.

**Web3 is a subsystem, not a theme.** 7 independent services behind a thin
façade, off by default, inert when disabled. Everything that *can* be real *is*
real (live JSON-RPC chain data, on-chain balances, persisted voting). Adopting a
real token later is a **config change**: set `token.contract` and the services
switch from configured figures to live ERC-20 reads with no code change.

**Decisions never act destructively.** The engine flags and reroutes; it never
deletes files or writes to remote nodes. Autonomy is opt-in and limited to a
low-risk whitelist, always audited.

---

## 5. ⚠️ Hard-won gotchas — read before debugging

These each cost significant time. They are not obvious.

### JavaScript
- **Temporal-dead-zone bug that blanks half the dashboard.** `let` stashes
  (`lastAgenda`, `lastWeatherOk`, …) must be declared **at the top of
  `app.js`**. They were declared low in the file while `refreshWeather()` — called
  early during load — referenced them, throwing a TDZ `ReferenceError` at top
  level. That **halted every remaining top-level statement**, so the fleet,
  decisions, security and session panels silently stopped populating (functions
  still *existed*, because declarations hoist — only their invocations were
  skipped). **If lower-half panels ever go blank, suspect a TDZ from an early
  call into a function that uses a late `let`.**
- **Always bump `?v=` on `app.js`/`style.css`** after UI changes. Browsers cached
  a stale `app.js` and silently dropped new panels. `no-cache` headers are also
  set on `/` and `/static/`.
- **`ghost-btn` is a 22px icon square with a rotate-on-hover** — never use it for
  text labels. Use `cmd-btn`. Reusing `ghost-btn` produced badly overlapping
  buttons across every new panel.
- **Beware async renders overwriting live data.** The world-atlas render finished
  *after* the fleet refresh and reset the node badge to a hardcoded value.

### Claude CLI (used as the cloud brain)
- **`--dangerously-skip-permissions` is required for vision.** Headless
  `claude -p` blocks its own `Read` tool behind a permission prompt, so image
  analysis silently failed ("I don't have permission to read that file"). Scoped
  to vision calls only; it reads one temp frame the app itself wrote.
- **`stdin=DEVNULL`** — otherwise the CLI waits ~3s for stdin on *every* call.

### Python / platform
- **Node agent must stay Python 3.7+ compatible.** `str | None` annotations
  (PEP 604) are evaluated at runtime on Python <3.10 and crash. Fixed with
  `from __future__ import annotations`. A Mac node on conda 3.9 hit this.
- **Avoid Python 3.14 for the venv.** Some deps don't support it. The old
  machine used 3.12. The current laptop runs **3.11.9**, which works fine.
- **Ollama via httpx needs `trust_env=False`** or the system proxy 403s
  localhost.
- **Order matters in `lifespan`** — `fleet` must be constructed *before*
  `decisions` (which takes `fleet=`), or you get `UnboundLocalError`.

### Windows / PowerShell
- *(Old machine only)* the `system32` git was a stub, so the real binary
  `C:\Users\sai\AppData\Local\Programs\Git\cmd\git.exe` had to be used. The
  current laptop's `git` on PATH (Git for Windows 2.55) is fine.
- **Claude CLI on Windows** is installed natively at `~\.local\bin\claude.exe`,
  often not on PATH. `brain.py` auto-discovers it, runs it from a neutral cwd
  (so the dev `CLAUDE.md` never reaches agent replies) and tool-less for
  text. Details in `CLAUDE.md` §10.
- **PowerShell 5.1:** no `&&`; `"$i:"` is a parse trap (use `-f` formatting);
  `New-Item` has no `-LiteralPath` (use `[System.IO.Directory]::CreateDirectory`);
  **commit messages with `|`, backticks, em-dashes or inner quotes break
  here-strings** — keep them plain ASCII.
- **Firewall rules need an elevated shell.**
- **`run.ps1` reads `JARVIS_HOST` from `.env`** (fixed) — it used to read only the
  OS env var, so editing `.env` silently didn't change the bind address.

### Google OAuth
- **`access_type=offline` + `prompt=consent` are mandatory**, or the saved token
  has no `refresh_token` and every later load fails with
  *"missing fields refresh_token"*.

### Networking
- **Corporate/office Wi-Fi often blocks device-to-device traffic** (client
  isolation), which stops fleet nodes from reaching the host even when the host
  is configured perfectly. A **phone hotspot** is the reliable workaround.

### Ops
- **The operator starts/stops the server manually** (`start-jarvis.cmd`) and
  prefers it **not left running**. Audio defaults to **muted** (`audio_pref.json`)
  because agent TTS speaking unprompted was unwanted; there's a 🔊 header control.
- **Screenshots of the dashboard time out** (heavy canvas animation) — verify UI
  by inspecting the DOM via JS instead. It's more rigorous anyway.

---

## 6. State files (gitignored — these ARE your data)

`.env` · `credentials.json` · `token.json` · `auth.json` · `fleet_token.txt` ·
`pair_code.txt` · `node_id.txt` · `model_pref.json` · `audio_pref.json` ·
`notes.json` · `operator_memory.json` · `productivity.json` · `team_memory.json` ·
`audit.log` · `operators.json` · `decisions.json` · `billing.json` ·
`web3_config.json` · `governance.json` · `treasury.json` · `marketplace.json` ·
`web3_analytics.json` · `waitlist.jsonl`

They are excluded from git deliberately (secrets + personal data). **Back them up
separately** — see `MIGRATION.md`.

---

## 7. Where to pick up next

- **Phase 6 remaining:** live token market data (needs a listing) and on-chain
  staking (needs contracts).
- **Web3 optional slots:** `explorer_api_key` unlocks real transaction history;
  `walletconnect_project_id` would add mobile QR pairing (needs their SDK —
  deliberately not bundled; EIP-6963 already covers desktop wallets).
- **Known manual steps:** Netlify may need a redeploy from `docs/` if it isn't
  repo-linked; GitHub Pages can serve `/docs`.
