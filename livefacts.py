"""Live grounding for real-time answers.

Every LLM answer gets the real local date/time, the operator's location and the
latest measured weather. A time-sensitive question ("latest", "today", "price",
"who won", "current …") additionally gets live sources fetched right now, in
parallel and under a hard time budget:

  Google News RSS search   current events, scores, launches, elections (keyless)
  Wikipedia REST           who/what is X (current summary of the article)
  CoinGecko simple price   crypto prices (keyless)

The model is told to answer time-sensitive facts ONLY from these blocks, cite the
source and date, and say it couldn't verify live rather than guess.
"""

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

UA = {"User-Agent": "BUCS-LiveFacts/1.0 (personal assistant; https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/)"}
BUDGET_S = 3.5                    # total time a voice answer may wait for live sources
CACHE_S = 120

TIME_SENSITIVE = re.compile(
    r"\b(latest|current(ly)?|today|tonight|tomorrow|yesterday|this (week|month|year)|right now|now|recent(ly)?|"
    r"breaking|news|update[sd]?|happening|live|score[sd]?|who won|winner|result[s]?|match|election|poll[s]?|"
    r"price[sd]?|rate[s]?|stock[s]?|share price|market|bitcoin|btc|ethereum|eth|crypto|weather in|"
    r"launch(ed|es)?|release[sd]?|announce[sd]?|still|anymore|as of|202\d)\b|"
    r"\bwho is (the )?(current|new|president|prime minister|pm|ceo|chief)", re.I)
ENTITY_Q = re.compile(r"^\s*(?:who|what) (?:is|was|are) (?:the )?(.{2,80}?)[?.!]*\s*$", re.I)
COINS = {"bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum", "solana": "solana",
         "sol": "solana", "dogecoin": "dogecoin", "doge": "dogecoin", "xrp": "ripple", "cardano": "cardano",
         "bnb": "binancecoin", "usdc": "usd-coin", "tether": "tether", "usdt": "tether"}
STOP = {"what", "whats", "what's", "is", "the", "a", "an", "of", "in", "on", "for", "to", "me", "tell", "give",
        "jarvis", "please", "about", "latest", "current", "currently", "today", "now", "right", "news", "update",
        "show", "any", "are", "there", "who", "how", "much", "does", "do", "did", "can", "you", "i", "my"}

_cache: dict[str, tuple[float, str]] = {}


def is_time_sensitive(q: str) -> bool:
    return bool(TIME_SENSITIVE.search(q or ""))


def clock_block(state: dict | None = None, location: bool = True) -> str:
    now = datetime.now().astimezone()
    off = now.strftime("%z")
    loc = ""
    w = (state or {}).get("weather") if location else None
    if w is not None:
        loc = f" · operator location: {getattr(w, 'city', '?')}"
        latest = None
        for result, _ in list(getattr(w, "_stale", {}).values()):
            if not latest or result.get("fetched_at", 0) > latest.get("fetched_at", 0):
                latest = result
        if latest and time.time() - latest.get("fetched_at", 0) < 3 * 3600:
            age = int((time.time() - latest["fetched_at"]) / 60)
            loc += (f" · weather measured {age} min ago: {latest.get('temp_c')}°C, {latest.get('label')}, "
                    f"humidity {latest.get('humidity')}%")
    return (f"[NOW — {now.strftime('%A %d %B %Y, %H:%M')} local time (UTC{off[:3]}:{off[3:]}){loc}. "
            "Use this for any date, day or time question.]\n")


def search_terms(q: str) -> str:
    words = re.findall(r"[\w.+#-]+", (q or "").lower())
    keep = [w for w in words if w not in STOP]
    return " ".join(keep[:8]) or (q or "")[:60]


async def _news(c: httpx.AsyncClient, q: str) -> list[str]:
    r = await c.get("https://news.google.com/rss/search",
                    params={"q": search_terms(q) + " when:7d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.text).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone().strftime("%d %b %H:%M")
        except Exception:
            when = "?"
        if title:
            out.append(f"- {title} ({src or 'news'}, {when})")
        if len(out) >= 6:
            break
    return out


async def _wiki(c: httpx.AsyncClient, q: str) -> list[str]:
    m = ENTITY_Q.match(q or "")
    if not m:
        return []
    subject = m.group(1)
    s = await c.get("https://en.wikipedia.org/w/api.php",
                    params={"action": "query", "list": "search", "srsearch": subject, "srlimit": 1, "format": "json"})
    hits = s.json().get("query", {}).get("search", [])
    if not hits:
        return []
    title = hits[0]["title"]
    r = await c.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_")))
    d = r.json()
    extract = (d.get("extract") or "").strip()
    stamp = (d.get("timestamp") or "")[:10]
    return [f"- Wikipedia “{title}” (revised {stamp}): {extract[:700]}"] if extract else []


async def _crypto(c: httpx.AsyncClient, q: str) -> list[str]:
    ids = sorted({v for k, v in COINS.items() if re.search(rf"\b{re.escape(k)}\b", (q or "").lower())})
    if not ids:
        return []
    r = await c.get("https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": ",".join(ids), "vs_currencies": "usd,inr", "include_24hr_change": "true",
                            "include_last_updated_at": "true"})
    out = []
    for k, v in r.json().items():
        when = datetime.fromtimestamp(v.get("last_updated_at", time.time())).strftime("%H:%M")
        out.append(f"- {k}: ${v.get('usd'):,} / ₹{v.get('inr'):,} ({v.get('usd_24h_change', 0):+.2f}% 24 h, CoinGecko {when})")
    return out


async def ground(state: dict | None, question: str, client: httpx.AsyncClient | None = None,
                 location: bool = True) -> str:
    """The grounding block to prepend to an LLM prompt. Never raises; never waits past BUDGET_S.
    location=False leaves out the operator's city and weather (e.g. on calls with third parties)."""
    block = clock_block(state, location)
    if not is_time_sensitive(question):
        return block
    key = search_terms(question)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return block + hit[1]
    own = client is None
    c = client or httpx.AsyncClient(timeout=httpx.Timeout(BUDGET_S, connect=2.0), headers=UA, follow_redirects=True)
    try:
        tasks = {name: asyncio.create_task(fn(c, question)) for name, fn in
                 (("Google News (last 7 days)", _news), ("Wikipedia", _wiki), ("CoinGecko", _crypto))}
        done, pending = await asyncio.wait(tasks.values(), timeout=BUDGET_S)
        for t in pending:
            t.cancel()
        lines = []
        for name, t in tasks.items():
            if t in done and not t.exception() and t.result():
                lines.append(f"{name}:\n" + "\n".join(t.result()))
    finally:
        if own:
            await c.aclose()
    fetched = datetime.now().strftime("%H:%M")
    if lines:
        live = (f"[LIVE SOURCES — fetched {fetched} for this question:\n" + "\n".join(lines) +
                "\nFor anything time-sensitive answer ONLY from these sources and name the source and date. "
                "If they don't answer the question, say you couldn't verify it live — never guess or use "
                "older memory for current facts.]\n")
    else:
        live = ("[LIVE SOURCES — none reachable right now. For current facts (news, scores, prices, office "
                "holders) say you can't verify them live instead of guessing.]\n")
    _cache[key] = (time.time(), live)
    return block + live
