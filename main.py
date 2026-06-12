import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import browser
import connectivity
from agenda import Agenda
from agents import build_team
from brain import Brain
from google_sync import GoogleCalendar
from hub import Hub
from insights import InsightsEngine
from llm_local import LocalLLM
from local_brain import LocalBrain
from routines import schedule_all
from services import NewsService, SystemMonitor
from threats import ThreatEngine
from tts import TTSPlayer
from voice import VoiceLoop
from weather import WeatherService

load_dotenv()

ROOT = Path(__file__).parent
STATIC = ROOT / "static"

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub = Hub()

    sysmon = SystemMonitor(hub)
    news = NewsService(api_key=os.getenv("NEWSAPI_KEY"), hub=hub)
    local_brain = LocalBrain(sysmon=sysmon, news=news)
    local_llm = LocalLLM(hub=hub)
    brain = Brain(claude_bin=os.getenv("CLAUDE_BIN", "claude"),
                  local_brain=local_brain, local_llm=local_llm)

    tts = TTSPlayer(hub=hub)
    if os.getenv("JARVIS_TTS", "1") != "0":
        if tts.start():
            await hub.broadcast({"type": "log", "level": "info",
                                  "msg": "TTS subsystem online — agents will speak"})
        else:
            await hub.broadcast({"type": "log", "level": "warn",
                                  "msg": "TTS unavailable — pip install pyttsx3 in .venv"})

    # Probe the LLM chain: claude CLI → local OpenAI-compatible endpoint → templates.
    mode = await brain.probe()
    if mode == "llm":
        await hub.broadcast({"type": "log", "level": "info",
                              "msg": "LLM brain online (claude CLI reachable)"})
    elif mode == "local-llm":
        await hub.broadcast({"type": "log", "level": "info",
                              "msg": f"local LLM brain online ({local_llm.model} @ {local_llm.base_url})"})
    else:
        await hub.broadcast({"type": "log", "level": "warn",
                              "msg": "LLM brain offline — running on local templates. "
                                     "Set CLAUDE_BIN in .env, or run Ollama and set LOCAL_LLM_URL."})
    if local_llm.available:
        await hub.broadcast({"type": "log", "level": "info",
                              "msg": f"insights engine: local LLM available ({local_llm.model})"})

    if not os.getenv("NEWSAPI_KEY"):
        await hub.broadcast({"type": "log", "level": "warn",
                              "msg": "News disabled — paste a NEWSAPI_KEY into .env "
                                     "(free at newsapi.org/register)"})

    weather = WeatherService(hub=hub)
    agenda = Agenda(hub=hub)
    gcal = GoogleCalendar(root=ROOT, hub=hub)
    if gcal.credentials_present():
        await hub.broadcast({"type": "log", "level": "info",
                              "msg": "Google credentials found — calendar sync armed "
                                     + ("(connected)" if gcal.token_present()
                                        else "(POST /api/calendar/connect to authorize)")})

    team = build_team(hub=hub, brain=brain, speaker=tts, local_brain=local_brain)
    insights = InsightsEngine(hub=hub, sysmon=sysmon, news=news, agenda=agenda,
                              team=team, local_llm=local_llm, brain=brain, tts=tts)
    threats = ThreatEngine(hub=hub, sysmon=sysmon, news=news, agenda=agenda,
                           insights=insights, team=team)
    insights.threats = threats
    scheduler = schedule_all(team=team, news=news, sysmon=sysmon, hub=hub,
                             agenda=agenda, tts=tts,
                             insights=insights if os.getenv("JARVIS_INSIGHTS", "1") != "0" else None,
                             threats=threats, gcal=gcal)
    voice = VoiceLoop(hub=hub, brain=brain, team=team,
                      services={"agenda": agenda, "threats": threats,
                                "insights": insights}) \
        if os.getenv("JARVIS_VOICE") == "1" else None

    state.update({
        "hub": hub, "team": team, "brain": brain, "tts": tts,
        "sysmon": sysmon, "news": news, "scheduler": scheduler, "voice": voice,
        "local_brain": local_brain, "weather": weather, "agenda": agenda,
        "local_llm": local_llm, "insights": insights,
        "threats": threats, "gcal": gcal,
    })

    sysmon_task = asyncio.create_task(sysmon.run())
    voice_task = asyncio.create_task(voice.run()) if voice else None
    scheduler.start()

    await hub.broadcast({
        "type": "system", "event": "online",
        "msg": f"JARVIS online — brain mode: {mode.upper()}. All agents engaged.",
    })

    try:
        yield
    finally:
        sysmon_task.cancel()
        if voice_task:
            voice_task.cancel()
        scheduler.shutdown(wait=False)
        tts.stop()


app = FastAPI(lifespan=lifespan, title="JARVIS")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def root():
    return FileResponse(STATIC / "index.html")


@app.get("/landing")
async def landing():
    return FileResponse(STATIC / "landing.html")


class Ask(BaseModel):
    prompt: str
    agent: str | None = None


@app.post("/api/ask")
async def ask(req: Ask):
    team = state["team"]
    hub: Hub = state["hub"]
    tts: TTSPlayer = state["tts"]

    key = (req.agent or "jarvis").lower()
    target = team.get(key)
    if not target:
        return {"error": f"Unknown agent: {req.agent}",
                "available": list(team.keys())}

    # ── browser intent fast-path ──────────────────────────────────
    intent = browser.parse_browser_intent(req.prompt)
    if intent:
        try:
            result = browser.open_url(intent["url"], fullscreen=intent["fullscreen"])
            success = bool(result.get("opened"))
        except Exception as e:
            result = {"opened": False, "error": str(e)}
            success = False

        fs = " in fullscreen" if intent["fullscreen"] else ""
        reply = (
            f"Opening {intent['name']}{fs}, sir."
            if success
            else f"Unable to open {intent['name']}. {result.get('error', '')}"
        )

        await target._emit("reply", q=req.prompt[:140], a=reply)
        await hub.broadcast({
            "type": "browser", "event": "opened",
            "url": intent["url"], "name": intent["name"],
            "fullscreen": intent["fullscreen"], **result,
        })
        if tts.enabled:
            await tts.say(reply)
        return {"reply": reply, "agent": target.name, "browser": result}

    # ── normal agent flow ─────────────────────────────────────────
    reply = await target.handle(req.prompt)
    return {"reply": reply, "agent": target.name, "codename": target.codename}


class BrowserReq(BaseModel):
    url: str
    fullscreen: bool = False
    app_mode: bool = True


@app.post("/api/browser/open")
async def browser_open(req: BrowserReq):
    result = browser.open_url(req.url, fullscreen=req.fullscreen, app_mode=req.app_mode)
    await state["hub"].broadcast({
        "type": "browser", "event": "opened",
        "url": req.url, "name": req.url, **result,
    })
    return result


@app.post("/api/speak")
async def speak(payload: dict):
    text = (payload or {}).get("text", "").strip()
    if not text:
        return {"spoken": False, "error": "empty text"}
    await state["tts"].say(text)
    return {"spoken": True}


@app.get("/api/status")
async def status():
    return {
        "agents": [a.snapshot() for a in state["team"].values()],
        "metrics": state["sysmon"].latest,
        "news_count": len(state["news"].recent),
        "tts_enabled": state["tts"].enabled,
        "brain_mode": state["brain"].mode,
    }


@app.post("/api/news/refresh")
async def refresh_news():
    items = await state["news"].fetch_top()
    return {"count": len(items)}


@app.get("/api/weather")
async def weather(lat: float | None = None, lon: float | None = None):
    data = await state["weather"].get(lat=lat, lon=lon)
    return data or {"error": "unavailable"}


@app.get("/api/agenda")
async def agenda():
    return state["agenda"].snapshot()


@app.get("/api/connectivity")
async def connectivity_endpoint():
    return await connectivity.gather()


@app.get("/api/processes")
async def processes_endpoint():
    """Top processes by CPU and memory, aggregated by name (chrome ×40 → one row)."""
    import psutil
    agg: dict[str, dict] = {}
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            info = p.info
            name = (info.get("name") or "?").replace(".exe", "")[:26]
            mem = info.get("memory_info")
            a = agg.setdefault(name, {"name": name, "cpu": 0.0, "mem_mb": 0.0, "count": 0})
            a["cpu"] += info.get("cpu_percent") or 0.0
            a["mem_mb"] += (mem.rss / 1048576) if mem else 0.0
            a["count"] += 1
        except Exception:
            continue
    SKIP = {"System Idle Process", "System", "Registry", "?", "smss", "Memory Compression"}
    rows = [r for r in agg.values() if r["name"] not in SKIP]
    for r in rows:
        r["cpu"] = round(r["cpu"], 1)
        r["mem_mb"] = round(r["mem_mb"], 1)
    by_mem = sorted(rows, key=lambda r: r["mem_mb"], reverse=True)[:6]
    by_cpu = sorted(rows, key=lambda r: r["cpu"], reverse=True)[:6]
    # psutil's first sample reports 0% for everything — fall back to memory order
    if by_cpu and by_cpu[0]["cpu"] == 0:
        by_cpu = by_mem
    return {"top_cpu": by_cpu, "top_mem": by_mem}


@app.get("/api/insights")
async def insights_endpoint():
    eng: InsightsEngine = state["insights"]
    return {"insights": eng.recent[:8], "engine": eng.status,
            "presence": eng.presence}


@app.get("/api/aiops")
async def aiops_endpoint():
    return state["insights"].aiops_status()


class PresenceReq(BaseModel):
    state: str
    motion: float = 0.0


@app.post("/api/presence")
async def presence_endpoint(req: PresenceReq):
    allowed = {"active", "idle", "away", "no-user", "offline"}
    st = req.state if req.state in allowed else "no-user"
    result = await state["insights"].update_presence(st, req.motion)
    return {"ok": True, **result}


@app.get("/api/threats")
async def threats_endpoint():
    return state["threats"].snapshot()


class AlertReq(BaseModel):
    severity: str = "info"          # info | warning | critical | emergency
    title: str
    detail: str | None = ""
    source: str | None = "manual"
    action: str | None = ""


@app.post("/api/alert")
async def alert_endpoint(req: AlertReq):
    sev = req.severity if req.severity in ("info", "warning", "critical", "emergency") else "info"
    await state["hub"].broadcast({
        "type": "alert", "severity": sev, "title": req.title[:140],
        "detail": (req.detail or "")[:240], "source": req.source or "manual",
        "action": (req.action or "")[:140],
    })
    return {"ok": True, "severity": sev}


@app.post("/api/briefing")
async def briefing_endpoint():
    return await state["insights"].briefing(speak=True)


@app.get("/api/calendar/status")
async def calendar_status():
    return state["gcal"].status()


@app.post("/api/calendar/connect")
async def calendar_connect():
    """Kick the OAuth flow (opens a browser on this machine). Then syncs."""
    result = await state["gcal"].connect()
    if result.get("ok"):
        events = await state["gcal"].sync()
        if events is not None:
            state["agenda"].set_events(events)
            result["synced"] = len(events)
    return result


class WaitlistReq(BaseModel):
    name: str
    email: str
    organization: str | None = ""
    use_case: str | None = ""
    intent: str | None = "waitlist"


@app.post("/api/waitlist")
async def waitlist_endpoint(req: WaitlistReq):
    import json as _json
    import time as _time
    entry = req.model_dump()
    entry["ts"] = _time.time()
    with open(ROOT / "waitlist.jsonl", "a", encoding="utf-8") as f:
        f.write(_json.dumps(entry) + "\n")
    await state["hub"].broadcast({
        "type": "log", "level": "info",
        "msg": f"waitlist signup ({entry.get('intent')}): {req.name} <{req.email}>",
    })
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    hub: Hub = state["hub"]
    q = hub.subscribe()
    try:
        await ws.send_json({
            "type": "snapshot",
            "agents": [a.snapshot() for a in state["team"].values()],
        })
        for msg in hub.recent[-30:]:
            await ws.send_json(msg)
        while True:
            msg = await q.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(q)
