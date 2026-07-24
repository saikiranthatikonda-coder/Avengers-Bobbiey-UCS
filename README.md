<div align="center">

# ◆ BOBBIEY UCS
### Unified Command System — a real-world JARVIS for human operators

**An AI-powered operational command center that fuses telemetry, calendar, vision, voice, news, threat intelligence, and a fleet of AI agents into one cinematic, local-first dashboard.**

[![Live Site](https://img.shields.io/badge/▶_LIVE_SITE-avengers--bobbiey.netlify.app-00d9ff?style=for-the-badge)](https://avengers-bobbiey.netlify.app/)
[![Status](https://img.shields.io/badge/status-active_development-50fa7b?style=for-the-badge)]()
[![Platform](https://img.shields.io/badge/platform-Windows_·_macOS_·_Linux-8b7cf8?style=for-the-badge)]()

`Python` · `FastAPI` · `WebSockets` · `Ollama / Claude` · `faster-whisper` · `d3` · `Chart.js`

</div>

---

## 🌐 Live

- **Product site / waitlist:** **https://avengers-bobbiey.netlify.app/**
- **The command center itself** runs **locally on your machine** (privacy-first by design) — see [Quick Start](#-quick-start).

> Bobbiey UCS keeps your telemetry, camera, and voice **on your own device**. The public site is just the pitch; the intelligence never leaves your laptop.

---

## 📸 Screenshots

> **▶ See it live & animated:** **[avengers-bobbiey.netlify.app](https://avengers-bobbiey.netlify.app/)**

Dashboard captures live in [`docs/screenshots/`](docs/screenshots/). The command center is a cinematic dark HUD: a central reactor orb with a live rotating Earth, eight AI-agent cards, real-time telemetry sparklines, a six-domain threat radar, world activity map, and India/Telangana regional grids — all updating in real time over WebSockets.

<!-- Captures are added to docs/screenshots/ — see that folder's README to add your own -->

---

## ⚡ What it does

Bobbiey UCS turns a passive dashboard into an **active operations brain**:

- 🧠 **8 specialist AI agents** (Jarvis · Captain · Stark · Black Widow · Hawkeye · Hulk · Thor · Vision) that observe, summarize, and recommend — each with live status, task, confidence, and history.
- 🎙️ **Voice command** — wake-word per agent ("Hey Stark", "Hey Cap"…), faster-whisper STT, spoken replies, and dashboard control by voice ("open threat intelligence").
- 📊 **Real-time telemetry** — CPU / memory / disk / network / processes streamed over WebSockets every 2 s, with predictive 15-minute forecasting.
- 📅 **Calendar intelligence** — real Google Calendar sync, meeting countdowns, conflict detection, focus-block analysis, readiness scoring, and voice/visual reminders.
- 🛰️ **Threat Intelligence Center** — six-domain risk matrix (radar), SOC counters, incident-response timeline, live risk scoring, and a four-tier **emergency alert system** (info → warning → critical → emergency) with looping audible alerts and acknowledge/dismiss.
- 👁️ **Vision awareness** — identity-free webcam presence detection (active / idle / away), session tracking, motion metering. _No face recognition, ever._
- 🤖 **Local-first AI** — runs on **Ollama** (Llama 3, Qwen, Mistral, Phi, DeepSeek…) or the Claude CLI, with an in-UI model manager that auto-detects installed models.
- 🌍 **Live world view** — rotating d3 globe in the orb core, global activity map, India + Telangana regional grids, world clocks, and live Hyderabad weather.
- 🚨 **JARVIS Insights** — an engine that reads every widget and surfaces the single most useful observation, plus daily executive briefings (spoken).
- 🕸️ **Multi-agent orchestration** _(Phase 2)_ — Jarvis derives **directives from live conditions**, delegates them to specialist agents over a **shared context blackboard**, resolves competing priorities by preemption, triggers **agent-to-agent consults**, and every agent builds on a persistent **cross-agent team memory**.
- 🧭 **Command recommendations + Knowledge Hub** _(Phase 2)_ — a standing briefing panel of every currently-applicable action, and one ranked search box across memory, notes, incidents, mail, calendar, agents and news, with AI answers from your chosen brain.
- 🎥 **Vision operations** _(Phase 3)_ — **multi-camera** enumeration with hot-switching, **3×3 zone monitoring** with away-intrusion alerts naming the sector, and camera-tagged AI observations. Frames never leave the machine.
- 🏢 **Enterprise command** _(Phase 4)_ — **append-only audit trail** of every command action, **commander/observer roles enforced server-side** (optional PIN), operator profiles, and one-click **compliance data export**.
- 🛰️ **Command Fleet** _(Phase 4)_ — turn **every machine you own** into a live node: a tiny agent (`pip install psutil`, nothing else) reports each laptop's real CPU/RAM/disk/network/GPU/hardware/peripherals to one command view with a fleet overview, per-node detail, and token-gated ingestion. See [`FLEET.md`](FLEET.md).
- 🔐 **Authenticated remote access** _(Phase 4)_ — the perimeter that makes `0.0.0.0` exposure safe: the loopback console is always trusted, remote access is **denied by default** until you set a password, then remote operators log in (PBKDF2 + HMAC-signed HttpOnly sessions) and scripts use **API bearer tokens** — with per-IP lockout, revocable sessions/tokens, and full audit logging. Stdlib-only crypto, no dependencies.
- ⚖️ **Decision support** _(Phase 5)_ — the platform **proposes** actions from real conditions, **simulates** their impact from live numbers, and **executes only under human authority** — with an opt-in supervised-autonomy whitelist, fully audited.
- 💳 **License & monetization** _(Phase 6)_ — live subscription editions (Community/Pro/Team/Enterprise), **AI-credit metering**, and feature **entitlements** in-dashboard; multiple monetization paths coexist (subscriptions, credits, marketplace, services, on-prem, optional token).
- 🪙 **Web3 Command Center** _(Phase 6, optional & modular)_ — a toggleable blockchain layer styled into the HUD: **wallet** (MetaMask/injected), token analytics, treasury, governance, premium-access staking, marketplace and community. **Off by default; the platform runs fully without it.** Mock/demo data until any token exists — the token is access & participation, never speculation or a requirement.
- 📈 **Product Evolution panel** — the public roadmap rendered live inside the product, every feature verified against the running system with auto-versioning.

---

## 🚀 Quick Start

> Requires **Python 3.10+**. First launch auto-creates a virtual environment and installs dependencies (~1–2 min).

### Windows
```powershell
# 1. clone
git clone https://github.com/saikiranthatikonda-coder/Avengers-Bobbiey-UCS.git
cd Avengers-Bobbiey-UCS

# 2. launch (double-click start-jarvis.cmd, or:)
.\start-jarvis.cmd
```

### macOS / Linux
```bash
git clone https://github.com/saikiranthatikonda-coder/Avengers-Bobbiey-UCS.git
cd Avengers-Bobbiey-UCS
chmod +x start-jarvis.sh
./start-jarvis.sh
```

The dashboard opens automatically at **http://127.0.0.1:8765**. Stop with **Ctrl+C**.

That's it — it runs with zero config. Everything below is **optional** to unlock more power.

---

## ⚙️ Configuration

Copy `.env.example` → `.env` (the launcher does this for you) and set what you want:

| Variable | Purpose | Default |
|---|---|---|
| `NEWSAPI_KEY` | Live world news feed — free key at [newsapi.org](https://newsapi.org/register) | _(empty → mock feed)_ |
| `JARVIS_TTS` | Spoken responses | `1` |
| `JARVIS_VOICE` | Wake-word listening (needs mic + extra deps) | `0` |
| `JARVIS_WHISPER_MODEL` | STT model (`tiny.en`, `base.en`, `small`) | `small` |
| `LOCAL_LLM_URL` | Ollama / OpenAI-compatible endpoint | `http://127.0.0.1:11434/v1` |
| `LOCAL_LLM_MODEL` | Pin a model (else auto-detect) | _(auto)_ |
| `CLAUDE_BIN` | Path to Claude CLI (alternative brain) | `claude` |

### Optional integrations

<details>
<summary><b>🤖 Local AI with Ollama</b> (recommended — fully offline)</summary>

```bash
# install Ollama from https://ollama.com, then:
ollama pull llama3.2      # or qwen, mistral, phi, deepseek...
ollama serve
```
Open the dashboard → click the **MODEL** row or the **LLM CORE** scanner tile → pick your model. The choice persists across restarts.
</details>

<details>
<summary><b>📅 Google Calendar</b></summary>

1. [console.cloud.google.com](https://console.cloud.google.com) → new project → enable **Google Calendar API**.
2. Create **OAuth client credentials** (Desktop app is simplest) → download JSON → save as `credentials.json` in the project root.
3. Click the **G** button next to AGENDA in the dashboard → approve in browser. Real events flow in, syncing every 5 min.

_`credentials.json` and `token.json` are gitignored — they never leave your machine._
</details>

<details>
<summary><b>🎙️ Wake-word voice</b></summary>

```bash
.venv/bin/pip install faster-whisper sounddevice    # (Scripts\pip on Windows)
```
Set `JARVIS_VOICE=1` in `.env`, restart, and say **"Hey Jarvis"**.
</details>

---

## 🎙️ Voice commands

| Say | Result |
|---|---|
| "Hey Jarvis / Stark / Cap / Widow / Hawkeye / Hulk / Thor / Vision …" | Routes to that agent |
| "What meetings do I have today?" | Live calendar summary |
| "Summarize my inbox" | Inbox triage |
| "What's the threat status?" | Risk briefing |
| "Open threat intelligence / map / camera …" | Dashboard control |

---

## 🏗️ Architecture

```
Browser HUD (index.html · app.js · style.css)
   │  WebSocket /ws (live events)   REST /api/*
   ▼
FastAPI (main.py · asyncio, single process)
   ├─ hub.py          in-process pub/sub → WS fan-out
   ├─ agents.py       8 AI agents (status · task · confidence)
   ├─ brain.py        LLM chain: Claude CLI → local LLM → templates
   ├─ llm_local.py    Ollama / OpenAI-compatible client + model manager
   ├─ insights.py     JARVIS Insights + executive briefings
   ├─ threats.py      Threat Intelligence engine (risk · incidents · matrix)
   ├─ agenda.py       Calendar + inbox + calendar intelligence
   ├─ google_sync.py  Google Calendar OAuth + sync
   ├─ voice.py        faster-whisper STT · wake phrases · intent router
   ├─ tts.py          cross-platform speech (SAPI / say / espeak)
   ├─ services.py     psutil telemetry + NewsAPI
   ├─ weather.py · connectivity.py · browser.py
   └─ routines.py     APScheduler — agent rotation · syncs · alerts · briefings
```

Full detail and the migration path to the enterprise stack (Next.js · Postgres · Redis · LangGraph) is in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 🗺️ Roadmap

The roadmap is **live inside the product** — the Product Evolution panel probes the running system and versions itself. **All five phases are shipped: v5.0 · 100% built.**

- **Phase 1 — AI Command Dashboard** ✅ **SHIPPED** — telemetry, agents, voice, vision, Google Calendar + Gmail, local AI, threat intelligence
- **Phase 2 — Multi-Agent Intelligence** ✅ **SHIPPED** — orchestration engine, delegation, shared blackboard, priority resolution, agent consults, cross-agent team memory
- **Phase 3 — Computer Vision Operations** ✅ **SHIPPED** — multi-camera hot-switching, 3×3 zone monitoring with away-intrusion alerts, activity analytics
- **Phase 4 — Enterprise Command Platform** ✅ **SHIPPED** — audit trail, commander/observer roles, operator profiles, compliance export, **multi-site fleet** (node agents on the maps), **authenticated remote access** (login + API tokens), **on-prem AI clusters** (route inference to a fleet GPU node)
- **Phase 5 — Autonomous Decision Support** ✅ **SHIPPED** — propose → simulate → approve → execute with supervised autonomy, including **fleet-wide operations** (flag strained nodes, offload inference)
- **Phase 6 — Ecosystem & Monetization** 🔨 **IN DEVELOPMENT** — live: subscription editions (Community→Enterprise), AI-credit metering, entitlements, and an **optional, modular Web3 Command Center** (wallet, token, treasury, governance, marketplace, community) · next: live token market data, on-chain staking

The commercial plan lives in [`SAAS_PLAN.md`](SAAS_PLAN.md).

---

## 🔒 Security & Privacy

- **Local-first.** Telemetry, camera frames, and voice are processed **on-device**. Camera presence detection is motion-based — **no facial recognition or identity data**.
- **Loopback-bound.** The server binds `127.0.0.1` by default.
- **Secrets stay out of git** — `.env`, `credentials.json`, `token.json`, and waitlist data are gitignored.

---

## 📁 Project structure

```
Avengers-Bobbiey-UCS/
├─ main.py              FastAPI app + all REST/WS endpoints
├─ agents.py · brain.py · llm_local.py · local_brain.py
├─ orchestrator.py · shared_memory.py    Phase 2 · multi-agent intelligence
├─ roadmap.py · knowledge.py · productivity.py
├─ audit.py             Phase 4 · append-only enterprise audit trail
├─ fleet.py · node_probe.py · node_agent.py   Phase 4 · multi-site fleet
├─ auth.py              Phase 4 · authenticated remote access (login + tokens)
├─ decisions.py         Phase 5 · supervised decision engine
├─ billing.py           Phase 6 · editions, AI credits, entitlements
├─ web3_service.py      Phase 6 · optional modular Web3 layer (mock)
├─ insights.py · threats.py · agenda.py · google_sync.py · memory.py
├─ services.py · weather.py · connectivity.py · voice.py · tts.py · browser.py
├─ routines.py          scheduler
├─ static/              the dashboard (index.html · app.js · style.css)
├─ site/               the public product/landing site
├─ start-jarvis.cmd     Windows launcher
├─ start-jarvis.sh      macOS / Linux launcher
├─ requirements.txt · .env.example
└─ ARCHITECTURE.md · SAAS_PLAN.md
```

---

## 👤 Founder

Built end-to-end by **Bobbiey** — founder, architect, and builder of Bobbiey UCS — from Hyderabad, India. The mission: redefine how humans interact with data and AI, from passive screens to active command systems.

<div align="center">

**◆ Bobbiey UCS** · _jarvis-class systems for human operators_

[Live Site](https://avengers-bobbiey.netlify.app/) · Built in Hyderabad, India

</div>
