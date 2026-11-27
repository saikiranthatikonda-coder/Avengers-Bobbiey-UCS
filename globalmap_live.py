"""GLOBAL ACTIVITY MAP · LIVE close-up (v8.5) — backend for the SATELLITE view's ◉ LIVE mode.

What "live" honestly means here (each part says its source and capture time):

  * Geostationary weather satellites: a new full-disk frame every 10–15 minutes, published
    20–60 minutes after capture, ~0.5–3 km per pixel (clouds, smoke, storms, day/night).
      GOES-West / GOES-East GeoColor  (NOAA via NASA GIBS)       Americas & Pacific
      Meteosat MTG GeoColour          (EUMETSAT)                 Europe, Africa, Atlantic
      Meteosat IODC natural / IR      (EUMETSAT)                 Middle East, India, Indian Ocean
      Himawari visible / IR           (JMA via NASA GIBS)        East Asia, Australia, W. Pacific
    The newest available frame time is read from the provider (GIBS DescribeDomains / EUMETSAT
    WMS capabilities), never guessed.
  * Weather radar: RainViewer's latest composite (every 10 min, coverage varies by country).
  * Live public webcams near the map centre: Windy Webcams API — needs the operator's own free
    key (stored in .update/livecams.json, never returned by any endpoint).

Ground detail underneath stays the archival Esri mosaic, labelled as such: no free source offers
real-time sub-metre imagery, and this module never presents archive imagery as live.

  GET  /api/globalmap/live?lat=&lon=           satellite + radar layer descriptors for that spot
  GET  /api/globalmap/webcams?lat=&lon=&radius= live webcams near a point (needs a key)
  POST /api/globalmap/webcams/key {"key": ""}   commander only · empty key removes it
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atomicio import atomic_write_text

KEY_FILE = Path(__file__).resolve().parent / ".update" / "livecams.json"
UA = "BUCS-GlobalMap/1.0 (+local dashboard; read-only)"
TIME_TTL = 240            # newest-frame lookups are cached 4 minutes
CAM_TTL = 300             # webcam lists 5 minutes (Windy image tokens are short-lived)
HTTP_TIMEOUT = 15.0

GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
EUM = "https://view.eumetsat.int/geoserver/"
GIBS_ATTR = "NASA GIBS · Worldview"

# key, name, operator, lon range [lo, hi), cadence min, km/px, provider, day layer, night layer
SATS = [
    ("goes-west", "GOES-West GeoColor", "NOAA / NASA GIBS", (-180, -110), 10, 2.0, "gibs",
     ("GOES-West_ABI_GeoColor", "GoogleMapsCompatible_Level7", 7), None),
    ("goes-east", "GOES-East GeoColor", "NOAA / NASA GIBS", (-110, -40), 10, 2.0, "gibs",
     ("GOES-East_ABI_GeoColor", "GoogleMapsCompatible_Level7", 7), None),
    ("mtg", "Meteosat-12 (MTG) GeoColour", "EUMETSAT", (-40, 25), 10, 1.0, "eum",
     ("mtg_fd", "rgb_geocolour"), None),
    ("iodc", "Meteosat IODC (Indian Ocean)", "EUMETSAT", (25, 105), 15, 3.0, "eum",
     ("msg_iodc", "rgb_naturalenhncd"), ("msg_iodc", "ir108")),
    ("himawari", "Himawari-9", "JMA / NASA GIBS", (105, 180.01), 10, 1.0, "gibs",
     ("Himawari_AHI_Band3_Red_Visible_1km", "GoogleMapsCompatible_Level7", 7),
     ("Himawari_AHI_Band13_Clean_Infrared", "GoogleMapsCompatible_Level6", 6)),
]

Fetch = Callable[[str, dict], Awaitable[tuple[int, bytes]]]


def _norm_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def solar_elevation(lat: float, lon: float, when: datetime) -> float:
    """Approximate solar elevation in degrees (NOAA formula; good to ~1° — enough for day/night)."""
    d = when.timetuple().tm_yday
    hr = when.hour + when.minute / 60 + when.second / 3600
    g = 2 * math.pi / 365 * (d - 1 + (hr - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    tst = hr * 60 + eqt + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cosz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cosz))))


def pick_sat(lon: float):
    lon = _norm_lon(lon)
    for s in SATS:
        lo, hi = s[3]
        if lo <= lon < hi:
            return s
    return SATS[0]


def _iso(ts: str) -> datetime | None:
    ts = (ts or "").replace(".000Z", "Z")
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):              # sub-daily frames · daily mosaics
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def latest_from_domain(xml: str) -> str | None:
    """GIBS DescribeDomains → newest time ('a/b/PT10M,c/d/PT10M' → d)."""
    m = re.search(r"<Domain>([^<]+)</Domain>", xml or "")
    if not m:
        return None
    best = None
    for part in m.group(1).split(","):
        bits = part.strip().split("/")
        t = bits[1] if len(bits) >= 2 else bits[0]
        if _iso(t) and (best is None or _iso(t) > _iso(best)):
            best = t
    return best


def latest_from_wms(xml: str, layer: str) -> str | None:
    """EUMETSAT WMS capabilities → the layer's default (newest) time."""
    xml = xml or ""
    i = xml.find(f"<Name>{layer}</Name>")
    if i < 0 and ":" in layer:                     # workspace-scoped capabilities drop the prefix
        i = xml.find(f"<Name>{layer.split(':', 1)[1]}</Name>")
    if i < 0:
        return None
    j = xml.find("</Layer>", i)
    blk = xml[i: j if j > 0 else None]
    m = re.search(r'<Dimension[^>]*name="time"[^>]*default="([^"]+)"', blk) or \
        re.search(r'<Dimension[^>]*default="([^"]+)"[^>]*name="time"', blk)
    if m:
        return m.group(1).replace(".000Z", "Z")
    m = re.search(r"<Dimension[^>]*>([^<]+)</Dimension>", blk)
    if m:
        last = m.group(1).strip().split(",")[-1].split("/")
        t = last[1] if len(last) >= 2 else last[0]
        return t.replace(".000Z", "Z")
    return None


def _safe_url(u, hosts=("windy.com",)) -> str | None:
    if isinstance(u, dict):
        u = u.get("embed") or u.get("link") or u.get("url")
    if not isinstance(u, str):
        return None
    try:
        p = urlparse(u)
    except Exception:
        return None
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not any(host == h or host.endswith("." + h) for h in hosts):
        return None
    return u


class LiveMap:
    def __init__(self, fetch: Fetch | None = None, now: Callable[[], float] = time.time,
                 key_file: Path = KEY_FILE) -> None:
        self._fetch = fetch
        self.now = now
        self.key_file = key_file
        self._times: dict[str, tuple[float, str | None, str]] = {}
        self._cams: dict[str, tuple[float, dict]] = {}
        self._client = None

    # -- network
    async def get(self, url: str, headers: dict | None = None) -> tuple[int, bytes]:
        if self._fetch:
            return await self._fetch(url, headers or {})
        import httpx
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True,
                                             headers={"User-Agent": UA})
        r = await self._client.get(url, headers=headers or {})
        return r.status_code, r.content

    # -- key
    def key(self) -> str:
        try:
            k = json.loads(self.key_file.read_text(encoding="utf-8")).get("windy_key") or ""
            return k if isinstance(k, str) else ""
        except Exception:
            return ""

    def set_key(self, key: str) -> None:
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.key_file, json.dumps({"windy_key": key, "set_at": int(self.now())}))
        self._cams.clear()

    # -- newest frame time
    async def _latest(self, sat, layer) -> tuple[str | None, str]:
        ck = f"{sat[0]}:{layer}"
        hit = self._times.get(ck)
        if hit and self.now() - hit[0] < TIME_TTL:
            return hit[1], hit[2]
        t, err = None, ""
        try:
            if sat[6] == "gibs":
                end = datetime.fromtimestamp(self.now(), timezone.utc)
                start = end - timedelta(hours=4)
                f = "%Y-%m-%dT%H:%M:%SZ"
                url = (f"{GIBS}1.0.0/{layer[0]}/default/{layer[1]}/all/"
                       f"{start.strftime(f)}--{end.strftime(f)}.xml")
                code, body = await self.get(url)
                t = latest_from_domain(body.decode("utf-8", "replace")) if code == 200 else None
            else:
                url = f"{EUM}{layer[0]}/ows?service=WMS&request=GetCapabilities&version=1.3.0"
                code, body = await self.get(url)
                t = latest_from_wms(body.decode("utf-8", "replace"), f"{layer[0]}:{layer[1]}") if code == 200 else None
            if not t:
                err = f"{sat[1]}: no recent frame listed (HTTP {code})"
        except Exception as e:
            err = f"{sat[1]}: {type(e).__name__}"
        if t or not hit:
            self._times[ck] = (self.now(), t, err)
        else:                                       # keep the last good time, but say it is stale
            return hit[1], err or "stale"
        return t, err

    async def satellite(self, lat: float, lon: float) -> dict:
        sat = pick_sat(lon)
        el = solar_elevation(lat, _norm_lon(lon), datetime.fromtimestamp(self.now(), timezone.utc))
        day = el > -4 or sat[8] is None
        layer = sat[7] if day else sat[8]
        t, err = await self._latest(sat, layer)
        if not t:
            return {"ok": False, "key": sat[0], "name": sat[1], "error": err or "no frame available"}
        if sat[6] == "gibs":
            tiles = [f"{GIBS}{layer[0]}/default/{t}/{layer[1]}/{{z}}/{{y}}/{{x}}.png"]
            maxzoom = layer[2]
            product = layer[0].replace("_", " ")
        else:
            tiles = [f"{EUM}wms?service=WMS&version=1.3.0&request=GetMap&layers={layer[0]}:{layer[1]}"
                     f"&styles=&crs=EPSG:3857&bbox={{bbox-epsg-3857}}&width=256&height=256"
                     f"&format=image/png&transparent=true&time={t}"]
            maxzoom = 9
            product = f"{layer[0]}:{layer[1]}"
        cap = _iso(t)
        age = round(self.now() - cap.timestamp()) if cap else None
        return {"ok": True, "key": sat[0], "name": sat[1], "operator": sat[2], "product": product,
                "band": ("GeoColor: true colour by day, infrared + city lights by night" if "geocolo" in product.lower()
                         else "visible / natural colour (daylight)" if day else "infrared (night)"),
                "tiles": tiles, "maxzoom": maxzoom, "time": t, "age_s": age,
                "cadence_min": sat[4], "km_per_px": sat[5] if day or sat[8] is None else max(sat[5], 2.0),
                "stale": bool(err), "note": err or None, "sun_elevation": round(el, 1),
                "attribution": f"Live satellite: {sat[1]} · {sat[2]}" + (f" · {GIBS_ATTR}" if sat[6] == "gibs" else "")}

    async def radar(self) -> dict:
        hit = self._times.get("radar")
        if hit and self.now() - hit[0] < TIME_TTL and hit[1]:
            return json.loads(hit[1])
        try:
            code, body = await self.get("https://api.rainviewer.com/public/weather-maps.json")
            d = json.loads(body) if code == 200 else {}
            past = (d.get("radar") or {}).get("past") or []
            host = d.get("host") or ""
            if not past or not host.startswith("https://"):
                raise ValueError(f"no radar frames (HTTP {code})")
            last = past[-1]
            out = {"ok": True, "tiles": [f"{host}{last['path']}/256/{{z}}/{{x}}/{{y}}/2/1_1.png"],
                   "time": int(last["time"]), "age_s": round(self.now() - int(last["time"])),
                   "maxzoom": 7, "cadence_min": 10,
                   "attribution": "Radar: RainViewer.com (coverage varies by country)"}
            self._times["radar"] = (self.now(), json.dumps(out), "")
            return out
        except Exception as e:
            if hit and hit[1]:
                return {**json.loads(hit[1]), "stale": True}
            return {"ok": False, "error": f"RainViewer: {e}"}

    async def today(self) -> dict:
        """Newest DAILY true-colour mosaic (one polar-orbit pass per day, ~375 m/px): NOAA-20 VIIRS, falling
        back to MODIS Terra. The date comes from GIBS DescribeDomains — never assumed to be 'today'."""
        for layer, tms, mz, name in (("VIIRS_NOAA20_CorrectedReflectance_TrueColor", "GoogleMapsCompatible_Level9", 9,
                                      "NOAA-20 VIIRS true colour"),
                                     ("MODIS_Terra_CorrectedReflectance_TrueColor", "GoogleMapsCompatible_Level9", 9,
                                      "Terra MODIS true colour")):
            ck = f"today:{layer}"
            hit = self._times.get(ck)
            t = hit[1] if hit and self.now() - hit[0] < TIME_TTL * 4 else None
            if not t:
                try:
                    end = datetime.fromtimestamp(self.now(), timezone.utc)
                    start = end - timedelta(days=6)
                    code, body = await self.get(f"{GIBS}1.0.0/{layer}/default/{tms}/all/"
                                                f"{start.strftime('%Y-%m-%d')}--{end.strftime('%Y-%m-%d')}.xml")
                    t = latest_from_domain(body.decode("utf-8", "replace")) if code == 200 else None
                except Exception:
                    t = None
                if t:
                    t = t[:10]
                    self._times[ck] = (self.now(), t, "")
            if t:
                return {"ok": True, "name": name, "date": t, "maxzoom": mz,
                        "tiles": [f"{GIBS}{layer}/default/{t}/{tms}/{{z}}/{{y}}/{{x}}.jpg"],
                        "km_per_px": 0.375 if "VIIRS" in layer else 0.25,
                        "attribution": f"Daily imagery: {name} · NASA GIBS / Worldview",
                        "note": "one satellite pass per day: areas pass at different local times; gaps and clouds are real"}
        return {"ok": False, "error": "NASA GIBS: no recent daily mosaic listed"}

    async def live(self, lat: float, lon: float) -> dict:
        sat, rad = await asyncio.gather(self.satellite(lat, lon), self.radar())
        return {"ok": True, "generated": round(self.now()), "lat": lat, "lon": lon,
                "satellite": sat, "radar": rad, "webcams": {"configured": bool(self.key())},
                "ground": "Esri World Imagery · archival mosaic, capture dates vary · NOT live"}

    # -- webcams
    async def webcams(self, lat: float, lon: float, radius: int) -> dict:
        key = self.key()
        if not key:
            return {"ok": True, "configured": False, "webcams": [],
                    "how": "Live public webcams come from the Windy Webcams API. Get a free key at "
                           "https://api.windy.com/keys (choose Webcams API) and add it here."}
        ck = f"{lat:.2f},{lon:.2f},{radius}"
        hit = self._cams.get(ck)
        if hit and self.now() - hit[0] < CAM_TTL:
            return hit[1]
        url = (f"https://api.windy.com/webcams/api/v3/webcams?nearby={lat:.4f},{lon:.4f},{radius}"
               f"&limit=25&include=images,location,player,urls")
        try:
            code, body = await self.get(url, {"X-WINDY-API-KEY": key})
        except Exception as e:
            return {"ok": False, "configured": True, "webcams": [], "error": f"Windy: {type(e).__name__}"}
        if code in (401, 403):
            return {"ok": False, "configured": True, "webcams": [], "error": "Windy rejected the API key"}
        if code != 200:
            return {"ok": False, "configured": True, "webcams": [], "error": f"Windy HTTP {code}"}
        try:
            d = json.loads(body)
        except Exception:
            return {"ok": False, "configured": True, "webcams": [], "error": "Windy returned invalid JSON"}
        cams = []
        for w in (d.get("webcams") or [])[:25]:
            loc = w.get("location") or {}
            try:
                la, lo = float(loc.get("latitude")), float(loc.get("longitude"))
            except (TypeError, ValueError):
                continue
            img = ((w.get("images") or {}).get("current") or {})
            pl = w.get("player") or {}
            live = _safe_url(pl.get("live"))
            cams.append({
                "id": str(w.get("webcamId") or w.get("id") or ""), "title": str(w.get("title") or "webcam")[:120],
                "lat": la, "lon": lo, "status": w.get("status"),
                "place": ", ".join(str(x) for x in (loc.get("city"), loc.get("country")) if x)[:120],
                "updated": w.get("lastUpdatedOn"),
                "preview": _safe_url(img.get("preview") or img.get("thumbnail")),
                "player": live or _safe_url(pl.get("day")),
                "player_kind": "live stream" if live else "today's timelapse",
                "detail": _safe_url((w.get("urls") or {}).get("detail")),
            })
        out = {"ok": True, "configured": True, "webcams": cams, "total": d.get("total"),
               "radius_km": radius, "fetched_at": round(self.now()),
               "attribution": "Webcams provided by windy.com"}
        self._cams[ck] = (self.now(), out)
        return out


def _engine(ctx) -> LiveMap:
    st = ctx["state"]
    if "globalmap_live" not in st:
        st["globalmap_live"] = LiveMap()
    return st["globalmap_live"]


def _coords(lat, lon):
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= la <= 90 and math.isfinite(lo)):
        return None
    return round(la, 3), round(_norm_lon(lo), 3)


def build_router(ctx) -> APIRouter:
    r = APIRouter()

    @r.get("/api/globalmap/live")
    async def live(lat: float = 20.0, lon: float = 0.0):
        c = _coords(lat, lon)
        if not c:
            return JSONResponse({"ok": False, "error": "bad coordinates"}, status_code=400)
        return await _engine(ctx).live(*c)

    @r.get("/api/globalmap/today")
    async def today():
        return await _engine(ctx).today()

    @r.get("/api/globalmap/webcams")
    async def webcams(lat: float, lon: float, radius: int = 30):
        c = _coords(lat, lon)
        if not c:
            return JSONResponse({"ok": False, "error": "bad coordinates"}, status_code=400)
        return await _engine(ctx).webcams(*c, max(1, min(250, int(radius))))

    @r.post("/api/globalmap/webcams/key")
    async def set_key(request: Request):
        if not ctx["commander"]():
            return JSONResponse({"ok": False, "error": "OBSERVER role: switch to COMMANDER to change the webcam key."},
                                status_code=403)
        try:
            b = await request.json()
        except Exception:
            b = {}
        key = str((b or {}).get("key") or "").strip() if isinstance(b, dict) else ""
        if key and not re.fullmatch(r"[A-Za-z0-9_\-]{16,128}", key):
            return JSONResponse({"ok": False, "error": "that does not look like a Windy API key"}, status_code=400)
        _engine(ctx).set_key(key)
        try:
            await ctx["audit"]("globalmap.webcams.key", "set" if key else "removed")
        except Exception:
            pass
        return {"ok": True, "configured": bool(key)}

    return r
