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
from knowledge import KnowledgeHub
from llm_local import LocalLLM
from local_brain import LocalBrain
from memory import OperatorMemory
from orchestrator import Orchestrator
from productivity import Productivity
from roadmap import Roadmap
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

    # restore the operator's brain choice across restarts (model_pref.json):
    # if they last picked a local/Ollama model, keep routing replies to it.
    if local_llm.pref_force_local and local_llm.available:
        brain.force_local = True
        await hub.broadcast({"type": "log", "level": "info",
                              "msg": f"brain restored → local model {local_llm.model} "
                                     "(Claude not used for replies)"})
    elif local_llm.pref_force_local and not local_llm.available:
        await hub.broadcast({"type": "log", "level": "warn",
                              "msg": "saved brain is a local model but the Ollama endpoint "
                                     "is offline — start Ollama and re-select, or replies "
                                     "fall back to Claude"})

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
    memory = OperatorMemory(hub=hub, brain=brain)
    insights.memory = memory
    productivity = Productivity(hub=hub, insights=insights, agenda=agenda,
                                memory=memory)
    orchestrator = Orchestrator(hub=hub, team=team, sysmon=sysmon,
                                threats=threats, agenda=agenda,
                                insights=insights, memory=memory,
                                productivity=productivity)
    knowledge = KnowledgeHub(memory=memory, insights=insights, team=team,
                             threats=threats, agenda=agenda, news=news,
                             brain=brain)
    scheduler = schedule_all(team=team, news=news, sysmon=sysmon, hub=hub,
                             agenda=agenda, tts=tts,
                             insights=insights if os.getenv("JARVIS_INSIGHTS", "1") != "0" else None,
                             threats=threats, gcal=gcal, memory=memory,
                             orchestrator=orchestrator)
    voice = VoiceLoop(hub=hub, brain=brain, team=team,
                      services={"agenda": agenda, "threats": threats,
                                "insights": insights, "memory": memory}) \
        if os.getenv("JARVIS_VOICE") == "1" else None

    state.update({
        "hub": hub, "team": team, "brain": brain, "tts": tts,
        "sysmon": sysmon, "news": news, "scheduler": scheduler, "voice": voice,
        "local_brain": local_brain, "weather": weather, "agenda": agenda,
        "local_llm": local_llm, "insights": insights,
        "threats": threats, "gcal": gcal, "memory": memory,
        "productivity": productivity, "orchestrator": orchestrator,
        "knowledge": knowledge,
    })
    state["roadmap"] = Roadmap(state)

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


def _scan_processes() -> list[dict]:
    """Synchronous psutil sweep — MUST run in an executor, never on the loop."""
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
    return [r for r in agg.values() if r["name"] not in SKIP]


_gpu_cache = {"ts": 0.0, "pct": None}


def _read_gpu() -> float | None:
    """Real GPU utilization via nvidia-smi; None when no NVIDIA GPU present."""
    import subprocess
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


@app.get("/api/processes")
async def processes_endpoint():
    """Top processes by CPU and memory, aggregated by name (chrome ×40 → one row)."""
    import time as _t
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, _scan_processes)
    for r in rows:
        r["cpu"] = round(r["cpu"], 1)
        r["mem_mb"] = round(r["mem_mb"], 1)
    by_mem = sorted(rows, key=lambda r: r["mem_mb"], reverse=True)[:6]
    by_cpu = sorted(rows, key=lambda r: r["cpu"], reverse=True)[:6]
    # psutil's first sample reports 0% for everything — fall back to memory order
    if by_cpu and by_cpu[0]["cpu"] == 0:
        by_cpu = by_mem
    # real GPU (cached 10s); null when machine has no NVIDIA GPU
    if _t.time() - _gpu_cache["ts"] > 10:
        _gpu_cache["pct"] = await loop.run_in_executor(None, _read_gpu)
        _gpu_cache["ts"] = _t.time()
    return {"top_cpu": by_cpu, "top_mem": by_mem, "gpu": _gpu_cache["pct"]}


# ── real personal notes (persisted to notes.json, gitignored) ────
NOTES_FILE = ROOT / "notes.json"


def _load_notes() -> list[dict]:
    import json as _json
    try:
        return _json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_notes(notes: list[dict]) -> None:
    import json as _json
    NOTES_FILE.write_text(_json.dumps(notes, indent=1, ensure_ascii=False),
                          encoding="utf-8")


@app.get("/api/notes")
async def notes_get():
    return {"notes": _load_notes()}


class NoteReq(BaseModel):
    text: str


@app.post("/api/notes")
async def notes_add(req: NoteReq):
    import time as _t
    import uuid as _uuid
    text = req.text.strip()[:200]
    if not text:
        return {"ok": False}
    notes = _load_notes()
    notes.insert(0, {"id": _uuid.uuid4().hex[:8], "text": text, "ts": _t.time()})
    _save_notes(notes[:30])
    return {"ok": True}


@app.delete("/api/notes/{note_id}")
async def notes_delete(note_id: str):
    notes = [n for n in _load_notes() if n.get("id") != note_id]
    _save_notes(notes)
    return {"ok": True}


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
    try:
        await state["memory"].record_presence(st, result.get("prev", "offline"))
    except Exception:
        pass
    return {"ok": True, **result}


@app.get("/api/threats")
async def threats_endpoint():
    return state["threats"].snapshot()


_vision_state = {"ts": 0.0, "n": 0}

# Rotating lenses so frequent webcam comments stay varied, not repetitive.
_VISION_LENSES = [
    "note what the operator appears to be doing or their current state (present and working, away, on a call, stepped out, etc)",
    "comment briefly on the operator's focus or energy level, like a butler checking in",
    "note the lighting or environment around the operator and whether conditions look good for work",
    "give a short, warm morale-boosting remark based on how engaged the operator looks",
    "note whether the operator seems present and attentive, or distracted / away from the desk",
]


class VisionReq(BaseModel):
    frame: str            # data URL or raw base64 JPEG
    prompt: str | None = None
    speak: bool = True


@app.post("/api/vision/analyze")
async def vision_analyze(req: VisionReq):
    """Real-time webcam insight via Claude. The browser sends a frame; Claude
    (the operator's own subscription, via the CLI) describes it in one sentence,
    which is displayed + spoken. Rate-limited to protect Claude usage."""
    import base64 as _b64
    import time as _t
    now = _t.time()
    gap = now - _vision_state["ts"]
    if gap < 12:
        return {"ok": False, "error": "rate-limited", "retry_in": round(12 - gap, 1)}
    _vision_state["ts"] = now

    try:
        raw = req.frame.split(",", 1)[-1]
        data = _b64.b64decode(raw)
        if len(data) < 500:
            return {"ok": False, "error": "empty frame"}
    except Exception:
        return {"ok": False, "error": "bad frame"}

    path = ROOT / "vision_frame.jpg"
    path.write_bytes(data)

    lens = _VISION_LENSES[_vision_state["n"] % len(_VISION_LENSES)]
    _vision_state["n"] += 1
    mem: OperatorMemory = state["memory"]

    # Recognition block: description-based (text sketch from enrollment),
    # NOT biometric. Claude judges whether the person matches the sketch.
    recog = ""
    if mem.enrolled:
        recog = (
            "\nKNOWN OPERATOR (text description, non-biometric): "
            f"{mem.data['profile']['appearance']}\n"
            "Start your reply with exactly one tag on its own line: "
            "WHO: OPERATOR (matches the description) | WHO: GUEST (a different "
            "person) | WHO: MULTIPLE (more than one person) | WHO: NONE (nobody "
            "visible). Then give the observation sentence on the next line.")

    prompt = req.prompt or (
        "You are JARVIS observing your operator through their webcam feed. "
        f"In ONE concise, warm butler-style sentence, {lens}. "
        "Do NOT state the person's name or identity — presence and activity only. "
        "Vary your phrasing from a typical status line." + recog)
    try:
        obs = await state["brain"].see(str(path), prompt, timeout=60)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not obs or obs.lstrip().startswith("["):
        return {"ok": False, "error": "vision unavailable — Claude CLI required (set CLAUDE_BIN)"}

    # parse WHO tag when recognition was requested
    who = "unknown"
    text = obs.strip()
    if mem.enrolled:
        import re as _re
        m = _re.search(r"WHO:\s*(OPERATOR|GUEST|MULTIPLE|NONE)", text, _re.IGNORECASE)
        if m:
            who = m.group(1).lower()
            text = _re.sub(r"WHO:\s*(OPERATOR|GUEST|MULTIPLE|NONE)\s*", "", text,
                           flags=_re.IGNORECASE).strip()
    text = text[:280]

    await mem.record_observation(text, who=who)
    await state["hub"].broadcast({
        "type": "insight", "insight": text, "recommendation": "",
        "severity": "info", "confidence": 90, "source": "vision-ai", "ts": now,
    })
    await state["hub"].broadcast({"type": "vision-obs", "text": text, "who": who})
    if who in ("guest", "multiple"):
        await state["hub"].broadcast({
            "type": "alert", "severity": "warning",
            "title": "Unrecognized person at the console" if who == "guest"
                     else "Multiple people at the console",
            "detail": text, "source": "vision-ai · recognition",
            "action": "Verify who is at the workstation.",
        })
    if req.speak and state["tts"].enabled:
        await state["tts"].say(text)
    return {"ok": True, "observation": text, "who": who}


class EnrollReq(BaseModel):
    frame: str
    name: str | None = None


@app.post("/api/memory/enroll")
async def memory_enroll(req: EnrollReq):
    """'Remember me': Claude writes a short TEXT appearance sketch of the person
    in frame (no biometrics), stored in operator_memory.json for description-
    based recognition."""
    import base64 as _b64
    try:
        data = _b64.b64decode(req.frame.split(",", 1)[-1])
        if len(data) < 500:
            return {"ok": False, "error": "empty frame"}
    except Exception:
        return {"ok": False, "error": "bad frame"}
    path = ROOT / "vision_frame.jpg"
    path.write_bytes(data)
    desc = await state["brain"].see(
        str(path),
        "Describe the person in this frame in 2-3 short factual sentences for "
        "later TEXT-based re-identification: hair, glasses/no glasses, general "
        "build, clothing style, notable accessories. No name, no age guess, no "
        "ethnicity. If nobody is clearly visible reply exactly: NOBODY",
        timeout=60)
    low = (desc or "").lower()
    if (not desc or desc.lstrip().startswith("[") or "NOBODY" in desc.upper()[:12]
            or any(s in low for s in ("no image", "not attached", "cannot read",
                                       "nothing to analyze", "unable to", "no file"))):
        return {"ok": False, "error": "couldn't read the camera frame — ensure the camera is live and retry"}
    mem: OperatorMemory = state["memory"]
    await mem.enroll(desc.strip(), name=req.name)
    who_name = req.name or "sir"
    line = f"Enrollment complete — I will remember you, {who_name}."
    await state["hub"].broadcast({"type": "log", "level": "info",
                                  "msg": f"memory: operator enrolled ({who_name})"})
    if state["tts"].enabled:
        await state["tts"].say(line)
    return {"ok": True, "appearance": desc.strip()[:600]}


@app.get("/api/memory")
async def memory_get():
    return state["memory"].snapshot()


class FactReq(BaseModel):
    text: str


@app.post("/api/memory/fact")
async def memory_fact(req: FactReq):
    await state["memory"].add_fact(req.text, source="manual")
    return {"ok": True}


@app.post("/api/memory/forget")
async def memory_forget():
    await state["memory"].forget()
    await state["hub"].broadcast({"type": "log", "level": "warn",
                                  "msg": "memory: operator memory wiped"})
    return {"ok": True}


@app.post("/api/memory/speak")
async def memory_speak():
    """JARVIS says what it knows about the operator."""
    text = state["memory"].summary_text()
    await state["hub"].broadcast({
        "type": "insight", "insight": text, "recommendation": "",
        "severity": "info", "confidence": 88, "source": "memory",
        "ts": __import__("time").time(),
    })
    if state["tts"].enabled:
        await state["tts"].say(text)
    return {"ok": True, "summary": text}


@app.get("/api/models")
async def models_endpoint():
    llm = state["local_llm"]
    brain = state["brain"]
    # which brain is actually answering right now
    active = "local:" + str(llm.model) if getattr(brain, "force_local", False) else \
             ("claude" if brain.mode == "llm" else brain.mode)
    return {
        "local": await llm.list_models(),
        "selected": llm.model,
        "local_available": llm.available,
        "endpoint": llm.base_url,
        "cloud_claude": brain.mode == "llm",
        "brain_mode": brain.mode,
        "active_brain": active,
        "force_local": getattr(brain, "force_local", False),
    }


class ModelSel(BaseModel):
    model: str   # a local model name, or "claude" to switch back to the cloud brain


@app.post("/api/models/select")
async def models_select(req: ModelSel):
    llm = state["local_llm"]
    brain = state["brain"]
    name = req.model.strip()
    if name.lower() == "claude":
        brain.force_local = False
        llm.save_pref(force_local=False)   # persist: next boot uses Claude
        await state["hub"].broadcast({"type": "log", "level": "info",
                                      "msg": "brain switched → Claude (cloud)"})
        await state["hub"].broadcast({"type": "models-updated"})
        return {"ok": True, "selected": "claude", "active_brain": "claude"}
    # a local model: point the client at it, mark available, and REDIRECT the
    # brain to actually use it for replies (verify with a quick probe first)
    llm.model = name
    ok = await llm.probe()          # confirms endpoint + keeps model
    llm.model = name                # probe may overwrite; pin the chosen one
    llm.available = True
    llm.save_pref(force_local=True)  # persist: next boot keeps this brain
    brain.force_local = True
    await state["hub"].broadcast({
        "type": "log", "level": "info",
        "msg": f"brain switched → local model {name}"
               + ("" if ok else " (endpoint probe failed — is Ollama running?)"),
    })
    await state["hub"].broadcast({"type": "models-updated"})
    return {"ok": True, "selected": name, "active_brain": "local:" + name,
            "endpoint_ok": ok}


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
        mails = await state["gcal"].fetch_emails()
        if mails is not None:
            state["agenda"].set_emails(mails)
            result["emails"] = len(mails)
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


# ═══ PHASE 2 · MULTI-AGENT INTELLIGENCE + PRODUCT EVOLUTION ═══════

@app.get("/api/orchestrator")
async def orchestrator_endpoint():
    return state["orchestrator"].snapshot()


@app.get("/api/roadmap")
async def roadmap_endpoint():
    return state["roadmap"].snapshot()


@app.get("/api/recommendations")
async def recommendations_endpoint():
    return {"recommendations": state["insights"].recommendations(
        productivity=state.get("productivity"),
        orchestrator=state.get("orchestrator"))}


@app.get("/api/productivity")
async def productivity_get():
    return state["productivity"].snapshot()


class ProdReq(BaseModel):
    action: str            # task | task_done | focus_start | focus_stop
    text: str | None = None


@app.post("/api/productivity")
async def productivity_post(req: ProdReq):
    p = state["productivity"]
    if req.action == "task":
        return await p.set_task(req.text or "")
    if req.action == "task_done":
        return await p.complete_task()
    if req.action == "focus_start":
        return await p.focus_start()
    if req.action == "focus_stop":
        return await p.focus_stop()
    return {"error": f"unknown action: {req.action}"}


@app.get("/api/knowledge/stats")
async def knowledge_stats():
    return state["knowledge"].stats()


@app.get("/api/knowledge/search")
async def knowledge_search(q: str = ""):
    return state["knowledge"].search(q)


class AskKB(BaseModel):
    query: str


@app.post("/api/knowledge/ask")
async def knowledge_ask(req: AskKB):
    return await state["knowledge"].ask(req.query.strip()[:200])


@app.post("/api/threats/ack/{event_id}")
async def threats_ack(event_id: str):
    ok = state["threats"].acknowledge(event_id)
    if ok:
        await state["hub"].broadcast({"type": "log", "level": "info",
                                      "msg": f"incident {event_id} acknowledged by operator"})
    return {"ok": ok}


@app.get("/api/models/stats")
async def models_stats():
    """Local AI Control Center: running models (RAM/VRAM/context via Ollama
    /api/ps), measured latency + tokens/sec, and host memory headroom."""
    import psutil
    llm = state["local_llm"]
    vm = psutil.virtual_memory()
    return {
        "running": await llm.running(),
        "perf": llm.perf(),
        "selected": llm.model,
        "force_local": getattr(state["brain"], "force_local", False),
        "endpoint_online": llm.available,
        "host_ram_used_gb": round((vm.total - vm.available) / 1e9, 1),
        "host_ram_total_gb": round(vm.total / 1e9, 1),
    }


_agenda_summary_cache = {"ts": 0.0, "text": None, "sig": None}


@app.get("/api/agenda/summary")
async def agenda_summary():
    """AI-generated one-paragraph schedule summary (cached 10 min per-day-shape)."""
    import time as _t
    snap = state["agenda"].snapshot()
    intel = snap.get("intel") or {}
    if intel.get("source") != "google":
        return {"summary": None, "reason": "calendar not connected"}
    sig = f"{intel.get('meetings_today')}·{intel.get('next_title')}·{intel.get('conflicts')}"
    now = _t.time()
    if (_agenda_summary_cache["text"] and _agenda_summary_cache["sig"] == sig
            and now - _agenda_summary_cache["ts"] < 600):
        return {"summary": _agenda_summary_cache["text"], "cached": True}
    facts = (
        f"{intel.get('meetings_today', 0)} meetings today ({intel.get('meeting_minutes', 0)} min, "
        f"{intel.get('density')}), conflicts: {intel.get('conflicts', 0)}, "
        f"largest free block {intel.get('largest_free_block_min', 0)} min. "
        + (f"Currently in: {intel['current_title']} (ends in {intel['current_ends_min']} min). "
           if intel.get("current_title") else "")
        + (f"Next: {intel['next_title']} in {intel['next_in_min']:.0f} min."
           if intel.get("next_title") else "Nothing else scheduled.")
    )
    text = await state["brain"].think(
        "Summarize this schedule in ONE crisp butler-toned sentence with the "
        "single most useful planning insight: " + facts,
        system="You are JARVIS summarizing sir's calendar. One sentence, plain text.",
        agent="captain", fast=True, timeout=45)
    if not text or text.lstrip().startswith("[brain"):
        text = facts
    text = text.strip()[:300]
    _agenda_summary_cache.update(ts=now, text=text, sig=sig)
    return {"summary": text}


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
