"""Hacker News digest — first-party BUCS plugin.

Tool hn_top_stories: the current Hacker News front page (official Firebase API), filtered by
score and optional keyword. Read-only; needs the "network" permission.
"""

import asyncio
import time

API = "https://hacker-news.firebaseio.com/v0"
FEEDS = {"top": "topstories", "best": "beststories", "new": "newstories", "ask": "askstories", "show": "showstories"}


def _age(ts):
    if not ts:
        return "?"
    m = int((time.time() - ts) / 60)
    return f"{m} min ago" if m < 90 else f"{m // 60} h ago" if m < 2880 else f"{m // 1440} d ago"


def register(api):
    async def hn_top_stories(args, ctx=None):
        feed = FEEDS.get(str(args.get("feed") or "top"), "topstories")
        limit = max(1, min(20, int(args.get("limit") or 10)))
        min_score = int(args.get("min_score") or 0)
        kw = str(args.get("keyword") or "").lower().strip()
        ids = await api.http_get(f"{API}/{feed}.json")
        ids = list(ids or [])[: (60 if kw or min_score else limit * 2)]
        sem = asyncio.Semaphore(10)

        async def item(i):
            async with sem:
                try:
                    return await api.http_get(f"{API}/item/{int(i)}.json", timeout=8)
                except Exception:
                    return None
        items = [x for x in await asyncio.gather(*(item(i) for i in ids)) if isinstance(x, dict)]
        out = []
        for it in items:
            if it.get("dead") or it.get("deleted") or it.get("type") not in ("story", "job", "poll"):
                continue
            if (it.get("score") or 0) < min_score:
                continue
            if kw and kw not in (it.get("title") or "").lower():
                continue
            out.append({"title": it.get("title"), "score": it.get("score"), "comments": it.get("descendants", 0),
                        "by": it.get("by"), "age": _age(it.get("time")),
                        "url": it.get("url") or f"https://news.ycombinator.com/item?id={it.get('id')}",
                        "discussion": f"https://news.ycombinator.com/item?id={it.get('id')}"})
            if len(out) >= limit:
                break
        return {"feed": feed, "stories": out, "source": "Hacker News API", "fetched": time.strftime("%Y-%m-%d %H:%M")}

    api.tool("hn_top_stories", hn_top_stories)
