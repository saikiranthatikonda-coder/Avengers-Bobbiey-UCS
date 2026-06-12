import asyncio
import time
from typing import Any


class Hub:
    """In-process pub/sub. Backend modules publish dicts; WS clients consume them."""

    def __init__(self) -> None:
        self.subscribers: list[asyncio.Queue] = []
        self.recent: list[dict] = []
        self.max_recent = 200

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        msg.setdefault("ts", time.time())
        self.recent.append(msg)
        if len(self.recent) > self.max_recent:
            self.recent = self.recent[-self.max_recent :]
        dead = []
        for q in self.subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)
