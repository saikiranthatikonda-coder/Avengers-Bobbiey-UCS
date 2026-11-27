"""Fast-path intents — deterministic answers and actions with NO LLM call.

The real-time engine (realtime.py) tries these before any model is invoked:
a match answers from live data (telemetry, network, weather, agenda, threats,
news, fleet, orchestrator) or performs an allow-listed action (dashboard
navigation, browser sites, Windows apps/folders/media keys, lock), typically
in milliseconds. Anything unmatched goes to an agent (LLM).

Every handler is async (ctx, text, low) -> Result | None. `ctx` is the shared
server `state` dict, so new subsystems in later phases plug in by adding a
handler here — voice, typed and UI commands inherit them automatically.

Result keys: say (str, required) · agent (who answers) · ui (list of hub
messages, e.g. dash-cmd) · action (dict, audited) · slow (bool: the engine
acknowledges instantly before running) · run (async callable for slow work).
"""

import re
import time
from datetime import datetime

import browser
import win_actions

_SECTIONS = {
    "threat": "threats", "security": "threats", "map": "map", "global": "map",
    "agenda": "agenda", "calendar": "agenda", "schedule": "agenda",
    "insight": "insights", "intel": "insights", "news": "news", "feed": "news",
    "camera": "camera", "operator": "camera", "roster": "roster", "agent": "roster",
    "readiness": "readiness", "mission": "readiness",
}

CANCEL_RE = re.compile(r"^\s*(stop|cancel|abort|never ?mind|shut up|quiet|silence|enough|"
                       r"stop talking|be quiet|that's enough)\b[\s.!]*$", re.I)


def is_cancel(text: str) -> bool:
    return bool(CANCEL_RE.match(text or ""))


def _r(say, agent=None, **kw) -> dict:
    return {"say": say, "agent": agent, **kw}


# ── live-data answers ────────────────────────────────────────────
async def i_conversation(ctx, text, low):
    tts = ctx.get("tts")
    if tts is None or not hasattr(tts, "set_conversation_mode"):
        return None
    on = re.search(r"\b(conversation|focus) mode (on|enable|start)\b|\b(mute|silence|stop) (the )?(background|event|other agents?|announcements?)( voices?| stream| speech)?\b", low)
    off = re.search(r"\b(conversation|focus) mode (off|disable|stop)\b|\b(unmute|resume|enable) (the )?(background|event|other agents?|announcements?)( voices?| stream| speech)?\b", low)
    if not (on or off):
        return None
    tts.set_conversation_mode(bool(on))
    if ctx.get("hub"):
        await ctx["hub"].broadcast({"type": "conversation", "on": bool(on)})
    return _r("Conversation mode on. It's just you and me, sir, background voices are silenced." if on
              else "Conversation mode off. The team's announcements are back on.",
              action={"name": "voice.conversation_mode", "target": "on" if on else "off"})




# ── Computer Use from chat / voice ────────────────────────────────
# "Jarvis, use the computer to …" → a two-step confirm, then the SAME entry the dashboard and
# AI Core use (state["computeruse"].submit_task): commander role + host machine only, and every
# Computer Use safety gate (sensitive-action approvals, pause/stop/takeover, privacy guard,
# step/time limits, audit) still applies inside the Computer Use engine. Nothing is bypassed.
_CU_START = (
    re.compile(r"\b(?:use|take (?:over|control of)|control|operate|drive)\s+(?:the|my|this)\s+"
               r"(?:computer|pc|laptop|desktop|screen|mouse(?: and keyboard)?)\s+(?:to|and)\s+(.{3,600}?)[.!]?$", re.I),
    re.compile(r"^(?:(?:hey |ok )?jarvis[, ]+)?(?:start (?:a )?)?computer[- ]use(?: task)?[:,]?\s+(?:to\s+)?(.{3,600}?)[.!]?$",
               re.I),
    # v8.6 · more natural voice phrasings: "on my computer, open notepad and type hello",
    # "open excel and make a budget sheet on my laptop for me"
    re.compile(r"^(?:(?:hey |ok )?\w+[, ]+)?(?:can you |please |could you )?on (?:my|the|this) (?:computer|pc|laptop|desktop)[, ]+"
               r"(.{3,600}?)[.!?]?$", re.I),
    re.compile(r"^(?:(?:hey |ok )?\w+[, ]+)?(?:can you |please |could you )?(.{3,600}?) on (?:my|the|this) "
               r"(?:computer|pc|laptop|desktop|screen) for me[.!?]?$", re.I),
)
_CU_STATUS = re.compile(r"\b(status of (?:the |my )?computer[- ]use|computer[- ]use (?:task )?(?:status|progress)|"
                        r"what(?:'s| is) (?:the )?computer[- ]use (?:task )?doing|how(?:'s| is) (?:the )?computer[- ]use"
                        r"(?: task)? going|is computer[- ]use (?:still )?running|what is the computer doing)\b")
_CU_STOP = re.compile(r"\b(?:stop|cancel|abort|halt|kill)\s+(?:the\s+)?computer[- ]use(?: task)?\b")
_CU_CONFIRM = re.compile(r"^\W*(?:(?:hey |ok )?jarvis[, ]+)?(?:confirm(?:ed)?|yes(?:,? (?:go ahead|do it|start(?: it)?|confirm))?|"
                         r"go ahead|start it|do it)\W*$")
_CU_DECLINE = re.compile(r"^\W*(?:(?:hey |ok )?jarvis[, ]+)?(?:no|nope|don't|do not|cancel that|never ?mind|abort that)\b")
CU_CONFIRM_S = 60


def _current_cmd():
    try:
        import realtime
        return realtime.CURRENT_CMD.get()
    except Exception:
        return None


def _cu_gate(ctx) -> str | None:
    """Why this caller may NOT start Computer Use (None = allowed). Commander + host only."""
    if (ctx.get("rbac") or {}).get("role", "commander") != "commander":
        return "Computer Use needs the commander role, sir."
    cmd = _current_cmd()
    if cmd is None or not getattr(cmd, "host", False):
        return "Computer Use can only be started from the host machine itself, sir."
    return None


async def i_computeruse(ctx, text, low):
    eng = ctx.get("computeruse")
    pending = ctx.get("computeruse_chat_pending")
    if pending and time.time() - pending["ts"] > CU_CONFIRM_S:
        ctx.pop("computeruse_chat_pending", None)
        pending = None
    if pending and _CU_DECLINE.match(low):
        ctx.pop("computeruse_chat_pending", None)
        return _r("Understood, I won't touch the computer.", action={"name": "computeruse.request.declined"})
    if pending and _CU_CONFIRM.match(low):
        ctx.pop("computeruse_chat_pending", None)
        why = _cu_gate(ctx)
        if why or eng is None:
            return _r(why or "Computer Use isn't loaded on this install, sir.",
                      action={"name": "computeruse.submit", "ok": False})
        cmd = _current_cmd()
        src = "voice" if getattr(cmd, "source", "") in ("voice", "clap") else "chat"
        goal = pending["text"]

        async def run(progress):
            await progress("starting Computer Use…")
            r = await eng.submit_task(goal, source=src)
            if not r.get("ok"):
                return f"Computer Use didn't start: {r.get('error', 'refused')}."
            return (f"Computer Use is running with {r.get('adapter', 'the vision model')}. Sensitive steps will pause "
                    "for your approval on the Computer Use panel, and STOP always works.")
        return _r("Starting it now, sir.", slow=True, run=run,
                  action={"name": "computeruse.submit", "target": goal[:80]},
                  ui=[{"type": "dash-cmd", "target": "computeruse"}])
    if _CU_STOP.search(low):
        if eng is None:
            return _r("Computer Use isn't loaded on this install, sir.")
        res = eng.control("stop", "operator said stop (chat/voice)")          # stopping is always allowed
        return _r("Computer Use stopped." if res.get("ok") else "Computer Use has nothing running, sir.",
                  action={"name": "computeruse.stop", "ok": bool(res.get("ok"))})
    if _CU_STATUS.search(low):
        try:
            import dashboard_context
            return _r(dashboard_context.computeruse_status_line(ctx))
        except Exception:
            return None
    goal = None
    for rx in _CU_START:
        m = rx.search(re.sub(r"\s+", " ", text).strip())          # keep the operator's casing
        if m:
            goal = m.group(1).strip()
            break
    if not goal:
        return None
    if eng is None:
        return _r("Computer Use isn't loaded on this install, sir.")
    why = _cu_gate(ctx)
    if why:
        return _r(why, action={"name": "computeruse.request", "ok": False})
    ctx["computeruse_chat_pending"] = {"text": goal[:600], "ts": time.time()}
    return _r(f"I'll use the computer to {goal[:200].rstrip('.')}. Sensitive steps will still pause for your "
              "approval and STOP always works. Say confirm to start, or no to cancel.",
              action={"name": "computeruse.request", "target": goal[:80]})


# ── v8.6 · voice settings by voice: mute all agents, hands-free on/off ──────────
_MUTE_AG = re.compile(r"\b(mute|silence|quiet) (all )?(the |my )?(agents?|avengers|team|agent voices?|voices of the agents)\b")
_UNMUTE_AG = re.compile(r"\b(unmute|un-mute) (all )?(the |my )?(agents?|avengers|team|agent voices?)\b|"
                        r"\bagent voices? (back )?on\b|\bturn (the )?agent voices? (back )?on\b")


async def i_agentvoice(ctx, text, low):
    tts = ctx.get("tts")
    if tts is None or not hasattr(tts, "set_muted"):
        return None
    if _UNMUTE_AG.search(low):
        tts.set_muted(False)
        if ctx.get("hub"):
            await ctx["hub"].broadcast({"type": "audio", **tts.audio_state()})
        return _r("Agent voices are back on, sir.", action={"name": "voice.agents_muted", "target": "off"})
    if _MUTE_AG.search(low):
        if ctx.get("hub"):
            await ctx["hub"].broadcast({"type": "log", "level": "info", "msg": "agent voices muted — replies stay on screen"})
        tts.set_muted(True)
        if ctx.get("hub"):
            await ctx["hub"].broadcast({"type": "audio", **tts.audio_state()})
        return _r("Agent voices muted. Replies will stay on screen.", action={"name": "voice.agents_muted", "target": "on"})
    return None


_HF = re.compile(r"\b(?:(turn|switch|set) )?hands[- ]?free(?: mode)? (on|off|enable|disable)\b|"
                 r"\b(turn|switch) (on|off) (?:the )?hands[- ]?free\b|\b(enable|disable) (?:the )?hands[- ]?free\b")


async def i_handsfree(ctx, text, low):
    m = _HF.search(low)
    if not m:
        return None
    word = next(g for g in (m.group(2), m.group(4), m.group(5)) if g)
    on = word in ("on", "enable")
    v = ctx.get("voice")
    if v is None or not hasattr(v, "set_handsfree"):
        return _r("Voice isn't running on this install, so there is no hands-free to switch, sir.")
    if (ctx.get("rbac") or {}).get("role", "commander") != "commander":
        return _r("Changing voice settings needs the commander role, sir.")
    await v.set_handsfree(enabled=on)
    return _r("Hands-free on. Just talk, no wake word needed." if on else
              "Hands-free off. Start with an agent's name when you need one.",
              action={"name": "voice.handsfree", "target": "on" if on else "off"})


# ── v8.6 · shared commander memory: "remember that …" / "forget …" ──────────────
_REMEMBER = re.compile(r"^(?:(?:hey |ok )?(?:jarvis|stark|tony|cap|captain|steve|widow|natasha|hawkeye|clint|hulk|banner|thor|vision|doom|bob|bobbiey|team|guys)[, ]+)?(?:please )?(?:remember|note|keep in mind|don't forget)"
                       r"(?: that|:)?\s+(?!to\b)(.{4,400}?)[.!]?$", re.I)
_FORGET = re.compile(r"^(?:(?:hey |ok )?(?:jarvis|stark|tony|cap|captain|steve|widow|natasha|hawkeye|clint|hulk|banner|thor|vision|doom|bob|bobbiey|team|guys)[, ]+)?(?:please )?forget (?:that |the fact that |about )?(.{3,200}?)[.!]?$", re.I)


async def i_remember(ctx, text, low):
    mem = ctx.get("memory")
    if mem is None or not hasattr(mem, "add_fact"):
        return None
    clean = re.sub(r"\s+", " ", text).strip()
    m = _FORGET.match(clean)
    if m and hasattr(mem, "remove_facts"):
        gone = await mem.remove_facts(m.group(1))
        return _r(f"Done. The whole team has forgotten: {'; '.join(gone)[:240]}." if gone
                  else "Nothing in the team's memory matched that, sir.",
                  action={"name": "memory.forget", "target": m.group(1)[:60], "ok": bool(gone)})
    m = _REMEMBER.match(clean)
    if not m:
        return None
    fact = m.group(1).strip()
    fact = fact[:1].upper() + fact[1:]
    if re.search(r"\b(i|i'm|i've|i'd|my|me|mine)\b", fact, re.I):      # first person → keep the commander's words
        fact = f'Commander said: "{fact}"'
    cmd = _current_cmd()
    await mem.add_fact(fact, source=getattr(cmd, "source", None) or "chat")
    return _r(f"Noted, and shared with the whole team: {fact}.", action={"name": "memory.remember", "target": fact[:60]})


# ── v8.6 · drive the dashboard by voice / chat (the UI acts like the operator's own clicks) ──
_MAP_MODES = {"2d": "2d", "tactical": "2d", "2d tactical": "2d", "flat": "2d", "3d": "3d", "globe": "3d", "3d globe": "3d", "orbital": "orbit", "orbit": "orbit",
              "satellite": "sat:sat", "hybrid": "sat:hybrid", "terrain": "sat:terrain", "3d terrain": "sat:terrain",
              "city": "sat:city", "3d city": "sat:city", "night": "sat:night", "night lights": "sat:night",
              "live": "sat:live", "live satellite": "sat:live", "today": "sat:today", "today's imagery": "sat:today",
              "topo": "sat:topo", "topographic": "sat:topo", "dark": "sat:dark", "dark tactical": "sat:dark"}
_THEMES = {"jarvis": "jarvis", "cyan": "jarvis", "stark": "stark", "red": "stark", "iron man": "stark",
           "stealth": "stealth", "mono": "stealth", "doom": "doom", "doomsday": "doom", "green": "doom"}
_MAPWORD = r"(?:the )?(?:global |activity |world )*map"


_REGION = r"(india|indian|telangana|disaster(?: response)?|hazard|threat)"
_REGION_KEY = {"india": "india", "indian": "india", "telangana": "telangana"}
_RM_METRIC = {"temperature": "temp", "temp": "temp", "heat": "temp", "rain": "rain", "rainfall": "rain", "air quality": "aqi",
              "air": "aqi", "aqi": "aqi", "pollution": "aqi", "wind": "wind", "alerts": "alerts", "hazards": "alerts"}


def _region(word: str) -> str:
    return _REGION_KEY.get(word, "disaster")


async def i_region_map(ctx, text, low):
    """v8.8: the INDIA / TELANGANA / DISASTER immersive maps by voice."""
    low = low.rstrip(" .!?")
    m = re.search(rf"\b(?:switch|change|set|turn|put|show)\s+(?:the\s+)?{_REGION}\s+map\s+(?:view\s+)?(?:to|into|on|in)\s+(?:the\s+)?([a-z0-9' ]+?)(?:\s+(?:view|mode))?$", low)
    if m and m.group(2).strip() in _MAP_MODES and _MAP_MODES[m.group(2).strip()].startswith("sat:"):
        reg, mode = _region(m.group(1)), _MAP_MODES[m.group(2).strip()].split(":")[1]
        return _r(f"Switching the {reg} map to {m.group(2).strip()}.", ui=[{"type": "ui", "action": "region-mode", "region": reg, "mode": mode}],
                  action={"name": "dashboard.region_mode", "target": f"{reg}:{mode}"})
    m = re.search(rf"\b(?:show|colou?r|paint)\s+(?:me\s+)?(?:the\s+)?(temperature|temp|heat|rainfall|rain|air quality|air|aqi|pollution|wind|alerts|hazards)\s+(?:on|across|over|for)\s+(?:the\s+)?(india|telangana)(?:\s+map)?$", low)
    if m:
        reg, met = _region(m.group(2)), _RM_METRIC[m.group(1)]
        return _r(f"Colouring the {reg.title()} map by live {m.group(1)}.", ui=[{"type": "ui", "action": "region-metric", "region": reg, "metric": met}],
                  action={"name": "dashboard.region_metric", "target": f"{reg}:{met}"})
    m = re.search(r"\b(?:tour|fly through|fly over)\s+(?:the\s+)?(india|telangana)(?:\s+map)?\b", low) \
        or re.search(r"\b(?:start|run|begin)\s+(?:the\s+)?(india|telangana|disaster)\s+(?:map\s+)?tour\b", low)
    if m:
        reg = _region(m.group(1))
        return _r(f"Starting the {reg} fly-through with live readings at each stop.", ui=[{"type": "ui", "action": "region-tour", "region": reg}],
                  action={"name": "dashboard.region_tour", "target": reg})
    m = re.search(r"\b(?:show|track)\s+(?:the\s+)?(?:aircraft|planes|flights|air traffic)\s+(?:over|above|in)\s+(india|telangana)\b", low)
    if m:
        reg = _region(m.group(1))
        return _r(f"Showing live aircraft over {reg.title()} from OpenSky.", ui=[{"type": "ui", "action": "region-air", "region": reg}],
                  action={"name": "dashboard.region_air", "target": reg})
    return None


async def i_ui(ctx, text, low):
    # map view / mode: "switch the map to satellite", "show the live satellite view", "map to 3d globe"
    m = re.search(rf"\b(?:switch|change|set|turn|put)\s+{_MAPWORD}\s+(?:view\s+)?(?:to|into|on)\s+(?:the\s+)?([a-z0-9' ]+?)(?:\s+(?:view|mode))?$", low) \
        or re.search(rf"\b(?:show|open|give me)\s+(?:me\s+)?(?:the\s+)?([a-z0-9' ]+?)\s+(?:view|mode)\s+(?:of|on)\s+{_MAPWORD}$", low) \
        or re.search(rf"\b(?:show|open)\s+(?:me\s+)?(?:the\s+)?([a-z0-9' ]+?)\s+{_MAPWORD}$", low)
    if m and m.group(1).strip() in _MAP_MODES:
        mode = _MAP_MODES[m.group(1).strip()]
        return _r(f"Switching the map to {m.group(1).strip()} view.", ui=[{"type": "ui", "action": "map-mode", "mode": mode}],
                  action={"name": "dashboard.map_mode", "target": mode})
    # my location on the map
    if re.search(rf"\b(?:show|take me to|go to|fly to|zoom (?:in )?to|find)\s+(?:me\s+)?my (?:location|position|place|house|home)\b", low) \
            or re.search(r"\bwhere am i\b.*\bmap\b|\bmap\b.*\bwhere am i\b", low):
        return _r("Flying the map to your location.", ui=[{"type": "ui", "action": "map-locate"}],
                  action={"name": "dashboard.map_locate"})
    # fly to a place: "show Tokyo on the map", "fly to Paris", "zoom the map to Hyderabad"
    m = re.search(rf"\b(?:show|find|locate)\s+(?:me\s+)?(.{{2,60}}?)\s+on\s+{_MAPWORD}$", low) \
        or re.search(rf"\b(?:fly|zoom|take the map|move the map|pan)\s+(?:in\s+)?(?:the map\s+)?to\s+(.{{2,60}}?)(?:\s+on\s+{_MAPWORD})?$", low)
    if m:
        place = m.group(1).strip(" .?!")
        if place and place not in ("my location", "me"):
            return _r(f"Flying the map to {place}.", ui=[{"type": "ui", "action": "map-fly", "q": place}],
                      action={"name": "dashboard.map_fly", "target": place[:60]})
    # theme
    m = re.search(r"\b(?:switch|change|set|use)\s+(?:the\s+)?(?:dashboard\s+)?(?:theme|colou?rs?|skin)\s+to\s+([a-z ]+?)(?:\s+theme)?$", low) \
        or re.search(r"\b(?:switch to|use|activate)\s+(?:the\s+)?([a-z ]+?)\s+theme$", low)
    if m and m.group(1).strip() in _THEMES:
        th = _THEMES[m.group(1).strip()]
        return _r(f"{th.title()} theme on.", ui=[{"type": "ui", "action": "theme", "theme": th}],
                  action={"name": "dashboard.theme", "target": th})
    # camera full screen
    if re.search(r"\b(?:camera|webcam|video feed)\b.*\bfull ?screen\b|\bfull ?screen\b.*\b(?:camera|webcam)\b", low):
        off = bool(re.search(r"\b(exit|leave|close|stop)\b", low))
        return _r("Leaving full screen." if off else "Camera full screen.",
                  ui=[{"type": "ui", "action": "camfs", "on": not off}], action={"name": "dashboard.camfs"})
    # scrolling
    m = re.search(r"^(?:please )?(?:scroll|page) (up|down)\b|^(?:go|scroll|jump) (?:back )?to (?:the )?(top|bottom)\b", low)
    if m:
        where = m.group(1) or m.group(2)
        return _r("Scrolling.", ui=[{"type": "ui", "action": "scroll", "to": where}], action={"name": "dashboard.scroll", "target": where})
    return None


_BRAIN_ALIASES = {"claude": "claude", "claude cli": "claude", "claude code": "claude", "cloud": "claude",
                  "ollama": "ollama", "local": "ollama", "local model": "ollama", "local ai": "ollama",
                  "llama": "ollama", "auto": "auto", "automatic": "auto", "default": "auto",
                  "smart": "smart", "smart router": "smart", "router": "smart"}
_BRAIN_NOUN = r"(?:brain|model|llm|ai|a\.i\.?)"
_BRAIN_RES = (
    re.compile(rf"\b(?:switch|change|set)\s+(?:the\s+|your\s+)?{_BRAIN_NOUN}\s+(?:over\s+)?to\s+(.+)$"),
    re.compile(rf"\b(?:use|switch to)\s+(.+?)\s+(?:as|for)\s+(?:the\s+|your\s+)?{_BRAIN_NOUN}$"),
    re.compile(r"^(?:(?:hey |ok )?jarvis[, ]+)?switch (?:over )?to (claude|ollama|local|auto|smart)"
               r"(?: (?:mode|brain|model|router))?$"),
)


async def i_brain(ctx, text, low):
    """"Jarvis, switch the brain to Ollama" / "use claude as the model": the same switch as the
    LLM SWITCH modal (/api/llm/active), so it persists in providers.json and every dashboard
    updates through the "models-updated" broadcast."""
    reg = ctx.get("providers")
    if reg is None:
        return None
    clean = low.strip(" .!?")
    want = None
    for rx in _BRAIN_RES:
        m = rx.search(clean)
        if m:
            want = re.sub(r"\b(please|now|instead|the|model|brain|provider)\b", " ", m.group(1))
            want = re.sub(r"\s+", " ", want).strip(" ,.")
            break
    if want is None:
        return None
    provs = reg.list()
    pid = _BRAIN_ALIASES.get(want)
    if pid is None:
        hit = next((p for p in provs if want in (p["id"].lower(), p["name"].lower())), None) or \
              next((p for p in provs if p["name"].lower().startswith(want) or p["id"].lower().startswith(want)), None)
        pid = hit["id"] if hit else None
    if pid is None or not any(p["id"] == pid for p in provs):
        names = ", ".join(p["name"].split(" (")[0] for p in provs)
        return _r(f"I don't have a brain called {want}, sir. Available: {names}.",
                  action={"name": "brain.switch", "target": want, "ok": False})
    if (ctx.get("rbac") or {}).get("role", "commander") != "commander":
        return _r("Switching the brain needs the commander role, sir.",
                  action={"name": "brain.switch", "target": pid, "ok": False})
    p = next(p for p in provs if p["id"] == pid)
    if p.get("locked"):
        return _r("Cloud brains need a Pro licence on this install, sir. Local models stay free.",
                  action={"name": "brain.switch", "target": pid, "ok": False})
    if p.get("active"):
        return _r(f"{p['name'].split(' (')[0]} is already the active brain, sir.",
                  action={"name": "brain.switch", "target": pid})
    res = reg.set_active(pid)
    if not res.get("ok"):
        return _r(f"I couldn't switch the brain: {res.get('error', 'refused')}.",
                  action={"name": "brain.switch", "target": pid, "ok": False})
    return _r(f"Switched the brain to {p['name'].split(' (')[0]}, sir.",
              ui=[{"type": "models-updated"},
                  {"type": "log", "level": "info", "msg": f"brain switched → {pid} (by command)"}],
              action={"name": "brain.switch", "target": pid})


_DEV_VERB =r"\b(fix|debug|troubleshoot|diagnose|repair|investigate|not (working|showing|loading|updating|responding)|isn't (working|showing|loading)|doesn't (work|show|load)|won't (work|show|load|open)|broken|error|bug|crash(es|ing)?|implement|add (a |an )?(new )?(feature|panel|section|button|widget)|remove (the )?.{0,30}(section|panel|button|widget))\b"
_DEV_SUBJECT = r"\b(bucs|ucs|dashboard|panel|section|button|tile|widget|orb|agenda|inbox|calendar|news|feed|digest|vision|voice|mic|jarvis|agent|map|globe|ui|page|screen|icon|code|feature|server|engine|console|weather|network|fleet)\b"


async def i_dev(ctx, text, low):
    dev = ctx.get("dev")
    if dev is None:
        return None
    lic = ctx.get("licensing")
    if lic is not None and not lic.allows("engineering"):
        if re.search(_DEV_VERB, low) and re.search(_DEV_SUBJECT, low):
            return _r("Engineering mode is part of BUCS Pro, sir. Activate a licence in the LICENSE panel.")
        return None
    # approvals for the open engineering job
    if dev.job and dev.job.get("stage") == "proposed" and re.search(r"\b(apply|do it|go ahead|implement it|make the (fix|change))\b", low):
        async def run(progress):
            await progress("applying the approved fix…")
            r = await dev.apply()
            if not r.get("ok"):
                return r.get("error", "Couldn't apply it, sir.")
            t = r["tests"]
            return (f"Applied — {len(r['changed'])} file{'s' if len(r['changed']) != 1 else ''} changed, tests "
                    f"{'passing' if t['ok'] else 'FAILING'}. Say keep it, undo that, or ship it.")
        return _r("Applying the fix now, sir.", slow=True, run=run, action={"name": "dev.apply"})
    if dev.job and dev.job.get("stage") == "applied":
        for pat, fn, say in ((r"\b(undo|revert|roll ?back)\b", dev.undo, "Reverted."),
                             (r"\b(ship it|commit and push|push it)\b", dev.ship, None),
                             (r"\b(keep it|keep the (fix|change)|looks good)\b", dev.keep, "Kept.")):
            if re.search(pat, low):
                r = await fn()
                msg = say if r.get("ok") and say else (
                    f"Shipped as {r['commit']}{'' if r.get('pushed') else ' — push failed, check GitHub access'}."
                    if r.get("ok") else r.get("error", "That didn't work."))
                return _r(msg, action={"name": f"dev.{fn.__name__}"})
    if not (re.search(_DEV_VERB, low) and re.search(_DEV_SUBJECT, low)):
        return None

    async def run(progress):
        await progress("reading the code and live diagnostics…")
        r = await dev.diagnose(text)
        if not r.get("ok"):
            return "I couldn't complete the diagnosis, sir. Details are on the engineering panel."
        tail = " I have a fix ready — say apply the fix, or use the panel." if r.get("fixable") else ""
        return r["summary"] + tail
    return _r("On it, sir. I'm looking into the code now — I'll show you what I find before changing anything.",
              slow=True, run=run, action={"name": "dev.diagnose"},
              ui=[{"type": "dev-open"}])


async def i_time(ctx, text, low):
    if re.search(r"\bwhat(?:'s| is)? the (time|date|day)\b|\bwhat time is it\b|\btoday's date\b", low):
        now = datetime.now()
        if "time" in low:
            return _r(f"It's {now.strftime('%H:%M')}, sir.")
        return _r(f"Today is {now.strftime('%A, %d %B %Y')}.")


async def i_system(ctx, text, low):
    if not re.search(r"\b(cpu|processor|memory|ram|disk|storage|system status|vitals|battery)\b", low):
        return None
    if not re.search(r"\b(what|how|status|load|usage|level|free|check|report|much|doing)\b", low):
        return None
    m = (ctx.get("sysmon").latest if ctx.get("sysmon") else {}) or {}
    if "cpu" not in m:
        return _r("Telemetry is still warming up, sir. Ask me again in a moment.", "stark")
    parts = []
    if re.search(r"\b(cpu|processor|system status|vitals)\b", low):
        parts.append(f"CPU at {m['cpu']:.0f}%")
    if re.search(r"\b(memory|ram|system status|vitals)\b", low):
        parts.append(f"memory at {m['mem']:.0f}%")
    if re.search(r"\b(disk|storage|system status|vitals)\b", low):
        parts.append(f"disk at {m['disk']:.0f}%")
    if "battery" in low:
        try:
            import psutil
            b = psutil.sensors_battery()
            parts.append(f"battery {b.percent:.0f}%{' and charging' if b.power_plugged else ''}" if b else "no battery reported")
        except Exception:
            parts.append("battery unavailable")
    agent = "hulk" if re.search(r"\b(memory|ram)\b", low) and "cpu" not in low else "stark"
    line = ", ".join(parts) or f"CPU at {m['cpu']:.0f}%"
    return _r(line[0].upper() + line[1:] + ".", agent,
              ui=[{"type": "dash-cmd", "target": "readiness"}])


async def i_network(ctx, text, low):
    net = ctx.get("net")
    if net is None:
        return None
    if re.search(r"\b(run|start|do|perform)\b.*\bspeed ?test\b|\bspeed ?test (now|again)\b|\btest (my |the )?(internet|connection) speed\b", low):
        async def run(progress):
            await progress("measuring download and upload…")
            res = await net.speed_test()
            if res.get("ok"):
                return f"Internet speed: {res['down_mbps']:.0f} megabits down, {res['up_mbps']:.0f} up, {net.last_latency_ms or 0:.0f} millisecond latency."
            return f"The speed test failed, sir: {res.get('error', 'unknown error')}"
        return _r("Running a speed test now, sir. About ten seconds.", "stark", slow=True, run=run,
                  action={"name": "network.speedtest"})
    if re.search(r"\b(internet|network|connection|wifi|wi-fi)\b.*\b(speed|fast|slow)\b|\bspeed of (my|the) internet\b|\b(latency|ping)\b", low):
        snap = net.snapshot(); sp = snap.get("speed") or {}
        bits = []
        if sp.get("ok"):
            bits.append(f"last measured {sp['down_mbps']:.0f} megabits down and {sp['up_mbps']:.0f} up")
        if snap.get("latency_ms") is not None:
            bits.append(f"latency {snap['latency_ms']:.0f} milliseconds")
        if snap.get("loss_pct"):
            bits.append(f"{snap['loss_pct']:.0f}% of connection probes failing")
        if not bits:
            return _r("No network measurements yet. Say 'run a speed test' and I'll measure it.", "stark")
        return _r("Internet: " + ", ".join(bits) + ".", "stark")


async def i_weather(ctx, text, low):
    if not re.search(r"\b(weather|temperature|forecast|raining|rain|hot|cold) (outside|today|now)?\b", low) \
            or not re.search(r"\b(weather|temperature|forecast|rain|outside)\b", low):
        return None
    w = ctx.get("weather")
    d = await w.get() if w else None
    if not d or d.get("error"):
        return _r("Weather data is unavailable right now, sir.")
    return _r(f"{d['city']}: {d['temp_c']:.0f} degrees, {d['label'].lower()}, feels like {d['feels_c']:.0f}, "
              f"humidity {d['humidity']:.0f}%.")


async def i_news(ctx, text, low):
    if not re.search(r"\b(news|headlines|what's happening in the world)\b", low):
        return None
    news = ctx.get("news")
    items = list(news.recent) if news else []
    if not items:
        return _r("No headlines yet, sir. The feed refreshes every ten minutes.", "widow")
    top = "; ".join(f"{a['title']} ({a['source']})" for a in items[:3])
    return _r(f"Top headlines: {top}.", "widow", ui=[{"type": "dash-cmd", "target": "news"}])


async def i_threats(ctx, text, low):
    t = ctx.get("threats")
    if t and re.search(r"\b(threat|threats|security status|risk level|any risks?)\b", low):
        return _r(t.summary_text(), "widow")


async def i_calendar(ctx, text, low):
    agenda = ctx.get("agenda")
    if not (agenda and re.search(r"\b(meeting|meetings|schedule|agenda|calendar)\b", low)
            and re.search(r"\b(what|how many|next|today|do i have|upcoming|any)\b", low)):
        return None
    snap = agenda.snapshot(); intel = snap.get("intel", {}); evs = snap.get("events") or []
    if snap.get("source") != "google":
        return _r("Google Calendar isn't connected yet, sir. Click G on the dashboard to sign in once.", "captain")
    if not evs:
        return _r("Your calendar is clear, sir. No upcoming meetings.", "captain")
    nxt = evs[0]
    parts = [f"You have {intel.get('meetings_today', len(evs))} meetings today.",
             f"Next is {nxt['title']} in {max(0, round(nxt['minutes_until']))} minutes."]
    if intel.get("conflicts"):
        parts.append(f"Warning: {intel['conflicts']} scheduling conflict detected.")
    return _r(" ".join(parts), "captain", ui=[{"type": "dash-cmd", "target": "agenda"}])


async def i_inbox(ctx, text, low):
    agenda = ctx.get("agenda")
    if not (agenda and re.search(r"\b(inbox|email|emails|mail)\b", low)):
        return None
    snap = agenda.snapshot(); mails = snap.get("emails") or []
    if snap.get("source") != "google":
        return _r("Gmail isn't connected yet, sir. Click G on the dashboard to sign in once.", "thor")
    if not mails:
        return _r("Inbox is clear, sir.", "thor")
    prio = [m for m in mails if m.get("priority") == "priority"]
    line = f"{len(mails)} messages in view, {len(prio)} priority."
    if prio:
        line += f" Top priority: {prio[0]['subject']} from {prio[0]['sender'].split('@')[0]}."
    return _r(line, "thor")


async def i_privacy(ctx, text, low):
    if not re.search(r"\b(privacy (report|status|score|check|shield)|who is (bucs|the dashboard|this system|it) "
                     r"(talking|connected|connecting) to|what (data )?(leaves|is leaving|left) (this|my) (machine|computer|pc)|"
                     r"is (bucs|anything) (phoning home|sending my data))\b", low):
        return None
    ps = ctx.get("privacy")
    if ps is None:
        return _r("The Privacy Shield isn't loaded on this install.", "doom")
    return _r(ps.summary(), "doom", ui=[{"type": "dash-cmd", "target": "privacy"}],
              action={"name": "privacy.report"})


async def i_sentinel(ctx, text, low):
    """v8.9 · DOOM SENTINEL: DEFCON, intrusions, code integrity, injection shield."""
    if not re.search(r"\b(defcon|threat level|security level|any (intrusions?|intruders?|break[- ]?ins?|attacks?)|"
                     r"(intrusion|attack) (report|status|watch)|is (bucs|the system|my system|the dashboard) "
                     r"(safe|secure|compromised|under attack|being attacked)|is (the|our|my) code (intact|tampered|safe)|"
                     r"code integrity|integrity (check|report|status)|(prompt )?injection (attempts?|report|shield)|sentinel (report|status))\b", low):
        return None
    s = ctx.get("doomsentinel")
    if s is None:
        return _r("DOOM Sentinel isn't loaded on this install.", "doom")
    if re.search(r"\b(check|scan|verify)\b.*\bintegrity\b|\bintegrity (check|scan)\b|\bis (the|our|my) code (intact|tampered|safe)", low):
        try:
            await s.integrity()
        except Exception:
            pass
    return _r(s.summary(), "doom", ui=[{"type": "dash-cmd", "target": "sentinel"}], action={"name": "sentinel.report"})


async def i_memory(ctx, text, low):
    mem = ctx.get("memory")
    if mem and re.search(r"\b(what do you know about me|who am i|remember about me|my memory)\b", low):
        cm = ctx.get("commander")
        if cm is not None and cm.sections:
            meta = cm.meta()
            return _r(f"I hold your commander profile: {meta['sections']} sections, including "
                      f"{', '.join(meta['titles'][:4])}. {mem.summary_text()}")
        return _r(mem.summary_text())


async def i_vision(ctx, text, low):
    if re.search(r"\b(what do you see|what am i doing|look at me|see me|analys?e me|how do i look)\b", low):
        return _r("Let me take a look, sir.", "vision", ui=[{"type": "vision-request"}])


async def i_missions(ctx, text, low):
    if not re.search(r"\b(what are (the )?(agents|avengers|you all|the team) doing|mission status|active (missions|directives)|team status)\b", low):
        return None
    o = ctx.get("orchestrator")
    snap = o.snapshot() if o else {}
    act = [d for d in snap.get("directives", []) if d.get("status") == "active"]
    if not act:
        return _r("No active missions, sir. The team is on routine watch.", ui=[{"type": "dash-cmd", "target": "roster"}])
    lines = "; ".join(f"{d['agent'].title()} on {d['title']}" for d in act[:4])
    return _r(f"{len(act)} active: {lines}.", ui=[{"type": "dash-cmd", "target": "roster"}])


# ── actions ───────────────────────────────────────────────────────
_OPEN = r"\b(open|launch|start|run|bring up|show|display|pull up)\b"


async def i_windows(ctx, text, low):
    if re.search(r"\block (my |the )?(computer|pc|screen|workstation|laptop)\b", low):
        return _r(None, action={"name": "win.lock"}, do=win_actions.lock_screen)
    m = re.search(r"\b(volume|sound) (up|down)(?: by (\d+))?\b|\b(turn|crank) (?:the )?(?:volume|sound) (up|down)\b", low)
    if m:
        direction = m.group(2) or m.group(5)
        steps = int(m.group(3)) // 2 if m.group(3) else 5      # one key ≈ 2%
        return _r(None, action={"name": f"win.volume_{direction}"},
                  do=lambda: win_actions.media_key(f"volume_{direction}", steps))
    if re.search(r"^\s*(mute|unmute)( (the )?(sound|audio|volume|computer))?\s*[.!]?$", low):
        return _r(None, action={"name": "win.mute"}, do=lambda: win_actions.media_key("mute"))
    for phrase, key in (("pause", "play_pause"), ("play music", "play_pause"), ("resume", "play_pause"),
                        ("next track", "next"), ("next song", "next"), ("skip song", "next"),
                        ("previous track", "previous"), ("previous song", "previous")):
        if re.search(rf"^\s*{phrase}\b", low):
            return _r(None, action={"name": f"win.media_{key}"}, do=lambda k=key: win_actions.media_key(k))
    if re.search(_OPEN, low):
        for name in sorted(win_actions.APPS, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", low):
                return _r(None, action={"name": "win.open_app", "target": name},
                          do=lambda n=name: win_actions.open_app(n))
        fm = re.search(r"\b(downloads|documents|desktop|pictures|music|videos)\b(?: folder)?", low)
        if fm and ("folder" in low or "my " + fm.group(1) in low):
            return _r(None, action={"name": "win.open_folder", "target": fm.group(1)},
                      do=lambda f=fm.group(1): win_actions.open_folder(f))


# well-known sites by spoken name (browser.parse_browser_intent only knows
# explicit URLs/domains like "open youtube.com")
SITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com",
    "google calendar": "https://calendar.google.com", "google drive": "https://drive.google.com",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com",
    "google": "https://www.google.com", "github": "https://github.com",
    "linkedin": "https://www.linkedin.com", "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com", "whatsapp": "https://web.whatsapp.com",
    "bucs website": "https://avengers-bobbiey.netlify.app",
    "bobbiey website": "https://avengers-bobbiey.netlify.app",
}


async def i_browser(ctx, text, low):
    intent = browser.parse_browser_intent(text)
    if not intent and re.search(_OPEN, low):
        for name in sorted(SITES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", low):
                intent = {"url": SITES[name], "name": name.title(),
                          "fullscreen": bool(re.search(r"\bfull ?screen\b", low))}
                break
    if not intent:
        return None

    def do():
        try:
            res = browser.open_url(intent["url"], fullscreen=intent["fullscreen"])
        except Exception as e:
            res = {"opened": False, "error": str(e)}
        fs = " in fullscreen" if intent["fullscreen"] else ""
        ok = bool(res.get("opened"))
        return {"ok": ok, "say": f"Opening {intent['name']}{fs}, sir." if ok
                else f"Unable to open {intent['name']}. {res.get('error', '')}", "browser": res}
    return _r(None, action={"name": "browser.open", "target": intent["url"]}, do=do,
              ui=[{"type": "browser", "event": "opened", "url": intent["url"], "name": intent["name"],
                   "fullscreen": intent["fullscreen"]}])


async def i_dashboard(ctx, text, low):
    if not re.search(_OPEN, low):
        return None
    for word, target in _SECTIONS.items():
        if re.search(rf"\b{word}", low):
            return _r(f"Bringing up {target} now, sir.", ui=[{"type": "dash-cmd", "target": target}],
                      action={"name": "dashboard.open", "target": target})


# order matters: actions that start with "open" (apps, sites) before dashboard
# sections, specific questions before broad ones
HANDLERS = [i_conversation, i_agentvoice, i_handsfree, i_remember, i_computeruse, i_brain, i_dev, i_time, i_region_map, i_ui,
            i_windows, i_browser, i_network, i_system, i_weather, i_calendar,
            i_inbox, i_sentinel, i_threats, i_news, i_privacy, i_memory, i_vision, i_missions, i_dashboard]


async def match(ctx: dict, text: str) -> dict | None:
    """First matching fast-path intent, or None (→ agent/LLM path)."""
    low = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if not low:
        return None
    for h in HANDLERS:
        try:
            res = await h(ctx, text, low)
        except Exception as e:                    # a broken handler must never block the LLM path
            res = None
            if ctx.get("hub"):
                await ctx["hub"].broadcast({"type": "log", "level": "warn",
                                            "msg": f"intent {h.__name__} failed: {e}"})
        if res:
            res["intent"] = h.__name__[2:]
            return res
    return None


# ── delegation: which specialist owns a free-form request ─────────
ROUTES = [
    ("bob",     r"\b(sign[- ]?off|approv(e|al|als)|oversee|supervis\w*|orchestrat\w*|self[- ]?(develop\w*|improv\w*|evol\w*)|"
                r"evolution|governance|whole team|team (status|report|overview)|system[- ]level|who should handle)\b"),
    ("captain", r"\b(schedule|meeting|calendar|brief(ing)?|remind|plan my|agenda|today's plan)\b"),
    ("stark",   r"\b(cpu|process|system|disk|code|script|automate|performance|laptop|computer|windows|driver|bug)\b"),
    ("hulk",    r"\b(memory|ram|gpu|compute|heavy|model|cluster|benchmark)\b"),
    ("doom",    r"\b(cyber|hack(ed|er|ers|ing)?|breach(ed|es)?|malware|ransomware|phishing|scam|vulnerab\w*|cves?|"
                r"exploit\w*|firewall|exposure|attack surface|harden\w*|security (posture|scan|audit|score)|"
                r"intrusion|threat hunt\w*|sitrep|global (situation|awareness))\b"),
    ("widow",   r"\b(research|investigate|intel|news|threat|security|risk|who is|what happened)\b"),
    ("hawkeye", r"\b(monitor|watch|anomal|alert|vitals|spike)\b"),
    ("thor",    r"\b(announce|email|mail|message|draft|write to|tell everyone)\b"),
    ("vision",  r"\b(summari[sz]e|synthesi[sz]e|connect the dots|big picture|analy[sz]e)\b"),
]


def delegate(text: str) -> str | None:
    """Specialist for an unaddressed request, or None (JARVIS keeps it)."""
    low = (text or "").lower()
    for agent, pat in ROUTES:
        if re.search(pat, low):
            return agent
    return None
