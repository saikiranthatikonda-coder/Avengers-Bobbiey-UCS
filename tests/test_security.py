"""Security regressions: browser-origin guard, fleet sanitising, provider keys, URL opening,
weather resilience and the fast process scanner."""

import asyncio
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import IsolatedServer  # noqa: E402

SERVER: IsolatedServer | None = None


def setUpModule():
    global SERVER
    SERVER = IsolatedServer().start()


def tearDownModule():
    if SERVER:
        SERVER.stop()


def raw(method, path, headers=None, body=b"{}"):
    req = urllib.request.Request(SERVER.base + path, data=body if method != "GET" else None, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class OriginGuard(unittest.TestCase):
    def test_a_foreign_website_cannot_drive_bucs(self):
        evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
        self.assertEqual(raw("POST", "/api/system/shutdown", evil), 403)        # would stop BUCS
        self.assertEqual(raw("POST", "/api/llm/active", evil, b'{"id":"auto"}'), 403)
        self.assertEqual(raw("GET", "/api/memory/profile/text", evil), 403)     # no cross-site reads
        self.assertEqual(raw("POST", "/api/notes", {"Origin": "null"}, b'{"text":"x"}'), 403)
        self.assertEqual(raw("GET", "/api/status", {"Host": "evil.example:8765"}), 403)   # DNS rebinding
        self.assertIsNone(SERVER.proc.poll(), "server must still be running")

    def test_the_dashboard_and_tools_still_work(self):
        same = {"Origin": SERVER.base, "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(raw("GET", "/api/status", same), 200)
        self.assertEqual(raw("POST", "/api/llm/active", same, b'{"id":"auto"}'), 200)
        self.assertEqual(raw("GET", "/api/status"), 200)                         # scripts / fleet nodes

    def test_websocket_refuses_foreign_pages(self):
        import websockets

        async def connect(origin):
            async with websockets.connect(f"ws://127.0.0.1:{SERVER.port}/ws", origin=origin) as ws:
                return await asyncio.wait_for(ws.recv(), 10)
        with self.assertRaises(Exception):
            asyncio.run(connect("https://evil.example"))
        self.assertTrue(asyncio.run(connect(SERVER.base)))


class Sanitising(unittest.TestCase):
    def test_fleet_reports_are_rebuilt_from_a_strict_schema(self):
        from fleet import Fleet, sanitize_report
        bad = {"node_id": "n1", "name": None, "cpu": "<img src=x onerror=alert(1)>", "cpu_cores": "lots",
               "ollama": {"available": True, "port": "80@evil.com:80"}, "volumes": "nope"}
        c = sanitize_report(bad)
        self.assertEqual((c["cpu"], c["cpu_cores"], c["ollama"]["port"], c["volumes"]), (0.0, 0, 11434, []))
        f = Fleet(); f.ingest(dict(bad)); f.ingest({"node_id": "n2", "name": 7})
        self.assertEqual(f.snapshot()["aggregate"]["nodes"], 2)                    # no crash on bad names

    def test_provider_key_never_follows_a_url_change(self):
        import llm_providers
        from llm_local import LocalLLM

        class B:
            mode = "llm"; force_local = False; provider = None; provider_name = None
        with tempfile.TemporaryDirectory() as d:
            reg = llm_providers.ProviderRegistry(B(), LocalLLM(base_url="http://127.0.0.1:9/v1", model="m"),
                                                 Path(d) / "p.json")
            reg.upsert("OpenAI", "https://api.openai.com/v1", "gpt", "sk-secret-key-123456")
            reg.upsert("OpenAI", "https://evil.example/v1", "gpt", None, "openai")
            self.assertEqual(reg.client("openai").api_key, None)
            reg.upsert("OpenAI", "https://evil.example/v1", "gpt2", None, "openai")      # same URL edit is fine

    def test_only_web_urls_open_in_the_browser(self):
        import browser
        with mock.patch.object(browser.subprocess, "Popen") as po, mock.patch.object(browser.webbrowser, "open") as wo:
            for u in ("--renderer-cmd-prefix=calc", "file:///C:/Windows/System32/calc.exe", "ms-settings:", "C:\\x.exe"):
                self.assertFalse(browser.open_url(u).get("opened", False), u)
            po.assert_not_called(); wo.assert_not_called()


class Resilience(unittest.TestCase):
    def test_weather_shares_calls_and_serves_last_good_reading(self):
        import weather as wm
        calls = {"n": 0}

        class Resp:
            def raise_for_status(self): pass
            def json(self): return {"current": {"temperature_2m": 25.0, "weather_code": 1}}

        class Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k):
                calls["n"] += 1; await asyncio.sleep(0.05); return Resp()

        class Down(Client):
            async def __aenter__(self): raise wm.httpx.ConnectTimeout("down")

        async def run():
            w = wm.WeatherService()
            with mock.patch.object(wm.httpx, "AsyncClient", Client):
                res = await asyncio.gather(*[w.get(1.0, 2.0) for _ in range(4)])
            w._cache.clear()
            with mock.patch.object(wm.httpx, "AsyncClient", Down):
                stale = await w.get(1.0, 2.0)
            return res, stale
        res, stale = asyncio.run(run())
        self.assertEqual(calls["n"], 1)                                   # 4 requests → 1 upstream call
        self.assertEqual({r["temp_c"] for r in res}, {25.0})
        self.assertTrue(stale["stale"] and stale["temp_c"] == 25.0)       # outage → cached, not "timeout"

    def test_process_scan_is_fast(self):
        import time
        import procscan
        procscan.snapshot()
        t = time.perf_counter(); rows = procscan.snapshot(); dt = time.perf_counter() - t
        self.assertTrue(rows)
        self.assertLess(dt, 0.5, f"process scan took {dt:.2f}s")         # psutil took ~2 s per sweep


if __name__ == "__main__":
    unittest.main()
