"""OPEN INTELLIGENCE · data sources (parsers are pure and unit-tested with recorded payloads).

Every source is a free, keyless, openly licensed public API, verified live on 2026-11-16.
Parsers never invent values: a field the upstream did not send is None / absent, a record
without a usable link keeps `url: None` (the dashboard then shows the source name only).

  air       Open-Meteo Air Quality API (CAMS)      AQI, PM, gases, UV, pollen where modelled
  who       WHO Disease Outbreak News (OData API)  latest outbreak notices
  launches  Launch Library 2 (The Space Devs)      upcoming orbital launches
  neo       NASA/JPL SBDB Close-Approach Data API  asteroids passing within 0.05 au, next 14 d
  iss       wheretheiss.at                         live ISS position
  fx        Frankfurter (ECB reference rates)      ~30 business days of rates
  arxiv     arXiv API                              newest papers in chosen categories
  hn        Hacker News Search API (Algolia)       current front page
  co2       NOAA GML Mauna Loa daily CO2           latest ppm + 1 year
  holidays  Nager.Date                             next public holidays for chosen countries

Sunrise / sunset and the moon phase are COMPUTED locally (labelled as such), not fetched.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import source_links as sl


class FetchError(RuntimeError):
    """An upstream refused or failed. `retry_after` (seconds) is set when it said when to come back."""

    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


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
            raise FetchError(f"HTTP {self.status}", retry_after=_num(self.headers.get("retry-after")))
        return self


# ───────────────────────── helpers ─────────────────────────


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _txt(s, n: int = 200) -> str:
    s = re.sub(r"\s+", " ", re.sub(r"<[^>]{0,200}>", "", str(s if s is not None else ""))).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _iso_ts(s) -> float | None:
    """ISO-8601 (Z or offset; naive = UTC) -> epoch seconds."""
    if not isinstance(s, str) or not s.strip():
        return None
    t = re.sub(r"(\.\d{6})\d+", r"\1", s.strip().replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _host_url(u, hosts: tuple) -> str | None:
    """A safe http(s) URL whose host is (a subdomain of) one of `hosts`, else None."""
    s = sl.safe_url(u)
    if not s:
        return None
    h = (urlsplit(s).hostname or "").lower()
    return s if any(h == x or h.endswith("." + x) for x in hosts) else None


def coarse(v) -> float:
    """Coordinates leave this machine rounded to 0.1 degree (about 11 km)."""
    return round(float(v), 1)


def haversine_km(a_lat, a_lon, b_lat, b_lon) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _thin(seq: list, n: int) -> list:
    """At most about n points, always keeping the last one."""
    if len(seq) <= n:
        return list(seq)
    out = seq[::math.ceil(len(seq) / n)]
    if out[-1] is not seq[-1]:
        out.append(seq[-1])
    return out


# ───────────────────────── air quality ─────────────────────────

AQI_BANDS = ((50, "Good"), (100, "Moderate"), (150, "Unhealthy for sensitive groups"),
             (200, "Unhealthy"), (300, "Very unhealthy"), (10 ** 9, "Hazardous"))
_AIR_FIELDS = (("us_aqi", "US AQI"), ("european_aqi", "European AQI"), ("pm2_5", "PM2.5"), ("pm10", "PM10"),
               ("ozone", "Ozone O₃"), ("nitrogen_dioxide", "Nitrogen dioxide NO₂"),
               ("sulphur_dioxide", "Sulphur dioxide SO₂"), ("carbon_monoxide", "Carbon monoxide CO"),
               ("uv_index", "UV index"), ("alder_pollen", "Alder pollen"), ("birch_pollen", "Birch pollen"),
               ("grass_pollen", "Grass pollen"), ("ragweed_pollen", "Ragweed pollen"))


def aqi_band(v) -> str | None:
    """US EPA AQI category name for an index value."""
    v = _num(v)
    if v is None or v < 0:
        return None
    return next(name for top, name in AQI_BANDS if v <= top)


def url_air(lat, lon) -> str:
    return ("https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={coarse(lat)}&longitude={coarse(lon)}"
            "&current=" + ",".join(k for k, _ in _AIR_FIELDS) +
            "&hourly=us_aqi,pm2_5&past_days=1&forecast_days=3&timezone=auto")


def page_air(lat, lon) -> str | None:
    return sl.safe_url(f"https://open-meteo.com/en/docs/air-quality-api?latitude={coarse(lat)}&longitude={coarse(lon)}")


def _local_ts(s, offset) -> float | None:
    t = _iso_ts(s)
    return None if t is None else t - (offset or 0)


def parse_air(p: dict, page: str | None = None) -> dict:
    cur, units, off = p.get("current") or {}, p.get("current_units") or {}, _num(p.get("utc_offset_seconds")) or 0
    items = []
    for key, label in _AIR_FIELDS:
        v = _num(cur.get(key))
        if v is None:
            continue                                   # e.g. pollen is only modelled for Europe
        unit = units.get(key) or ""
        items.append({"id": key, "title": label, "value": v, "unit": "" if unit in ("USAQI", "EAQI") else unit,
                      "detail": aqi_band(v) if key == "us_aqi" else None, "url": page})
    if not items:
        raise FetchError("no current air-quality values in response")
    h = p.get("hourly") or {}
    pts = []
    for t, a in zip(h.get("time") or [], h.get("us_aqi") or []):
        ts = _local_ts(t, off)
        if ts is not None and _num(a) is not None:
            pts.append([ts, _num(a)])
    as_of = _local_ts(cur.get("time"), off)
    ahead = [x for x in pts if as_of is not None and as_of < x[0] <= as_of + 86400]
    peak = max(ahead, key=lambda x: x[1]) if ahead else None
    aqi = _num(cur.get("us_aqi"))
    return {"as_of": as_of, "items": items,
            "summary": {"us_aqi": aqi, "band": aqi_band(aqi), "pm2_5": _num(cur.get("pm2_5")),
                        "pm10": _num(cur.get("pm10")), "uv_index": _num(cur.get("uv_index")),
                        "peak_24h": {"us_aqi": peak[1], "band": aqi_band(peak[1]), "time": peak[0]} if peak else None,
                        "grid": {"lat": _num(p.get("latitude")), "lon": _num(p.get("longitude"))}},
            "series": {"label": "US AQI · hourly, yesterday → 3-day forecast", "now": as_of, "points": _thin(pts, 96)}}


async def src_air(env) -> dict:
    lat, lon = env.loc()
    return parse_air((await env.get(url_air(lat, lon))).ok().json(), page_air(lat, lon))


# ───────────────────────── WHO disease outbreak news ─────────────────────────

URL_WHO = ("https://www.who.int/api/news/diseaseoutbreaknews?%24orderby=PublicationDateAndTime%20desc"
           "&%24top=12&%24select=Title,PublicationDateAndTime,UrlName,DonId")
_DON = re.compile(r"^\d{4}-DON\d{1,5}$")


def who_item_url(url_name) -> str | None:
    u = str(url_name or "").strip()
    return f"https://www.who.int/emergencies/disease-outbreak-news/item/{u}" if _DON.match(u) else None


def parse_who(p: dict) -> dict:
    items = []
    for v in (p.get("value") or [])[:20]:
        title = _txt(v.get("Title"), 180)
        if not title:
            continue
        items.append({"id": str(v.get("DonId") or v.get("UrlName") or title), "title": title,
                      "time": _iso_ts(v.get("PublicationDateAndTime")),
                      "badge": (str(v.get("DonId") or "").split("-")[-1] or None),
                      "url": who_item_url(v.get("UrlName"))})
    return {"as_of": items[0]["time"] if items else None, "items": items,
            "summary": {"count": len(items), "latest": items[0]["title"] if items else None}}


async def src_who(env) -> dict:
    return parse_who((await env.get(URL_WHO)).ok().json())


# ───────────────────────── launches ─────────────────────────

URL_LAUNCHES = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/?limit=12&mode=list&hide_recent_previous=true"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,120}$")


def launch_url(slug) -> str | None:
    s = str(slug or "").strip()
    return f"https://spacelaunchnow.me/launch/{s}" if _SLUG.match(s) else None


def parse_launches(p: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    items = []
    for r in (p.get("results") or [])[:20]:
        net, name = _iso_ts(r.get("net")), _txt(r.get("name"), 140)
        if not name or net is None:
            continue
        items.append({"id": str(r.get("id") or r.get("slug") or name), "title": name, "time": net,
                      "badge": _txt((r.get("status") or {}).get("abbrev"), 14) or None,
                      "detail": " · ".join(x for x in (_txt(r.get("lsp_name"), 60), _txt(r.get("location"), 70),
                                                       _txt(r.get("mission_type"), 30)) if x) or None,
                      "url": launch_url(r.get("slug")) or _host_url(r.get("url"), ("thespacedevs.com",))})
    items.sort(key=lambda i: i["time"])
    up = [i for i in items if i["time"] >= now]
    return {"as_of": None, "items": items,
            "summary": {"count": len(items), "next": {"title": up[0]["title"], "time": up[0]["time"]} if up else None,
                        "within_7d": sum(1 for i in up if i["time"] <= now + 7 * 86400)}}


async def src_launches(env) -> dict:
    return parse_launches((await env.get(URL_LAUNCHES)).ok().json(), env.now())


# ───────────────────────── near-earth objects ─────────────────────────

URL_NEO = "https://ssd-api.jpl.nasa.gov/cad.api?dist-max=0.05&date-min=now&date-max=%2B14&sort=date&fullname=true"
AU_KM, LD_PER_AU = 149_597_870.7, 389.1726


def neo_url(des) -> str | None:
    d = str(des or "").strip()
    if not re.match(r"^[A-Za-z0-9 /().'-]{1,40}$", d):
        return None
    return "https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=" + quote(d, safe="")


def parse_neo(p: dict) -> dict:
    f = {name: i for i, name in enumerate(p.get("fields") or [])}
    items = []
    for row in (p.get("data") or [])[:40]:
        try:
            des = str(row[f["des"]]).strip()
            au = _num(row[f["dist"]])
            ts = calendar.timegm(time.strptime(str(row[f["cd"]]), "%Y-%b-%d %H:%M"))
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if not des or au is None:
            continue
        v = _num(row[f["v_rel"]]) if "v_rel" in f else None
        h = _num(row[f["h"]]) if "h" in f else None
        items.append({"id": f"{des}@{ts}", "title": (_txt(row[f["fullname"]], 60) if "fullname" in f else "") or des,
                      "time": float(ts), "value": round(au * LD_PER_AU, 2), "unit": "LD", "dist_km": round(au * AU_KM),
                      "detail": " · ".join(x for x in (f"{au * AU_KM / 1e6:.2f} million km",
                                                       f"{v:.1f} km/s" if v is not None else "",
                                                       f"H {h:.1f}" if h is not None else "") if x),
                      "url": neo_url(des)})
    closest = min(items, key=lambda i: i["value"]) if items else None
    return {"as_of": None, "items": items,
            "summary": {"count": len(items), "window": "next 14 days, within 0.05 au (LD = lunar distance)",
                        "closest": {"title": closest["title"], "ld": closest["value"],
                                    "time": closest["time"]} if closest else None}}


async def src_neo(env) -> dict:
    return parse_neo((await env.get(URL_NEO)).ok().json())


# ───────────────────────── ISS ─────────────────────────

URL_ISS = "https://api.wheretheiss.at/v1/satellites/25544"
PAGE_ISS = "https://wheretheiss.at/"


def parse_iss(p: dict, home: tuple | None = None) -> dict:
    lat, lon = _num(p.get("latitude")), _num(p.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise FetchError("no position in response")
    alt, vel, ts = _num(p.get("altitude")), _num(p.get("velocity")), _num(p.get("timestamp"))
    dist = round(haversine_km(home[0], home[1], lat, lon)) if home else None
    items = [{"id": "pos", "title": "Ground-track position", "url": PAGE_ISS,
              "value_text": f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"}]
    if alt is not None:
        items.append({"id": "alt", "title": "Altitude", "value": round(alt, 1), "unit": "km", "url": PAGE_ISS})
    if vel is not None:
        items.append({"id": "vel", "title": "Speed", "value": round(vel), "unit": "km/h", "url": PAGE_ISS})
    if p.get("visibility"):
        items.append({"id": "vis", "title": "Sunlight", "value_text": _txt(p.get("visibility"), 20), "url": PAGE_ISS})
    if dist is not None:
        items.append({"id": "dist", "title": "Ground distance from you (computed)", "value": dist, "unit": "km",
                      "url": PAGE_ISS})
    return {"as_of": ts, "items": items,
            "summary": {"lat": round(lat, 2), "lon": round(lon, 2), "alt_km": alt, "vel_kmh": vel,
                        "visibility": _txt(p.get("visibility"), 20) or None, "ground_distance_km": dist}}


async def src_iss(env) -> dict:
    return parse_iss((await env.get(URL_ISS)).ok().json(), env.loc(optional=True))


# ───────────────────────── FX (ECB reference rates) ─────────────────────────

FX_CODES = ("AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "IDR", "ILS", "INR", "ISK",
            "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "USD", "ZAR")


def url_fx(base: str, now: float) -> str:
    return f"https://api.frankfurter.dev/v1/{time.strftime('%Y-%m-%d', time.gmtime(now - 45 * 86400))}..?base={base}"


def fx_pair_url(base: str, sym: str) -> str | None:
    if base in FX_CODES and sym in FX_CODES and base != sym:
        return f"https://api.frankfurter.dev/v1/latest?base={base}&symbols={sym}"
    return None


def parse_fx(p: dict, watch: list | tuple = ()) -> dict:
    base = str(p.get("base") or "").upper()
    days = sorted((d, r) for d, r in (p.get("rates") or {}).items() if isinstance(r, dict))
    if not days or base not in FX_CODES:
        raise FetchError("no rates in response")
    last_d, last = days[-1]
    prev, first = (days[-2][1] if len(days) > 1 else {}), days[0][1]
    rates = {k: _num(v) for k, v in last.items() if _num(v)}
    items = []
    for s in [s for s in watch if s in rates] + sorted(s for s in rates if s not in watch):
        r, pv, f0 = rates[s], _num(prev.get(s)), _num(first.get(s))
        items.append({"id": s, "title": f"{base}/{s}", "value": r, "unit": s, "watch": s in watch,
                      "change_pct": round((r - pv) / pv * 100, 3) if pv else None,
                      "change_period_pct": round((r - f0) / f0 * 100, 2) if f0 else None,
                      "spark": [x for x in (_num(rr.get(s)) for _, rr in days) if x is not None],
                      "url": fx_pair_url(base, s)})
    return {"as_of": _iso_ts(last_d), "items": items, "rates": rates,
            "summary": {"base": base, "date": last_d, "days": len(days), "from": days[0][0],
                        "note": "ECB euro reference rates (working days, ~16:00 CET) — not a live trading feed"}}


def fx_cross(data: dict | None, a: str, b: str) -> dict | None:
    """a -> b from the cached table (a cross rate is computed from two ECB reference rates)."""
    if not data:
        return None
    base, rates = (data.get("summary") or {}).get("base"), dict(data.get("rates") or {})
    if not base:
        return None
    rates[base] = 1.0
    if a not in rates or b not in rates or a == b:
        return None
    return {"pair": f"{a}/{b}", "rate": round(rates[b] / rates[a], 6), "date": data["summary"].get("date"),
            "method": "ECB reference rate" if base in (a, b) else "cross rate computed from two ECB reference rates"}


async def src_fx(env) -> dict:
    s = env.settings
    return parse_fx((await env.get(url_fx(s["fx_base"], env.now()))).ok().json(), s["fx_watch"])


# ───────────────────────── arXiv ─────────────────────────

_ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def url_arxiv(cats: list) -> str:
    q = "+OR+".join("cat:" + c for c in cats)
    return f"https://export.arxiv.org/api/query?search_query={q}&sortBy=submittedDate&sortOrder=descending&max_results=25"


def parse_arxiv(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    items = []
    for e in root.findall("a:entry", _ATOM)[:40]:
        title = _txt(e.findtext("a:title", "", _ATOM), 220)
        if not title:
            continue
        link = next((l.get("href") for l in e.findall("a:link", _ATOM) if l.get("rel") == "alternate"), None) \
            or e.findtext("a:id", "", _ATOM) or ""
        link = link.replace("http://arxiv.org/", "https://arxiv.org/")
        authors = [_txt(a.findtext("a:name", "", _ATOM), 50) for a in e.findall("a:author", _ATOM)]
        prim = e.find("x:primary_category", _ATOM)
        items.append({"id": (link.rsplit("/abs/", 1)[-1] if "/abs/" in link else link)[:40], "title": title,
                      "time": _iso_ts(e.findtext("a:published", "", _ATOM)),
                      "badge": prim.get("term") if prim is not None else None,
                      "detail": (", ".join(authors[:3]) + (f" +{len(authors) - 3}" if len(authors) > 3 else "")) or None,
                      "summary": _txt(e.findtext("a:summary", "", _ATOM), 300) or None,
                      "url": _host_url(link, ("arxiv.org",))})
    return {"as_of": items[0]["time"] if items else None, "items": items, "summary": {"count": len(items)}}


async def src_arxiv(env) -> dict:
    cats = env.settings["arxiv_cats"]
    if not cats:
        return {"as_of": None, "items": [], "summary": {"count": 0, "note": "no categories selected"}}
    out = parse_arxiv((await env.get(url_arxiv(cats))).ok().text)
    out["summary"]["categories"] = list(cats)
    return out


# ───────────────────────── Hacker News ─────────────────────────

URL_HN = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=20"


def hn_item_url(oid) -> str | None:
    o = str(oid or "").strip()
    return f"https://news.ycombinator.com/item?id={o}" if o.isdigit() and len(o) < 12 else None


def parse_hn(p: dict) -> dict:
    items = []
    for h in (p.get("hits") or [])[:30]:
        title, disc = _txt(h.get("title"), 200), hn_item_url(h.get("objectID"))
        if not title or not disc:
            continue
        pts, com = h.get("points"), h.get("num_comments")
        items.append({"id": str(h.get("objectID")), "title": title, "time": _num(h.get("created_at_i")),
                      "value": pts if isinstance(pts, int) else None, "unit": "pts",
                      "detail": f"{com} comments" if isinstance(com, int) else None,
                      "url": sl.safe_url(h.get("url")) or disc, "discuss_url": disc})
    items.sort(key=lambda i: -(i["value"] or 0))
    return {"as_of": None, "items": items, "summary": {"count": len(items)}}


async def src_hn(env) -> dict:
    return parse_hn((await env.get(URL_HN)).ok().json())


# ───────────────────────── CO2 (Mauna Loa) ─────────────────────────

URL_CO2 = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_daily_mlo.csv"
PAGE_CO2 = "https://gml.noaa.gov/ccgg/trends/"


def parse_co2(text: str) -> dict:
    rows = []
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        c = line.split(",")
        try:
            y, m, d, ppm = int(c[0]), int(c[1]), int(c[2]), float(c[4])
        except (ValueError, IndexError):
            continue
        if ppm > 0:
            rows.append([float(calendar.timegm((y, m, d, 0, 0, 0))), ppm])
    if not rows:
        raise FetchError("no CO2 rows in file")
    rows.sort()
    last = rows[-1]
    year = [r for r in rows if r[0] >= last[0] - 366 * 86400]
    target = last[0] - 365 * 86400
    ago = min(rows, key=lambda r: abs(r[0] - target))
    yoy = round(last[1] - ago[1], 2) if abs(ago[0] - target) < 10 * 86400 else None
    items = [{"id": "latest", "title": "Daily mean CO₂ at Mauna Loa", "value": last[1], "unit": "ppm", "time": last[0],
              "url": PAGE_CO2}]
    if yoy is not None:
        items.append({"id": "yoy", "title": "Change vs the same day last year", "value": yoy, "unit": "ppm",
                      "signed": True, "url": PAGE_CO2})
    items.append({"id": "hi", "title": "Highest daily value in the past year", "value": max(r[1] for r in year),
                  "unit": "ppm", "url": PAGE_CO2})
    return {"as_of": last[0], "items": items,
            "summary": {"ppm": last[1], "yoy_ppm": yoy, "note": "recent months are preliminary (NOAA GML)"},
            "series": {"label": "CO₂ ppm · daily, past year", "points": _thin(year, 120)}}


async def src_co2(env) -> dict:
    return parse_co2((await env.get(URL_CO2)).ok().text)


# ───────────────────────── public holidays ─────────────────────────

_ISO2 = re.compile(r"^[A-Z]{2}$")


def url_holidays(cc: str) -> str:
    return f"https://date.nager.at/api/v3/NextPublicHolidays/{cc}"


def holiday_page(cc) -> str | None:
    c = str(cc or "").upper()
    return f"https://date.nager.at/PublicHoliday/Country/{c}" if _ISO2.match(c) else None


def parse_holidays(p: list, cc: str) -> list[dict]:
    out = []
    for h in (p if isinstance(p, list) else [])[:6]:
        ts, name = _iso_ts(h.get("date")), _txt(h.get("name"), 80)
        if ts is None or not name:
            continue
        local = _txt(h.get("localName"), 80)
        out.append({"id": f"{cc}:{h.get('date')}:{name}", "title": name, "time": ts, "badge": cc, "date": h.get("date"),
                    "detail": " · ".join(x for x in (local if local and local != name else "",
                                                     "nationwide" if h.get("global") else "regional",
                                                     ", ".join(str(t) for t in (h.get("types") or [])[:3])) if x),
                    "url": holiday_page(cc)})
    return out


async def src_holidays(env) -> dict:
    items, missing = [], []
    for n, cc in enumerate(env.settings["holiday_countries"]):
        if n:
            await asyncio.sleep(env.gap)               # one country at a time, politely spaced
        r = await env.get(url_holidays(cc))
        if r.status in (204, 404) or (r.status == 200 and not r.body.strip()):
            missing.append(cc)                         # Nager.Date does not cover this country
            continue
        items += parse_holidays(r.ok().json(), cc)
    items.sort(key=lambda i: i["time"])
    return {"as_of": None, "items": items,
            "summary": {"count": len(items), "not_covered": missing,
                        "next": {"title": items[0]["title"], "date": items[0]["date"],
                                 "country": items[0]["badge"]} if items else None}}


# ───────────────────────── computed locally: sun + moon ─────────────────────────

SYNODIC = 29.530588853
_NEW_MOON = 947182440.0           # 2000-01-06 18:14 UTC
_PHASES = ("New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous",
           "Last quarter", "Waning crescent")


def moon_phase(now: float) -> dict:
    """Mean lunar phase from the synodic period (accurate to roughly half a day)."""
    age = ((now - _NEW_MOON) / 86400.0) % SYNODIC
    frac = age / SYNODIC
    return {"age_days": round(age, 1), "illumination_pct": round((1 - math.cos(2 * math.pi * frac)) / 2 * 100),
            "phase": _PHASES[int((frac * 8 + 0.5) % 8)], "next_full_days": round(((0.5 - frac) % 1.0) * SYNODIC, 1),
            "method": "computed locally (mean synodic month, ±0.5 day)"}


def sun_times(lat: float, lon: float, now: float) -> dict:
    """Sunrise / sunset (epoch seconds) for the solar day containing `now` — the standard
    sunrise equation (apparent sunrise, -0.833 degrees). Accurate to a couple of minutes."""
    jd = now / 86400.0 + 2440587.5
    n = math.floor(jd - 2451545.0 + lon / 360.0 + 0.5)
    js = n - lon / 360.0
    m = math.radians((357.5291 + 0.98560028 * js) % 360)
    c = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    transit = 2451545.0 + js + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    sin_d = math.sin(lam) * math.sin(math.radians(23.4397))
    cos_d = math.cos(math.asin(sin_d))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * sin_d) / (math.cos(phi) * cos_d)

    def ts(j):
        return (j - 2440587.5) * 86400.0

    out = {"solar_noon": ts(transit), "method": "computed locally (sunrise equation, ±2 min)"}
    if cos_w > 1:
        out.update(sunrise=None, sunset=None, note="polar night — the sun does not rise today")
    elif cos_w < -1:
        out.update(sunrise=None, sunset=None, note="midnight sun — the sun does not set today")
    else:
        w = math.degrees(math.acos(cos_w)) / 360.0
        out.update(sunrise=ts(transit - w), sunset=ts(transit + w), day_length_h=round(w * 48, 2))
    return out
