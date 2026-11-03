"""DISASTER RESPONSE · REAL-TIME EVENTS

Physical and digital events from free, keyless public feeds. Nothing is invented:
every item names its source and when it was fetched, and a source that cannot be
reached says so (stale data is kept and marked stale, never dressed up as fresh).

Physical
  USGS        earthquakes M4.5+ past day + significant past week (GeoJSON)
  GDACS       current global alerts: cyclones, floods, droughts, volcanoes, wildfires, quakes
  NASA EONET  open natural events (v3): wildfires, severe storms, volcanoes, ...
  NDMA SACHET India's CAP alert feed (IMD + state disaster management authorities)
Digital
  CISA KEV    newest Known Exploited Vulnerabilities
  Statuspage  live status of major services (GitHub, Cloudflare, OpenAI, ...)
  IODA        country-level internet outage signals (Georgia Tech)

Each source is fetched independently with its own timeout and TTL; one failing
feed never affects the others. Read-only: the module only GETs public JSON.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

POLL_EVERY = 300            # loop wake-up; each source also has its own TTL below
FIRST_POLL_DELAY = 8
REFRESH_MIN_GAP = 60        # POST /api/disaster/refresh at most once a minute
NEAR_KM = 500               # "near you" radius
HTTP_TIMEOUT = 15.0
UA = "BUCS-DisasterResponse/1.0 (+local dashboard; read-only)"
MAX_ALERTS_PER_CYCLE = 5

SEV_LABEL = {4: "CRITICAL", 3: "HIGH", 2: "MEDIUM", 1: "LOW", 0: "INFO"}

PHYSICAL_TYPES = {"earthquake", "cyclone", "storm", "flood", "rain", "thunderstorm",
                  "wildfire", "volcano", "drought", "tsunami", "coastal", "heat",
                  "landslide", "ice", "dust", "snow", "other"}

# ───────────────────────── helpers ─────────────────────────


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (mean Earth radius 6371 km)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _valid_ll(lat, lon) -> tuple[float, float] | None:
    la, lo = _num(lat), _num(lon)
    if la is None or lo is None or not (-90 <= la <= 90) or not (-180 <= lo <= 180):
        return None
    return la, lo


def _url(u) -> str | None:
    """Only plain http(s) links ever reach the dashboard."""
    if isinstance(u, str) and re.match(r"^https?://[^\s\"'<>]+$", u.strip()):
        return u.strip()[:500]
    return None


def _txt(s, n: int = 240) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _iso_ts(s) -> float | None:
    """ISO-8601 → epoch seconds. Naive timestamps are treated as UTC."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


_IST = timezone(timedelta(hours=5, minutes=30))


def _sachet_ts(s) -> float | None:
    """'Tue Nov 03 09:20:00 IST 2026' → epoch seconds."""
    if not isinstance(s, str):
        return None
    m = re.match(r"^\w{3} (\w{3}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (\w+) (\d{4})$", s.strip())
    if not m:
        return None
    mon, day, hh, mm, ss, tz, yr = m.groups()
    try:
        d = datetime.strptime(f"{yr} {mon} {day} {hh}:{mm}:{ss}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None
    return d.replace(tzinfo=_IST if tz == "IST" else timezone.utc).timestamp()


def _item(**kw) -> dict:
    sev = int(max(0, min(4, kw.get("severity", 0))))
    kw["severity"] = sev
    kw["sev_label"] = SEV_LABEL[sev]
    kw["title"] = _txt(kw.get("title"), 160)
    kw["detail"] = _txt(kw.get("detail"), 280)
    kw["link"] = _url(kw.get("link"))
    kw.setdefault("lat", None)
    kw.setdefault("lon", None)
    return kw


# ───────────────────────── parsers (pure, unit-tested) ─────────────────────────


def parse_usgs(payload: dict, src: str = "USGS") -> list[dict]:
    out = []
    for f in (payload or {}).get("features") or []:
        p = f.get("properties") or {}
        coords = ((f.get("geometry") or {}).get("coordinates") or [None, None])
        ll = _valid_ll(coords[1] if len(coords) > 1 else None, coords[0])
        mag = _num(p.get("mag"))
        if mag is None:
            continue
        sev = 4 if mag >= 7 else 3 if mag >= 6 else 2 if mag >= 5 else 1
        pager = (p.get("alert") or "").lower()
        sev = max(sev, {"red": 4, "orange": 3, "yellow": 2}.get(pager, 0))
        if p.get("tsunami"):
            sev = max(sev, 3)
        depth = _num(coords[2]) if len(coords) > 2 else None
        bits = [f"M{mag:.1f}"]
        if depth is not None:
            bits.append(f"depth {depth:.0f} km")
        if pager:
            bits.append(f"PAGER {pager}")
        if p.get("tsunami"):
            bits.append("tsunami flag set (check official warning centres)")
        t = _num(p.get("time"))
        out.append(_item(
            id="usgs:" + str(f.get("id") or p.get("code")), category="physical", type="earthquake",
            title=p.get("title") or f"M{mag:.1f} {p.get('place') or ''}",
            detail=" · ".join(bits), severity=sev, mag=mag,
            lat=ll[0] if ll else None, lon=ll[1] if ll else None,
            time=(t / 1000) if t else None, source=src, link=p.get("url")))
    return out


_GDACS_TYPE = {"EQ": "earthquake", "TC": "cyclone", "FL": "flood", "VO": "volcano",
               "DR": "drought", "WF": "wildfire", "TS": "tsunami"}


def parse_gdacs(payload: dict) -> list[dict]:
    out = []
    for f in (payload or {}).get("features") or []:
        p = f.get("properties") or {}
        if str(p.get("iscurrent", "true")).lower() != "true":
            continue
        level = str(p.get("alertlevel") or "").capitalize()
        sev = {"Red": 4, "Orange": 3, "Green": 1}.get(level, 1)
        g = f.get("geometry") or {}
        ll = None
        if g.get("type") == "Point":
            c = g.get("coordinates") or []
            if len(c) >= 2:
                ll = _valid_ll(c[1], c[0])
        sd = p.get("severitydata") or {}
        detail = " · ".join(x for x in (
            f"{level} alert" if level else "",
            _txt(p.get("country"), 80),
            _txt(sd.get("severitytext"), 120)) if x)
        url = p.get("url") or {}
        out.append(_item(
            id=f"gdacs:{p.get('eventtype')}:{p.get('eventid')}", category="physical",
            type=_GDACS_TYPE.get(p.get("eventtype"), "other"),
            title=p.get("name") or p.get("description") or "GDACS event",
            detail=detail, severity=sev, alertlevel=level or None,
            lat=ll[0] if ll else None, lon=ll[1] if ll else None,
            time=_iso_ts(p.get("datemodified")) or _iso_ts(p.get("todate")) or _iso_ts(p.get("fromdate")),
            source="GDACS", link=url.get("report") if isinstance(url, dict) else None))
    return out


_EONET_TYPE = {"wildfires": "wildfire", "severeStorms": "storm", "volcanoes": "volcano",
               "floods": "flood", "seaLakeIce": "ice", "drought": "drought",
               "dustHaze": "dust", "earthquakes": "earthquake", "landslides": "landslide",
               "snow": "snow", "tempExtremes": "heat"}


def _centroid(geom: dict) -> tuple[float, float] | None:
    c = geom.get("coordinates")
    if geom.get("type") == "Point" and c and len(c) >= 2:
        return _valid_ll(c[1], c[0])
    if geom.get("type") == "Polygon" and c and c[0]:
        ring = [p for p in c[0] if isinstance(p, (list, tuple)) and len(p) >= 2]
        if ring:
            return _valid_ll(sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))
    return None


def parse_eonet(payload: dict) -> list[dict]:
    out = []
    for e in (payload or {}).get("events") or []:
        if e.get("closed"):
            continue
        cats = e.get("categories") or [{}]
        cat = cats[0].get("id") or ""
        typ = _EONET_TYPE.get(cat, "other")
        title = e.get("title") or "EONET event"
        geoms = [g for g in (e.get("geometry") or []) if isinstance(g, dict)]
        geoms.sort(key=lambda g: g.get("date") or "")
        last = geoms[-1] if geoms else {}
        ll = _centroid(last) if last else None
        mag, unit = _num(last.get("magnitudeValue")), last.get("magnitudeUnit") or ""
        if typ == "storm" and re.search(r"cyclone|typhoon|hurricane", title, re.I):
            typ = "cyclone"
        if typ in ("storm", "cyclone"):
            sev = 3 if (mag or 0) >= 64 else 2 if (mag or 0) >= 34 else 1   # kts: hurricane / tropical storm
        elif typ == "volcano":
            sev = 2
        elif typ in ("ice", "dust", "snow"):
            sev = 0
        else:
            sev = 1
        detail = " · ".join(x for x in (
            cats[0].get("title") or "",
            f"{mag:g} {unit}" if mag is not None else "",
            ", ".join(s.get("id", "") for s in (e.get("sources") or []) if isinstance(s, dict))) if x)
        out.append(_item(
            id="eonet:" + str(e.get("id")), category="physical", type=typ, title=title,
            detail=detail, severity=sev, lat=ll[0] if ll else None, lon=ll[1] if ll else None,
            time=_iso_ts(last.get("date")), source="NASA EONET", link=e.get("link")))
    return out


def _sachet_type(t: str) -> str:
    t = (t or "").lower()
    for key, typ in (("cyclone", "cyclone"), ("earthquake", "earthquake"), ("tsunami", "tsunami"),
                     ("flood", "flood"), ("landslide", "landslide"), ("lightning", "thunderstorm"),
                     ("thunder", "thunderstorm"), ("rain", "rain"), ("wave", "coastal"),
                     ("heat", "heat"), ("cold", "other"), ("fire", "wildfire"),
                     ("avalanche", "snow"), ("snow", "snow"), ("drought", "drought")):
        if key in t:
            return typ
    return "other"


def parse_sachet(payload: list, now: float | None = None) -> list[dict]:
    """NDMA SACHET CAP alerts (India). Only alerts still in effect are kept.
    Warning text is shown only when it is English; otherwise type + area + issuer."""
    now = now or time.time()
    out = []
    for a in payload or []:
        if not isinstance(a, dict):
            continue
        end = _sachet_ts(a.get("effective_end_time"))
        if end is not None and end < now:
            continue
        color = str(a.get("severity_color") or "").lower()
        sev = {"red": 3, "orange": 2, "yellow": 1}.get(color, 1)
        ll = None
        cen = str(a.get("centroid") or "").split(",")
        if len(cen) == 2:
            ll = _valid_ll(cen[1], cen[0])
        dtype = _txt(a.get("disaster_type"), 60) or "Alert"
        area = _txt(str(a.get("area_description") or "").replace("_", " "), 140)
        msg = a.get("warning_message") if str(a.get("actual_lang")).lower() in ("en", "english") else ""
        detail = " · ".join(x for x in (str(a.get("severity") or "").title(), area,
                                         _txt(a.get("alert_source"), 60)) if x)
        if msg:
            detail = _txt(msg, 200) + " · " + _txt(a.get("alert_source"), 60)
        out.append(_item(
            id=f"sachet:{a.get('identifier')}", category="physical", type=_sachet_type(dtype),
            title=f"{dtype} — {area}" if area else dtype, detail=detail, severity=sev,
            lat=ll[0] if ll else None, lon=ll[1] if ll else None,
            time=_sachet_ts(a.get("effective_start_time")), until=end,
            source="NDMA SACHET", link="https://sachet.ndma.gov.in/"))
    return out


def parse_kev(payload: dict, limit: int = 15) -> list[dict]:
    vulns = [v for v in (payload or {}).get("vulnerabilities") or [] if isinstance(v, dict)]
    vulns.sort(key=lambda v: (v.get("dateAdded") or "", v.get("cveID") or ""), reverse=True)
    out = []
    for v in vulns[:limit]:
        ransom = str(v.get("knownRansomwareCampaignUse") or "").lower() == "known"
        cve = _txt(v.get("cveID"), 40)
        detail = " · ".join(x for x in (
            f"{v.get('vendorProject') or ''} {v.get('product') or ''}".strip(),
            "used in ransomware campaigns" if ransom else "",
            f"federal due date {v.get('dueDate')}" if v.get("dueDate") else "",
            _txt(v.get("requiredAction"), 120)) if x)
        out.append(_item(
            id="kev:" + cve, category="digital", type="vulnerability",
            title=f"{cve} · {v.get('vulnerabilityName') or ''}", detail=detail,
            severity=3 if ransom else 2, time=_iso_ts(v.get("dateAdded")), source="CISA KEV",
            link=f"https://nvd.nist.gov/vuln/detail/{cve}" if re.match(r"^CVE-\d{4}-\d+$", cve) else None))
    return out


_IMPACT_SEV = {"critical": 4, "major": 3, "minor": 2, "none": 1, "maintenance": 1}
_INDICATOR_SEV = {"critical": 4, "major": 3, "minor": 2, "maintenance": 1, "none": 0}


def parse_statuspage(name: str, status: dict, unresolved: dict | None, page_url: str) -> tuple[dict, list[dict]]:
    """Atlassian Statuspage /api/v2/status.json + /api/v2/incidents/unresolved.json."""
    st = (status or {}).get("status") or {}
    ind = str(st.get("indicator") or "unknown").lower()
    incidents = []
    for inc in (unresolved or {}).get("incidents") or []:
        impact = str(inc.get("impact") or "none").lower()
        incidents.append(_item(
            id=f"status:{name}:{inc.get('id')}", category="digital", type="service",
            title=f"{name}: {inc.get('name') or 'incident'}",
            detail=f"{impact} impact · {inc.get('status') or ''}",
            severity=_IMPACT_SEV.get(impact, 1),
            time=_iso_ts(inc.get("updated_at")) or _iso_ts(inc.get("created_at")),
            source=f"{name} Statuspage", link=inc.get("shortlink") or page_url))
    desc = _txt(st.get("description"), 80) or ind
    sev = _INDICATOR_SEV.get(ind, 0)
    if sev >= 2 and not incidents:     # degraded components without a declared incident
        incidents.append(_item(
            id=f"status:{name}:indicator:{ind}", category="digital", type="service",
            title=f"{name}: {desc}", detail=f"status indicator '{ind}' (no open incident declared)",
            severity=sev, time=_iso_ts(((status or {}).get("page") or {}).get("updated_at")),
            source=f"{name} Statuspage", link=page_url))
    svc = {"name": name, "indicator": ind, "description": desc,
           "severity": sev, "incidents": len(incidents), "url": _url(page_url)}
    return svc, incidents


def parse_slack(payload: dict) -> tuple[dict, list[dict]]:
    """Slack's own status API (api/v2.0.0/current)."""
    s = str((payload or {}).get("status") or "unknown").lower()
    items = []
    for inc in (payload or {}).get("active_incidents") or []:
        kind = str(inc.get("type") or "incident").lower()
        items.append(_item(
            id=f"status:Slack:{inc.get('id')}", category="digital", type="service",
            title=f"Slack: {inc.get('title') or kind}", detail=f"{kind} · {inc.get('status') or ''}",
            severity=3 if kind == "outage" else 2, time=_iso_ts(inc.get("date_updated")),
            source="Slack Status", link=inc.get("url") or "https://slack-status.com/"))
    sev = 0 if s == "ok" else max([i["severity"] for i in items] or [2])
    svc = {"name": "Slack", "indicator": "none" if s == "ok" else "major" if sev >= 3 else "minor",
           "description": "All systems operational" if s == "ok" else f"{len(items)} active incident(s)",
           "severity": sev, "incidents": len(items), "url": "https://slack-status.com/"}
    return svc, items


def parse_ioda(payload: dict, now: float | None = None, limit: int = 12) -> list[dict]:
    """IODA country-level outage events still ongoing in the last hour."""
    now = now or time.time()
    out = []
    for ev in (payload or {}).get("data") or []:
        ent = ev.get("entity") or {}
        if ent.get("type") != "country":
            continue
        until = _num(ev.get("until"))
        if until is None or until < now - 3600:
            continue
        score = _num(ev.get("score")) or 0
        frm = _num(ev.get("from"))
        hrs = (now - frm) / 3600 if frm else None
        out.append(_item(
            id=f"ioda:{ent.get('code')}:{ev.get('datasource')}", category="digital", type="outage",
            title=f"Internet outage signal · {ent.get('name') or ent.get('code')}",
            detail=" · ".join(x for x in (f"{ev.get('datasource') or ''} signal".strip(),
                                          f"score {score:,.0f}",
                                          f"ongoing {hrs:.0f} h" if hrs is not None else "") if x),
            severity=3 if score >= 1_000_000 else 2, score=score, time=frm, source="IODA (Georgia Tech)",
            link=f"https://ioda.inetintel.cc.gatech.edu/country/{ent.get('code')}"
            if re.match(r"^[A-Z]{2}$", str(ent.get("code") or "")) else None))
    # one row per country (several datasources may fire), strongest first
    best: dict[str, dict] = {}
    for it in sorted(out, key=lambda i: -i["score"]):
        best.setdefault(it["title"], it)
    return list(best.values())[:limit]


def dedupe_quakes(items: list[dict], km: float = 150, secs: float = 24 * 3600) -> list[dict]:
    """Drop GDACS earthquakes that USGS already reports (same place, same time)."""
    usgs = [i for i in items if i["source"] == "USGS" and i["lat"] is not None and i.get("time")]
    keep = []
    for i in items:
        if i["source"] == "GDACS" and i["type"] == "earthquake" and i["lat"] is not None:
            if any(abs((u["time"] or 0) - (i.get("time") or 0)) < secs
                   and haversine_km(u["lat"], u["lon"], i["lat"], i["lon"]) < km for u in usgs):
                continue
        keep.append(i)
    return keep


def sort_items(items: list[dict]) -> list[dict]:
    """Severity first, then nearer-to-operator, then newest."""
    return sorted(items, key=lambda i: (-i["severity"], 0 if i.get("near") else 1, -(i.get("time") or 0)))


# ───────────────────────── sources ─────────────────────────

STATUSPAGES = [
    ("GitHub", "https://www.githubstatus.com"),
    ("Cloudflare", "https://www.cloudflarestatus.com"),
    ("OpenAI", "https://status.openai.com"),
    ("Claude", "https://status.anthropic.com"),
    ("Discord", "https://discordstatus.com"),
    ("Zoom", "https://status.zoom.us"),
    ("Reddit", "https://www.redditstatus.com"),
]

Fetcher = Callable[[str], Awaitable[Any]]


async def src_usgs(get: Fetcher) -> dict:
    day = await get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson")
    items = parse_usgs(day)
    try:   # significant past week adds big quakes that fell out of the 24 h window
        sig = await get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.geojson")
        seen = {i["id"] for i in items}
        items += [i for i in parse_usgs(sig) if i["id"] not in seen]
    except Exception:
        pass
    return {"items": items}


async def src_gdacs(get: Fetcher) -> dict:
    return {"items": parse_gdacs(await get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"))}


async def src_eonet(get: Fetcher) -> dict:
    return {"items": parse_eonet(await get("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=14"))}


async def src_sachet(get: Fetcher) -> dict:
    return {"items": parse_sachet(await get("https://sachet.ndma.gov.in/cap_public_website/FetchAllAlertDetails"))}


async def src_kev(get: Fetcher) -> dict:
    d = await get("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
    return {"items": parse_kev(d), "meta": {"catalog": _txt(d.get("catalogVersion"), 20),
                                            "total": d.get("count")}}


async def src_status(get: Fetcher) -> dict:
    async def one(name, base):
        try:
            st = await get(base + "/api/v2/status.json")
            un = None
            for path in ("/api/v2/incidents/unresolved.json", "/api/v2/summary.json"):
                try:          # some pages (OpenAI) only serve summary.json
                    un = await get(base + path)
                    break
                except Exception:
                    continue
            return parse_statuspage(name, st, un, base)
        except Exception as e:
            return ({"name": name, "indicator": "unavailable", "severity": 0, "incidents": 0,
                     "description": f"unreachable ({type(e).__name__})", "url": base}, [])

    async def slack():
        try:
            return parse_slack(await get("https://status.slack.com/api/v2.0.0/current"))
        except Exception as e:
            return ({"name": "Slack", "indicator": "unavailable", "severity": 0, "incidents": 0,
                     "description": f"unreachable ({type(e).__name__})", "url": "https://slack-status.com/"}, [])

    res = await asyncio.gather(*(one(n, b) for n, b in STATUSPAGES), slack())
    services = [r[0] for r in res]
    if all(s["indicator"] == "unavailable" for s in services):
        raise RuntimeError("no status page reachable")
    return {"items": [i for r in res for i in r[1]], "services": services}


async def src_ioda(get: Fetcher) -> dict:
    now = int(time.time())
    d = await get("https://api.ioda.inetintel.cc.gatech.edu/v2/outages/events"
                  f"?from={now - 86400}&until={now}&entityType=country&format=ioda")
    return {"items": parse_ioda(d, now)}


# key, label, category, fn, ttl seconds, homepage
SOURCES = [
    ("usgs", "USGS Earthquakes", "physical", src_usgs, 300, "https://earthquake.usgs.gov/earthquakes/map/"),
    ("gdacs", "GDACS Global Alerts", "physical", src_gdacs, 300, "https://www.gdacs.org/"),
    ("eonet", "NASA EONET", "physical", src_eonet, 600, "https://eonet.gsfc.nasa.gov/"),
    ("sachet", "NDMA SACHET (India)", "physical", src_sachet, 300, "https://sachet.ndma.gov.in/"),
    ("kev", "CISA KEV", "digital", src_kev, 6 * 3600, "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"),
    ("status", "Service Status Pages", "digital", src_status, 300, None),
    ("ioda", "IODA Internet Outages", "digital", src_ioda, 900, "https://ioda.inetintel.cc.gatech.edu/"),
]

# ───────────────────────── preparedness (static, factual) ─────────────────────────

PREPAREDNESS = {
    "earthquake": ["DROP, COVER and HOLD ON under sturdy furniture; keep away from windows and heavy fixtures.",
                   "If outdoors, move to an open area away from buildings, trees and power lines.",
                   "After shaking: expect aftershocks, check for gas leaks and injuries, use stairs not lifts.",
                   "Near the coast and shaking was strong or long: move to high ground at once (tsunami risk)."],
    "cyclone": ["Follow IMD/NDMA bulletins; evacuate when local authorities say so.",
                "Secure loose objects, board windows, keep a go-bag (water, food, medicines, documents, torch, power bank).",
                "Stay indoors during landfall; the calm 'eye' is not the end — winds return from the other side.",
                "Do not go to the sea; avoid fallen power lines and flooded roads after the storm."],
    "flood": ["Move to higher ground; never walk, swim or drive through flood water (15 cm can knock you down).",
              "Switch off electricity and gas at the mains if water may enter the building.",
              "Drink boiled or treated water only; keep documents and medicines in waterproof bags.",
              "Heavy-rain alerts: avoid low-lying roads, underpasses, drains and riverbanks."],
    "thunderstorm": ["When thunder roars, go indoors; avoid open fields, hilltops, trees and water bodies.",
                     "Unplug electrical appliances; avoid wired phones and plumbing during lightning.",
                     "If caught outside, crouch low on the balls of your feet; do not lie flat."],
    "wildfire": ["Follow evacuation orders early; keep vehicles fuelled and facing the exit route.",
                 "Smoke: stay indoors, close windows, use N95 masks; clear dry material around buildings."],
    "volcano": ["Follow exclusion zones; ash fall: stay indoors, close windows, wear N95 masks and eye protection.",
                "Avoid river valleys downstream (lahars); do not drive in heavy ash fall."],
    "drought": ["Conserve water and store safe drinking water; follow local rationing advisories.",
                "Watch for heat illness: hydrate, avoid midday exertion, check on the elderly."],
    "heat": ["Stay hydrated (ORS, lassi, buttermilk), avoid 12:00–15:00 outdoors, wear light loose cotton.",
             "Know heat-stroke signs: hot dry skin, confusion — cool the person and get medical help."],
    "tsunami": ["Move inland or to high ground immediately after strong coastal shaking or an official warning.",
                "Stay away until authorities give the all-clear; waves come in series."],
    "coastal": ["High-wave alert: stay off beaches and away from the shoreline; fishers should not venture out."],
    "vulnerability": ["Check whether you run the listed product; apply the vendor patch or mitigation now.",
                      "If you cannot patch, isolate or disable the affected service and monitor its logs.",
                      "Ransomware-linked entries: verify offline backups and restore procedures."],
    "service": ["Check the provider's status page before troubleshooting locally.",
                "Switch to fallbacks (local LLM, alternate VCS mirror, offline docs) until resolved."],
    "outage": ["Country-level internet disruption: expect failures to reach services hosted there.",
               "Keep an offline copy of critical contacts and documents; use SMS/voice as fallback."],
}
PREPAREDNESS["storm"] = PREPAREDNESS["cyclone"]
PREPAREDNESS["rain"] = PREPAREDNESS["flood"]
PREPAREDNESS_SOURCE = "Summarised from NDMA (ndma.gov.in) public guidelines and CISA advisories."

# ───────────────────────── engine ─────────────────────────


class Disaster:
    def __init__(self, state: dict | None = None, fetch: Fetcher | None = None,
                 sources: list | None = None) -> None:
        self.state = state if state is not None else {}
        self.sources = sources if sources is not None else SOURCES
        self._fetch = fetch
        self.cache: dict[str, dict] = {k: {"key": k, "label": lbl, "category": cat, "status": "pending",
                                           "items": [], "fetched_at": None, "last_ok": None,
                                           "error": None, "home": home, "meta": None, "services": None}
                                       for k, lbl, cat, _fn, _ttl, home in self.sources}
        self._seen: set[str] = set()
        self._seeded = False
        self._last_refresh = 0.0
        self._lock = asyncio.Lock()

    # -- network
    async def _client_get(self, url: str):
        import httpx
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True,
                                     headers={"User-Agent": UA, "Accept": "application/json"}) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()

    async def _get(self, url: str):
        return await (self._fetch or self._client_get)(url)

    # -- polling
    async def _run_source(self, key, fn, ttl, force) -> None:
        c = self.cache[key]
        if not force and c["last_ok"] and time.time() - c["last_ok"] < ttl:
            return
        now = time.time()
        c["fetched_at"] = now
        try:
            res = await asyncio.wait_for(fn(self._get), timeout=HTTP_TIMEOUT * 3)
            for it in res.get("items", []):
                it["fetched_at"] = now
            c.update(items=res.get("items", []), status="ok", last_ok=now, error=None,
                     meta=res.get("meta"), services=res.get("services"))
        except Exception as e:          # stale-while-error: keep last good items, say so
            c["error"] = _txt(f"{type(e).__name__}: {e}", 160)
            c["status"] = "stale" if c["last_ok"] else "unavailable"

    async def poll(self, force: bool = False) -> None:
        async with self._lock:
            await asyncio.gather(*(self._run_source(k, fn, ttl, force)
                                   for k, _l, _c, fn, ttl, _h in self.sources))
        await self._alert()

    # -- location
    def location(self, lat=None, lon=None) -> dict | None:
        ll = _valid_ll(lat, lon)
        if ll:
            return {"lat": ll[0], "lon": ll[1], "label": "browser location", "source": "dashboard"}
        g = self.state.get("geo")
        if isinstance(g, dict) and _valid_ll(g.get("lat"), g.get("lon")):
            return {"lat": float(g["lat"]), "lon": float(g["lon"]), "label": g.get("city") or "host",
                    "source": g.get("source") or "geo"}
        w = self.state.get("weather")
        if w is not None and _valid_ll(getattr(w, "lat", None), getattr(w, "lon", None)):
            return {"lat": float(w.lat), "lon": float(w.lon), "label": getattr(w, "city", None) or "configured",
                    "source": "weather config"}
        return None

    def items(self, loc: dict | None = None) -> list[dict]:
        out = []
        for c in self.cache.values():
            for it in c["items"]:
                it = dict(it)
                it["stale"] = c["status"] == "stale"
                if loc and it.get("lat") is not None:
                    d = haversine_km(loc["lat"], loc["lon"], it["lat"], it["lon"])
                    it["distance_km"] = round(d)
                    it["near"] = d <= NEAR_KM
                else:
                    it["distance_km"], it["near"] = None, False
                out.append(it)
        return sort_items(dedupe_quakes(out))

    def snapshot(self, lat=None, lon=None) -> dict:
        loc = self.location(lat, lon)
        items = self.items(loc)
        by_type: dict[str, int] = {}
        by_sev = {v: 0 for v in SEV_LABEL.values()}
        for i in items:
            by_type[i["type"]] = by_type.get(i["type"], 0) + 1
            by_sev[i["sev_label"]] += 1
        services = (self.cache.get("status") or {}).get("services") or []
        types_present = {i["type"] for i in items if i["severity"] >= 1}
        return {
            "generated": time.time(), "poll_s": POLL_EVERY, "near_km": NEAR_KM, "location": loc,
            "items": items,
            "counts": {"total": len(items), "physical": sum(i["category"] == "physical" for i in items),
                       "digital": sum(i["category"] == "digital" for i in items),
                       "near": sum(bool(i["near"]) for i in items), "by_type": by_type, "by_severity": by_sev},
            "sources": [{k: c[k] for k in ("key", "label", "category", "status", "fetched_at", "last_ok",
                                           "error", "home", "meta")} | {"count": len(c["items"])}
                        for c in self.cache.values()],
            "services": services,
            "preparedness": {t: PREPAREDNESS[t] for t in PREPAREDNESS if t in types_present},
            "preparedness_all": PREPAREDNESS, "preparedness_source": PREPAREDNESS_SOURCE,
            "refresh_in": max(0, int(REFRESH_MIN_GAP - (time.time() - self._last_refresh))),
        }

    # -- alerts (WebSocket → dashboard event stream via the existing "log" handler)
    async def _alert(self) -> None:
        loc = self.location()
        items = self.items(loc)
        fresh = [i for i in items if i["id"] not in self._seen and not i["stale"]]
        self._seen.update(i["id"] for i in items)
        hub = self.state.get("hub")
        if not hub:
            self._seeded = True
            return
        if not self._seeded:          # first poll: one summary line, not a flood of toasts
            self._seeded = True
            hi = [i for i in items if i["severity"] >= 3]
            near = [i for i in items if i["near"] and i["severity"] >= 2]
            if hi or near:
                await hub.broadcast({"type": "log", "level": "warn",
                                     "msg": f"[disaster] {len(hi)} high-severity events worldwide"
                                            + (f", {len(near)} within {NEAR_KM} km of you" if near else "")})
            return
        pick = [i for i in fresh if i["severity"] >= 3 or (i["near"] and i["severity"] >= 2)]
        for i in pick[:MAX_ALERTS_PER_CYCLE]:
            where = f" · {i['distance_km']} km away" if i.get("distance_km") is not None else ""
            await hub.broadcast({"type": "log", "level": "error" if i["severity"] >= 4 else "warn",
                                 "msg": f"[disaster] {i['sev_label']} {i['title']}{where} ({i['source']})"})

    async def loop(self) -> None:
        await asyncio.sleep(FIRST_POLL_DELAY)
        while True:
            try:
                await self.poll()
            except Exception as e:     # never let the loop die
                print(f"[disaster] poll failed: {type(e).__name__}: {e}")
            await asyncio.sleep(POLL_EVERY)

    async def refresh(self) -> tuple[bool, int]:
        wait = REFRESH_MIN_GAP - (time.time() - self._last_refresh)
        if wait > 0:
            return False, int(math.ceil(wait))
        self._last_refresh = time.time()
        await self.poll(force=True)
        return True, 0


# ───────────────────────── wiring ─────────────────────────


def _engine(ctx) -> Disaster:
    st = ctx["state"]
    if "disaster" not in st:
        st["disaster"] = Disaster(st)
    return st["disaster"]


async def start(ctx) -> None:
    await _engine(ctx).loop()


def build_router(ctx) -> APIRouter:
    r = APIRouter()

    @r.get("/api/disaster")
    async def disaster_get(lat: float | None = None, lon: float | None = None):
        return _engine(ctx).snapshot(lat, lon)

    @r.post("/api/disaster/refresh")
    async def disaster_refresh(request: Request, lat: float | None = None, lon: float | None = None):
        eng = _engine(ctx)
        ok, wait = await eng.refresh()
        if not ok:
            return JSONResponse({"error": "rate limited", "retry_after": wait}, status_code=429,
                                headers={"Retry-After": str(wait)})
        try:
            await ctx["audit"]("disaster.refresh", "manual refresh of public disaster feeds")
        except Exception:
            pass
        return eng.snapshot(lat, lon)

    return r
