"""GLOBAL ACTIVITY MAP · live planetary overview (backend for static/globalmap.js).

Aggregates free, keyless public feeds (see globalmap_sources.py) into map layers.
Each source is fetched independently with its own TTL, timeout and back-off; one
failing feed never affects the others. Nothing is invented: every layer carries its
source, fetched-at time and an honest status:

  pending      not fetched yet
  ok           last fetch succeeded and is within its freshness window
  stale        last fetch failed (or is old) - the last good data is kept and marked
  unavailable  never fetched successfully
  limited      the provider's rate limit is in force (OpenSky) - waiting, not hammering

Polling is demand-driven: a source group is only polled while a dashboard has asked
for it in the last DEMAND_WINDOW seconds (flights / satellites only while those
layers are switched on), so an idle dashboard costs the public feeds nothing.

Endpoints
  GET /api/globalmap                  summary + all light layers (events, alerts, space weather)
  GET /api/globalmap/status           per-source status only
  GET /api/globalmap/layer/{key}      one layer (flights -> compact rows, satellites -> TLEs)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import globalmap_sources as gs
from atomicio import atomic_write_text

STORE = Path(__file__).resolve().parent / ".update" / "globalmap.json"   # operator's saved places only
MAX_PLACES = 60

HTTP_TIMEOUT = 25.0
SOURCE_TIMEOUT = 100.0
TICK = 10
DEMAND_WINDOW = 600
UA = "BUCS-GlobalMap/1.0 (+local dashboard; read-only)"

# key, label, group, ttl seconds, homepage, fn
SOURCES = [
    ("usgs", "USGS Earthquakes M2.5+ · 24 h", "core", 300, "https://earthquake.usgs.gov/earthquakes/map/", gs.src_usgs),
    ("gdacs", "GDACS Global Disaster Alerts", "core", 600, "https://www.gdacs.org/", gs.src_gdacs),
    ("eonet", "NASA EONET Natural Events", "core", 900, "https://eonet.gsfc.nasa.gov/", gs.src_eonet),
    ("gvp", "Smithsonian GVP Weekly Volcanic Activity", "core", 6 * 3600, "https://volcano.si.edu/reports_weekly.cfm", gs.src_gvp),
    ("nws", "NWS Active Alerts (US)", "core", 300, "https://alerts.weather.gov/", gs.src_nws),
    ("sachet", "NDMA SACHET Alerts (India)", "core", 300, "https://sachet.ndma.gov.in/", gs.src_sachet),
    ("ioda", "IODA Internet Outages", "core", 900, "https://ioda.inetintel.cc.gatech.edu/", gs.src_ioda),
    ("swpc", "NOAA SWPC Space Weather", "core", 300, "https://www.swpc.noaa.gov/", gs.src_swpc),
    ("ovation", "NOAA SWPC OVATION Aurora", "core", 900, "https://www.swpc.noaa.gov/products/aurora-30-minute-forecast", gs.src_ovation),
    ("opensky", "OpenSky Network · live aircraft", "flights", 0, "https://opensky-network.org/", gs.src_opensky),
    ("celestrak", "CelesTrak GP element sets", "satellites", 6 * 3600, "https://celestrak.org/", gs.src_celestrak),
]

# layer key -> (label, source keys)
LAYERS = {
    "quakes": ("EARTHQUAKES", ("usgs",)),
    "disasters": ("GDACS ALERTS", ("gdacs",)),
    "events": ("NATURAL EVENTS", ("eonet",)),
    "volcanoes": ("VOLCANOES", ("gvp", "eonet")),
    "wxalerts": ("WEATHER ALERTS", ("nws", "sachet")),
    "outages": ("INTERNET OUTAGES", ("ioda",)),
    "flights": ("AIR TRAFFIC", ("opensky",)),
    "satellites": ("SATELLITES", ("celestrak",)),
    "space": ("SPACE WEATHER", ("swpc", "ovation")),
}
_RANK = {"ok": 4, "stale": 3, "limited": 2, "pending": 1, "unavailable": 0}

Fetch = Callable[[str], Awaitable[gs.Resp]]


class Env:
    def __init__(self, eng: "GlobalMap", ttl: int) -> None:
        self.eng, self.ttl, self.opensky = eng, ttl, eng.opensky

    def now(self) -> float:
        return self.eng.now()

    async def get(self, url: str) -> gs.Resp:
        return await self.eng._get(url)

    def shared(self, key: str):
        """Fresh items from the Disaster Response engine (same feed) - no duplicate fetch."""
        d = self.eng.state.get("disaster")
        c = getattr(d, "cache", {}).get(key) if d is not None else None
        if c and c.get("status") == "ok" and c.get("last_ok") and self.eng.now() - c["last_ok"] < self.ttl:
            return [dict(i) for i in c.get("items") or []]
        return None


class GlobalMap:
    def __init__(self, state: dict | None = None, fetch: Fetch | None = None,
                 sources: list | None = None, now: Callable[[], float] = time.time) -> None:
        self.state = state if state is not None else {}
        self.sources = sources if sources is not None else SOURCES
        self._fetch = fetch
        self.now = now
        self.opensky = gs.OpenSkyGate()
        self._client = None
        self.demand: dict[str, float] = {}
        self.cache: dict[str, dict] = {}
        for key, label, group, ttl, home, _fn in self.sources:
            self.cache[key] = {"key": key, "label": label, "group": group, "ttl": ttl, "home": home,
                               "status": "pending", "items": [], "extra": {}, "meta": None,
                               "fetched_at": None, "last_ok": None, "error": None,
                               "next_at": 0.0, "fails": 0, "inflight": False}
        self._fns = {s[0]: s[5] for s in self.sources}

    # -- network
    async def _client_get(self, url: str) -> gs.Resp:
        import httpx
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True,
                                             headers={"User-Agent": UA, "Accept": "*/*"})
        try:
            r = await self._client.get(url)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            await asyncio.sleep(1.5)        # one retry: some hosts (CelesTrak) drop the first connect
            r = await self._client.get(url)
        return gs.Resp(r.status_code, r.content, dict(r.headers))

    async def _get(self, url: str) -> gs.Resp:
        return await (self._fetch or self._client_get)(url)

    # -- scheduling
    def touch(self, group: str) -> None:
        self.demand[group] = self.now()

    def wanted(self, group: str) -> bool:
        return self.now() - self.demand.get(group, -1e12) < DEMAND_WINDOW

    def due(self, key: str) -> bool:
        c = self.cache[key]
        if c["inflight"] or not self.wanted(c["group"]):
            return False
        if key == "opensky":
            return self.opensky.allowed(self.now())
        return self.now() >= c["next_at"]

    async def run(self, key: str) -> None:
        c = self.cache[key]
        c["inflight"] = True
        now = self.now()
        c["fetched_at"] = now
        try:
            res = await asyncio.wait_for(self._fns[key](Env(self, c["ttl"])), timeout=SOURCE_TIMEOUT)
            c["items"] = res.get("items") or []
            c["extra"] = {k: v for k, v in res.items() if k not in ("items", "meta")}
            c.update(status="ok", meta=res.get("meta"), last_ok=now, error=None, fails=0,
                     next_at=now + c["ttl"])
        except Exception as e:              # stale-while-error: keep last good data, say so
            c["fails"] += 1
            c["error"] = gs._txt(f"{type(e).__name__}: {e}", 160)
            c["status"] = "stale" if c["last_ok"] else "unavailable"
            c["next_at"] = now + min(max(c["ttl"], 120), 60 * 2 ** min(c["fails"], 5))
        finally:
            c["inflight"] = False

    async def tick(self) -> None:
        due = [k for k in self.cache if self.due(k)]
        if due:
            await asyncio.gather(*(self.run(k) for k in due))

    def kick(self) -> None:
        """Start due fetches in the background (request handlers never wait on the network)."""
        for k in self.cache:
            if self.due(k):
                self.cache[k]["inflight"] = True       # claim before the task starts
                asyncio.ensure_future(self._run_claimed(k))

    async def _run_claimed(self, key: str) -> None:
        self.cache[key]["inflight"] = False
        await self.run(key)

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:          # never let the loop die
                print(f"[globalmap] tick failed: {type(e).__name__}: {e}")
            await asyncio.sleep(TICK)

    # -- views
    def status_of(self, key: str) -> str:
        c = self.cache[key]
        now = self.now()
        if key == "opensky":
            g = self.opensky
            fresh = c["last_ok"] is not None and now - c["last_ok"] <= 3 * max(min(g.interval(now), 1200), 300)
            if g.retry_until > now or g.budget(now) - g.RESERVE < g.COST:
                return "ok" if (fresh and c["status"] == "ok") else "limited"
            if c["status"] == "ok" and not fresh:
                return "stale"
            return c["status"]
        if c["status"] == "ok" and c["ttl"] and now - c["last_ok"] > 3 * c["ttl"]:
            return "stale"
        return c["status"]

    def source_info(self, key: str) -> dict:
        c = self.cache[key]
        out = {k: c[k] for k in ("key", "label", "group", "home", "fetched_at", "last_ok", "error", "meta")}
        out["status"] = self.status_of(key)
        out["count"] = (len(c["extra"].get("rows") or c["extra"].get("sats") or []) or len(c["items"]))
        out["next_at"] = self.opensky.next_allowed(self.now()) if key == "opensky" else c["next_at"]
        out["polling"] = self.wanted(c["group"])
        return out

    def layer_info(self, layer: str) -> dict:
        label, keys = LAYERS[layer]
        keys = [k for k in keys if k in self.cache]
        srcs = [self.source_info(k) for k in keys]
        if layer == "flights":
            count = len((self.cache.get("opensky") or {}).get("extra", {}).get("rows") or [])
        elif layer == "satellites":
            count = len((self.cache.get("celestrak") or {}).get("extra", {}).get("sats") or [])
        elif layer == "space":
            count = len(((self.cache.get("swpc") or {}).get("extra", {}).get("space") or {}).get("alerts") or [])
        else:
            count = sum(1 for k in keys for i in self.cache[k]["items"] if i.get("layer") == layer)
        best = max((s["status"] for s in srcs), key=lambda s: _RANK.get(s, 0), default="unavailable")
        worst = min((s["status"] for s in srcs), key=lambda s: _RANK.get(s, 0), default="unavailable")
        oks = [s["last_ok"] for s in srcs if s["last_ok"]]
        return {"key": layer, "label": label, "count": count, "status": best,
                "degraded": worst != best and best == "ok", "sources": keys,
                "fetched_at": max(oks) if oks else None}

    def layer_items(self, layer: str) -> list[dict]:
        out = []
        for k in LAYERS[layer][1]:
            c = self.cache.get(k)
            if not c:
                continue
            stale = self.status_of(k) == "stale"
            for i in c["items"]:
                if i.get("layer") == layer:
                    out.append(dict(i, stale=True) if stale else i)
        return out

    def snapshot(self) -> dict:
        light = [l for l in LAYERS if l not in ("flights", "satellites", "space")]
        items = [i for l in light for i in self.layer_items(l)]
        items.sort(key=lambda i: (-(i.get("severity") or 0), -(i.get("time") or 0)))
        sw, ov = self.cache.get("swpc"), self.cache.get("ovation")
        return {
            "generated": self.now(), "poll_s": 60, "demand_window_s": DEMAND_WINDOW,
            "layers": {l: self.layer_info(l) for l in LAYERS},
            "items": items,
            "space": (sw["extra"].get("space") if sw else None),
            "aurora": (ov["extra"].get("aurora") if ov else None),
            "sources": [self.source_info(k) for k in self.cache],
            "opensky": self.opensky.snapshot(self.now()),
        }

    def layer(self, layer: str) -> dict:
        info = self.layer_info(layer)
        empty = {"extra": {}, "meta": None}
        if layer == "flights":
            c = self.cache.get("opensky", empty)
            return {**info, "rows": c["extra"].get("rows") or [], "meta": c["meta"],
                    "columns": ["icao24", "callsign", "country", "lon", "lat", "alt_m", "vel_ms",
                                "track_deg", "vrate_ms", "t_pos", "squawk"],
                    "gate": self.opensky.snapshot(self.now()),
                    "link_template": gs.ADSBX_TEMPLATE, "link_source": "ADS-B Exchange",
                    "source": self.source_info("opensky") if "opensky" in self.cache else None}
        if layer == "satellites":
            c = self.cache.get("celestrak", empty)
            return {**info, "sats": c["extra"].get("sats") or [], "meta": c["meta"],
                    "source": self.source_info("celestrak") if "celestrak" in self.cache else None}
        if layer == "space":
            return {**info, "space": self.cache.get("swpc", empty)["extra"].get("space"),
                    "aurora": self.cache.get("ovation", empty)["extra"].get("aurora")}
        return {**info, "items": self.layer_items(layer)}


# ───────────────────────── saved places (operator bookmarks) ─────────────────────────


class Places:
    """Named map views the operator saved: centre, zoom and (optionally) which layers were on.
    Operator data only: nothing here is a feed, so nothing is invented."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.items: list[dict] = []
        if path:
            try:
                d = json.loads(Path(path).read_text(encoding="utf-8"))
                for raw in (d.get("places") or [])[:MAX_PLACES]:
                    p, _err = self.clean(raw, known=None)
                    if p and re.fullmatch(r"[0-9a-f]{8,32}", str(raw.get("id", ""))):
                        p["id"], p["created"] = raw["id"], float(raw.get("created") or 0)
                        self.items.append(p)
            except (OSError, ValueError, AttributeError, TypeError):
                pass

    @staticmethod
    def clean(b: dict, known) -> tuple[dict | None, str | None]:
        if not isinstance(b, dict):
            return None, "body must be an object"
        name = gs._txt(b.get("name"), 60)
        if not name:
            return None, "name required"
        try:
            lat, lon, zoom = float(b.get("lat")), float(b.get("lon")), float(b.get("zoom", 4))
        except (TypeError, ValueError):
            return None, "lat, lon and zoom must be numbers"
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 1 <= zoom <= 24):
            return None, "lat -90..90, lon -180..180, zoom 1..24"
        out = {"name": name, "lat": round(lat, 4), "lon": round(lon, 4), "zoom": round(zoom, 2)}
        layers = b.get("layers")
        if isinstance(layers, dict):
            out["layers"] = {str(k): bool(v) for k, v in layers.items()
                             if isinstance(k, str) and (known is None or k in known) and re.fullmatch(r"[a-z_]{1,24}", k)}
        return out, None

    def save(self) -> None:
        if self.path:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(Path(self.path), json.dumps({"places": self.items}, indent=1))

    def add(self, b: dict, known) -> tuple[dict | None, str | None]:
        if len(self.items) >= MAX_PLACES:
            return None, f"limit of {MAX_PLACES} saved places reached: delete one first"
        p, err = self.clean(b, known)
        if err:
            return None, err
        p["id"], p["created"] = uuid.uuid4().hex[:12], time.time()
        self.items.append(p)
        self.save()
        return p, None

    def edit(self, pid: str, b: dict, known) -> tuple[dict | None, str | None]:
        cur = next((p for p in self.items if p["id"] == pid), None)
        if cur is None:
            return None, "unknown place"
        merged = {**cur, **{k: v for k, v in (b if isinstance(b, dict) else {}).items()
                           if k in ("name", "lat", "lon", "zoom", "layers")}}
        p, err = self.clean(merged, known)
        if err:
            return None, err
        if "layers" in cur and "layers" not in p:
            p["layers"] = cur["layers"]
        cur.clear()
        cur.update(p, id=pid, created=merged.get("created") or 0)
        self.save()
        return cur, None

    def delete(self, pid: str) -> bool:
        n = len(self.items)
        self.items = [p for p in self.items if p["id"] != pid]
        if len(self.items) == n:
            return False
        self.save()
        return True


# ───────────────────────── wiring ─────────────────────────


def _places(ctx) -> Places:
    st = ctx["state"]
    if "globalmap_places" not in st:
        st["globalmap_places"] = Places(STORE)
    return st["globalmap_places"]


def _engine(ctx) -> GlobalMap:
    st = ctx["state"]
    if "globalmap" not in st:
        st["globalmap"] = GlobalMap(st)
    return st["globalmap"]


async def start(ctx) -> None:
    await _engine(ctx).loop()


_GROUP = {"flights": "flights", "satellites": "satellites"}


def build_router(ctx) -> APIRouter:
    r = APIRouter()

    @r.get("/api/globalmap")
    async def globalmap_get():
        eng = _engine(ctx)
        eng.touch("core")
        eng.kick()
        return eng.snapshot()

    @r.get("/api/globalmap/status")
    async def globalmap_status():
        eng = _engine(ctx)
        return {"generated": eng.now(), "sources": [eng.source_info(k) for k in eng.cache],
                "layers": {l: eng.layer_info(l) for l in LAYERS}, "opensky": eng.opensky.snapshot(eng.now())}

    @r.get("/api/globalmap/layer/{key}")
    async def globalmap_layer(key: str):
        eng = _engine(ctx)
        if key not in LAYERS:
            return JSONResponse({"error": "unknown layer", "layers": list(LAYERS)}, status_code=404)
        eng.touch(_GROUP.get(key, "core"))
        eng.kick()
        return eng.layer(key)

    # -- saved places: commander-gated, audited, announced to every open dashboard
    async def _changed(action: str, detail: str) -> None:
        try:
            await ctx["audit"](action, detail)
        except Exception:
            pass
        hub = ctx["state"].get("hub")
        if hub:
            try:
                await hub.broadcast({"type": "sync", "topic": "globalmap"})
            except Exception:
                pass

    async def _body(request: Request) -> dict:
        try:
            b = await request.json()
        except Exception:
            b = None
        return b if isinstance(b, dict) else {}

    def _locked():
        return JSONResponse({"ok": False, "error": "OBSERVER role: switch to COMMANDER to edit saved places."},
                            status_code=403)

    def _list() -> dict:
        return {"ok": True, "places": _places(ctx).items, "max": MAX_PLACES, "commander": bool(ctx["commander"]())}

    @r.get("/api/globalmap/places")
    async def places_get():
        return _list()

    @r.post("/api/globalmap/places")
    async def places_add(request: Request):
        if not ctx["commander"]():
            return _locked()
        p, err = _places(ctx).add(await _body(request), set(LAYERS))
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        await _changed("globalmap.place.add", p["name"])
        return {**_list(), "place": p}

    @r.post("/api/globalmap/places/{pid}")
    async def places_edit(pid: str, request: Request):
        if not ctx["commander"]():
            return _locked()
        p, err = _places(ctx).edit(pid, await _body(request), set(LAYERS))
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=404 if err == "unknown place" else 400)
        await _changed("globalmap.place.edit", p["name"])
        return {**_list(), "place": p}

    @r.delete("/api/globalmap/places/{pid}")
    async def places_delete(pid: str):
        if not ctx["commander"]():
            return _locked()
        name = next((p["name"] for p in _places(ctx).items if p["id"] == pid), None)
        if not _places(ctx).delete(pid):
            return JSONResponse({"ok": False, "error": "unknown place"}, status_code=404)
        await _changed("globalmap.place.delete", name or pid)
        return _list()

    return r
