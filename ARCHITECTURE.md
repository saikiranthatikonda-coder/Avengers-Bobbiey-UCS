# Bobbiey Unified Command System — Architecture

## Current state (Phase 1 · AI Command Dashboard)

```
┌─────────────────────────── BROWSER (single-page HUD) ───────────────────────────┐
│ index.html + app.js + style.css                                                 │
│ · WebSocket /ws (live events)  · REST /api/*  · getUserMedia presence monitor   │
└──────────────────────────────────┬──────────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────────┐
│ FastAPI (main.py) — single process, asyncio                                     │
│                                                                                 │
│  hub.py            in-process pub/sub → WS fan-out                              │
│  agents.py         8 Avenger agents (status, task, queue, confidence)           │
│  brain.py          LLM chain: claude CLI → local LLM → rule templates           │
│  llm_local.py      OpenAI-compatible client (Ollama / LM Studio / vLLM)         │
│  insights.py       JARVIS Insights engine + executive briefings                 │
│  threats.py        Threat Intelligence engine (risk score, incident feed)       │
│  agenda.py         Calendar + inbox + calendar intelligence                     │
│  google_sync.py    Google Calendar OAuth + sync engine                          │
│  voice.py          faster-whisper STT, wake-phrases, intent router, memory      │
│  tts.py            SAPI voice output (per-utterance process)                    │
│  services.py       psutil telemetry + NewsAPI                                   │
│  weather.py        Open-Meteo                                                   │
│  connectivity.py   netsh WiFi / Bluetooth / ping                                │
│  routines.py       APScheduler: agent rotation, syncs, ticks, 08:30 briefing    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

State is in-memory; persistence is file-based (waitlist.jsonl, token.json).
This is correct for a single-operator local deployment.

## Target stack & migration path

| Layer     | Today                  | Target (Phase 3-4)        | Migration trigger                |
|-----------|------------------------|---------------------------|----------------------------------|
| Frontend  | Vanilla JS HUD         | Next.js + TS + Tailwind + Framer Motion | >1 operator, auth'd sessions |
| Backend   | FastAPI ✅ (keep)      | FastAPI (unchanged)       | —                                |
| Database  | in-memory + JSONL      | PostgreSQL                | history queries, multi-device    |
| Memory    | Python dicts           | Redis                     | multi-process / worker split     |
| AI runtime| claude CLI + Ollama ✅ | Ollama-first              | already supported                |
| Agents    | custom asyncio         | LangGraph                 | Phase 2 agent-to-agent delegation|
| Vision    | canvas frame-diff      | OpenCV + MediaPipe        | multi-camera, zone detection     |
| Voice     | faster-whisper + SAPI  | whisper + Kokoro/Piper    | natural-voice requirement        |
| Auth      | none (loopback only)   | Google OAuth ✅ (calendar) → full login | public deployment   |

### Why not rewrite now
The current asyncio monolith ships value daily and has zero infra cost. Each
target component slots in behind an existing seam:
- `hub.py` broadcast API → swap internals to Redis pub/sub, callers unchanged.
- `agenda/threats/insights` snapshot() dicts → become SQLAlchemy queries.
- `Avenger.handle()` → becomes a LangGraph node; roster/UI contract unchanged.
- The HUD's WS message contract is the API spec a Next.js client would consume.

### Phase roadmap
1. **AI Command Dashboard** — this repo, running now ✅
2. **Multi-Agent Intelligence** — LangGraph delegation between Avengers, shared task board (`task_queue` fields already in place)
3. **Computer Vision Ops** — OpenCV/MediaPipe service publishing to the hub; multi-camera; zones (presence contract already defined: active/idle/away/no-user)
4. **Enterprise Command Platform** — Postgres + Redis + Next.js + OAuth logins, multi-site
5. **Autonomous Decision Support** — insights engine gains propose→simulate→approve→execute loop with human sign-off
