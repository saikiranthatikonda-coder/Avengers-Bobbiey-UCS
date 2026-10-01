"""Keyless open news sources — the default feed when NEWSAPI_KEY isn't set.

Public RSS feeds (publisher-provided syndication) plus the Hacker News
Algolia API. Headlines link back to the publisher; nothing is republished.
Parsing is stdlib-only (xml.etree), so no new dependency.
"""

import asyncio
import email.utils
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

# (source label, feed URL, category)
RSS_FEEDS = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "world"),
    ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss", "india"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "world"),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews", "security"),
]
HN_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=10"
UA = {"User-Agent": "BobbieyUCS/1.0 (+https://avengers-bobbiey.netlify.app)"}
_TAG = re.compile(r"<[^>]+>")


def _clean(s: str | None) -> str:
    return html.unescape(_TAG.sub("", s or "")).strip()


def _iso(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(date_str)          # RSS (RFC 822)
    except Exception:
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))  # Atom / ISO
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_rss(xml_text: str, source: str, category: str, limit: int = 8) -> list[dict]:
    """RSS 2.0 <item> or Atom <entry> → article dicts."""
    root = ET.fromstring(xml_text)
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.iter("item") if root.find(".//item") is not None else root.iter(f"{atom}entry")
    for it in items:
        title = _clean(it.findtext("title") or it.findtext(f"{atom}title"))
        link = (it.findtext("link") or "").strip()
        if not link:
            el = it.find(f"{atom}link")
            link = el.get("href", "") if el is not None else ""
        when = it.findtext("pubDate") or it.findtext(f"{atom}updated") or it.findtext(f"{atom}published")
        if title and link.startswith("http"):
            out.append({"title": title[:200], "source": source, "url": link,
                        "ts": _iso(when), "category": category})
        if len(out) >= limit:
            break
    return out


def parse_hn(data: dict, limit: int = 8) -> list[dict]:
    out = []
    for h in data.get("hits", [])[:limit]:
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        if h.get("title"):
            out.append({"title": h["title"][:200], "source": "Hacker News", "url": url,
                        "ts": h.get("created_at"), "category": "tech"})
    return out


def merge(batches: list[list[dict]], limit: int = 20) -> list[dict]:
    """De-duplicate by normalised title, newest first, interleave sources so
    one prolific feed can't flood the panel."""
    seen, pool = set(), []
    for batch in batches:
        for a in batch:
            key = re.sub(r"\W+", "", a["title"].lower())[:80]
            if key and key not in seen:
                seen.add(key)
                pool.append(a)
    pool.sort(key=lambda a: a.get("ts") or "", reverse=True)
    by_src: dict[str, list[dict]] = {}
    for a in pool:
        by_src.setdefault(a["source"], []).append(a)
    out = []
    while len(out) < limit and any(by_src.values()):
        for src in list(by_src):
            if by_src[src]:
                out.append(by_src[src].pop(0))
    return out[:limit]


async def fetch_open_news(limit: int = 20) -> tuple[list[dict], list[str]]:
    """Returns (articles, failed_source_names). Never raises."""
    failed: list[str] = []
    async with httpx.AsyncClient(timeout=12, trust_env=False, headers=UA,
                                 follow_redirects=True) as c:
        async def rss(name, url, cat):
            try:
                r = await c.get(url); r.raise_for_status()
                return parse_rss(r.text, name, cat)
            except Exception:
                failed.append(name); return []

        async def hn():
            try:
                r = await c.get(HN_URL); r.raise_for_status()
                return parse_hn(r.json())
            except Exception:
                failed.append("Hacker News"); return []

        batches = await asyncio.gather(*(rss(*f) for f in RSS_FEEDS), hn())
    return merge(list(batches), limit), failed
