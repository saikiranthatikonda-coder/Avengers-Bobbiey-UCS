"""OPEN INTELLIGENCE · live open-data deck (backend for static/openintel.js).

Free, keyless, openly licensed public APIs only (see openintel_sources.py). Each source is
one adapter with its own TTL (chosen from the provider's documented limits), timeout,
exponential back-off (Retry-After honoured) and an honest status:

  pending   not fetched yet                    ok     last fetch succeeded, still fresh
  stale     last fetch failed or is old — the last good data is kept and marked
  error     never fetched successfully         off    its panel is hidden (not polled)

Nothing is invented. Polling is demand-driven: sources are only fetched while a dashboard
(or an agent question) asked for the deck in the last DEMAND_WINDOW seconds, and never for
hidden panels, so an idle install costs the public APIs nothing. The operator's location
comes from the app's existing location state and only ever leaves this machine as the
coordinates an API call itself needs, rounded to 0.1 degree.

Settings (panel order / visibility, arXiv categories, FX base + watch-list, holiday
countries, refresh interval) persist in .update/openintel.json; writes are commander-gated,
audited and broadcast as {"type": "sync", "topic": "openintel"}.

Endpoints
  GET  /api/openintel                    deck: settings, panels, every visible source + data
  GET  /api/openintel/status             per-source status only (no fetch is triggered)
  POST /api/openintel/refresh            {"source": key} or {"panel": key} — floor-limited
  GET  /api/openintel/settings           settings + allowed values
  POST /api/openintel/settings           partial update (commander)
  POST /api/openintel/settings/reset     defaults (commander)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import openintel_sources as os_

HTTP_TIMEOUT = 20.0
SOURCE_TIMEOUT = 60.0
TICK = 10
DEMAND_WINDOW = 900
MAX_BACKOFF = 6 * 3600
UA = "BUCS-OpenIntel/1.0 (local dashboard; read-only)"       # never any personal information
SETTINGS_FILE = Path(__file__).resolve().parent / ".update" / "openintel.json"

# key, label, panel, ttl s, manual-refresh floor s, needs location, home page, licence / terms, fn
SOURCES = [
    ("air", "Open-Meteo Air Quality (CAMS)", "air", 1800, 300, True, "https://open-meteo.com/en/docs/air-quality-api",
     "CC BY 4.0 · keyless free tier (non-commercial, <10,000 calls/day)", os_.src_air),
    ("who", "WHO Disease Outbreak News", "health", 3600, 600, False,
     "https://www.who.int/emergencies/disease-outbreak-news", "WHO public API · CC BY-NC-SA 3.0 IGO", os_.src_who),
    ("launches", "Launch Library 2 · The Space Devs", "space", 1800, 600, False, "https://thespacedevs.com/llapi",
     "free keyless tier, 15 requests/hour", os_.src_launches),
    ("neo", "NASA/JPL Close-Approach Data", "space", 6 * 3600, 900, False, "https://cneos.jpl.nasa.gov/ca/",
     "NASA/JPL public data · one request at a time", os_.src_neo),
    ("iss", "ISS position · wheretheiss.at", "space", 120, 30, False, "https://wheretheiss.at/",
     "free keyless API, ~1 request/second", os_.src_iss),
    ("fx", "Frankfurter · ECB reference rates", "markets", 3600, 600, False, "https://frankfurter.dev/",
     "open source API over ECB data (free reuse with attribution)", os_.src_fx),
    ("arxiv", "arXiv API", "science", 3600, 600, False, "https://arxiv.org/",
     "arXiv API terms · metadata CC0 · max 1 request / 3 s", os_.src_arxiv),
    ("hn", "Hacker News front page", "science", 600, 120, False, "https://news.ycombinator.com/",
     "HN Search API (Algolia) · keyless", os_.src_hn),
    ("co2", "NOAA GML · Mauna Loa CO₂", "climate", 12 * 3600, 3600, False, "https://gml.noaa.gov/ccgg/trends/",
     "US Government public domain (credit NOAA GML)", os_.src_co2),
    ("holidays", "Nager.Date public holidays", "holidays", 24 * 3600, 900, False, "https://date.nager.at/",
     "MIT-licensed open data · keyless", os_.src_holidays),
]
PANELS = {
    "air": "AIR QUALITY", "markets": "MARKETS", "space": "SPACE", "science": "SCIENCE & TECH",
    "health": "HEALTH", "climate": "CLIMATE", "holidays": "HOLIDAYS",
}
ARXIV_CATS = {
    "cs.AI": "Artificial Intelligence", "cs.LG": "Machine Learning", "cs.CL": "Computation & Language",
    "cs.CV": "Computer Vision", "cs.CR": "Cryptography & Security", "cs.RO": "Robotics",
    "cs.SE": "Software Engineering", "cs.DC": "Distributed Computing", "cs.HC": "Human-Computer Interaction",
    "stat.ML": "Statistics · ML", "math.OC": "Optimization & Control", "quant-ph": "Quantum Physics",
    "astro-ph.EP": "Earth & Planetary Astrophysics", "physics.ao-ph": "Atmospheric & Oceanic Physics",
    "q-bio.NC": "Neurons & Cognition", "q-fin.ST": "Statistical Finance", "econ.GN": "General Economics",
    "eess.SP": "Signal Processing",
}
REFRESH_MIN, REFRESH_MAX = 60, 3600
DEFAULTS = {
    "order": list(PANELS), "hidden": [], "arxiv_cats": ["cs.AI", "cs.LG"], "fx_base": "USD",
    "fx_watch": ["INR", "EUR", "GBP", "JPY"], "holiday_countries": ["US", "GB", "SG"], "refresh_s": 120,
}
_ISO2 = re.compile(r"^[A-Z]{2}$")
_AFFECTS = {"arxiv_cats": ("arxiv",), "fx_base": ("fx",), "fx_watch": ("fx",), "holiday_countries": ("holidays",)}


def _codes(v, allowed=None, rx=None, limit=8) -> list | None:
    if not isinstance(v, (list, tuple)):
        return None
    out = []
    for x in v:
        if not isinstance(x, str):
            return None
        x = x.strip()
        if (allowed is not None and x not in allowed) or (rx is not None and not rx.match(x)):
            return None
        if x not in out:
            out.append(x)
    return out if len(out) <= limit else None


def clean_settings(patch: dict, base: dict | None = None) -> tuple[dict, list[str]]:
    """Validated settings (base + patch). Unknown or invalid fields are reported, never stored."""
    cur = json.loads(json.dumps(base or DEFAULTS))
    errors = []
    if not isinstance(patch, dict):
        return cur, ["settings must be an object"]
    for k, v in patch.items():
        if k == "order":
            c = _codes(v, allowed=PANELS, limit=len(PANELS))
            if c is None:
                errors.append("order: unknown panel")
            else:
                cur["order"] = c + [p for p in cur["order"] if p not in c]
        elif k == "hidden":
            c = _codes(v, allowed=PANELS, limit=len(PANELS))
            errors.append("hidden: unknown panel") if c is None else cur.__setitem__("hidden", c)
        elif k == "arxiv_cats":
            c = _codes(v, allowed=ARXIV_CATS, limit=6)
            errors.append("arxiv_cats: up to 6 known categories") if c is None else cur.__setitem__("arxiv_cats", c)
        elif k == "fx_base":
            if isinstance(v, str) and v.strip().upper() in os_.FX_CODES:
                cur["fx_base"] = v.strip().upper()
            else:
                errors.append("fx_base: not an ECB reference currency")
        elif k == "fx_watch":
            c = _codes([str(x).upper() for x in v] if isinstance(v, (list, tuple)) else v, allowed=os_.FX_CODES, limit=8)
            errors.append("fx_watch: up to 8 ECB reference currencies") if c is None else cur.__setitem__("fx_watch", c)
        elif k == "holiday_countries":
            c = _codes([str(x).upper() for x in v] if isinstance(v, (list, tuple)) else v, rx=_ISO2, limit=5)
            errors.append("holiday_countries: up to 5 ISO-3166 alpha-2 codes") if c is None \
                else cur.__setitem__("holiday_countries", c)
        elif k == "refresh_s":
            if isinstance(v, (int, float)) and not isinstance(v, bool) and REFRESH_MIN <= v <= REFRESH_MAX:
                cur["refresh_s"] = int(v)
            else:
                errors.append(f"refresh_s: {REFRESH_MIN}–{REFRESH_MAX} seconds")
        else:
            errors.append(f"{k}: unknown setting")
    cur["order"] = [p for p in cur["order"] if p in PANELS] + [p for p in PANELS if p not in cur["order"]]
    cur["fx_watch"] = [s for s in cur["fx_watch"] if s != cur["fx_base"]]
    return cur, errors


Fetch = Callable[[str], Awaitable[os_.Resp]]


class Env:
    """What an adapter may use: one polite GET, the settings, a coarse location, the clock."""
    gap = 1.0                                           # seconds between calls inside one adapter

    def __init__(self, eng: "OpenIntel") -> None:
        self.eng, self.settings = eng, eng.settings

    def now(self) -> float:
        return self.eng.now()

    async def get(self, url: str) -> os_.Resp:
        return await self.eng._get(url)

    def loc(self, optional: bool = False):
        l = self.eng.location()
        if l is None:
            if optional:
                return None
            raise os_.FetchError("location not known yet")
        return l["lat"], l["lon"]


class OpenIntel:
    def __init__(self, state: dict | None = None, fetch: Fetch | None = None, sources: list | None = None,
                 now: Callable[[], float] = time.time, path: Path | None = None) -> None:
        self.state = state if state is not None else {}
        self.sources = sources if sources is not None else SOURCES
        self._fetch, self.now = fetch, now
        self.path = Path(path) if path else SETTINGS_FILE
        self._client = None
        self.demand = -1e12
        self.settings = self._load()
        self.cache: dict[str, dict] = {}
        for key, label, panel, ttl, floor, needs_loc, home, terms, _fn in self.sources:
            self.cache[key] = {"key": key, "label": label, "panel": panel, "ttl": ttl, "floor": floor,
                               "needs_location": needs_loc, "home": home, "terms": terms, "status": "pending",
                               "data": None, "fetched_at": None, "last_ok": None, "changed_at": None, "error": None,
                               "next_at": 0.0, "fails": 0, "inflight": False, "sig": None}
        self._fns = {s[0]: s[8] for s in self.sources}

    # -- settings
    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        return clean_settings(raw if isinstance(raw, dict) else {})[0]

    def save(self) -> None:
        from atomicio import atomic_write_text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(self.settings, indent=1), encoding="utf-8")

    def apply(self, new: dict) -> list[str]:
        """Store validated settings; sources whose query changed re-fetch (never sooner than their floor)."""
        old, self.settings = self.settings, new
        self.save()
        changed = [k for k in new if new[k] != old.get(k)]
        now = self.now()
        for k in changed:
            for src in _AFFECTS.get(k, ()):
                c = self.cache.get(src)
                if c:
                    c["next_at"] = max(now, (c["fetched_at"] or 0) + min(c["floor"], 60))
        return changed

    def options(self) -> dict:
        return {"panels": PANELS, "arxiv_cats": ARXIV_CATS, "fx_codes": list(os_.FX_CODES),
                "refresh_s": [REFRESH_MIN, REFRESH_MAX], "defaults": DEFAULTS,
                "limits": {"arxiv_cats": 6, "fx_watch": 8, "holiday_countries": 5}}

    # -- location (the app's existing state; rounded before any use)
    def location(self) -> dict | None:
        lat = lon = label = None
        d = self.state.get("disaster")
        try:
            l = d.location() if d is not None else None
            if l:
                lat, lon, label = l.get("lat"), l.get("lon"), l.get("label")
        except Exception:
            pass
        if lat is None:
            w = self.state.get("weather")
            lat, lon, label = getattr(w, "lat", None), getattr(w, "lon", None), getattr(w, "city", None)
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return {"lat": os_.coarse(lat), "lon": os_.coarse(lon), "label": str(label or "configured location")[:60],
                "note": "rounded to 0.1° before it is sent to any API"}

    # -- network
    async def _client_get(self, url: str) -> os_.Resp:
        import httpx
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT, connect=8.0), follow_redirects=True,
                                             headers={"User-Agent": UA, "Accept": "*/*"})
        r = await self._client.get(url)
        return os_.Resp(r.status_code, r.content, dict(r.headers))

    async def _get(self, url: str) -> os_.Resp:
        return await (self._fetch or self._client_get)(url)

    # -- scheduling
    def touch(self) -> None:
        self.demand = self.now()

    def visible(self, key: str) -> bool:
        return self.cache[key]["panel"] not in self.settings["hidden"]

    def due(self, key: str) -> bool:
        c = self.cache[key]
        return (not c["inflight"] and self.visible(key) and self.now() - self.demand < DEMAND_WINDOW
                and self.now() >= c["next_at"])

    async def run(self, key: str) -> None:
        c = self.cache[key]
        c["inflight"] = True
        now = self.now()
        c["fetched_at"] = now
        try:
            data = await asyncio.wait_for(self._fns[key](Env(self)), timeout=SOURCE_TIMEOUT)
            sig = hashlib.sha1(json.dumps([data.get("summary"), [i.get("id") for i in data.get("items") or []]],
                                          sort_keys=True, default=str).encode()).hexdigest()
            if sig != c["sig"]:
                c["sig"], c["changed_at"] = sig, now
            c.update(data=data, status="ok", last_ok=now, error=None, fails=0, next_at=now + c["ttl"])
        except asyncio.CancelledError:
            raise
        except Exception as e:              # stale-while-error: keep the last good data and say so
            c["fails"] += 1
            c["error"] = os_._txt(f"{type(e).__name__}: {e}", 160)
            c["status"] = "stale" if c["last_ok"] else "error"
            wait = min(MAX_BACKOFF, max(c["floor"], 60 * 2 ** min(c["fails"], 6)))
            ra = getattr(e, "retry_after", None)
            if ra:
                wait = min(MAX_BACKOFF, max(wait, float(ra)))
            c["next_at"] = now + wait
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
                self.cache[k]["inflight"] = True
                asyncio.ensure_future(self._run_claimed(k))

    async def _run_claimed(self, key: str) -> None:
        self.cache[key]["inflight"] = False
        await self.run(key)

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:          # never let the loop die
                print(f"[openintel] tick failed: {type(e).__name__}: {e}")
            await asyncio.sleep(TICK)

    def refresh(self, keys: list[str]) -> dict:
        """Manual refresh: honoured only when the source's floor has passed (never hammers an API)."""
        now, out = self.now(), {}
        self.touch()
        for k in keys:
            c = self.cache[k]
            wait = (c["fetched_at"] or -1e12) + c["floor"] - now
            if not self.visible(k):
                out[k] = {"queued": False, "reason": "panel hidden"}
            elif c["inflight"]:
                out[k] = {"queued": False, "reason": "already fetching"}
            elif wait > 0:
                out[k] = {"queued": False, "reason": f"refreshed recently — next allowed in {int(wait) + 1} s",
                          "retry_in_s": int(wait) + 1}
            else:
                c["next_at"] = now
                out[k] = {"queued": True}
        self.kick()
        return out

    # -- views
    def status_of(self, key: str) -> str:
        c = self.cache[key]
        if not self.visible(key):
            return "off"
        if c["status"] == "ok" and self.now() - c["last_ok"] > 3 * c["ttl"]:
            return "stale"
        return c["status"]

    def source_info(self, key: str, data: bool = False) -> dict:
        c = self.cache[key]
        out = {k: c[k] for k in ("key", "label", "panel", "home", "terms", "ttl", "floor", "fetched_at", "last_ok",
                                 "changed_at", "error", "next_at", "inflight")}
        out["status"] = self.status_of(key)
        out["count"] = len((c["data"] or {}).get("items") or [])
        if data:
            d = c["data"] or {}
            out["data"] = {k: d.get(k) for k in ("as_of", "summary", "items", "series")} if c["data"] else None
        return out

    def computed(self) -> dict:
        now, loc = self.now(), self.location()
        sky = {"moon": os_.moon_phase(now), "sun": None}
        if loc:
            sky["sun"] = os_.sun_times(loc["lat"], loc["lon"], now)
        return {"sky": sky, "label": "computed locally — not fetched from any service", "as_of": now}

    def snapshot(self) -> dict:
        s = self.settings
        return {"generated": self.now(), "settings": s, "location": self.location(),
                "panels": [{"key": p, "label": PANELS[p], "hidden": p in s["hidden"],
                            "sources": [k for k in self.cache if self.cache[k]["panel"] == p]} for p in s["order"]],
                "sources": {k: self.source_info(k, data=self.visible(k)) for k in self.cache},
                "computed": self.computed(), "poll_s": s["refresh_s"], "demand_window_s": DEMAND_WINDOW}


# ───────────────────────── agents: compact facts for dashboard_context ─────────────────────────

_TOPICS = (
    ("air", r"air quality|aqi|pm ?2\.?5|pm ?10|pollution|smog|pollen|uv index|ozone"),
    ("launches", r"launch(es)?|rockets?|lift-?off"),
    ("neo", r"asteroids?|near[- ]earth|neos?|close approach"),
    ("iss", r"\biss\b|space station"),
    ("fx", r"exchange rate|forex|\bfx\b|currenc(y|ies)|rupees?|dollars?|euros?|pounds?|yen|usd|inr|eur|gbp|jpy"),
    ("arxiv", r"arxiv|papers?|preprints?"),
    ("hn", r"hacker ?news|\bhn\b"),
    ("co2", r"co2|carbon dioxide|mauna loa"),
    ("who", r"outbreaks?|who alerts?|disease|epidemic|pandemic"),
    ("holidays", r"holidays?"),
    ("sky", r"sunrise|sunset|moon|daylight"),
)
_TOPIC_RX = [(k, re.compile(rf"\b({rx})\b", re.I)) for k, rx in _TOPICS]
_CUR_WORDS = {"dollar": "USD", "dollars": "USD", "rupee": "INR", "rupees": "INR", "euro": "EUR", "euros": "EUR",
              "pound": "GBP", "pounds": "GBP", "yen": "JPY"}
_CUR_LOWER = ("usd", "inr", "eur", "gbp", "jpy", "aud", "cad", "chf", "cny", "sgd", "hkd", "nzd")


def _when(t) -> str | None:
    try:
        return time.strftime("%Y-%m-%d %H:%M local", time.localtime(float(t)))
    except (TypeError, ValueError):
        return None


def _fx_pair(q: str, base: str) -> tuple[str, str] | None:
    """The currency pair a question names ("USD to INR", "euro in rupees"); one currency = base -> it."""
    found = []
    for w in re.findall(r"[A-Za-z]{3,7}", q):
        c = _CUR_WORDS.get(w.lower())
        if c is None and len(w) == 3 and w.upper() in os_.FX_CODES and (w.isupper() or w.lower() in _CUR_LOWER):
            c = w.upper()
        if c and c not in found:
            found.append(c)
    if len(found) >= 2:
        return found[0], found[1]
    if len(found) == 1 and found[0] != base:
        return base, found[0]
    return None


def _facts(eng: OpenIntel, topic: str, q: str) -> dict:
    if topic == "sky":
        c = eng.computed()["sky"]
        sun = c.get("sun") or {}
        return {"moon": {k: c["moon"][k] for k in ("phase", "illumination_pct", "next_full_days")},
                "sunrise": _when(sun.get("sunrise")), "sunset": _when(sun.get("sunset")),
                "source": "computed locally (not fetched)"}
    c = eng.cache.get(topic)
    if c is None:
        return {"unavailable": "source not installed"}
    st = eng.status_of(topic)
    if not c["data"]:
        why = {"off": "panel hidden in OPEN INTELLIGENCE settings", "pending": "being fetched now — ask again in a moment",
               "error": f"source unreachable ({c['error']})"}.get(st, st)
        return {"source": c["label"], "status": st, "unavailable": why}
    d, items = c["data"], c["data"].get("items") or []
    out = {"source": c["label"], "status": st, "fetched": _when(c["last_ok"])}
    s = d.get("summary") or {}
    if topic == "air":
        out.update({k: s.get(k) for k in ("us_aqi", "band", "pm2_5", "pm10", "uv_index")})
        if s.get("peak_24h"):
            out["peak_next_24h"] = {"us_aqi": s["peak_24h"]["us_aqi"], "band": s["peak_24h"]["band"],
                                    "at": _when(s["peak_24h"]["time"])}
        out["measured_at"], out["units"] = _when(d.get("as_of")), "PM in µg/m³"
    elif topic == "launches":
        now = eng.now()
        out["within_7_days"] = s.get("within_7d")
        out["upcoming"] = [{"name": i["title"], "net": _when(i["time"]), "status": i.get("badge")}
                           for i in items if i["time"] >= now][:5]
    elif topic == "neo":
        out["window"], out["count"] = s.get("window"), s.get("count")
        out["closest"] = [{"name": i["title"], "lunar_distances": i["value"], "at": _when(i["time"])}
                          for i in sorted(items, key=lambda i: i["value"])[:4]]
    elif topic == "iss":
        out.update({k: s.get(k) for k in ("lat", "lon", "visibility", "ground_distance_km")})
        out["alt_km"], out["at"] = (round(s["alt_km"]) if s.get("alt_km") else None), _when(d.get("as_of"))
    elif topic == "fx":
        pair = _fx_pair(q, s.get("base"))
        if pair:
            out["asked"] = os_.fx_cross(d, *pair) or {"pair": "/".join(pair), "unavailable": "not in the ECB table"}
        out["base"], out["rate_date"] = s.get("base"), s.get("date")
        out["watch"] = {i["id"]: i["value"] for i in items if i.get("watch")}
        out["note"] = "ECB reference rates, not live trading prices"
    elif topic == "arxiv":
        out["categories"] = s.get("categories")
        out["newest"] = [{"title": os_._txt(i["title"], 90), "cat": i.get("badge"), "at": (_when(i["time"]) or "")[:10]}
                         for i in items[:5]]
    elif topic == "hn":
        out["top"] = [{"title": os_._txt(i["title"], 80), "points": i.get("value")} for i in items[:5]]
    elif topic == "co2":
        out.update({"ppm": s.get("ppm"), "change_vs_last_year_ppm": s.get("yoy_ppm"), "day": (_when(d.get("as_of")) or "")[:10]})
    elif topic == "who":
        out["latest"] = [{"title": os_._txt(i["title"], 100), "published": (_when(i["time"]) or "")[:10]} for i in items[:5]]
    elif topic == "holidays":
        out["next"] = [{"name": i["title"], "date": i.get("date"), "country": i.get("badge")} for i in items[:5]]
        if s.get("not_covered"):
            out["not_covered_by_source"] = s["not_covered"]
    return out


async def agent_facts(state: dict, question: str = "", wait_s: float = 0.8) -> dict:
    """Compact, real facts for JARVIS / the agents. Marks demand, starts due fetches and waits
    briefly for the asked topics; anything still missing is reported as unavailable."""
    eng = state.get("openintel")
    if eng is None:
        return {"unavailable": "open intelligence module not running"}
    q = question or ""
    topics = [k for k, rx in _TOPIC_RX if rx.search(q)][:3]
    eng.touch()
    eng.kick()
    deadline = time.monotonic() + max(0.0, wait_s)
    while time.monotonic() < deadline and any(t in eng.cache and eng.cache[t]["inflight"] and not eng.cache[t]["data"]
                                              for t in topics):
        await asyncio.sleep(0.05)
    out = {"source": "open intelligence deck (keyless public APIs · /api/openintel)",
           "as_of": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not topics:
        out["sources"] = {k: {"status": eng.status_of(k), "items": len((c["data"] or {}).get("items") or [])}
                          for k, c in eng.cache.items()}
        out["hint"] = "ask about air quality, launches, asteroids, the ISS, exchange rates, arXiv papers, " \
                      "Hacker News, CO2, WHO outbreaks, holidays, sunrise or the moon"
        return out
    for t in topics:
        out[t] = _facts(eng, t, q)
    return out


# ───────────────────────── wiring ─────────────────────────


def _engine(ctx) -> OpenIntel:
    st = ctx["state"]
    if "openintel" not in st:
        st["openintel"] = OpenIntel(st)
    return st["openintel"]


async def start(ctx) -> None:
    await _engine(ctx).loop()


def build_router(ctx) -> APIRouter:
    r = APIRouter()
    locked = {"ok": False, "error": "OBSERVER role — command actions are locked. Switch to COMMANDER to proceed."}

    async def _changed(eng: OpenIntel, changed: list[str], what: str) -> None:
        await ctx["audit"]("openintel.settings", what)
        hub = ctx["state"].get("hub")
        if hub is not None:
            await hub.broadcast({"type": "sync", "topic": "openintel"})
        eng.kick()

    @r.get("/api/openintel")
    async def openintel_get():
        eng = _engine(ctx)
        eng.touch()
        eng.kick()
        return eng.snapshot()

    @r.get("/api/openintel/status")
    async def openintel_status():
        eng = _engine(ctx)
        return {"generated": eng.now(), "sources": {k: eng.source_info(k) for k in eng.cache}}

    @r.post("/api/openintel/refresh")
    async def openintel_refresh(request: Request):
        eng = _engine(ctx)
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        if body.get("source") in eng.cache:
            keys = [body["source"]]
        elif body.get("panel") in PANELS:
            keys = [k for k, c in eng.cache.items() if c["panel"] == body["panel"]]
        else:
            return JSONResponse({"ok": False, "error": "unknown source or panel"}, status_code=400)
        return {"ok": True, "results": eng.refresh(keys)}

    @r.get("/api/openintel/settings")
    async def openintel_settings_get():
        eng = _engine(ctx)
        return {"settings": eng.settings, "options": eng.options(), "can_edit": bool(ctx["commander"]())}

    @r.post("/api/openintel/settings")
    async def openintel_settings_set(request: Request):
        if not ctx["commander"]():
            return locked
        eng = _engine(ctx)
        try:
            patch = await request.json()
        except Exception:
            patch = None
        new, errors = clean_settings(patch, eng.settings)
        if errors:
            return JSONResponse({"ok": False, "error": "; ".join(errors)[:300], "settings": eng.settings}, status_code=400)
        changed = eng.apply(new)
        if changed:
            await _changed(eng, changed, "changed " + ", ".join(
                f"{k}={json.dumps(new[k], separators=(',', ':'))[:80]}" for k in changed))
        return {"ok": True, "changed": changed, "settings": eng.settings}

    @r.post("/api/openintel/settings/reset")
    async def openintel_settings_reset():
        if not ctx["commander"]():
            return locked
        eng = _engine(ctx)
        changed = eng.apply(clean_settings({})[0])
        if changed:
            await _changed(eng, changed, "reset to defaults")
        return {"ok": True, "changed": changed, "settings": eng.settings}

    return r
