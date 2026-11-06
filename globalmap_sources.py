"""GLOBAL ACTIVITY MAP · data sources (parsers are pure and unit-tested).

Every source is a free, keyless public feed, verified live. Parsers never invent
values: a record without a usable position is dropped (or, when the source only
names an area, placed at that area's centroid and flagged `approx`).

  usgs      USGS earthquakes M2.5+, past 24 h (+ significant past week)
  gdacs     GDACS current alerts               (shared with Disaster Response when fresh)
  eonet     NASA EONET open natural events     (shared with Disaster Response when fresh)
  gvp       Smithsonian GVP / USGS Weekly Volcanic Activity Report (GeoRSS)
  nws       US National Weather Service active alerts
  sachet    NDMA SACHET CAP alerts, India      (shared with Disaster Response when fresh)
  ioda      IODA country-level internet outages (shared with Disaster Response when fresh)
  opensky   OpenSky Network live aircraft states (anonymous; credit-budgeted, see OpenSkyGate)
  celestrak CelesTrak GP element sets (TLE): space stations, brightest, Starlink sample
  swpc      NOAA SWPC: Kp (1-min estimate + 3-hourly), NOAA G/S/R scales, solar wind, alerts
  ovation   NOAA SWPC OVATION aurora nowcast -> auroral oval + probability cells
"""

from __future__ import annotations

import asyncio
import email.utils
import html
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import disaster as dz          # read-only reuse of the Disaster Response parsers
import source_links as sl
from globalmap_geo import COUNTRY, US_STATE

SEV_LABEL = dz.SEV_LABEL
_num, _valid_ll, _txt, _url, _iso_ts = dz._num, dz._valid_ll, dz._txt, dz._url, dz._iso_ts


class FetchError(RuntimeError):
    pass


class Resp:
    """Minimal HTTP response the sources work with (real client or test fake)."""

    def __init__(self, status: int, body: bytes | str, headers: dict | None = None) -> None:
        self.status = int(status)
        self.body = body.encode() if isinstance(body, str) else (body or b"")
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)

    def ok(self) -> "Resp":
        if self.status >= 400:
            raise FetchError(f"HTTP {self.status}")
        return self


def map_item(it: dict, layer: str, **extra) -> dict | None:
    """Normalise a Disaster-style item into a map marker (None when it has no position)."""
    if it.get("lat") is None or it.get("lon") is None:
        return None
    out = {k: it.get(k) for k in ("id", "type", "title", "detail", "severity", "sev_label",
                                  "lat", "lon", "time", "source", "link")}
    out["source_url"] = out.get("link")
    out["layer"] = layer
    if it.get("mag") is not None:
        out["mag"] = it["mag"]
    if it.get("until") is not None:
        out["until"] = it["until"]
    out.update(extra)
    return out


def _mk(**kw) -> dict:
    sev = int(max(0, min(4, kw.get("severity", 0))))
    kw["severity"], kw["sev_label"] = sev, SEV_LABEL[sev]
    kw["title"] = _txt(kw.get("title"), 160)
    kw["detail"] = _txt(kw.get("detail"), 280)
    kw["link"] = _url(kw.get("link"))
    kw["source_url"] = kw["link"]
    return kw


# ───────────────────────── earthquakes / disasters ─────────────────────────


def parse_usgs(payload: dict) -> list[dict]:
    return [m for m in (map_item(i, "quakes") for i in dz.parse_usgs(payload)) if m]


def split_eonet(items: list[dict]) -> list[dict]:
    """EONET volcanoes belong to the volcano layer, everything else to natural events."""
    out = []
    for i in items:
        m = map_item(i, "volcanoes" if i.get("type") == "volcano" else "events")
        if m:
            out.append(m)
    return out


def parse_gdacs(payload: dict) -> list[dict]:
    return [m for m in (map_item(i, "disasters") for i in dz.parse_gdacs(payload)) if m]


def parse_sachet(payload: list, now: float | None = None) -> list[dict]:
    return [m for m in (map_item(i, "wxalerts") for i in dz.parse_sachet(payload, now)) if m]


def parse_ioda(payload: dict, now: float | None = None, limit: int = 60) -> list[dict]:
    """IODA outages are per country: placed at the country centroid (approx)."""
    out = []
    for i in dz.parse_ioda(payload, now, limit=limit):
        code = (i.get("link") or "").rsplit("/", 1)[-1] if i.get("link") else ""
        if not code:
            m = re.match(r"^ioda:([A-Z]{2}):", i.get("id") or "")
            code = m.group(1) if m else ""
        c = COUNTRY.get(code)
        if not c:
            continue
        i = dict(i, lat=c[0], lon=c[1])
        out.append(map_item(i, "outages", country=code, iso_num=c[2], approx=True, score=i.get("score")))
    return out


def merge_ioda(items: list[dict]) -> list[dict]:
    """Disaster Response caches IODA items without coordinates; place them on the map."""
    out = []
    for i in items:
        m = re.match(r"^ioda:([A-Z]{2}):", i.get("id") or "")
        c = COUNTRY.get(m.group(1)) if m else None
        if c:
            out.append(map_item(dict(i, lat=c[0], lon=c[1]), "outages", country=m.group(1),
                                iso_num=c[2], approx=True, score=i.get("score")))
    return out


# ───────────────────────── volcanoes (GVP weekly report) ─────────────────────────

_GEORSS = "{http://www.georss.org/georss}point"


def parse_gvp(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pt = (item.findtext(_GEORSS) or "").split()
        ll = _valid_ll(pt[0], pt[1]) if len(pt) == 2 else None
        if not title or not ll:
            continue
        parts = [p.strip() for p in title.split(" - ")]
        name = parts[0]
        period = parts[1] if len(parts) > 1 else ""
        status = parts[2] if len(parts) > 2 else ""
        sev = 3 if status.lower().startswith("new eruptive") else 2
        desc = re.sub(r"<[^>]+>", " ", html.unescape(item.findtext("description") or ""))
        pub = item.findtext("pubDate")
        try:
            ts = email.utils.parsedate_to_datetime(pub).timestamp() if pub else None
        except (TypeError, ValueError):
            ts = None
        guid = (item.findtext("guid") or "").strip()
        out.append(_mk(id="gvp:" + (guid.rsplit("#", 1)[-1] if "#" in guid else name), layer="volcanoes",
                       type="volcano", title=name, detail=" · ".join(x for x in (status, period, _txt(desc, 200)) if x),
                       severity=sev, lat=ll[0], lon=ll[1], time=ts, source="Smithsonian GVP / USGS",
                       link=guid or item.findtext("link")))
    return out


# ───────────────────────── NWS alerts (US) ─────────────────────────

_NWS_SEV = {"extreme": 4, "severe": 3, "moderate": 2, "minor": 1}


def _ring_centroid(ring: list) -> tuple[float, float] | None:
    pts = [p for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return None
    return _valid_ll(sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))


def _thin(ring: list, n: int = 48) -> list:
    step = max(1, math.ceil(len(ring) / n))
    pts = ring[::step]
    if pts and pts[-1] != ring[-1]:
        pts.append(ring[-1])
    return [[round(p[0], 3), round(p[1], 3)] for p in pts]


def parse_nws(payload: dict) -> list[dict]:
    """Alerts with a polygon are drawn as polygons; zone-only alerts (no geometry) are
    grouped per state and placed at the state centroid (approx). Marine zones are skipped."""
    out, per_state = [], {}
    for f in (payload or {}).get("features") or []:
        p = f.get("properties") or {}
        if str(p.get("status", "Actual")).lower() != "actual":
            continue
        sev = _NWS_SEV.get(str(p.get("severity") or "").lower(), 1)
        ts = _iso_ts(p.get("sent")) or _iso_ts(p.get("effective"))
        until = _iso_ts(p.get("ends")) or _iso_ts(p.get("expires"))
        g = f.get("geometry") or {}
        rings = []
        if g.get("type") == "Polygon":
            rings = [g["coordinates"][0]] if g.get("coordinates") else []
        elif g.get("type") == "MultiPolygon":
            rings = [poly[0] for poly in g.get("coordinates") or [] if poly]
        event = _txt(p.get("event"), 60) or "Alert"
        if rings:
            ll = _ring_centroid(rings[0])
            if not ll:
                continue
            out.append(_mk(id="nws:" + str(p.get("id") or f.get("id")), layer="wxalerts", type="alert",
                           title=f"{event} · {_txt(p.get('areaDesc'), 90)}",
                           detail=" · ".join(x for x in (p.get("severity"), p.get("urgency"), p.get("certainty"),
                                                         _txt(p.get("senderName"), 50)) if x),
                           severity=sev, lat=ll[0], lon=ll[1], time=ts, until=until, source="NWS",
                           link=f.get("id") or p.get("@id"), poly=[_thin(r) for r in rings[:4]]))
            continue
        states = {u[:2] for u in ((p.get("geocode") or {}).get("UGC") or [])
                  if isinstance(u, str) and len(u) >= 3 and u[2] in "ZC"}
        for st in states:
            if st not in US_STATE:
                continue          # marine areas (AMZ, GMZ, PZZ, ...) have no state centroid
            b = per_state.setdefault(st, {"n": 0, "sev": 0, "time": 0, "events": {}, "until": 0})
            b["n"] += 1
            b["sev"] = max(b["sev"], sev)
            b["time"] = max(b["time"], ts or 0)
            b["until"] = max(b["until"], until or 0)
            b["events"][event] = b["events"].get(event, 0) + 1
    # an alert and its later updates are all "active": keep the newest per event + area
    best: dict[tuple, dict] = {}
    for it in out:
        k = (it["title"], tuple(map(tuple, it["poly"][0][:3])) if it.get("poly") else None)
        if k not in best or (it.get("time") or 0) > (best[k].get("time") or 0):
            best[k] = it
    out = list(best.values())
    for st, b in per_state.items():
        lat, lon = US_STATE[st]
        ev = sorted(b["events"].items(), key=lambda kv: -kv[1])
        out.append(_mk(id=f"nws-zone:{st}", layer="wxalerts", type="alert",
                       title=f"{st} · {b['n']} NWS zone alert{'s' if b['n'] != 1 else ''}",
                       detail=" · ".join(f"{e} ×{n}" if n > 1 else e for e, n in ev[:5]),
                       severity=b["sev"], lat=lat, lon=lon, time=b["time"] or None, until=b["until"] or None,
                       source="NWS", link=f"https://api.weather.gov/alerts/active?area={st}", approx=True,
                       count=b["n"]))
    return out


# ───────────────────────── OpenSky (aircraft) ─────────────────────────

EMERGENCY_SQUAWK = {"7500": "unlawful interference", "7600": "radio failure", "7700": "general emergency"}
# rows are compact (up to 6000): the dashboard builds each aircraft's public page from this
# documented pattern with the row's real icao24 (validated as 6 hex digits on both sides)
ADSBX_TEMPLATE = "https://globe.adsbexchange.com/?icao={icao24}"


def parse_opensky(payload: dict, max_rows: int = 6000) -> tuple[list[list], dict]:
    """state vectors -> compact rows [icao24, callsign, country, lon, lat, alt_m, vel_ms,
    track_deg, vrate_ms, t_pos, squawk]. Airborne aircraft only. If more than max_rows,
    a deterministic uniform sample is kept (emergency squawks are always kept)."""
    rows, total = [], 0
    for s in (payload or {}).get("states") or []:
        if not isinstance(s, list) or len(s) < 15:
            continue
        total += 1
        if s[8]:                                  # on_ground
            continue
        ll = _valid_ll(s[6], s[5])
        if not ll:
            continue
        alt = _num(s[13]) if _num(s[13]) is not None else _num(s[7])
        rows.append([str(s[0] or "")[:6], str(s[1] or "").strip()[:8], _txt(s[2], 40),
                     round(ll[1], 4), round(ll[0], 4),
                     int(alt) if alt is not None else None,
                     round(_num(s[9]), 1) if _num(s[9]) is not None else None,
                     int(_num(s[10])) if _num(s[10]) is not None else None,
                     round(_num(s[11]), 1) if _num(s[11]) is not None else None,
                     int(_num(s[3])) if _num(s[3]) is not None else None,
                     str(s[14]) if s[14] else None])
    airborne = len(rows)
    emerg = [r for r in rows if r[10] in EMERGENCY_SQUAWK]
    if len(rows) > max_rows:
        step = len(rows) / max_rows
        keep = {int(i * step) for i in range(max_rows)}
        rows = [r for i, r in enumerate(rows) if i in keep or r[10] in EMERGENCY_SQUAWK]
    meta = {"time": _num((payload or {}).get("time")), "states": total, "airborne": airborne,
            "shown": len(rows), "sampled": len(rows) < airborne,
            "emergencies": [{"icao24": r[0], "callsign": r[1], "squawk": r[10],
                             "meaning": EMERGENCY_SQUAWK[r[10]], "lat": r[4], "lon": r[3],
                             "url": sl.adsbx_aircraft(r[0])} for r in emerg],
            "link_template": ADSBX_TEMPLATE, "link_source": "ADS-B Exchange"}
    return rows, meta


class OpenSkyGate:
    """Anonymous OpenSky budget: 400 credits/day, a global /states/all call costs 4.
    Calls are paced so the budget lasts until the daily reset (00:00 UTC), never faster
    than MIN_INTERVAL; X-Rate-Limit-Remaining and 429 Retry-After are honoured."""

    MIN_INTERVAL = 120
    DAILY = 400
    COST = 4
    RESERVE = 8

    def __init__(self) -> None:
        self.remaining: int | None = None
        self.retry_until = 0.0
        self.last_call = 0.0
        self.day = None
        self.spent = 0

    @staticmethod
    def secs_to_reset(now: float) -> float:
        d = datetime.fromtimestamp(now, timezone.utc)
        midnight = d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
        return max(60.0, midnight - now)

    def _roll(self, now: float) -> None:
        day = datetime.fromtimestamp(now, timezone.utc).date()
        if day != self.day:
            self.day, self.spent = day, 0
            if self.remaining is not None and self.retry_until <= now:
                self.remaining = None            # new day: trust the next header again

    def budget(self, now: float) -> int:
        self._roll(now)
        return self.remaining if self.remaining is not None else max(0, self.DAILY - self.spent)

    def interval(self, now: float) -> float:
        calls_left = (self.budget(now) - self.RESERVE) // self.COST
        if calls_left <= 0:
            return self.secs_to_reset(now)
        return max(self.MIN_INTERVAL, self.secs_to_reset(now) / calls_left)

    def next_allowed(self, now: float) -> float:
        nxt = self.last_call + self.interval(now) if self.last_call else now
        if self.budget(now) - self.RESERVE < self.COST:
            nxt = max(nxt, now + self.secs_to_reset(now))
        return max(nxt, self.retry_until)

    def allowed(self, now: float) -> bool:
        return now >= self.next_allowed(now)

    def record(self, now: float, status: int, headers: dict) -> None:
        self._roll(now)
        self.last_call = now
        self.spent += self.COST
        rem = _num(headers.get("x-rate-limit-remaining"))
        if rem is not None:
            self.remaining = int(rem)
        elif self.remaining is not None:
            self.remaining = max(0, self.remaining - self.COST)
        if status == 429:
            wait = _num(headers.get("x-rate-limit-retry-after-seconds")) or _num(headers.get("retry-after"))
            self.retry_until = now + (wait if wait is not None else self.secs_to_reset(now))
            self.remaining = 0

    def snapshot(self, now: float) -> dict:
        return {"credits_left": self.budget(now), "interval_s": int(self.interval(now)),
                "next_at": self.next_allowed(now), "retry_until": self.retry_until or None,
                "policy": "anonymous: 400 credits/day, 4 per global call, paced to last until 00:00 UTC"}


# ───────────────────────── CelesTrak (satellites) ─────────────────────────


def _tle_ok(l1: str, l2: str) -> bool:
    return (len(l1) >= 69 and len(l2) >= 69 and l1.startswith("1 ") and l2.startswith("2 ")
            and l1[2:7] == l2[2:7])


def parse_tle(text: str, group: str) -> list[dict]:
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    out, i = [], 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i].strip(), lines[i + 1].strip(), lines[i + 2].strip()
        if _tle_ok(l1, l2):
            out.append({"id": int(l1[2:7]), "name": _txt(name, 40), "group": group, "l1": l1[:69], "l2": l2[:69],
                        "url": sl.n2yo_satellite(int(l1[2:7]))})
            i += 3
        else:
            i += 1
    return out


def sample_evenly(seq: list, n: int) -> list:
    if len(seq) <= n:
        return list(seq)
    step = len(seq) / n
    return [seq[int(i * step)] for i in range(n)]


def merge_sats(groups: list[list[dict]]) -> list[dict]:
    seen, out = set(), []
    for g in groups:
        for s in g:
            if s["id"] not in seen:
                seen.add(s["id"])
                out.append(s)
    return out


# ───────────────────────── NOAA SWPC (space weather) ─────────────────────────


def _swpc_ts(s) -> float | None:
    if not isinstance(s, str):
        return None
    s = s.strip().replace(" ", "T")
    return _iso_ts(s if ("+" in s[10:] or s.endswith("Z")) else s + "Z")


def parse_kp_1m(rows: list) -> dict | None:
    for r in reversed(rows or []):
        if isinstance(r, dict):
            kp = _num(r.get("estimated_kp"))
            if kp is None:
                kp = _num(r.get("kp_index"))
            if kp is not None:
                return {"kp": round(kp, 2), "time": _swpc_ts(r.get("time_tag"))}
    return None


def parse_kp_3h(rows: list, n: int = 8) -> list[dict]:
    """Accepts the current list-of-dicts format and the legacy header-row list-of-lists."""
    out = []
    rows = rows or []
    if rows and isinstance(rows[0], list):
        hdr = [str(h).lower() for h in rows[0]]
        ki, ti = (hdr.index("kp") if "kp" in hdr else 1), (hdr.index("time_tag") if "time_tag" in hdr else 0)
        rows = [{"time_tag": r[ti], "Kp": r[ki]} for r in rows[1:] if isinstance(r, list) and len(r) > ki]
    for r in rows:
        if not isinstance(r, dict):
            continue
        kp = _num(r.get("Kp") if r.get("Kp") is not None else r.get("kp"))
        if kp is not None:
            out.append({"time": _swpc_ts(r.get("time_tag")), "kp": round(kp, 2)})
    return out[-n:]


def parse_scales(d: dict) -> dict | None:
    cur = (d or {}).get("0")
    if not isinstance(cur, dict):
        return None
    def sc(k):
        v = (cur.get(k) or {}).get("Scale")
        return int(v) if str(v).isdigit() else None
    return {"G": sc("G"), "S": sc("S"), "R": sc("R"),
            "time": _swpc_ts(f"{cur.get('DateStamp')}T{cur.get('TimeStamp')}") if cur.get("DateStamp") else None}


def parse_wind(speed: list, mag: list) -> dict | None:
    s = (speed or [{}])[-1] if speed else {}
    m = (mag or [{}])[-1] if mag else {}
    v, bt, bz = _num(s.get("proton_speed")), _num(m.get("bt")), _num(m.get("bz_gsm"))
    if v is None and bt is None:
        return None
    return {"speed_kms": v, "bt_nt": bt, "bz_nt": bz, "time": _swpc_ts(s.get("time_tag") or m.get("time_tag"))}


_ALERT_LINE = re.compile(r"^(ALERT|WARNING|WATCH|SUMMARY|EXTENDED WARNING|CONTINUED ALERT|CANCEL \w+):\s*(.+)$", re.M)


def parse_swpc_alerts(rows: list, now: float | None = None, hours: float = 24, limit: int = 12) -> list[dict]:
    now = now or time.time()
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        ts = _swpc_ts(r.get("issue_datetime"))
        if ts is None or ts < now - hours * 3600:
            continue
        msg = str(r.get("message") or "")
        m = _ALERT_LINE.search(msg)
        kind, title = (m.group(1), m.group(2).strip()) if m else ("NOTICE", _txt(msg, 80))
        sev = 3 if kind.startswith(("ALERT", "WARNING", "EXTENDED")) and re.search(r"G[3-5]|S[3-5]|R[3-5]|X-?ray.*X\d", title) \
            else 2 if kind in ("ALERT", "WARNING", "EXTENDED WARNING", "CONTINUED ALERT") else 1
        out.append({"id": f"swpc:{r.get('product_id')}:{int(ts)}", "kind": kind.title(), "title": _txt(title, 140),
                    "time": ts, "severity": sev, "code": _txt(r.get("product_id"), 12),
                    "link": sl.SWPC["alerts"], "source_url": sl.SWPC["alerts"]})
    out.sort(key=lambda a: -a["time"])
    return out[:limit]


def parse_ovation(d: dict, threshold: int = 10, cell_min: int = 5, lon_step: int = 2) -> dict:
    """OVATION nowcast grid [lon 0..359, lat -90..90, probability %] ->
    equatorward boundary of the oval (prob >= threshold) per longitude, both hemispheres,
    plus a thinned list of probability cells for the glow."""
    north, south, cells = {}, {}, []
    for c in (d or {}).get("coordinates") or []:
        if not isinstance(c, (list, tuple)) or len(c) < 3:
            continue
        lon, lat, p = _num(c[0]), _num(c[1]), _num(c[2])
        if lon is None or lat is None or p is None:
            continue
        lon = lon - 360 if lon > 180 else lon
        if p >= threshold:
            if lat > 0:
                north[lon] = min(north.get(lon, 90), lat)
            elif lat < 0:
                south[lon] = max(south.get(lon, -90), lat)
        if p >= cell_min and int(c[0]) % lon_step == 0 and abs(lat) >= 40:
            cells.append([lon, lat, int(p)])
    return {"north": [[k, north[k]] for k in sorted(north)], "south": [[k, south[k]] for k in sorted(south)],
            "cells": cells, "threshold": threshold,
            "observation": _iso_ts((d or {}).get("Observation Time")),
            "forecast": _iso_ts((d or {}).get("Forecast Time"))}


def kp_label(kp: float | None) -> str:
    if kp is None:
        return "unknown"
    return ("quiet" if kp < 4 else "active" if kp < 5 else "G1 storm" if kp < 6 else "G2 storm" if kp < 7
            else "G3 storm" if kp < 8 else "G4 storm" if kp < 9 else "G5 storm")


# ───────────────────────── fetch functions ─────────────────────────
# Each takes an Env (env.get(url) -> Resp, env.shared(key, ttl) -> cached Disaster items)
# and returns {"items": [...], "meta": {...}}. Exceptions mark the source stale/unavailable.


async def src_usgs(env) -> dict:
    items = parse_usgs((await env.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson")).ok().json())
    try:
        sig = parse_usgs((await env.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.geojson")).ok().json())
        seen = {i["id"] for i in items}
        items += [i for i in sig if i["id"] not in seen]
    except Exception:
        pass
    return {"items": items}


async def src_gdacs(env) -> dict:
    shared = env.shared("gdacs")
    if shared is not None:
        return {"items": [m for m in (map_item(i, "disasters") for i in shared) if m], "meta": {"via": "Disaster Response cache"}}
    return {"items": parse_gdacs((await env.get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP")).ok().json())}


async def src_eonet(env) -> dict:
    shared = env.shared("eonet")
    if shared is not None:
        return {"items": split_eonet(shared), "meta": {"via": "Disaster Response cache"}}
    d = (await env.get("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=14")).ok().json()
    return {"items": split_eonet(dz.parse_eonet(d))}


async def src_sachet(env) -> dict:
    shared = env.shared("sachet")
    if shared is not None:
        return {"items": [m for m in (map_item(i, "wxalerts") for i in shared) if m], "meta": {"via": "Disaster Response cache"}}
    return {"items": parse_sachet((await env.get("https://sachet.ndma.gov.in/cap_public_website/FetchAllAlertDetails")).ok().json())}


async def src_ioda(env) -> dict:
    shared = env.shared("ioda")
    if shared is not None:
        return {"items": merge_ioda(shared), "meta": {"via": "Disaster Response cache"}}
    now = int(time.time())
    d = (await env.get("https://api.ioda.inetintel.cc.gatech.edu/v2/outages/events"
                       f"?from={now - 86400}&until={now}&entityType=country&format=ioda")).ok().json()
    return {"items": parse_ioda(d, now)}


async def src_gvp(env) -> dict:
    items = parse_gvp((await env.get("https://volcano.si.edu/news/WeeklyVolcanoRSS.xml")).ok().body)
    return {"items": items, "meta": {"report": items[0]["detail"].split(" · ")[1] if items and " · " in items[0]["detail"] else None}}


async def src_nws(env) -> dict:
    d = (await env.get("https://api.weather.gov/alerts/active?status=actual&message_type=alert,update")).ok().json()
    items = parse_nws(d)
    feats = (d or {}).get("features") or []
    return {"items": items, "meta": {"active": len(feats), "polygons": sum(1 for i in items if not i.get("approx")),
                                     "zone_groups": sum(1 for i in items if i.get("approx"))}}


async def src_opensky(env) -> dict:
    gate: OpenSkyGate = env.opensky
    now = env.now() if hasattr(env, "now") else time.time()
    r = await env.get("https://opensky-network.org/api/states/all")
    gate.record(now, r.status, r.headers)
    if r.status == 429:
        raise FetchError("rate limited by OpenSky (anonymous daily credits used)")
    rows, meta = parse_opensky(r.ok().json())
    meta["gate"] = gate.snapshot(now)
    return {"rows": rows, "items": [], "meta": meta}


CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php?GROUP={g}&FORMAT=tle"
SAT_GROUPS = (("stations", None), ("visual", None), ("starlink", 240))


AMSAT = "https://www.amsat.org/tle/current/nasabare.txt"


async def src_celestrak(env) -> dict:
    """CelesTrak groups fetched concurrently; if CelesTrak is unreachable altogether the
    AMSAT amateur-satellite element set (includes the ISS) is used and labelled as such."""
    async def one(g, cap):
        sats = parse_tle((await env.get(CELESTRAK.format(g=g))).ok().text, g)
        return g, sats, (sample_evenly(sats, cap) if cap else sats)

    res = await asyncio.gather(*(one(g, cap) for g, cap in SAT_GROUPS), return_exceptions=True)
    groups, counts, errs = [], {}, []
    for (g, _cap), r in zip(SAT_GROUPS, res):
        if isinstance(r, Exception):
            errs.append(f"{g}: {type(r).__name__}")
        else:
            counts[g] = len(r[1])
            groups.append(r[2])
    provider = "CelesTrak"
    if not groups:
        try:
            groups = [parse_tle((await env.get(AMSAT)).ok().text, "amateur")]
            counts["amateur"] = len(groups[0])
            provider = "AMSAT (fallback: CelesTrak unreachable)"
        except Exception as e:
            errs.append(f"amsat: {type(e).__name__}")
    if not groups or not any(groups):
        raise FetchError("CelesTrak and AMSAT unreachable (" + ", ".join(errs) + ")")
    sats = merge_sats(groups)
    return {"sats": sats, "items": [], "meta": {"provider": provider, "group_totals": counts, "shown": len(sats),
                                                "errors": errs,
                                                "note": f"Starlink: {SAT_GROUPS[2][1]} sampled evenly from the full constellation"
                                                if "starlink" in counts else None}}


SWPC = "https://services.swpc.noaa.gov"


async def src_swpc(env) -> dict:
    urls = {"kp1m": "/json/planetary_k_index_1m.json", "kp3h": "/products/noaa-planetary-k-index.json",
            "scales": "/products/noaa-scales.json", "speed": "/products/summary/solar-wind-speed.json",
            "mag": "/products/summary/solar-wind-mag-field.json", "alerts": "/products/alerts.json"}

    async def one(path):
        return (await env.get(SWPC + path)).ok().json()

    res = await asyncio.gather(*(one(p) for p in urls.values()), return_exceptions=True)
    got = {k: (None if isinstance(v, Exception) else v) for k, v in zip(urls, res)}
    errs = [f"{k}: {type(v).__name__}" for k, v in zip(urls, res) if isinstance(v, Exception)]
    if all(v is None for v in got.values()):
        raise FetchError("NOAA SWPC unreachable")
    now_kp = parse_kp_1m(got["kp1m"]) if got["kp1m"] else None
    hist = parse_kp_3h(got["kp3h"]) if got["kp3h"] else []
    kp = now_kp["kp"] if now_kp else (hist[-1]["kp"] if hist else None)
    space = {"kp": kp, "kp_time": (now_kp or {}).get("time") or (hist[-1]["time"] if hist else None),
             "kp_label": kp_label(kp), "kp_3h": hist,
             "scales": parse_scales(got["scales"]) if got["scales"] else None,
             "wind": parse_wind(got["speed"], got["mag"]) if got["speed"] or got["mag"] else None,
             "alerts": parse_swpc_alerts(got["alerts"]) if got["alerts"] else [], "errors": errs,
             "links": dict(sl.SWPC)}
    return {"items": [], "space": space, "meta": {"errors": errs}}


async def src_ovation(env) -> dict:
    d = (await env.get(SWPC + "/json/ovation_aurora_latest.json")).ok().json()
    return {"items": [], "aurora": {**parse_ovation(d), "source_url": sl.SWPC["aurora"]}}
