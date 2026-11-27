"""REGION MAPS (v8.8) — live data for the immersive INDIA and TELANGANA maps beside the global map.

Everything here is measured and named, never invented:

  * Info points — India: 36 state/UT label points + 14 metro cities; Telangana: 33 district
    label points (the geometric centre of each official district polygon, from
    static/geo/india_ts_ll.json — labelled "district centre", not a town) + Hyderabad.
  * Current weather at every point — Open-Meteo forecast API (one batched request, 10-min cache).
  * Current air quality at every point — Open-Meteo Air Quality (CAMS), batched, 30-min cache.
  * Active hazards INSIDE the region boundary (point-in-polygon on the official outline) and
    within NEAR_KM of it — from the disaster engine (USGS, GDACS, NASA EONET, NDMA SACHET).
  * Aircraft over the region — OpenSky rows already fetched by the global map (no extra call).

A source that fails is reported with its error and the last good values are kept and marked
stale; nothing is filled in.

  GET /api/regionmap/{india|telangana}[?flights=1]
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

GEO = Path(__file__).resolve().parent / "static" / "geo" / "india_ts_ll.json"
UA = "BUCS-RegionMap/1.0 (+local dashboard; read-only)"
WX_TTL, AQ_TTL, HTTP_TIMEOUT = 600, 1800, 15.0
NEAR_KM = {"india": 150, "telangana": 60}
WX_URL = "https://api.open-meteo.com/v1/forecast"
AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WX_VARS = "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,wind_direction_10m,cloud_cover"
AQ_VARS = "us_aqi,pm2_5,pm10"

# metro cities (well-known centre coordinates)
INDIA_CITIES = [
    ("Delhi", 28.6139, 77.2090), ("Mumbai", 19.0760, 72.8777), ("Kolkata", 22.5726, 88.3639),
    ("Chennai", 13.0827, 80.2707), ("Bengaluru", 12.9716, 77.5946), ("Hyderabad", 17.3850, 78.4867),
    ("Ahmedabad", 23.0225, 72.5714), ("Pune", 18.5204, 73.8567), ("Jaipur", 26.9124, 75.7873),
    ("Lucknow", 26.8467, 80.9462), ("Patna", 25.5941, 85.1376), ("Guwahati", 26.1445, 91.7362),
    ("Bhubaneswar", 20.2961, 85.8245), ("Thiruvananthapuram", 8.5241, 76.9366),
]
TS_CITIES = [("Hyderabad", 17.3850, 78.4867)]

WMO = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers",
       81: "rain showers", 82: "violent rain showers", 85: "snow showers", 86: "snow showers",
       95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def haversine_km(a1, o1, a2, o2) -> float:
    p1, p2 = math.radians(a1), math.radians(a2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(o2 - o1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


class Region:
    """One region's outline (as rings + bbox) for containment and distance tests."""

    def __init__(self, key: str, outline: dict, points: list[dict]):
        self.key = key
        self.rings = [poly[0] for poly in outline["geometry"]["coordinates"] if poly and len(poly[0]) >= 4]
        xs = [p[0] for r in self.rings for p in r]
        ys = [p[1] for r in self.rings for p in r]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self.points = points
        # sampled boundary vertices for "how far outside" (cheap, ~2 km accuracy is plenty)
        verts = [p for r in self.rings for p in r]
        step = max(1, len(verts) // 1500)
        self.edge = verts[::step]

    def contains(self, lat: float, lon: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            return False
        return any(_in_ring(lon, lat, r) for r in self.rings)

    def distance_km(self, lat: float, lon: float) -> float:
        if self.contains(lat, lon):
            return 0.0
        return min(haversine_km(lat, lon, p[1], p[0]) for p in self.edge)

    def near_bbox(self, lat: float, lon: float, pad_deg: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        return x0 - pad_deg <= lon <= x1 + pad_deg and y0 - pad_deg <= lat <= y1 + pad_deg


def load_regions(path: Path = GEO) -> dict[str, Region]:
    g = json.loads(path.read_text(encoding="utf-8"))
    india_pts = [{"id": "st:" + f["properties"]["name"], "name": f["properties"]["name"], "kind": "state",
                  "lat": f["properties"]["lat"], "lon": f["properties"]["lon"]} for f in g["india"]["states"]["features"]]
    india_pts += [{"id": "city:" + n, "name": n, "kind": "city", "lat": la, "lon": lo} for n, la, lo in INDIA_CITIES]
    ts_pts = [{"id": "dt:" + f["properties"]["name"], "name": f["properties"]["name"], "kind": "district",
               "lat": f["properties"]["lat"], "lon": f["properties"]["lon"]} for f in g["telangana"]["districts"]["features"]]
    ts_pts += [{"id": "city:" + n, "name": n, "kind": "city", "lat": la, "lon": lo} for n, la, lo in TS_CITIES]
    return {"india": Region("india", g["india"]["outline"], india_pts),
            "telangana": Region("telangana", g["telangana"]["outline"], ts_pts)}


def parse_batch(payload, n: int) -> list[dict | None]:
    """Open-Meteo returns one object for a single location and a list for several."""
    rows = payload if isinstance(payload, list) else [payload]
    out = [(r or {}).get("current") if isinstance(r, dict) else None for r in rows]
    return (out + [None] * n)[:n]


def wx_row(c: dict | None) -> dict | None:
    if not isinstance(c, dict) or c.get("temperature_2m") is None:
        return None
    code = c.get("weather_code")
    return {"t": c.get("temperature_2m"), "feels": c.get("apparent_temperature"), "rh": c.get("relative_humidity_2m"),
            "rain": c.get("precipitation"), "code": code, "desc": WMO.get(code, "—"),
            "wind": c.get("wind_speed_10m"), "wdir": c.get("wind_direction_10m"), "cloud": c.get("cloud_cover"),
            "time": c.get("time")}


def aq_row(c: dict | None) -> dict | None:
    if not isinstance(c, dict) or c.get("us_aqi") is None:
        return None
    return {"aqi": c.get("us_aqi"), "pm25": c.get("pm2_5"), "pm10": c.get("pm10"), "time": c.get("time")}


def aqi_band(v) -> str:
    if v is None:
        return "unknown"
    for lim, name in ((50, "good"), (100, "moderate"), (150, "unhealthy for sensitive groups"),
                      (200, "unhealthy"), (300, "very unhealthy")):
        if v <= lim:
            return name
    return "hazardous"


class RegionMaps:
    def __init__(self, state: dict | None = None, fetch=None, now=time.time, regions: dict | None = None):
        self.state = state if state is not None else {}
        self.now = now
        self._fetch = fetch
        self.regions = regions or load_regions()
        self.cache: dict[tuple[str, str], dict] = {}
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def _get(self, url: str, params: dict):
        if self._fetch:
            return await self._fetch(url, params)
        import httpx
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": UA}) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            return r.json()

    async def _source(self, region: str, kind: str) -> dict:
        key = (region, kind)
        ttl = WX_TTL if kind == "wx" else AQ_TTL
        c = self.cache.get(key)
        if c and self.now() - c["fetched_at"] < ttl:
            return c
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            c = self.cache.get(key)
            if c and self.now() - c["fetched_at"] < ttl:
                return c
            pts = self.regions[region].points
            params = {"latitude": ",".join(f"{p['lat']:.4f}" for p in pts),
                      "longitude": ",".join(f"{p['lon']:.4f}" for p in pts), "timezone": "auto"}
            if kind == "wx":
                params.update(current=WX_VARS, wind_speed_unit="kmh")
            else:
                params.update(current=AQ_VARS)
            try:
                rows = parse_batch(await self._get(WX_URL if kind == "wx" else AQ_URL, params), len(pts))
                conv = wx_row if kind == "wx" else aq_row
                vals = [conv(r) for r in rows]
                c = {"ok": True, "values": vals, "fetched_at": self.now(), "error": None, "stale": False}
            except Exception as e:      # keep the last good values, marked stale
                old = self.cache.get(key)
                c = {"ok": bool(old and old.get("values")), "values": (old or {}).get("values") or [None] * len(pts),
                     "fetched_at": self.now() - ttl + 120, "error": f"{type(e).__name__}", "stale": True,
                     "last_ok": (old or {}).get("last_ok") or (old or {}).get("fetched_at")}
            else:
                c["last_ok"] = c["fetched_at"]
            self.cache[key] = c
            return c

    def events(self, region: str) -> dict:
        reg = self.regions[region]
        eng = self.state.get("disaster")
        if eng is None:
            return {"ok": False, "items": [], "error": "disaster feeds not loaded"}
        near = NEAR_KM[region]
        pad = near / 100.0
        out = []
        try:
            items = eng.items(None)
        except Exception as e:
            return {"ok": False, "items": [], "error": type(e).__name__}
        for it in items:
            la, lo = it.get("lat"), it.get("lon")
            if la is None or lo is None or it.get("muted") or not reg.near_bbox(la, lo, pad):
                continue
            d = reg.distance_km(la, lo)
            if d > near:
                continue
            out.append({k: it.get(k) for k in ("id", "type", "title", "detail", "severity", "sev_label", "lat", "lon",
                                                "time", "until", "source", "link", "stale", "mag")}
                       | {"inside": d == 0.0, "outside_km": round(d)})
        out.sort(key=lambda i: (-(i["severity"] or 0), -(i["time"] or 0)))
        srcs = [{"label": c.get("label"), "status": c.get("status"), "fetched_at": c.get("fetched_at")}
                for c in getattr(eng, "cache", {}).values() if c.get("category") == "physical"]
        return {"ok": True, "items": out[:200], "near_km": near, "sources": srcs}

    def flights(self, region: str) -> dict:
        eng = self.state.get("globalmap")
        if eng is None:
            return {"ok": False, "rows": [], "error": "global map not loaded"}
        try:
            eng.touch("flights")
            eng.kick()
            c = eng.cache.get("opensky") or {}
        except Exception as e:
            return {"ok": False, "rows": [], "error": type(e).__name__}
        reg = self.regions[region]
        rows = [r for r in (c.get("extra") or {}).get("rows") or []
                if isinstance(r, list) and len(r) > 7 and r[4] is not None and r[3] is not None
                and reg.near_bbox(r[4], r[3], 0.3) and reg.contains(r[4], r[3])]
        meta = c.get("meta") or {}
        return {"ok": bool(c.get("fetched_at")), "rows": rows[:1500],
                "columns": ["icao24", "callsign", "country", "lon", "lat", "alt_m", "vel_ms", "track_deg", "vrate_ms", "t_pos", "squawk"],
                "time": meta.get("time") if isinstance(meta, dict) else None, "fetched_at": c.get("fetched_at"),
                "status": c.get("status"), "source": "OpenSky Network"}

    async def snapshot(self, region: str, flights: bool = False) -> dict:
        reg = self.regions[region]
        wx, aq = await asyncio.gather(self._source(region, "wx"), self._source(region, "aq"))
        pts = []
        for i, p in enumerate(reg.points):
            w = wx["values"][i] if i < len(wx["values"]) else None
            a = aq["values"][i] if i < len(aq["values"]) else None
            if a:
                a = {**a, "band": aqi_band(a.get("aqi"))}
            pts.append({**p, "wx": w, "aq": a})
        meas = [p for p in pts if p["wx"]]
        hot = max(meas, key=lambda p: p["wx"]["t"], default=None)
        cold = min(meas, key=lambda p: p["wx"]["t"], default=None)
        wet = max(meas, key=lambda p: p["wx"]["rain"] or 0, default=None)
        aqm = [p for p in pts if p["aq"]]
        worst = max(aqm, key=lambda p: p["aq"]["aqi"], default=None)
        ev = self.events(region)
        out = {
            "region": region, "generated": self.now(), "bbox": reg.bbox, "points": pts,
            "weather": {"source": "Open-Meteo", "url": "https://open-meteo.com/", "ok": wx["ok"], "stale": wx["stale"],
                        "fetched_at": wx.get("last_ok"), "error": wx["error"]},
            "air": {"source": "Open-Meteo Air Quality (CAMS)", "url": "https://open-meteo.com/en/docs/air-quality-api",
                    "ok": aq["ok"], "stale": aq["stale"], "fetched_at": aq.get("last_ok"), "error": aq["error"]},
            "extremes": {
                "hottest": hot and {"name": hot["name"], "t": hot["wx"]["t"]},
                "coolest": cold and {"name": cold["name"], "t": cold["wx"]["t"]},
                "wettest": wet and (wet["wx"]["rain"] or 0) > 0 and {"name": wet["name"], "rain": wet["wx"]["rain"]} or None,
                "worst_air": worst and {"name": worst["name"], "aqi": worst["aq"]["aqi"], "band": worst["aq"]["band"]},
            },
            "events": ev,
            "point_note": ("state/UT points are each polygon's geometric centre" if region == "india"
                           else "district points are each official district polygon's geometric centre, not a town"),
        }
        if flights:
            out["flights"] = self.flights(region)
        return out

    def summary(self, region: str) -> str:
        """One line for the agents (no network): uses whatever is cached."""
        wx = self.cache.get((region, "wx"))
        ev = self.events(region)
        name = "India" if region == "india" else "Telangana"
        bits = []
        if wx and wx.get("values"):
            pts = [(p, v) for p, v in zip(self.regions[region].points, wx["values"]) if v]
            if pts:
                hot = max(pts, key=lambda t: t[1]["t"])
                bits.append(f"hottest {hot[0]['name']} {hot[1]['t']:.0f}°C")
        n_in = sum(1 for i in ev.get("items", []) if i["inside"])
        bits.append(f"{n_in} active hazard alert(s) inside {name}")
        return f"{name} map: " + ", ".join(bits) + "."


def _engine(ctx) -> RegionMaps:
    st = ctx["state"]
    if "regionmap" not in st:
        st["regionmap"] = RegionMaps(st)
    return st["regionmap"]


def build_router(ctx) -> APIRouter:
    r = APIRouter()

    @r.get("/api/regionmap/{region}")
    async def region_get(region: str, flights: int = 0):
        if region not in ("india", "telangana"):
            return JSONResponse({"error": "unknown region", "regions": ["india", "telangana"]}, status_code=404)
        return await _engine(ctx).snapshot(region, bool(flights))

    return r
