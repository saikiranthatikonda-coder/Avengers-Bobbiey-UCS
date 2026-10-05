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


async def i_memory(ctx, text, low):
    mem = ctx.get("memory")
    if mem and re.search(r"\b(what do you know about me|who am i|remember about me|my memory)\b", low):
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
HANDLERS = [i_conversation, i_time, i_windows, i_browser, i_network, i_system, i_weather, i_calendar,
            i_inbox, i_threats, i_news, i_memory, i_vision, i_missions, i_dashboard]


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
    ("captain", r"\b(schedule|meeting|calendar|brief(ing)?|remind|plan my|agenda|today's plan)\b"),
    ("stark",   r"\b(cpu|process|system|disk|code|script|automate|performance|laptop|computer|windows|driver|bug)\b"),
    ("hulk",    r"\b(memory|ram|gpu|compute|heavy|model|cluster|benchmark)\b"),
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
