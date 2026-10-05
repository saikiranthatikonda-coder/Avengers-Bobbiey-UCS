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

## Real-Time Interaction & Action Engine (foundational, since 2026-10-05)

Every command, whether spoken, typed, clicked, clapped or raised by a subsystem,
goes through **one pipeline**, `realtime.Engine.submit()`:

```
 voice.py ─┐  (wake word / follow-up / "stop" / double clap)
 ask box ──┼─► Engine.submit(text, source, agent?)        realtime.py
 /api/ask ─┤        │  RECEIVED → ACK (instant, broadcast)
 /api/cmd ─┘        ▼
            intents.match()  ── fast path, no LLM ──► live data / allow-listed action
                    │ (no match)                      (win_actions.py, browser, dashboard)
                    ▼
            intents.delegate() → JARVIS keeps it or HANDOFF → specialist
                    ▼
            Avenger.handle_stream() → Brain.stream()  Claude stream-json → Ollama SSE → templates
                    │ tokens ──► {"type":"cmd","stage":"delta"} ──► dashboard LIVE COMMANDS
                    │        └─► SentenceChunker ──► TTSPlayer (persistent speaker) ──► audio
                    ▼
            DONE / FAILED / CANCELLED  + timings (ack, route, first token, first audio, total)
```

| Piece | File | Role |
|---|---|---|
| Engine | `realtime.py` | Lifecycle, events, cancellation, shared context, latency stats (`/api/engine`) |
| Fast path | `intents.py` | Deterministic handlers (time, telemetry, network, weather, news, agenda, threats, fleet, missions, dashboard, browser, Windows actions, cancel) + `delegate()` routing table |
| Actions | `win_actions.py` | Allow-listed Windows actions (apps, folders, media/volume keys, lock). Never arbitrary commands. Audited |
| Streaming | `brain.py` / `llm_local.py` | `Brain.stream()`: Claude `--output-format stream-json`, stop reading at message end (saves ~2 s), Ollama SSE |
| Speech | `tts.py` | Persistent PowerShell speaker (~1.4 s vs 2.8 s/utterance), `cancel()` barge-in in ~10 ms, real playback-start callback |
| Voice | `voice.py` | Wake word → engine. Bare wake word / double clap → `summon`. Double clap while speaking = interrupt |

**Measured on the dev laptop (2026-10-05):** fast path 0–2 ms end-to-end. Claude path
first token ~1.9 s, first audio ~2.5 s, total ~2.5–3.5 s for short replies (previously
~5 s to generate + 2.8 s speech start). Cancel mid-stream ~0.5 s (CLI process killed).

**Extension contract for every phase:** add a fast-path handler to `intents.HANDLERS`
(or a route to `intents.ROUTES`), or call `state["engine"].submit(text, source="agent")`
from a subsystem. Events, timings, cancellation, context, speech and the dashboard
console come for free.

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
