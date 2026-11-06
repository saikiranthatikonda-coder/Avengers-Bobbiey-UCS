"""DASHBOARD CONTEXT · let JARVIS and every agent read the live dashboard.

Each section reader returns a compact, REAL view of what that dashboard panel shows right
now, read server-side from the same objects / functions the panel's endpoint uses (never
HTTP to ourselves, never fabricated):

    {"section", "title", "source", "as_of", "ok", ...facts}      or
    {"section", "title", "source", "as_of", "ok": False, "unavailable": "<why>"}

Readers run concurrently, each under its own time budget; one failing or slow section
never breaks the others. Everything that leaves this module is scrubbed: secret-looking
dict keys are dropped and key/token-like strings are masked (evolution_core.scrub patterns),
private files are never read, licence keys / install ids / call SIDs never appear.

Role: observer sessions get the same read-only facts the dashboard already shows them,
minus operator-only detail (Computer Use task text/steps, phone numbers and call summaries,
operator list). Nothing here performs an action.

Used by:
  realtime.Engine._reason   keyword routing → a bounded [DASHBOARD FACTS …] block in the prompt
  agents.Avenger.handle     the same block on the legacy (engine-less) path
  agentos_tools             read-only `dashboard_read` tool (free tier) for the tool-calling loop
  intents.i_computeruse     Computer Use status for the fast path
  build_router              /api/dashboard/context* (read-only inspection, for the UI and tests)
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import sys
import time
from datetime import datetime

SECTION_BUDGET_S = 1.2           # one section may take at most this long
TOTAL_BUDGET_S = 2.5             # the whole block never delays a reply longer than this
SECTION_CHARS = 700              # per-section cap inside the prompt
BLOCK_CHARS = 2600               # whole block cap (small local models have small contexts)
MAX_SECTIONS = 4                 # sections per question
CACHE_S = 5.0

_STATE: dict = {}                # bound at startup (build_router / realtime.Engine)
LAST: dict = {}                  # last injection: what the reply path received (for tests/UI)
_cache: dict = {}


def bind(state: dict | None) -> None:
    global _STATE
    if isinstance(state, dict):
        _STATE = state


def bound_state() -> dict:
    return _STATE


# ═══ safety: scrub everything that leaves this module ═════════════════════════
_SECRET_KEY = re.compile(r"(api[_-]?key|apikey|token|secret|passw|pwd|^pin$|_pin$|credential|cookie|session|"
                         r"private|signing|^key$|_key$|^auth|^sid$|_sid$|install_id|webhook|license_key|"
                         r"licence_key|^ip$|^headers?$|^env$)", re.I)
_EXTRA = [
    (re.compile(r"(sk-ant-|sk-|gsk_|AIza|xai-|pk-|rk_|whsec_)[A-Za-z0-9_\-\.]{6,}"), "<key>"),
    (re.compile(r"\bAC[0-9a-f]{32}\b|\bCA[0-9a-f]{32}\b"), "<sid>"),
]
_FALLBACK_SCRUB = [
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"), "<jwt>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "<key>"),
    (re.compile(r"\bBUCS-[A-Za-z0-9_.=-]{8,}"), "<licence-key>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b([\w-]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|apikey|client[_-]?secret|"
                r"private[_-]?key)[\w-]*)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",;&}]+"), r"\1\2<redacted>"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "<hex>"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), "<blob>"),
]


def _patterns() -> list:
    """Reuse evolution_core's credential scrubbers (minus e-mail/path rules: the dashboard
    shows senders, and those are not secrets); fall back to a local copy if it can't load."""
    try:
        import evolution_core
        pats = [(rx, rep) for rx, rep in evolution_core._SCRUB if rep not in ("<email>", "~")]
    except Exception:
        pats = list(_FALLBACK_SCRUB)
    return _EXTRA + pats


_PATS: list | None = None


def scrub_text(s) -> str:
    global _PATS
    if _PATS is None:
        _PATS = _patterns()
    out = str(s if s is not None else "")
    for rx, rep in _PATS:
        out = rx.sub(rep, out)
    return out


def sanitize(obj, depth: int = 0):
    """Drop secret-looking keys, mask key-like strings, bound the size. Never raises."""
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for k, v in list(obj.items())[:40]:
            ks = str(k)
            if _SECRET_KEY.search(ks):
                continue
            out[ks] = sanitize(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple, set)):
        return [sanitize(v, depth + 1) for v in list(obj)[:25]]
    if isinstance(obj, float):
        return round(obj, 2) if math.isfinite(obj) else None
    if isinstance(obj, (int, bool)) or obj is None:
        return obj
    s = scrub_text(obj)
    return s if len(s) <= 400 else s[:400] + "…"


def _mask_number(n) -> str:
    s = re.sub(r"[^\d+]", "", str(n or ""))
    return ("…" + s[-4:]) if len(s) > 4 else ("" if not s else "…")


# ═══ helpers ═══════════════════════════════════════════════════════════════════
def _now_s() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts(t) -> str | None:
    try:
        t = float(t)
        if t <= 0:
            return None
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _pick(d: dict | None, keys) -> dict:
    d = d or {}
    return {k: d.get(k) for k in keys if d.get(k) not in (None, "", [], {})}


def _main():
    """main.py's module (for the module-level helpers its endpoints use). None in unit tests."""
    m = sys.modules.get("main")
    return m if m is not None and hasattr(m, "app") else None


def _role(state: dict) -> bool:
    return (state.get("rbac") or {}).get("role", "commander") == "commander"


class Unavailable(Exception):
    pass


def _need(state: dict, key: str):
    obj = state.get(key)
    if obj is None:
        raise Unavailable(f"{key} service not running")
    return obj


def _dist_km(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(h)))


# Named regions for "what's on the map near India?" — centroid + radius (km). Approximate on
# purpose and labelled as such in the facts.
REGIONS = {
    "india": (22.0, 79.0, 1800), "pakistan": (30.0, 70.0, 900), "nepal": (28.2, 84.0, 500),
    "bangladesh": (23.8, 90.3, 500), "sri lanka": (7.8, 80.7, 400), "china": (35.0, 103.0, 2200),
    "japan": (36.5, 138.0, 1100), "indonesia": (-2.5, 118.0, 2200), "philippines": (12.5, 122.0, 1000),
    "australia": (-25.0, 134.0, 2500), "new zealand": (-41.5, 173.0, 900), "turkey": (39.0, 35.0, 900),
    "iran": (32.5, 54.0, 1100), "middle east": (29.0, 45.0, 1800), "europe": (50.0, 12.0, 2200),
    "uk": (54.0, -2.5, 600), "united kingdom": (54.0, -2.5, 600), "russia": (60.0, 90.0, 3500),
    "africa": (2.0, 20.0, 4000), "usa": (39.5, -98.0, 2600), "united states": (39.5, -98.0, 2600),
    "america": (39.5, -98.0, 2600), "us": (39.5, -98.0, 2600), "canada": (57.0, -101.0, 2600),
    "mexico": (23.5, -102.0, 1200), "brazil": (-10.0, -52.0, 2300), "south america": (-15.0, -60.0, 3500),
    "california": (37.0, -119.5, 700), "alaska": (63.5, -152.0, 1200), "hyderabad": (17.4, 78.5, 600),
    "telangana": (17.9, 79.1, 500), "delhi": (28.6, 77.2, 600), "mumbai": (19.1, 72.9, 600),
    "chennai": (13.1, 80.3, 600), "bengaluru": (12.97, 77.6, 600), "bangalore": (12.97, 77.6, 600),
    "kolkata": (22.6, 88.4, 600), "asia": (30.0, 95.0, 4500), "pacific": (0.0, -160.0, 6000),
}


def region_of(q: str):
    low = (q or "").lower()
    for name in sorted(REGIONS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", low):
            if name == "us" and not re.search(r"\b(the us|in us|near us\b(?! me)|us region)\b", low):
                continue                      # "us" is too often the pronoun
            lat, lon, rad = REGIONS[name]
            return {"name": name, "lat": lat, "lon": lon, "radius_km": rad}
    return None


def _home(state: dict):
    d = state.get("disaster")
    try:
        loc = d.location() if d is not None else None
        if loc and loc.get("lat") is not None:
            return {"name": loc.get("label") or loc.get("city") or "your location",
                    "lat": float(loc["lat"]), "lon": float(loc["lon"]), "radius_km": 1000}
    except Exception:
        pass
    m = _main()
    geo = getattr(m, "_geo_cache", {}).get("data") if m else None
    if geo and geo.get("lat") is not None:
        return {"name": geo.get("city") or "your location", "lat": float(geo["lat"]), "lon": float(geo["lon"]),
                "radius_km": 1000}
    return None


# ═══ section readers  (state, question, commander) -> dict ════════════════════
async def r_system(state, q, cmd):
    sysmon = _need(state, "sysmon")
    m = dict(getattr(sysmon, "latest", None) or {})
    if "cpu" not in m:
        raise Unavailable("telemetry still warming up")
    out = {"source": "system monitor (psutil, every 2 s) · /api/status",
           "as_of": _ts(m.get("ts")) or _now_s(),
           "cpu_pct": m.get("cpu"), "mem_pct": m.get("mem"), "disk_pct": m.get("disk"),
           "net_up_kbps": m.get("net_up"), "net_down_kbps": m.get("net_down")}
    out.update(_pick(m, ("uptime", "procs", "battery", "temp")))
    mm = _main()
    if mm is not None and hasattr(mm, "_proc_rows") and re.search(
            r"process|app|program|using|hog|heavy|top|what.*running|slow", (q or "").lower()):
        try:
            rows = await mm._proc_rows()
            out["top_cpu"] = [{"name": r.get("name"), "cpu": round(r.get("cpu", 0), 1),
                               "mem_mb": round(r.get("mem_mb", 0))}
                              for r in sorted(rows, key=lambda r: r.get("cpu", 0), reverse=True)[:5]]
            out["top_mem"] = [{"name": r.get("name"), "mem_mb": round(r.get("mem_mb", 0))}
                              for r in sorted(rows, key=lambda r: r.get("mem_mb", 0), reverse=True)[:5]]
            out["source"] += " · processes (/api/processes)"
        except Exception:
            pass
    return out


async def r_hardware(state, q, cmd):
    m = _main()
    cache = getattr(m, "_hw_cache", None) if m else None
    data = (cache or {}).get("data")
    if not data and m is not None and hasattr(m, "hardware_endpoint"):
        data = await m.hardware_endpoint()
    if not data:
        raise Unavailable("hardware scan not available")
    keep = {k: v for k, v in data.items() if k != "peripherals"}
    keep = json.loads(json.dumps(keep, default=str))
    periph = data.get("peripherals") or []
    return {"source": "hardware scan (/api/hardware)", "as_of": _ts((cache or {}).get("ts")) or _now_s(),
            "hardware": keep, "peripherals": [_pick(p, ("name", "kind", "type", "status")) for p in periph[:8]]}


async def r_network(state, q, cmd):
    net = _need(state, "net")
    snap = net.snapshot()
    sp = snap.get("speed") or {}
    out = {"source": "network truth (netspeed.py · /api/network)", "as_of": _now_s(),
           "latency_ms": snap.get("latency_ms"), "loss_pct": snap.get("loss_pct"),
           "speed_test": _pick(sp, ("ok", "down_mbps", "up_mbps", "ts", "server", "error"))}
    if sp.get("ts"):
        out["speed_test"]["measured"] = _ts(sp.get("ts"))
    m = _main()
    conn = (getattr(m, "_conn_cache", {}) or {}).get("data") if m else None
    if conn:
        out["connectivity"] = _pick(conn, ("online", "uplink", "active", "wifi", "bluetooth", "ping_ms", "ssid",
                                           "interface", "signal", "kind"))
        out["source"] += " · connectivity (/api/connectivity)"
    return out


async def r_threats(state, q, cmd):
    t = _need(state, "threats")
    s = t.snapshot()
    open_ev = [e for e in s.get("events") or [] if e.get("stage") not in ("resolved", "acknowledged")]
    return {"source": "threat engine (/api/threats)", "as_of": _now_s(),
            "risk_score": s.get("risk_score"), "level": s.get("level"),
            "matrix": {k: (v.get("score") if isinstance(v, dict) else v) for k, v in (s.get("matrix") or {}).items()}
            if isinstance(s.get("matrix"), dict) else s.get("matrix"),
            "open_incidents": len(open_ev), "by_severity": s.get("by_severity"), "by_stage": s.get("by_stage"),
            "open": [{**_pick(e, ("title", "severity", "category", "stage")), "at": _ts(e.get("ts"))}
                     for e in open_ev[:6]],
            "response_queue": [_pick(e, ("title", "severity")) for e in (s.get("response_queue") or [])[:4]],
            "soc": _pick(s.get("soc") or {}, ("investigations", "resolved", "alerts", "events_total"))}


async def r_insights(state, q, cmd):
    ins = _need(state, "insights")
    a = ins.aiops_status()
    out = {"source": "insights engine (/api/insights, /api/aiops)", "as_of": _now_s(),
           "readiness": a.get("readiness"), "anomaly": a.get("anomaly"), "presence": a.get("presence"),
           "engine": _pick(a.get("engine") or {}, ("source", "model", "last_run", "ok", "error")),
           "insights": [(_pick(i, ("title", "text", "severity", "agent", "kind")) if isinstance(i, dict) else str(i))
                        for i in (a.get("insights") or [])[:5]]}
    try:
        recs = ins.recommendations(productivity=state.get("productivity"), orchestrator=state.get("orchestrator"))
        out["recommendations"] = [(_pick(r, ("title", "text", "action", "priority")) if isinstance(r, dict) else str(r))
                                  for r in (recs or [])[:4]]
    except Exception:
        pass
    return out


async def r_decisions(state, q, cmd):
    d = _need(state, "decisions")
    s = d.snapshot()
    props = s.get("proposals") or []
    return {"source": "decision support (/api/decisions)", "as_of": _now_s(), "autonomy": s.get("autonomy"),
            "pending": [{**_pick(p, ("title", "action", "status", "risk", "confidence", "agent")),
                         "at": _ts(p.get("ts"))} for p in props if p.get("status") in ("proposed", "simulated", "pending")][:5],
            "proposals_total": len(props), "executed_total": s.get("executed_total"),
            "auto_executed_total": s.get("auto_executed_total"),
            "recent_executed": [_pick(e, ("title", "action", "result", "by")) if isinstance(e, dict) else str(e)
                                for e in list(s.get("executed") or [])[:3]]}


async def r_missions(state, q, cmd):
    o = _need(state, "orchestrator")
    s = o.snapshot()
    team = state.get("team") or {}
    return {"source": "orchestrator mission board (/api/orchestrator)", "as_of": _now_s(),
            "summary": {k: v for k, v in s.items() if isinstance(v, (int, float, str)) and k != "cycles"},
            "active_directives": [_pick(d, ("agent", "title", "status", "priority", "progress"))
                                  for d in (s.get("directives") or [])[:6]],
            "recent_done": [_pick(d, ("agent", "title", "result")) if isinstance(d, dict) else str(d)
                            for d in (s.get("recent_done") or [])[:4]],
            "agents": {k: {"status": getattr(a, "status", None), "task": getattr(a, "current_task", None),
                           "confidence": getattr(a, "confidence", None)} for k, a in list(team.items())[:8]}}


async def r_productivity(state, q, cmd):
    out = {"source": "productivity (/api/productivity) · notes (/api/notes)", "as_of": _now_s()}
    p = state.get("productivity")
    if p is not None:
        s = p.snapshot()
        out["productivity"] = {k: v for k, v in s.items() if isinstance(v, (int, float, str, bool)) or k == "tasks"}
        if isinstance(out["productivity"].get("tasks"), list):
            out["productivity"]["tasks"] = out["productivity"]["tasks"][:8]
    m = _main()
    if m is not None and hasattr(m, "_load_notes"):
        notes = await asyncio.to_thread(m._load_notes)
        out["notes"] = [{"text": n.get("text"), "at": _ts(n.get("ts"))} for n in notes[:8]]
    if len(out) == 2:
        raise Unavailable("productivity service not running")
    return out


async def r_agenda(state, q, cmd):
    ag = _need(state, "agenda")
    s = ag.snapshot()
    connected = s.get("source") == "google"
    out = {"source": "agenda (Google Calendar/Gmail/Tasks · /api/agenda)", "as_of": _now_s(),
           "connected": connected, "events_today": s.get("events_today"),
           "events": [{**_pick(e, ("title", "minutes_until", "duration_min", "location", "calendar", "organizer",
                                   "attendee_count", "response")), "start": (e.get("start_iso") or "")[:16]}
                      for e in (s.get("events") or [])[:6]],
           "priority_unread": s.get("priority_unread"),
           "emails": [{**_pick(m, ("subject", "sender", "priority")), "at": _ts(m.get("ts"))}
                      for m in (s.get("emails") or [])[:5]],
           "tasks": [(_pick(t, ("title", "due", "status")) if isinstance(t, dict) else str(t))
                     for t in (s.get("tasks") or [])[:6]]}
    if not connected:
        out["note"] = "Google is not connected (sign in with G on the dashboard); no calendar data exists."
    return out


async def r_weather(state, q, cmd):
    w = _need(state, "weather")
    latest = None
    for result, _ in list(getattr(w, "_stale", {}).values()):
        if not latest or result.get("fetched_at", 0) > latest.get("fetched_at", 0):
            latest = result
    if not latest or time.time() - latest.get("fetched_at", 0) > 1800:
        try:
            latest = await w.get() or latest
        except Exception:
            pass
    if not latest or latest.get("error"):
        raise Unavailable("weather not fetched yet")
    return {"source": "Open-Meteo (/api/weather)", "as_of": _ts(latest.get("fetched_at")) or _now_s(),
            **_pick(latest, ("city", "temp_c", "feels_c", "label", "humidity", "wind_kmh", "precip_mm",
                             "uv", "high_c", "low_c", "sunrise", "sunset")),
            "forecast": [(_pick(d, ("day", "date", "high_c", "low_c", "label", "rain_pct")) if isinstance(d, dict) else d)
                         for d in (latest.get("daily") or latest.get("forecast") or [])[:4]]}


async def r_news(state, q, cmd):
    n = _need(state, "news")
    items = list(getattr(n, "recent", []) or [])
    if not items:
        raise Unavailable("no headlines fetched yet (feed refreshes every 10 min)")
    return {"source": f"news / intel ticker ({getattr(n, 'source', 'open feeds')})", "as_of": _now_s(),
            "headlines": [{**_pick(a, ("title", "source")), "at": (a.get("publishedAt") or a.get("published") or "")[:16]}
                          for a in items[:8]]}


async def r_fleet(state, q, cmd):
    f = _need(state, "fleet")
    s = f.snapshot()
    return {"source": "fleet registry (/api/fleet)", "as_of": _now_s(), "aggregate": s.get("aggregate"),
            "nodes": [{**_pick(n, ("name", "status", "os", "cpu", "mem", "disk", "cpu_cores", "mem_total_gb",
                                   "is_local", "last_seen_sec")),
                       "ollama": bool((n.get("ollama") or {}).get("models"))} for n in (s.get("nodes") or [])[:10]]}


async def r_devices(state, q, cmd):
    import devices
    devs = [devices.public(d) for d in list(devices._devices.values())]
    disc = getattr(devices, "_discovery", {}) or {}
    return {"source": "devices (/api/devices)", "as_of": _now_s(),
            "devices": [{**_pick(d, ("name", "kind", "label", "control")),
                         "online": (d.get("status") or {}).get("online"),
                         "detail": (d.get("status") or {}).get("detail")} for d in devs[:12]],
            "discovered_on_lan": len(disc.get("items") or [])}


async def r_disaster(state, q, cmd):
    d = _need(state, "disaster")
    s = d.snapshot()
    items = s.get("items") or []
    low = (q or "").lower()
    want = None
    for word, typ in (("quake", "earthquake"), ("earthquake", "earthquake"), ("cyclone", "cyclone"),
                      ("flood", "flood"), ("fire", "wildfire"), ("volcan", "volcano"), ("outage", "outage"),
                      ("storm", "storm"), ("tsunami", "tsunami"), ("heat", "heat")):
        if word in low:
            want = typ
            break
    sel = [i for i in items if not want or want in str(i.get("type", "")).lower()]
    near = [i for i in sel if i.get("near")]
    loc = s.get("location") or {}
    return {"source": "disaster monitor (USGS, GDACS, NASA EONET, NDMA, status pages · /api/disaster)",
            "as_of": _ts(s.get("generated")) or _now_s(),
            "your_location": _pick(loc, ("label", "city", "source")), "near_km": s.get("near_km"),
            "counts": s.get("counts"), "filter": want,
            "matching": len(sel), "near_you": len(near),
            "nearest": [{**_pick(i, ("title", "type", "sev_label", "distance_km", "source")), "at": _ts(i.get("ts"))}
                        for i in sorted(sel, key=lambda i: (i.get("distance_km") is None, i.get("distance_km") or 0))[:5]],
            "most_severe": [{**_pick(i, ("title", "type", "sev_label", "distance_km", "source")), "at": _ts(i.get("ts"))}
                            for i in sorted(sel, key=lambda i: -(i.get("severity") or 0))[:3]]}


async def r_globalmap(state, q, cmd):
    g = _need(state, "globalmap")
    s = g.snapshot()
    low = (q or "").lower()
    region = region_of(q)
    if region is None and re.search(r"\b(near me|around me|my area|nearby|close to me|near here)\b", low):
        region = _home(state)
    layers = {k: {"count": v.get("count"), "status": v.get("status")} for k, v in (s.get("layers") or {}).items()}
    items = s.get("items") or []
    layer_want = None
    for word, layer in (("quake", "quakes"), ("earthquake", "quakes"), ("gdacs", "disasters"), ("volcan", "volcanoes"),
                        ("outage", "outages"), ("weather alert", "wxalerts"), ("flight", "flights"),
                        ("plane", "flights"), ("aircraft", "flights"), ("satellite", "satellites"),
                        ("space weather", "space"), ("aurora", "space"), ("solar", "space")):
        if word in low:
            layer_want = layer
            break
    if layer_want:
        items = [i for i in items if i.get("layer") == layer_want]
    out = {"source": "global map (USGS, GDACS, EONET, GVP, NWS, IODA, OpenSky, CelesTrak, NOAA SWPC · /api/globalmap)",
           "as_of": _ts(s.get("generated")) or _now_s(), "layers": layers, "layer_filter": layer_want}
    if region:
        rows = []
        for i in items:
            if i.get("lat") is None or i.get("lon") is None:
                continue
            dk = _dist_km(region["lat"], region["lon"], float(i["lat"]), float(i["lon"]))
            if dk <= region["radius_km"]:
                rows.append((dk, i))
        rows.sort(key=lambda r: r[0])
        out["region"] = {"name": region["name"], "radius_km": region["radius_km"],
                         "note": "approximate region (centroid + radius)"}
        out["in_region"] = len(rows)
        out["items"] = [{**_pick(i, ("title", "layer", "sev_label", "source")), "km_from_centre": round(dk),
                         "at": _ts(i.get("time"))} for dk, i in rows[:6]]
        if layer_want in (None, "flights"):
            try:
                fl = g.layer("flights").get("rows") or []
                out["flights_in_region"] = sum(
                    1 for r in fl if r[4] is not None and r[3] is not None
                    and _dist_km(region["lat"], region["lon"], float(r[4]), float(r[3])) <= region["radius_km"])
            except Exception:
                pass
    else:
        out["items_total"] = len(items)
        out["top"] = [{**_pick(i, ("title", "layer", "sev_label", "source")), "at": _ts(i.get("time"))}
                      for i in items[:6]]
    if layer_want in (None, "space") and s.get("space"):
        sp = s.get("space") or {}
        out["space_weather"] = _pick(sp, ("kp", "kp_label", "scale", "g", "s", "r", "summary")) or None
        out["space_alerts"] = len(sp.get("alerts") or [])
    osk = s.get("opensky") or {}
    if layer_want == "flights":
        out["opensky"] = _pick(osk, ("status", "credits_left", "next_at", "reason"))
    return out


async def r_secagent(state, q, cmd):
    a = _need(state, "secagent")
    s = a.snapshot()
    ls = s.get("last_scan") or {}
    return {"source": "cyber analyst (secagent · /api/secagent)", "as_of": _ts(ls.get("light") or ls.get("full")) or _now_s(),
            "score": s.get("score"), "counts": s.get("counts"), "running": s.get("running"),
            "last_full_scan": _ts(ls.get("full")), "last_light_scan": _ts(ls.get("light")),
            "brief": (s.get("brief") or {}).get("text") if isinstance(s.get("brief"), dict) else s.get("brief"),
            "findings": [_pick(f, ("severity", "title", "category", "fix")) for f in (s.get("findings") or [])[:6]],
            "new_since_last": len(s.get("new") or []), "resolved_since_last": len(s.get("resolved") or []),
            "accepted_risks": len(s.get("accepted") or [])}


async def r_computeruse(state, q, cmd):
    eng = _need(state, "computeruse")
    t = eng.task
    status = eng.status()
    out = {"source": "Computer Use (/api/computeruse)", "as_of": _now_s(), "status": status,
           "awaiting_approval": bool(eng.ctl and eng.ctl.pending),
           "paused_reason": (eng.ctl.paused_reason if eng.ctl else "") or None,
           "history_count": len(eng.history)}
    try:
        avail = [c.get("name") for c in (eng.candidates or []) if c.get("available")]
        out["vision_models_available"] = avail[:4]
    except Exception:
        pass
    if t:
        cur = {"status": t.get("status"), "step": t.get("n", 0), "max_steps": t.get("max_steps"),
               "started": _ts(t.get("started")), "ended": _ts(t.get("ended")), "model": t.get("adapter_name"),
               "requested_from": t.get("source")}
        if cmd:                                             # operator-only detail
            cur.update({"task": t.get("task"), "summary": t.get("summary") or None, "error": t.get("error") or None,
                        "last_steps": [_pick(s, ("desc", "result", "ok")) for s in (t.get("steps") or [])[-3:]]})
        out["task"] = cur
    if cmd:
        if eng.ctl and eng.ctl.pending:
            p = eng.ctl.pending
            out["pending_approval"] = _pick(p, ("kind", "reason", "category", "action"))
        out["recent_tasks"] = [_pick(h, ("task", "status", "summary", "steps")) | {"ended": _ts(h.get("ended"))}
                               for h in eng.history[:3]]
    else:
        out["note"] = "observer session: task text and steps are shown to the commander only"
    return out


async def r_phone(state, q, cmd):
    a = _need(state, "phone_agent")
    setup = a.setup() if hasattr(a, "setup") else {}
    out = {"source": "phone agent (/api/phone)", "as_of": _now_s(),
           "ready": bool((setup or {}).get("ready")), "active_calls": len(a.live),
           "analytics": a.store.analytics() if hasattr(a.store, "analytics") else None}
    calls = a.store.calls(5)
    if cmd:
        out["recent_calls"] = [{**_pick(c, ("direction", "contact_name", "status", "purpose", "duration", "summary",
                                            "end_reason")),
                                "number": _mask_number(c.get("to_number") if c.get("direction") == "outbound"
                                                       else c.get("from_number")),
                                "at": _ts(c.get("created"))} for c in calls]
        out["live"] = [{"direction": L.get("direction"), "name": L.get("name") or "", "status": L.get("status"),
                        "turns": len(L.get("turns") or [])} for L in list(a.live.values())[:3]]
        try:
            out["open_followups"] = [_pick(f, ("text", "due", "status")) for f in a.store.followups(status="open")[:4]]
        except Exception:
            pass
    else:
        out["recent_calls"] = [{**_pick(c, ("direction", "status", "duration")), "at": _ts(c.get("created"))}
                               for c in calls]
        out["note"] = "observer session: numbers and call summaries are shown to the commander only"
    return out


async def r_evolution(state, q, cmd):
    e = _need(state, "evolution")
    s = e.snapshot()
    cur = s.get("current") or {}
    tests = (s.get("tests") or {}).get("last") or {}
    return {"source": "evolution loop (/api/evolution)", "as_of": _now_s(), "mode": s.get("mode"),
            "autonomy": s.get("autonomy_effective"), "loop": s.get("loop"),
            "current": _pick(cur, ("title", "stage", "priority", "area")) or None,
            "awaiting_approval": [_pick(i, ("title", "stage", "priority")) for i in (s.get("approvals") or [])[:4]],
            "backlog_open": len(s.get("backlog") or []),
            "top_backlog": [_pick(i, ("title", "stage", "priority")) for i in (s.get("backlog") or [])[:4]],
            "last_tests": _pick(tests, ("ok", "passed", "failed", "total", "seconds")) | {"at": _ts(tests.get("ts"))}
            if isinstance(tests, dict) else None,
            "heartbeat": _pick(s.get("heartbeat") or {}, ("last", "status", "interval_min"))}


async def r_aicore(state, q, cmd):
    c = _need(state, "agentos")
    snap = c.snapshot()
    provs = snap.get("providers")
    if isinstance(provs, dict):
        provs = provs.get("providers") or provs.get("models") or list(provs.values())
    plist = []
    for p in (provs or [])[:10]:
        if isinstance(p, dict):
            plist.append(_pick(p, ("name", "id", "provider", "status", "available", "healthy", "kind", "local")))
    studio = snap.get("studio") or {}
    plugins = snap.get("plugins") or {}
    last = snap.get("last_decision") or {}
    return {"source": "AI Core (/api/agentos)", "as_of": _now_s(), "booted": snap.get("booted"),
            "providers": plist, "routing_policy": (snap.get("routing") or {}).get("policy"),
            "last_route": _pick(last, ("used", "explain", "task", "policy")),
            "memory": snap.get("memory"), "tools": len(snap.get("tools") or []),
            "pending_approvals": len(snap.get("approvals") or []),
            "workflows": len(studio.get("workflows") or []) if isinstance(studio, dict) else None,
            "custom_agents": len(studio.get("agents") or []) if isinstance(studio, dict) else None,
            "plugins": [_pick(p, ("name", "id", "enabled")) for p in (plugins.get("plugins") or [])[:6]]
            if isinstance(plugins, dict) else None,
            "cloud_allowed": snap.get("cloud_allowed"), "smart_router_active": snap.get("smart_active")}


async def r_selfheal(state, q, cmd):
    sh = _need(state, "selfheal")
    s = sh.snapshot()
    checks = s.get("checks") or {}
    bad = {k: _pick(v, ("status", "detail")) for k, v in checks.items() if v.get("status") not in ("ok", "idle")}
    return {"source": "self-heal watchdog (/api/health)", "as_of": _ts(s.get("checked_at")) or _now_s(),
            "overall": s.get("overall"), "auto_repair": s.get("auto"), "checks_total": len(checks),
            "not_ok": dict(list(bad.items())[:8]), "restarts_24h": s.get("restarts_24h"),
            "recent_log": [(_pick(x, ("msg", "level")) if isinstance(x, dict) else str(x)) for x in (s.get("log") or [])[:3]]}


async def r_enterprise(state, q, cmd):
    rb = state.get("rbac") or {}
    out = {"source": "enterprise (/api/enterprise)", "as_of": _now_s(), "role": rb.get("role"),
           "operator": rb.get("operator")}
    a = state.get("audit")
    if a is not None:
        try:
            snap = a.snapshot()
            out["audit"] = {k: v for k, v in snap.items() if isinstance(v, (int, float, str))}
        except Exception:
            pass
    m = _main()
    if cmd and m is not None and hasattr(m, "_load_operators"):
        out["operators"] = [_pick(o, ("name", "role")) for o in (await asyncio.to_thread(m._load_operators))[:10]]
    return out


async def r_licence(state, q, cmd):
    lic = _need(state, "licensing")
    s = lic.snapshot()
    return {"source": "licence (/api/license) — keys are never shown", "as_of": _now_s(),
            **_pick(s, ("edition", "paid", "licensed", "expired", "days_left", "owner")),
            "expires": _ts(s.get("expires_at")),
            "entitlements": s.get("entitlements")}


async def r_brain(state, q, cmd):
    b = _need(state, "brain")
    out = {"source": "brain / LLM switch (/api/llm/providers, /api/models)", "as_of": _now_s(),
           "brain_mode": getattr(b, "mode", None), "reply_path": getattr(b, "provider_name", None),
           "replies_by_path": dict(getattr(b, "route_counts", {}) or {}),
           "last_ms_by_path": dict(getattr(b, "route_ms", {}) or {}),
           "cloud_locked": getattr(b, "cloud_locked", False)}
    reg = state.get("providers")
    if reg is not None:
        try:
            out["active_provider"] = reg.active
            out["providers"] = [_pick(p, ("id", "name", "active", "locked", "healthy", "model")) for p in reg.list()[:8]]
        except Exception:
            pass
    llm = state.get("local_llm")
    if llm is not None:
        out["local_model"] = getattr(llm, "model", None)
        out["local_available"] = getattr(llm, "available", None)
    tts = state.get("tts")
    if tts is not None:
        out["conversation_mode"] = bool(getattr(tts, "conversation_mode", False))
    return out


async def r_roadmap(state, q, cmd):
    r = _need(state, "roadmap")
    s = r.snapshot()
    return {"source": "product evolution / roadmap (/api/roadmap)", "as_of": _now_s(),
            **_pick(s, ("version", "progress_pct", "completed_features", "total_features", "current_phase",
                        "in_development", "upcoming", "failing_tests"))}


# section key → (title, reader, routing regex)
SECTIONS: dict[str, tuple[str, object, str]] = {
    "system": ("System metrics & processes", r_system,
               r"\b(cpu|processor|ram|memory usage|disk|storage|telemetry|vitals|system (load|status|health)|"
               r"process(es)?|what'?s running|using (the most|most)|hogging|uptime|battery|my (pc|computer|laptop|machine))\b"),
    "hardware": ("Hardware", r_hardware, r"\b(hardware|gpu|graphics card|cores|specs?|peripherals?|monitor(s)? connected|usb)\b"),
    "network": ("Network & connectivity", r_network,
                r"\b(network|internet|wi-?fi|bluetooth|latency|ping|speed ?test|bandwidth|connectivity|connection|uplink|packet loss)\b"),
    "threats": ("Threats & threat matrix", r_threats,
                r"\b(threats?|incidents?|risk (score|level|matrix)|threat matrix|soc|alerts? open|security status|response queue)\b"),
    "insights": ("Insights / AI ops", r_insights,
                 r"\b(insights?|aiops|ai ops|readiness|anomal(y|ies)|recommendations?|briefing)\b"),
    "decisions": ("Decisions", r_decisions, r"\b(decisions?|proposals?|autonomy|proposed actions?)\b"),
    "missions": ("Orchestrator / mission board", r_missions,
                 r"\b(missions?|directives?|orchestrator|mission board|blackboard|agents? (doing|status|busy)|team status|avengers doing)\b"),
    "productivity": ("Productivity & notes", r_productivity,
                     r"\b(productivity|focus (time|session|score)|notes?|my tasks?|to-?dos?|context switch)\b"),
    "agenda": ("Calendar / agenda / inbox", r_agenda,
               r"\b(calendar|agenda|meetings?|schedule|appointments?|inbox|e-?mails?|gmail|google tasks)\b"),
    "weather": ("Weather", r_weather, r"\b(weather|temperature|forecast|rain(ing)?|humidity|wind)\b"),
    "news": ("News / intel ticker", r_news, r"\b(news|headlines?|ticker|intel feed|what'?s happening)\b"),
    "fleet": ("Fleet / nodes", r_fleet, r"\b(fleet|nodes?|other machines?|cluster)\b"),
    "devices": ("Devices", r_devices, r"\b(devices?|smart home|lights?|plugs?|cameras?|thermostat|home assistant|iot)\b"),
    "disaster": ("Disaster events", r_disaster,
                 r"\b(disasters?|earthquakes?|quakes?|cyclones?|floods?|wildfires?|tsunami|volcan(o|oes|ic)|"
                 r"outages?|emergenc(y|ies)|ndma|gdacs|usgs|natural events?)\b"),
    "globalmap": ("Global map layers", r_globalmap,
                  r"\b(global map|world map|the map|on the map|map layers?|flights?|planes?|aircraft|air traffic|"
                  r"satellites?|space weather|aurora|solar (storm|flare)|geomagnetic|kp index|internet outages?|"
                  r"gdacs|quakes?|earthquakes?|volcan(o|oes))\b"),
    "secagent": ("Cyber analyst findings", r_secagent,
                 r"\b(cyber ?analyst|secagent|security (scan|findings?|score|agent|analyst)|vulnerabilit(y|ies)|cves?|"
                 r"findings?|exposure|dependency (issues?|risks?))\b"),
    "computeruse": ("Computer Use", r_computeruse,
                    r"\b(computer[- ]use|computeruse|using the computer|the computer (doing|task)|cu task|screen agent)\b"),
    "phone": ("Phone agent", r_phone, r"\b(phone|calls?|caller|voicemail|twilio|dial(ed|led)?|rang)\b"),
    "evolution": ("Evolution loop", r_evolution,
                  r"\b(evolution|self[- ]improv\w*|backlog|sandbox|improvements?|upgrade proposals?)\b"),
    "aicore": ("AI Core", r_aicore,
               r"\b(ai core|agentos|providers?|routing|router|workflows?|plugins?|ai memory|tool approvals?|models? available)\b"),
    "selfheal": ("Self-heal", r_selfheal, r"\b(self[- ]?heal\w*|health checks?|watchdog|repairs?|system health)\b"),
    "enterprise": ("Enterprise / operators", r_enterprise,
                   r"\b(enterprise|operators?|audit (log|trail)|roles?|commander|observer|rbac)\b"),
    "licence": ("Licence", r_licence, r"\b(licen[cs]e|subscription|pro plan|edition|expir(y|es|ation)|entitlements?)\b"),
    "brain": ("Active brain / model", r_brain,
              r"\b(brain|which (model|llm|ai)|what model|active model|llm|ollama|claude|local model|conversation mode)\b"),
    "roadmap": ("Roadmap", r_roadmap, r"\b(roadmap|product evolution|which phase|version progress)\b"),
}
_COMPILED = {k: re.compile(v[2], re.I) for k, v in SECTIONS.items()}
_OVERVIEW = re.compile(r"\b(dashboard|overview|status report|sitrep|situation report|everything|all sections|"
                       r"full status|what'?s going on|anything (i should know|important))\b", re.I)
# a bare command like "dashboard overview" / "sitrep" is a request even without a question word
_OVERVIEW_CMD = re.compile(r"\b(overview|sitrep|status report|situation report|full status)\b", re.I)
OVERVIEW_SET = ("system", "threats", "secagent", "disaster", "computeruse", "selfheal")
# question words that make a keyword hit a real question about live state (not e.g. "write a poem about rain")
_ASKY = re.compile(r"\b(what|whats|what's|how|any|show|tell|give|list|status|state|is|are|did|do|does|which|who|"
                   r"when|where|why|current|latest|last|now|open|summar\w*|report|check|count|many|much|near)\b|\?", re.I)


def route(question: str, limit: int = MAX_SECTIONS) -> list[str]:
    """Sections relevant to a question, best first. [] when it isn't about the dashboard."""
    q = (question or "").strip()
    if not q or not (_ASKY.search(q) or _OVERVIEW_CMD.search(q)):
        return []
    hits: list[tuple[int, int, str]] = []
    for i, (k, rx) in enumerate(_COMPILED.items()):
        ms = list(rx.finditer(q))
        if ms:
            hits.append((-len(ms), ms[0].start(), k))
    hits.sort()
    out = [k for _, _, k in hits]
    # a region question about the map should also see the disaster feed (and vice versa for "near me")
    if "globalmap" in out and "disaster" not in out and re.search(r"\b(quake|earthquake|disaster|near me)\b", q, re.I):
        out.append("disaster")
    if "disaster" in out and "globalmap" not in out and region_of(q):
        out.append("globalmap")
    overview = False
    if not out and _OVERVIEW.search(q):
        out, overview = list(OVERVIEW_SET), True
    seen, res = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            res.append(k)
    return res[:max(limit, len(OVERVIEW_SET))] if overview else res[:limit]


# ═══ reading ══════════════════════════════════════════════════════════════════
async def read_section(state: dict, section: str, question: str = "", commander: bool | None = None,
                       budget: float = SECTION_BUDGET_S) -> dict:
    """One section, sanitized. Never raises; a failure is a clean 'unavailable' entry."""
    if section not in SECTIONS:
        return {"section": section, "ok": False, "unavailable": f"unknown section — one of {', '.join(SECTIONS)}",
                "source": None, "as_of": _now_s()}
    title, fn, _ = SECTIONS[section]
    cmd = _role(state) if commander is None else bool(commander)
    base = {"section": section, "title": title}
    try:
        data = await asyncio.wait_for(fn(state, question or "", cmd), timeout=budget)
        if not isinstance(data, dict):
            raise Unavailable("reader returned nothing")
        data.setdefault("source", title)
        data.setdefault("as_of", _now_s())
        return sanitize({**base, "ok": True, **data})
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        why = f"no answer within {budget:.1f} s"
    except Unavailable as e:
        why = str(e)
    except Exception as e:
        why = f"{type(e).__name__}"
    return sanitize({**base, "ok": False, "unavailable": why, "source": title, "as_of": _now_s()})


async def read_sections(state: dict, sections: list[str], question: str = "", commander: bool | None = None,
                        total_budget: float = TOTAL_BUDGET_S) -> list[dict]:
    sections = [s for s in sections if s in SECTIONS]
    if not sections:
        return []
    per = min(SECTION_BUDGET_S, total_budget)
    tasks = [asyncio.create_task(read_section(state, s, question, commander, per)) for s in sections]
    done, pending = await asyncio.wait(tasks, timeout=total_budget + 0.2)
    for t in pending:
        t.cancel()
    out = []
    for s, t in zip(sections, tasks):
        if t in done and not t.cancelled() and t.exception() is None:
            out.append(t.result())
        else:
            out.append({"section": s, "title": SECTIONS[s][0], "ok": False, "unavailable": "timed out",
                        "source": SECTIONS[s][0], "as_of": _now_s()})
    return out


def _compact(entry: dict, limit: int = SECTION_CHARS) -> str:
    body = {k: v for k, v in entry.items() if k not in ("section", "title", "source", "as_of", "ok")}
    txt = json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(txt) > limit:
        txt = txt[:limit] + "…"
    return scrub_text(txt)


def render_block(entries: list[dict], limit: int = BLOCK_CHARS) -> str:
    if not entries:
        return ""
    head = (f"[DASHBOARD FACTS — read live from the BUCS dashboard at {datetime.now().strftime('%H:%M:%S')} "
            f"(sections: {', '.join(e['title'] for e in entries)}). For questions about the dashboard, answer ONLY "
            "from these facts and name the panel you used (e.g. \"per the Threats panel\"). If a section is "
            "unavailable or doesn't contain the answer, say so plainly — never invent values.\n")
    lines, used = [], len(head) + 2
    per = max(250, min(SECTION_CHARS, (limit - len(head)) // max(1, len(entries))))
    for e in entries:
        if e.get("ok"):
            line = f"• {e['title']} (source: {e.get('source')}; as of {e.get('as_of')}): {_compact(e, per)}"
        else:
            line = f"• {e['title']}: UNAVAILABLE — {e.get('unavailable')}"
        if used + len(line) + 1 > limit:
            break
        lines.append(line)
        used += len(line) + 1
    return head + "\n".join(lines) + "\n]\n"


async def context_for(state: dict | None, question: str, commander: bool | None = None,
                      meta: dict | None = None) -> str:
    """The bounded [DASHBOARD FACTS …] block for a question, or "" when it isn't about the dashboard.
    Never raises, never waits past TOTAL_BUDGET_S."""
    try:
        state = state if state is not None else _STATE
        if not state:
            return ""
        secs = route(question)
        if not secs:
            return ""
        cmd = _role(state) if commander is None else bool(commander)
        key = (tuple(secs), cmd, (question or "").strip().lower()[:200])
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_S:
            block = hit[1]
        else:
            entries = await read_sections(state, secs, question, cmd)
            block = render_block(entries)
            _cache.clear() if len(_cache) > 64 else None
            _cache[key] = (time.time(), block)
        LAST.clear()
        LAST.update({"ts": time.time(), "question": (question or "")[:200], "sections": secs, "chars": len(block),
                     "commander": cmd, "block": block, **(meta or {})})
        return block
    except Exception:
        return ""


def catalog() -> list[dict]:
    return [{"section": k, "title": v[0]} for k, v in SECTIONS.items()]


def computeruse_status_line(state: dict, commander: bool | None = None) -> str:
    """Spoken one-liner for "computer use status" (fast path)."""
    eng = state.get("computeruse")
    if eng is None:
        return "Computer Use isn't loaded on this install, sir."
    cmd = _role(state) if commander is None else bool(commander)
    st = eng.status()
    t = eng.task
    if not t:
        return "Computer Use is idle. No task has run since start-up." if not eng.history else \
            f"Computer Use is idle. The last task {eng.history[0].get('status', 'ended')}."
    what = f" on “{scrub_text(t.get('task'))[:120]}”" if cmd else ""
    line = f"Computer Use is {st}{what}, step {t.get('n', 0)} of {t.get('max_steps')}."
    if eng.ctl and eng.ctl.pending:
        line += " It is waiting for your approval on the Computer Use panel."
    elif st in ("done", "failed", "stopped", "error") and cmd and (t.get("summary") or t.get("error")):
        line += " " + scrub_text(t.get("summary") or t.get("error"))[:200]
    return line


# ═══ wiring (main.py FEATURE_MODULES) ═════════════════════════════════════════
def build_router(ctx):
    from fastapi import APIRouter
    st = ctx["state"]
    bind(st)
    r = APIRouter()

    @r.get("/api/dashboard/context")
    async def dc_catalog(q: str = ""):
        """Sections the agents can read, and (with ?q=) which ones a question would use."""
        return {"sections": catalog(), "route": route(q[:500]) if q else []}

    @r.get("/api/dashboard/context/read/{section}")
    async def dc_read(section: str, q: str = ""):
        return await read_section(st, section, q[:500], ctx["commander"]())

    @r.get("/api/dashboard/context/last")
    async def dc_last():
        """What the reply path last received (sections + size; the facts themselves for the commander)."""
        out = {k: v for k, v in LAST.items() if k != "block"}
        if ctx["commander"]() and LAST.get("block"):
            out["block"] = LAST["block"]
        return out

    return r
