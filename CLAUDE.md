# Avengers × Bobbiey Unified Command System
## Claude Code Development Instructions

This is the permanent operating manual for Claude Code on this repository.
Facts marked *verified* were checked against the code and this machine on
2026-09-28. If they drift, check the repository and update this file. Don't
trust the file over the repo.

---

### 1. Project Identity

**Bobbiey UCS** (Avengers × Bobbiey Unified Command System) is a **local-first
AI operational command platform**, a real-world "JARVIS" for one operator.
A single FastAPI process on the operator's machine streams live telemetry,
calendar/mail intelligence, threat monitoring, news, weather, vision presence
and fleet data over WebSockets into a cinematic HUD dashboard. Eight named AI
agents drive it (Jarvis, Captain, Stark, Black Widow, Hawkeye, Hulk, Thor, Vision).
It also has an optional commercial layer (editions/credits) and an optional
Web3 subsystem. The public website is a pitch and waitlist only. The
intelligence runs locally.

---

### 2. Source of Truth

- **This repository is the source of truth** for the project.
- GitHub origin: `https://github.com/saikiranthatikonda-coder/Avengers-Bobbiey-UCS.git`
- Production branch: **`main`**.
- The local repository must stay in sync with GitHub (verify with
  `git status` / `git fetch` before major work).
- `PROJECT_CONTEXT.md`: historical project context, design decisions and
  **hard-won gotchas**. Read the relevant sections before changing related code.
- `ARCHITECTURE.md`: architecture diagram, target stack and migration seams.
- `README.md`: setup and user-facing documentation.
- Also: `FLEET.md` (multi-node setup), `SAAS_PLAN.md` (commercial plan),
  `MIGRATION.md` (moving machines / restoring private data).

Where docs and code disagree, **the code wins**. Mention the gap to the user.

---

### 3. Current Local Development Environment (verified)

| | |
|---|---|
| OS | Windows 11 |
| Repository | `C:\Users\Saikiran\Avengers-Bobbiey-UCS` |
| Python (venv) | **3.11.9** in `.venv` (base: `C:\Users\Saikiran\AppData\Local\Programs\Python\Python311`) |
| System Python | same 3.11 install on PATH; `py` launcher also present |
| Git | Git for Windows 2.55 (the normal `git` on PATH works) |
| Branch | `main` |
| Known HEAD | `bbc0bac` (as of 2026-09-28) |
| Shells | PowerShell 5.1 and Git Bash both available |
| Claude CLI | `C:\Users\Saikiran\.local\bin\claude.exe` (native install, authenticated, **not on PATH**, auto-discovered by `brain.resolve_claude_bin`) |
| Ollama | 0.34.4 at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` (winget, not on PATH), API `:11434`, model **`llama3.2:3b`** (CPU only, ~2.6 GB RAM loaded) |

**Do not upgrade Python, recreate `.venv`, or install/upgrade dependencies
unless explicitly asked.** (`PROJECT_CONTEXT.md` says "build the venv with
3.12". That note is from the old machine. This machine runs 3.11.9, which is
fine because the code supports 3.10+.)

A second git worktree exists at
`C:\Users\Saikiran\copilot-worktrees\Avengers-Bobbiey-UCS\srisaikiranthatikonda-supreme-doodle`
(branch `srisaikiranthatikonda-supreme-doodle`). Leave it alone unless asked.

Paths under `C:\Users\sai\...` (including the old git binary path and the
OneDrive `jarvis` folder) are **historical**. Do not use them here. See §20.

---

### 4. Repository Structure

Flat Python layout. Everything runs in one process from `main.py`.

**Core backend**
| File | Responsibility |
|---|---|
| `main.py` | FastAPI app, `lifespan` wiring of every service, **all ~90 HTTP/WS endpoints**, the auth middleware, `/static` mount, vision endpoint |
| `hub.py` | In-process WebSocket pub/sub. Modules call `hub.broadcast(...)` |
| `agents.py` | The 8 Avengers: status, task, confidence, history |
| `brain.py` | LLM routing: Claude CLI → local OpenAI-compatible LLM → rule templates. Also `see()` for vision |
| `llm_local.py` | OpenAI-compatible client (Ollama / LM Studio / vLLM). Persists choice in `model_pref.json` |
| `local_brain.py` | Rule-based fallback replies so the platform never goes silent |
| `orchestrator.py` | Phase 2: directives, delegation, shared blackboard, preemption, agent consults |
| `shared_memory.py` | Phase 2: persistent cross-agent `TeamMemory` |
| `knowledge.py` | Ranked search across memory/notes/incidents/mail/calendar/agents/news + AI answers |
| `insights.py` | Insight engine, executive briefing, recommendations |
| `productivity.py` | Focus timer, tasks, context switches, score |
| `threats.py` | Threat engine: 6-domain risk matrix, SOC counters, incidents, alert tiers |
| `decisions.py` | Phase 5: propose → simulate → execute under human authority, supervised-autonomy whitelist |
| `audit.py` | Append-only audit trail (`audit.log`) |
| `auth.py` | Remote-access perimeter: PBKDF2 password, HMAC sessions, API tokens, lockout (`auth.json`) |
| `fleet.py` | Fleet node registry (online/stale/offline, aggregates) |
| `node_probe.py` | Telemetry collector shared by host and nodes (psutil + stdlib) |
| `node_agent.py` | Standalone agent a remote machine runs to join the fleet (Python 3.7+ compatible) |
| `billing.py` | Phase 6: editions, AI credits, entitlements (**demo, no real payments**) |
| `agenda.py` | Calendar/inbox intelligence over real Google data |
| `google_sync.py` | Google OAuth + Calendar + Gmail (read-only scopes) |
| `memory.py` | Operator memory: description-based recognition, **not biometric** |
| `voice.py` | Wake phrases, faster-whisper STT, intent router (optional deps) |
| `tts.py` | Cross-platform TTS (Windows System.Speech via PowerShell, macOS `say`, Linux `spd-say`) with mute/volume in `audio_pref.json` |
| `services.py` | `SystemMonitor` (psutil telemetry every 2 s) + `NewsService` (NewsAPI) |
| `weather.py` | Open-Meteo client |
| `connectivity.py` | Wi-Fi / Bluetooth / ping / active-uplink detection |
| `routines.py` | APScheduler jobs: agent ticks, syncs, orchestration, decisions, briefing |
| `roadmap.py` | Live "Product Evolution" panel. Probes the running system for capabilities |
| `browser.py` | Opens URLs in a real Chrome/Edge window and parses "open …" browser intents (`/api/browser`) |

**Other directories**
- `static/`: the dashboard: `index.html`, `app.js`, `style.css`, `login.html`, `landing.html` (vanilla JS, no build step).
- `web3mod/`: optional Web3 subsystem: `service.py` (façade) plus `config`, `blockchain`, `wallet`, `token`, `governance`, `treasury`, `marketplace`, `analytics`.
- `docs/`: **canonical public website** (`index.html`, `screenshots/`). Root `netlify.toml` publishes it. GitHub Pages can serve it too.
- `site/`: legacy mirror for the old `netlify deploy` CLI link (`site/.netlify/`). `site/index.html` must equal `docs/index.html` (`tests/test_site.py`). Edit `docs/`, then copy.

**Documentation**: `PROJECT_CONTEXT.md`, `ARCHITECTURE.md`, `FLEET.md`,
`SAAS_PLAN.md`, `MIGRATION.md`, `README.md`, this `CLAUDE.md`.

**Launch / ops scripts**
- `start-jarvis.cmd` → `run.ps1`: Windows server launcher.
- `start-jarvis.sh`: macOS/Linux server launcher.
- `start-node.cmd/.sh`, `join-fleet.cmd/.sh`: join another machine to the fleet.
- `restore-data.ps1/.sh`: restore gitignored private data from a migration bundle (see `MIGRATION.md`).
- `requirements.txt`: pinned core deps. Voice/wake-word deps are commented out (optional).

- `tests/`: stdlib `unittest` suite (no extra deps, no credentials). See §18.

---

### 5. Architecture Principles

Keep these. Every past decision was shaped by them.

- **Local-first & privacy-first.** Telemetry, camera frames and voice stay on
  the device. Vision presence is motion/description based, **never facial
  recognition or biometrics**.
- **Real data over fabricated data.** Never add mock or placeholder values to
  the dashboard. If something can't be measured, say so honestly. This is the
  single most important product value.
- **Loopback-first.** Bind `127.0.0.1` unless the operator explicitly exposes it.
- **Optional modules stay optional.** Web3, monetization, voice, Google, local
  LLM: the platform must boot and work without any of them.
- Preserve existing functionality. Develop incrementally.
- Avoid unnecessary dependencies (the node agent needs only `psutil`; auth crypto is stdlib).
- Reuse the existing architecture and seams (`hub.broadcast`, `snapshot()` dicts, `state[...]` in `main.py`).
- Avoid rewrites. Keep backwards compatibility where practical.

---

### 6. Application Architecture

**Data flow.** `uvicorn main:app` → FastAPI `lifespan` builds every service in
order (Hub → SystemMonitor/News → LocalBrain/LocalLLM/Brain → TTS → Weather/
Agenda/GoogleCalendar → TeamMemory → Insights/Threats → OperatorMemory →
Productivity → Orchestrator → Knowledge → Voice → Audit → Fleet → Decisions →
Billing → Roadmap) and stores them in a shared `state` dict. Services push
updates through `hub.broadcast()` to every dashboard client on `/ws`. The
dashboard also calls REST endpoints under `/api/*`. APScheduler (`routines.py`)
drives the periodic work. State is in memory plus JSON/JSONL files in the repo
root (gitignored).

**Order matters in `lifespan`:** `fleet` must exist before `decisions`.

Status legend: **IMPLEMENTED** = working code path. **PARTIAL** = works with
limits. **MOCK/SIMULATED** = demo values, not real. **OPTIONAL** = off or
needs setup. **PLANNED** = not built.

| Area | Status | Notes (verified) |
|---|---|---|
| FastAPI backend + WebSocket hub | IMPLEMENTED | Single asyncio process, port 8765 |
| Dashboard (`static/`) | IMPLEMENTED | Vanilla JS HUD, cache-busted with `?v=5.0` |
| Telemetry (psutil) | IMPLEMENTED | CPU/mem/disk/net/processes every 2 s |
| AI brain chain | IMPLEMENTED | Claude CLI → local LLM → templates. **Here: Claude CLI live (`brain_mode: llm`) + Ollama `llama3.2:3b`**. Templates remain the final fallback |
| 8 agents | IMPLEMENTED | Reply quality depends on which brain is available |
| Orchestrator + team memory | IMPLEMENTED | Phase 2 |
| Knowledge hub, insights, briefing | IMPLEMENTED | AI answers depend on the brain |
| Threat intelligence | IMPLEMENTED | Built from live telemetry/news/agenda signals |
| Vision presence (camera) | PARTIAL | Browser-side frame analysis. AI image description goes through the Claude CLI (`brain.see`, Read tool only). Not yet re-verified with a camera on this machine |
| Voice STT / wake words + double-clap summon | IMPLEMENTED, enabled here | `JARVIS_VOICE=1`, `JARVIS_WHISPER_MODEL=base` (multilingual, better for Indian English than `.en`). Deps installed (faster-whisper 1.1.0, sounddevice 0.5.1, numpy 2.2.0, onnxruntime 1.20.1). Verified: mic capture, model load, STT, wake-word routing, listener online in the live app. **Live spoken-command test still pending** |
| TTS | IMPLEMENTED | Windows System.Speech via PowerShell. Audio defaults to muted |
| Google Calendar + Gmail | OPTIONAL, not connected here | Read-only OAuth. See §9 |
| News | IMPLEMENTED | Keyless open feeds (`open_news.py`: BBC World, The Hindu, Al Jazeera, The Hacker News RSS + Hacker News API) every 10 min. `NEWSAPI_KEY` optional (falls back to open feeds on failure) |
| Network truth (`netspeed.py`) | IMPLEMENTED | TCP-RTT latency + rolling packet loss every 15 s. Real speed test vs speed.cloudflare.com (adaptive 5–25 MB, `JARVIS_SPEEDTEST_MIN`, default 60) |
| Weather | IMPLEMENTED | Open-Meteo, no key |
| Fleet (multi-node) | IMPLEMENTED | Nodes push to `/api/fleet/report` with a token or pairing code. Host registers itself |
| Auth perimeter | IMPLEMENTED | Loopback trusted. Remote denied until a password is set. Sessions + API tokens + lockout |
| Audit trail / RBAC / export | IMPLEMENTED | Commander/observer roles enforced server-side (optional `JARVIS_PIN`) |
| Decision support | IMPLEMENTED | Propose → simulate → execute. Never destructive. Autonomy opt-in whitelist |
| On-prem AI cluster | IMPLEMENTED (needs fleet nodes running Ollama) | `/api/cluster` lists Ollama endpoints on fleet nodes. `/api/cluster/route` points the brain's inference at one |
| AIOps status | IMPLEMENTED | `/api/aiops` → `insights.aiops_status()` |
| Billing / editions / credits | **MOCK/SIMULATED** | Demo ledger in `billing.json`. **No real payment processing, and credits/entitlements are display-only (nothing enforces them)**. Target design in `SAAS_PLAN.md` §3 |
| Web3 subsystem | OPTIONAL (off by default) | `enabled: False`. When enabled: **real** JSON-RPC chain status, on-chain balances, persisted governance/marketplace/treasury |
| Web3 token figures | MOCK/SIMULATED until configured | Demo values until `token.contract` is set, then live ERC-20 reads |
| Token market data, on-chain staking | PLANNED | Market data needs a listing; staking needs contracts. Code refuses to invent prices |
| Public website (`docs/`, mirrored to `site/`) | IMPLEMENTED | Netlify `avengers-bobbiey.netlify.app` (manual CLI deploys until linked to GitHub). GitHub Pages not enabled (404). Waitlist via Netlify Forms. **Public claims must match the code** (no "live" billing while demo) |
| PostgreSQL / Redis / Next.js / LangGraph | PLANNED | Target stack in `ARCHITECTURE.md`. Not present |

A UI element does not prove a feature works. Check the code path (and
ideally the running endpoint) before calling something implemented.

---

### 7. Local Startup (verified from launcher scripts)

**Start the main application (Windows):**
```
start-jarvis.cmd
```
What it does: kills anything **LISTENING on port 8765** (and 8766–8768 OAuth
ports) with `taskkill /F`, then runs `run.ps1`, which starts
`.venv\Scripts\python.exe -m uvicorn main:app --host <JARVIS_HOST> --port <JARVIS_PORT>`
(defaults `127.0.0.1:8765`, read from the env var first, then `.env`). A hidden
PowerShell loop opens the browser once `/api/status` responds.

- ⚠️ If `.venv` is missing, `run.ps1` **creates it and installs deps**. If
  `.env` is missing, it copies `.env.example` and exits. Both exist here, so
  neither happens. Do not delete them.
- ⚠️ Running the launcher **kills a server that is already running**. Check
  first. The operator starts/stops the server manually and prefers it not left
  running. Don't start or stop it without a reason, and stop any server you
  started when you're done.
- The orphan-kill step matches `*jarvis*\.venv` paths, so it doesn't match this
  folder name. Harmless.

**Direct start (equivalent, no port-killing):**
```
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8765
```

**URL:** http://127.0.0.1:8765 (health: `/api/status`, login for remote: `/login`, landing: `/landing`).

**Stop:** Ctrl+C in the server window (or close it).

**Optional services**
- Fleet node on another machine: `join-fleet.cmd [http://<host-ip>:8765] ["Name"]`
  or `start-node.cmd <server-url> ["name"]` (needs host bound to `0.0.0.0`; see `FLEET.md`).
- Local LLM: Ollama is installed and runs as a tray app / `ollama serve`. UCS auto-picks the first installed model.
- Voice: requires optional packages (don't install unless asked) + `JARVIS_VOICE=1`.

macOS/Linux: `./start-jarvis.sh` (same behavior).

---

### 8. Environment Variables and Secrets

Variables from `.env.example`, cross-checked with `os.getenv` in the code.
Names only. **Never print values.**

| Variable | Used by | Purpose |
|---|---|---|
| `JARVIS_HOST` / `JARVIS_PORT` | `run.ps1`, `main.py` | Bind address (default `127.0.0.1:8765`). `0.0.0.0` exposes to LAN |
| `NEWSAPI_KEY` | `main.py` → `NewsService` | Live news. Empty → no news |
| `CLAUDE_BIN` | `main.py` → `Brain` | Claude CLI path (default `claude`) |
| `LOCAL_LLM_URL` / `LOCAL_LLM_MODEL` / `LOCAL_LLM_KEY` | `llm_local.py` | OpenAI-compatible endpoint (default Ollama `http://127.0.0.1:11434/v1`) |
| `JARVIS_TTS`, `JARVIS_VOICE_NAME`, `JARVIS_VOICE_RATE` | `tts.py` | Spoken output |
| `JARVIS_VOICE`, `JARVIS_WHISPER_MODEL`, `JARVIS_VOICE_THRESHOLD` | `voice.py` | Wake-word / STT |
| `JARVIS_MAC_VOICE` | `tts.py` | macOS voice (in code, not in `.env.example`) |
| `JARVIS_INSIGHTS` | `main.py` → scheduler | `0` stops scheduling insight jobs (default on) |
| `JARVIS_PIN` | `main.py` (role switch) | Optional commander PIN |
| `JARVIS_FLEET_TOKEN` | `main.py`, `node_agent.py` | Fleet ingestion token override |
| `JARVIS_SERVER`, `JARVIS_NODE_NAME`, `JARVIS_PAIR_CODE`, `JARVIS_NODE_INTERVAL` | `node_agent.py` / `node_probe.py` | Node-side settings |

Current `.env` on this machine is an unmodified copy of `.env.example`
(no keys configured).

**Security rules. Never:**
- print secrets or expose `.env` contents
- commit secrets or push private credentials to GitHub
- copy API keys into source code or hardcode credentials
- include tokens, codes or passwords in documentation

**Local/private files. Do not modify, read out, or commit unless explicitly required:**
`.env` · `auth.json` · `fleet_token.txt` · `pair_code.txt` · `node_id.txt` ·
`governance.json` · `operator_memory.json` · `audio_pref.json` · `audit.log` ·
`credentials.json` · `token.json` · `model_pref.json`
(plus others in `.gitignore`: `notes.json`, `productivity.json`,
`team_memory.json`, `operators.json`, `decisions.json`, `billing.json`,
`web3_config.json`, `treasury.json`, `marketplace.json`,
`web3_analytics.json`, `waitlist.jsonl`, `node_token.txt`, `vision_frame.jpg`).
All are gitignored. Keep it that way.

---

### 9. Google Integrations

- `google_sync.py` implements OAuth (Desktop flow via `run_local_server` on a
  free port 8766–8768) with **read-only** scopes:
  `calendar.readonly`, `gmail.readonly`, `tasks.readonly`. **One sign-in (G)
  covers all three.**
- It requires `credentials.json` (OAuth client) in the repo root and saves
  `token.json` after consent. The operator starts it with the **G** button in the dashboard.
- **G is sign-in-once.** `connect()` reuses a valid token that already grants every
  scope (it re-syncs, no browser). Consent only runs on first connect, when
  `SCOPES` grows, or with `force=True`. Tokens load with their own granted
  scopes, so an older token keeps refreshing after a scope is added.
- Sync reads **every selected calendar** (`fetch_all_calendars`, ≤15 calendars,
  7 days, de-duplicated by iCalUID) with join link/provider, organizer, RSVP,
  guest count and calendar name. Also Gmail (+ account address via
  `getProfile`) and open Tasks (`fetch_open_tasks`). A missing Tasks API/scope
  degrades to `tasks_error`, never breaking calendar/mail.
- Dashboard renders only `https://` join links (no other schemes).
- `access_type="offline"` + `prompt="consent"` are mandatory (otherwise no
  `refresh_token`). Already in the code. Don't remove them.
- **This machine currently has neither `credentials.json` nor `token.json`**
  (verified 2026-09-28), so Calendar/Gmail are not connected and `agenda.py`
  shows no events (it does not mock them).
- **Never start OAuth or any authentication flow automatically.** The operator does it.
- The setup docstring in `google_sync.py` references an old `C:\Users\sai\...` path. That path is historical.

---

### 10. Ollama / Local AI

- `llm_local.py` is an OpenAI-compatible client (Ollama at
  `http://127.0.0.1:11434/v1` by default; LM Studio/vLLM also work). httpx
  calls use `trust_env=False` so a system proxy can't intercept localhost.
- `brain.py` probe order: Claude CLI (`--version`) → local LLM endpoint →
  `local_brain` templates. Picking a local model in the UI sets
  `force_local` in `model_pref.json`. Otherwise local is only auto-preferred
  once it has proven fast (last call < 4 s).
- Vision always uses the Claude CLI (local models here are text-only).
- **Claude CLI calls (`brain._llm_call`), and why they're set up this way:**
  - `cwd` = `%TEMP%\bobbiey-ucs-brain`, **never the repo**. The CLI auto-loads
    CLAUDE.md from its working dir, and this developer manual must never reach
    operator-facing agent replies.
  - Text calls pass `--tools ""` (pure reasoning over the context we send;
    with tools on, the CLI tries blocked shell commands and stalls ~30 s).
    Vision passes `--tools Read --dangerously-skip-permissions`.
  - The prompt goes after `--`, because `--tools` is variadic and would
    otherwise swallow it.
  - `--bare` is **not** usable: it requires `ANTHROPIC_API_KEY` auth, and this
    machine uses the CLI's own login.
  - Agents get a `[LIVE TELEMETRY …]` line from `SystemMonitor.latest`
    (`Avenger._live_telemetry`) so they quote measured numbers.
- The brain is probed **once at boot**. After changing brain config, restart the server.
- **Routing policy: Claude first** (operator decision 2026-09-29). With the CLI
  reachable, agent/voice replies always go to Claude. The latency fast path to
  the local model only applies when Claude is not the brain. Ollama serves the
  insights engine (every 90 s) and takes over if a Claude call fails. An
  operator-forced local model (`force_local`) is still respected.
- **Ollama here:** `llama3.2:3b`, CPU only. ~12 tok/s warm, ~19 s per insight
  under memory load. The 90 s insights cycle keeps it resident (~2.6 GB), and
  this laptop runs at ~90% RAM, so watch memory. Don't pull bigger models
  without asking.

---

### 11. Claude Code Development Workflow

For every feature request:
1. Understand the requested outcome.
2. Inspect the existing implementation.
3. Identify the relevant files.
4. Check the existing architecture before creating new abstractions.
5. Explain the approach for significant changes.
6. Implement incrementally.
7. Run appropriate checks (§18).
8. Run the application when practical.
9. Fix errors caused by the change.
10. Review `git diff`.
11. Summarize what changed.

Don't modify unrelated files.

---

### 12. Git Workflow

Before major changes: check `git status`, confirm the branch, and keep
unrelated user changes intact.

**Never:** force push · destructive `git reset` · discard uncommitted work ·
overwrite unrelated changes · delete branches without approval.

**Default: ship on verified success** (operator policy since 2026-09-28).
When a requested feature or fix is implemented **and verified** (tests pass,
app runs, UI checked in the browser when UI is involved), commit and push it
to `main` (or its feature branch) without waiting to be asked.

**Do NOT push if:** tests fail · the app is broken · visual verification shows
unresolved issues · secrets are detected · the change is incomplete · the
operation would be destructive · there are unexpected unrelated changes · a
credential or external approval is still needed. Report instead.

"Ship it" / "commit and push" means the same pipeline, triggered explicitly:
1. `git status`
2. `git diff`
3. Inspect the changed files
4. Check for secrets (and that no gitignored/private file is staged)
5. `git add` only the intended files (never `git add -A` blindly)
6. Focused commit, conventional prefix: `feat:` `fix:` `test:` `refactor:` `docs:` `chore:`
7. Push to the intended branch
8. Verify the push (`git status -sb`, `git ls-remote origin`)
9. Report: **SHIPPED** · Feature · Commit · Branch · GitHub push · Tests · Browser verification · Known limitations

Large or experimental work goes on a feature branch. Don't merge it
automatically. Never force push. If a push fails, say so and diagnose it.

No git identity is configured on this machine. Commit as the repo's
historical author: `git -c user.name="Sai" -c user.email="saikiran.thatikonda@t-hub.co" commit ...`.

---

### 13. Change Safety

Ask for confirmation before:
- destructive operations or deleting files
- deleting functionality
- changing production infrastructure or deployment configuration (Netlify, Pages)
- destructive data operations (including the local JSON state files)
- changing authentication/security architecture
- force pushes, resets, discarding work
- major architectural rewrites
- changing Python versions or replacing major dependencies

For normal feature work, go ahead without extra confirmation once the task is clear.

---

### 14. Existing Project Gotchas

`PROJECT_CONTEXT.md` §5 has the full list. **Read it before changing related
code.** The most important:

- **`app.js` temporal-dead-zone bug:** shared `let` variables (`lastAgenda`,
  `lastWeatherOk`, …) must be declared **at the top of `app.js`**. An early
  call into a function that uses a late `let` throws and silently blanks the
  lower half of the dashboard.
- **Cache-busting:** bump `?v=` on `app.js` / `style.css` in
  `static/index.html` after UI changes (currently `?v=5.0`).
- **CSS classes:** `ghost-btn` is a 22px icon square. Use `cmd-btn` for text buttons.
- **Async renders can overwrite live data** (e.g. atlas render resetting the fleet badge).
- **Claude CLI flags:** `--dangerously-skip-permissions` is used only for
  vision calls. `stdin=DEVNULL` avoids a ~3 s stall per call.
- **Python compatibility:** `node_agent.py` must stay Python 3.7+ compatible
  (`from __future__ import annotations`).
- **PowerShell 5.1:** no `&&`. `"$i:"` is a parse trap (use `-f`). Keep commit
  messages plain ASCII (no `|`, backticks, em-dashes or inner quotes in here-strings).
- **Google OAuth:** keep `access_type=offline` + `prompt=consent`.
- **Ollama via httpx:** `trust_env=False`.
- **Local-only state files:** see §8. Never commit them.
- **Old-machine paths:** see §20.
- **UI verification:** screenshots of the dashboard tend to time out (heavy
  canvas). Inspect the DOM/JS state and API responses instead.
- **Firewall rules need an elevated shell.** Office Wi-Fi often isolates
  clients. A phone hotspot is the reliable fleet workaround.

---

### 14b. Dashboard truth rules (learned 2026-09-30)

- The six header tool cards and orb flanks were **hardcoded** ("14 SATS", "Project
  Helios" files, "AES-256", "1,247 calls"). They are now bound to live endpoints
  (`gatherToolSnapshot` / `refreshToolCardStats` in `app.js`). **Any new tile or
  readout must name its endpoint.** If there's no data source, show "—" or an honest
  empty state.
- Wi-Fi PHY rate ≠ internet speed. Internet speed only comes from the speed test.
- Values drawn inside a flank ring use `.flank-bigval.in-ring` (12.5 px). Keep ≤3–4
  glyphs (`fmtLatency` switches to seconds ≥1000 ms).
- New helpers that may run during load must be **function declarations** (hoisted),
  not `const` arrows (the TDZ trap, §14).
- Phone width: test with a 375 px iframe and hide-each-section bisection
  (emulated phones zoom out and hide the culprit). Grids need `minmax(0,1fr)`.
- Speaker→mic loopback tests don't work here: the Intel Smart Sound mic's echo
  cancellation removes system playback. Real claps/voice need the operator.

---

### 15. Roadmap

The project identifies itself as **v5.8 · ~96%**, with Phases 1–5 complete and
Phase 6 in progress (per `PROJECT_CONTEXT.md`). `roadmap.py` computes the
live version by probing the running system.

Don't state roadmap status from memory. Check `PROJECT_CONTEXT.md` and the
code, and keep "on the roadmap/UI" separate from "implemented".

---

### 16. Product Evolution

1. **AI Command Dashboard**: telemetry, agents, voice, vision, Google, local AI, threats, alerts
2. **Multi-Agent Intelligence**: orchestrator, delegation, blackboard, consults, team memory
3. **Computer Vision Operations**: multi-camera, 3×3 zone monitoring, activity analytics
4. **Enterprise Command Platform**: audit, RBAC, operators, compliance export, fleet, remote auth, on-prem AI clusters
5. **Autonomous Decision Support**: propose → simulate → execute, supervised autonomy
6. **Ecosystem & Monetization** (current): editions/credits (demo), optional Web3. Remaining: live token market data, on-chain staking

The repository's own documentation is the authoritative status for each phase.

---

### 17. Feature Development Philosophy

Bobbiey UCS is an evolving system. When adding a feature:
- integrate with the existing architecture (register in `lifespan`, broadcast via `hub`, expose under `/api/...`)
- don't create parallel duplicate systems
- preserve existing APIs and the WS message contract where practical
- preserve the existing dashboard, agent identities and the Avengers × Bobbiey visual language
- prefer modular components. Keep optional capabilities optional
- make failures graceful (the platform must never crash because an optional service is missing)
- log usefully (`hub.broadcast({"type": "log", ...})` pattern)
- avoid fake success states and fabricated data

---

### 18. Testing

Test suite: `tests/` (stdlib `unittest`, no credentials). Run it with
`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`.
Set `UCS_LIVE_AI=1` to include the live Claude CLI test (one real call).
`tests/harness.py` `IsolatedServer` boots the real app from a temp copy of the
code (no `.env`, no state files, Claude/LLM/TTS/voice off) on a free port.
Use it for endpoint tests. **Never point tests at the operator's live
server or real state files.** Add tests alongside meaningful changes.

**Live dev loop.** The desktop app's browser pane runs the server from
launch config `ucs` (`.claude/launch.json`; `preview_start ucs`), then
inspect `http://localhost:8765` with DOM/JS reads and screenshots.
Screenshots work in the pane. Before killing anything on :8765, identify
the owning process.

Before declaring a feature complete:
- run syntax/import checks, e.g. `.\.venv\Scripts\python.exe -m py_compile <file>.py`
  and an import of `main` for backend changes
- run relevant tests if any have been added
- start the application when practical (mind §7: don't kill the operator's running server unannounced)
- hit the affected API endpoints. For UI changes inspect DOM/console in the browser
- check server logs for errors
- verify existing functionality has not regressed

Don't claim a feature works without testing it when testing is reasonably possible.

---

### 19. Documentation

After significant architectural changes:
- update the relevant docs (`ARCHITECTURE.md`, `README.md`, `FLEET.md`, …)
- keep `CLAUDE.md` aligned with the actual development rules and environment
- update `PROJECT_CONTEXT.md` when important project context materially changes (new gotchas especially)
- document new environment variables (names and purpose, never values)

Don't update docs for trivial changes.

**Public website = product status.** When a phase or major feature changes
status, update `docs/index.html` in the same change (then copy it to
`site/index.html`), keeping every claim true to the code. Pushing to `main`
publishes it automatically:
- `.github/workflows/pages.yml` → GitHub Pages (`gh-pages` branch, build-stamped
  `<!-- build: sha · date -->`). URL: https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/
- `.github/workflows/tests.yml` runs the suite on every push (Ubuntu, Python 3.11).
- Netlify serves the same `docs/` once the operator links the site to the repo (`netlify.toml`).
After pushing, verify the deploy (Actions run status via the public API, then
the live URL's build stamp).

---

### 20. Current Machine vs Historical Machine

Old docs refer to **`C:\Users\sai`** (e.g. `C:\Users\sai\AppData\Local\Programs\Git\cmd\git.exe`,
`C:\Users\sai\OneDrive\Bobbiey's Claude\jarvis`, `C:\Users\sai\Desktop\jarvis`).

This machine uses **`C:\Users\Saikiran`**, with the repo at
`C:\Users\Saikiran\Avengers-Bobbiey-UCS`. Always use the current machine's
real paths. Don't run historical commands blindly. Adapt them first.

---

### 21. Claude Code Role

You are the ongoing engineering agent for Bobbiey UCS. Your job is **not** to
rebuild the project. It is to understand the existing system, keep what
works, and evolve it step by step.

For every feature request, think:

**UNDERSTAND → INSPECT → PLAN → IMPLEMENT → TEST → REVIEW → COMMIT → PUSH**

COMMIT and PUSH automatically only after verified success (§12). Otherwise stop and report.

---

### 22. Session Recovery

At the start of a new development session:
1. Read `CLAUDE.md`.
2. Read the relevant sections of `PROJECT_CONTEXT.md`.
3. Check `git status` (and `git log --oneline -5`).
4. Look at recent changes when needed.
5. Don't assume the previous session's state.
6. Recover context from the repository itself.

The repository should stay understandable even if the previous Claude Code
session disappears.
