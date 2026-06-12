import asyncio
import platform
import time

import httpx
import psutil

from hub import Hub


class SystemMonitor:
    def __init__(self, hub: Hub, interval: float = 2.0) -> None:
        self.hub = hub
        self.interval = interval
        self.latest: dict = {}
        self.disk_path = "C:\\" if platform.system() == "Windows" else "/"

    async def run(self) -> None:
        psutil.cpu_percent(interval=None)
        net_prev = psutil.net_io_counters()
        t_prev = time.time()
        while True:
            await asyncio.sleep(self.interval)
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            try:
                disk = psutil.disk_usage(self.disk_path).percent
            except Exception:
                disk = 0.0
            net = psutil.net_io_counters()
            now = time.time()
            dt = max(now - t_prev, 0.001)
            up = (net.bytes_sent - net_prev.bytes_sent) / dt / 1024
            down = (net.bytes_recv - net_prev.bytes_recv) / dt / 1024
            net_prev, t_prev = net, now
            self.latest = {
                "cpu": cpu,
                "mem": mem,
                "disk": disk,
                "net_up": round(up, 1),
                "net_down": round(down, 1),
            }
            await self.hub.broadcast({"type": "metrics", **self.latest})


class NewsService:
    def __init__(self, api_key: str | None, hub: Hub) -> None:
        self.api_key = api_key
        self.hub = hub
        self.recent: list[dict] = []

    async def fetch_top(self, country: str = "us", page_size: int = 12) -> list[dict]:
        if not self.api_key:
            await self.hub.broadcast({
                "type": "log", "level": "warn",
                "msg": "news fetch skipped — NEWSAPI_KEY not set in .env",
            })
            return []
        url = "https://newsapi.org/v2/top-headlines"
        params = {"country": country, "pageSize": page_size, "apiKey": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url, params=params)
                r.raise_for_status()
                data = r.json()
            articles = [
                {
                    "title": a.get("title"),
                    "source": (a.get("source") or {}).get("name"),
                    "url": a.get("url"),
                    "ts": a.get("publishedAt"),
                }
                for a in data.get("articles", [])
                if a.get("title") and a.get("title") != "[Removed]"
            ]
            self.recent = articles
            await self.hub.broadcast({"type": "news", "items": articles})
            return articles
        except Exception as e:
            await self.hub.broadcast({
                "type": "log", "level": "warn", "msg": f"news fetch failed: {e}",
            })
            return []
