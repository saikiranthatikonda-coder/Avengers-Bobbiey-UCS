"""Source links: every piece of external data on the dashboard points back to where it came from.

Two rules, both enforced here:
  1. Prefer the URL the upstream API itself returns (validated by `safe_url`).
  2. Otherwise build it from the upstream's documented public URL pattern with the
     REAL id, and only when the id has the documented shape. A bad id gives None —
     the dashboard then shows the source name without a link, never a guessed URL.

Only plain http(s) URLs ever leave this module (no javascript:/data:/file:, no
whitespace, quotes or angle brackets, bounded length). Stdlib only.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

MAX_LEN = 2000
_SAFE = re.compile(r"^https?://[^\s\"'<>\\`]+$", re.I)


def safe_url(u) -> str | None:
    """The URL if it is a well-formed absolute http(s) URL with a host, else None."""
    if not isinstance(u, str):
        return None
    u = u.strip()
    if not u or len(u) > MAX_LEN or not _SAFE.match(u):
        return None
    try:
        p = urlsplit(u)
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return None
    return u


# ───────────────────────── static source pages ─────────────────────────

SWPC = {
    "home": "https://www.swpc.noaa.gov/",
    "kp": "https://www.swpc.noaa.gov/products/planetary-k-index",
    "scales": "https://www.swpc.noaa.gov/noaa-scales-explanation",
    "wind": "https://www.swpc.noaa.gov/products/real-time-solar-wind",
    "aurora": "https://www.swpc.noaa.gov/products/aurora-30-minute-forecast",
    "alerts": "https://www.swpc.noaa.gov/products/alerts-watches-and-warnings",
}
KEV_CATALOG = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"
OSV_HOME = "https://osv.dev/"
SACHET_HOME = "https://sachet.ndma.gov.in/"
CLOUDFLARE_SPEED = "https://speed.cloudflare.com/"

# ───────────────────────── builders from documented patterns ─────────────────────────

_CVE = re.compile(r"^CVE-\d{4}-\d{4,7}$")
_OSV = re.compile(r"^[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9._:-]{2,80}$")
_USGS = re.compile(r"^[a-z]{2}[a-z0-9]{4,20}$")
_GDACS_TYPES = {"EQ", "TC", "FL", "VO", "DR", "WF", "TS"}
_ISO2 = re.compile(r"^[A-Z]{2}$")
_ICAO24 = re.compile(r"^[0-9a-f]{6}$")
_COIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
_GMAIL_ID = re.compile(r"^[0-9a-f]{8,32}$")
_EMAIL = re.compile(r"^[^@\s\"'<>]{1,64}@[A-Za-z0-9.-]{1,190}$")
_WIKI_LANG = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")


def nvd_cve(cve) -> str | None:
    """NVD vulnerability detail page for a CVE id (e.g. CVE-2024-3400)."""
    c = str(cve or "").strip().upper()
    return f"https://nvd.nist.gov/vuln/detail/{c}" if _CVE.match(c) else None


def osv_vuln(vid) -> str | None:
    """OSV.dev advisory page (GHSA-…, PYSEC-…, CVE-…, OSV-…)."""
    v = str(vid or "").strip()
    return f"https://osv.dev/vulnerability/{quote(v, safe='-._:')}" if _OSV.match(v) else None


def usgs_event(eid) -> str | None:
    """USGS earthquake event page (network code + event code, e.g. us7000abcd)."""
    e = str(eid or "").strip().lower()
    return f"https://earthquake.usgs.gov/earthquakes/eventpage/{e}" if _USGS.match(e) else None


def gdacs_report(eventtype, eventid, episodeid=None) -> str | None:
    t = str(eventtype or "").strip().upper()
    i = str(eventid or "").strip()
    if t not in _GDACS_TYPES or not i.isdigit():
        return None
    u = f"https://www.gdacs.org/report.aspx?eventtype={t}&eventid={i}"
    ep = str(episodeid or "").strip()
    return u + (f"&episodeid={ep}" if ep.isdigit() else "")


def eonet_event(eid) -> str | None:
    """NASA EONET's own record for an event id (EONET_1234) — used when the event cites no agency page."""
    e = str(eid or "").strip()
    return f"https://eonet.gsfc.nasa.gov/api/v3/events/{e}" if re.match(r"^EONET_[0-9]{1,9}$", e) else None


def ioda_country(code) -> str | None:
    c = str(code or "").strip().upper()
    return f"https://ioda.inetintel.cc.gatech.edu/country/{c}" if _ISO2.match(c) else None


def adsbx_aircraft(icao24) -> str | None:
    """ADS-B Exchange live map centred on one aircraft (24-bit ICAO hex address)."""
    h = str(icao24 or "").strip().lower()
    return f"https://globe.adsbexchange.com/?icao={h}" if _ICAO24.match(h) else None


def _norad(n) -> int | None:
    try:
        v = int(str(n).strip())
    except (TypeError, ValueError):
        return None
    return v if 0 < v < 1_000_000 else None


def n2yo_satellite(norad) -> str | None:
    v = _norad(norad)
    return f"https://www.n2yo.com/satellite/?s={v}" if v else None


def celestrak_elements(norad) -> str | None:
    v = _norad(norad)
    return f"https://celestrak.org/NORAD/elements/gp.php?CATNR={v}&FORMAT=tle" if v else None


def coingecko_coin(coin_id) -> str | None:
    c = str(coin_id or "").strip().lower()
    return f"https://www.coingecko.com/en/coins/{c}" if _COIN.match(c) else None


def wikipedia_article(title, lang: str = "en") -> str | None:
    t = str(title or "").strip()
    if not t or len(t) > 250 or not _WIKI_LANG.match(lang or ""):
        return None
    return f"https://{lang}.wikipedia.org/wiki/" + quote(t.replace(" ", "_"), safe="_()',-.")


def open_meteo(lat, lon) -> str | None:
    """Open-Meteo's public forecast page for these coordinates."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return f"https://open-meteo.com/en/docs?latitude={la:.4f}&longitude={lo:.4f}"


def gmail_message(msg_id, account=None) -> str | None:
    """Gmail web link for a message id (opens in the signed-in account)."""
    m = str(msg_id or "").strip().lower()
    if not _GMAIL_ID.match(m):
        return None
    a = str(account or "").strip()
    q = f"?authuser={quote(a, safe='@.')}" if _EMAIL.match(a) else ""
    return f"https://mail.google.com/mail/u/0/{q}#all/{m}"


def google_link(u, hosts=("www.google.com", "calendar.google.com", "tasks.google.com",
                          "mail.google.com")) -> str | None:
    """A Google-returned link (Calendar htmlLink, Tasks webViewLink) — https on a Google host only."""
    s = safe_url(u)
    if not s or not s.lower().startswith("https://"):
        return None
    return s if (urlsplit(s).hostname or "").lower() in hosts else None
