"""Real internet measurements: latency, packet loss and throughput.

* latency — TCP connect round-trip to well-known anycast resolvers (1.1.1.1,
  8.8.8.8) on :443. A TCP handshake is one network round trip, so this is a
  true link latency — unlike timing a whole HTTPS request (DNS + TLS + HTTP),
  which read ~1 s here and pinned the dashboard at "999".
* packet loss — share of failed probes over a rolling window (not a guess).
* speed test — Cloudflare's public speed endpoints (speed.cloudflare.com, no
  key). A 5 MB probe estimates the rate, then a download sized for ~4 s
  (5–25 MB) is timed, because short transfers end inside TCP slow start and
  under-read (10 MB read 95 Mbps on a 240 Mbps link). Upload is 3 MB. Uses
  real data (≤ ~33 MB per run), so it runs on demand and on a slow schedule
  (JARVIS_SPEEDTEST_MIN, default 60, 0 = manual only).
"""

import asyncio
import os
import statistics
import time
from collections import deque

import httpx

PROBE_TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 443)]
DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"
PROBE_BYTES = 5_000_000
MIN_BYTES, MAX_BYTES = 5_000_000, 25_000_000
TARGET_SECONDS = 4.0
UP_BYTES = 3_000_000
# the endpoint's WAF intermittently 403s bare clients; a Referer satisfies it
HEADERS = {"User-Agent": "BobbieyUCS/1.0", "Referer": "https://speed.cloudflare.com/"}


async def tcp_rtt_ms(host: str, port: int, timeout: float = 2.0) -> float | None:
    t = time.perf_counter()
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        rtt = (time.perf_counter() - t) * 1000
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return rtt
    except Exception:
        return None


class NetMonitor:
    def __init__(self, window: int = 30) -> None:
        self.probes: deque = deque(maxlen=window)   # True = answered
        self.last_latency_ms: float | None = None
        self.jitter_ms: float | None = None
        self.speed: dict | None = None               # last speed-test result
        self.testing = False
        self._lock = asyncio.Lock()

    # ── latency + loss ────────────────────────────────────────────
    async def probe(self) -> dict:
        rtts = []
        for host, port in PROBE_TARGETS:
            for _ in range(2):
                r = await tcp_rtt_ms(host, port)
                self.probes.append(r is not None)
                if r is not None:
                    rtts.append(r)
        if rtts:
            self.last_latency_ms = round(statistics.median(rtts), 1)
            self.jitter_ms = round(statistics.pstdev(rtts), 1) if len(rtts) > 1 else 0.0
        else:
            self.last_latency_ms = None
        return self.snapshot()

    @property
    def loss_pct(self) -> float | None:
        if not self.probes:
            return None
        return round(100 * (1 - sum(self.probes) / len(self.probes)), 1)

    # ── throughput ────────────────────────────────────────────────
    async def speed_test(self) -> dict:
        if self._lock.locked():
            return {"ok": False, "error": "speed test already running"}
        async with self._lock:
            self.testing = True
            try:
                res = await self._run_speed_test()
                self.speed = res
                return res
            finally:
                self.testing = False

    async def _run_speed_test(self) -> dict:
        started = time.time()
        try:
            async with httpx.AsyncClient(timeout=40, trust_env=False, headers=HEADERS) as c:
                # warm the connection so TLS setup isn't billed to throughput
                await c.get(DOWN_URL.format(n=1000))
                probe_bps = await self._timed_down(c, PROBE_BYTES)
                size = int(min(MAX_BYTES, max(MIN_BYTES, probe_bps / 8 * TARGET_SECONDS)))
                bps = await self._timed_down(c, size)
                got = size + PROBE_BYTES
                payload = os.urandom(UP_BYTES)
                t = time.perf_counter()
                r = await c.post(UP_URL, content=payload)
                r.raise_for_status()
                up_s = time.perf_counter() - t
            return {
                "ok": True,
                "down_mbps": round(bps / 1e6, 1),
                "up_mbps": round(UP_BYTES * 8 / up_s / 1e6, 1),
                "latency_ms": self.last_latency_ms,
                "bytes_used": got + UP_BYTES,
                "server": "speed.cloudflare.com",
                "ts": started,
            }
        except Exception as e:
            return {"ok": False, "error": f"speed test failed: {e}"[:200], "ts": started}

    @staticmethod
    async def _timed_down(c: httpx.AsyncClient, n: int) -> float:
        """Download n bytes; returns bits/second. One retry on a WAF 403."""
        for attempt in range(2):
            t = time.perf_counter(); got = 0
            async with c.stream("GET", DOWN_URL.format(n=n)) as r:
                if r.status_code == 403 and attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    got += len(chunk)
            return got * 8 / max(time.perf_counter() - t, 1e-6)
        raise RuntimeError("speed endpoint refused the download")

    def snapshot(self) -> dict:
        return {
            "latency_ms": self.last_latency_ms,
            "jitter_ms": self.jitter_ms,
            "loss_pct": self.loss_pct,
            "probes": len(self.probes),
            "speed": self.speed,
            "testing": self.testing,
        }
