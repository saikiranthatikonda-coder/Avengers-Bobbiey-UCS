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

import source_links as sl

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
# the same live sources as structured citations (title, source, url, when) so the dashboard can
# link an answer back to them; keyed like _cache, kept for CACHE_S
_sources: dict[str, tuple[float, list[dict]]] = {}


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


def parse_news_rss(xml_text: str, limit: int = 6) -> list[dict]:
    """Google News RSS search → citations. url = the item's <link> as Google returns it
    (falls back to the publisher's site from <source url=…>)."""
    out = []
    for it in ET.fromstring(xml_text).iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        src = ((src_el.text if src_el is not None else "") or "").strip()
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone().strftime("%d %b %H:%M")
        except Exception:
            when = "?"
        url = sl.safe_url((it.findtext("link") or "").strip()) or \
            sl.safe_url(src_el.get("url") if src_el is not None else None)
        if title:
            out.append({"title": title[:200], "source": src or "news", "when": when, "url": url,
                        "kind": "Google News"})
        if len(out) >= limit:
            break
    return out


async def _news_items(c: httpx.AsyncClient, q: str) -> list[dict]:
    r = await c.get("https://news.google.com/rss/search",
                    params={"q": search_terms(q) + " when:7d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    r.raise_for_status()
    return parse_news_rss(r.text)


def _remember(q: str, items: list[dict]) -> list[dict]:
    """Keep citations of tool-driven lookups (AI Core web_lookup/news_search) for sources_for()."""
    if items:
        key = search_terms(q)
        prev = _sources.get(key)
        old = prev[1] if prev and time.time() - prev[0] < CACHE_S else []
        seen = {x.get("url") or x.get("title") for x in items}
        _sources[key] = (time.time(), items + [x for x in old if (x.get("url") or x.get("title")) not in seen][:12])
    return items


async def _news(c: httpx.AsyncClient, q: str) -> list[str]:
    return [f"- {n['title']} ({n['source']}, {n['when']})" for n in _remember(q, await _news_items(c, q))]


async def _wiki(c: httpx.AsyncClient, q: str) -> list[str]:
    return [f"- Wikipedia “{w['title']}” (revised {w['when']}): {w['extract']}" for w in _remember(q, await _wiki_items(c, q))]


async def _wiki_items(c: httpx.AsyncClient, q: str) -> list[dict]:
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
    page = (((d.get("content_urls") or {}).get("desktop") or {}).get("page"))
    url = page if (sl.safe_url(page) or "").startswith("https://en.wikipedia.org/") else sl.wikipedia_article(title)
    return [{"title": title, "source": "Wikipedia", "when": stamp, "url": url, "extract": extract[:700],
             "kind": "Wikipedia"}] if extract else []


async def _crypto(c: httpx.AsyncClient, q: str) -> list[str]:
    return [f"- {x['coin']}: ${x['usd']:,} / ₹{x['inr']:,} ({x['change']:+.2f}% 24 h, CoinGecko {x['when']})"
            for x in await _crypto_items(c, q)]


async def _crypto_items(c: httpx.AsyncClient, q: str) -> list[dict]:
    ids = sorted({v for k, v in COINS.items() if re.search(rf"\b{re.escape(k)}\b", (q or "").lower())})
    if not ids:
        return []
    r = await c.get("https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": ",".join(ids), "vs_currencies": "usd,inr", "include_24hr_change": "true",
                            "include_last_updated_at": "true"})
    out = []
    for k, v in r.json().items():
        when = datetime.fromtimestamp(v.get("last_updated_at", time.time())).strftime("%H:%M")
        out.append({"coin": k, "usd": v.get("usd"), "inr": v.get("inr"), "change": v.get("usd_24h_change", 0) or 0,
                    "when": when, "title": f"{k} price", "source": "CoinGecko", "url": sl.coingecko_coin(k),
                    "kind": "CoinGecko"})
    return out


def _line(name: str, x: dict) -> str:
    """Prompt line for one citation (format unchanged from the string helpers above)."""
    if name == "Wikipedia":
        return f"- Wikipedia “{x['title']}” (revised {x['when']}): {x['extract']}"
    if name == "CoinGecko":
        return f"- {x['coin']}: ${x['usd']:,} / ₹{x['inr']:,} ({x['change']:+.2f}% 24 h, CoinGecko {x['when']})"
    return f"- {x['title']} ({x['source']}, {x['when']})"


def sources_for(question: str) -> list[dict]:
    """Citations fetched for this question in the last CACHE_S (empty when none / not time-sensitive).
    Only http(s) urls; an item without a valid url keeps url=None (shown unlinked)."""
    hit = _sources.get(search_terms(question or ""))
    if not hit or time.time() - hit[0] >= CACHE_S:
        return []
    return [{"title": s.get("title"), "source": s.get("source"), "kind": s.get("kind"), "when": s.get("when"),
             "url": sl.safe_url(s.get("url"))} for s in hit[1]]


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
                 (("Google News (last 7 days)", _news_items), ("Wikipedia", _wiki_items), ("CoinGecko", _crypto_items))}
        done, pending = await asyncio.wait(tasks.values(), timeout=BUDGET_S)
        for t in pending:
            t.cancel()
        lines, cites = [], []
        for name, t in tasks.items():
            if t in done and not t.exception() and t.result():
                try:
                    lines.append(f"{name}:\n" + "\n".join(_line(name, x) for x in t.result()))
                    cites += t.result()
                except (KeyError, TypeError, ValueError):     # malformed upstream row: skip that source
                    continue
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
    _sources[key] = (time.time(), cites)
    return block + live
