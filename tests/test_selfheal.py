"""Self-healing watchdog: real repairs, honest escalation, grounded AI diagnosis."""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import selfheal  # noqa: E402


class Hub:
    def __init__(self): self.msgs = []
    async def broadcast(self, m): self.msgs.append(m)


class Feed:
    def __init__(self, ok_after_refresh=True):
        self.last_ok = 0.0; self.calls = 0; self._ok = ok_after_refresh
    async def get(self):
        self.calls += 1
        if self._ok: self.last_ok = time.time()
    fetch_top = get


class Brain:
    route_counts = {"claude": 8, "template": 2}; provider_name = None; mode = "llm"
    def __init__(self): self.prompts = []
    async def think(self, prompt, agent="jarvis", timeout=60):
        self.prompts.append(prompt); return "The weather feed is stale; it was refreshed. No action needed."


class Watchdog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(selfheal, "STATE_FILE", Path(self.tmp.name) / "selfheal.json"); self.p.start()

    def tearDown(self):
        self.p.stop(); self.tmp.cleanup()

    def run_(self, coro):
        return asyncio.run(coro)

    def test_crashed_task_is_restarted_and_stale_feeds_refreshed(self):
        async def go():
            started = []

            async def crashes():
                raise RuntimeError("boom")

            async def healthy():
                started.append(1); await asyncio.sleep(3600)
            dead = asyncio.create_task(crashes()); await asyncio.sleep(0)
            st = {"hub": Hub(), "runtime_started": True, "runtime_tasks": {"telemetry": dead},
                  "runtime_factories": {"telemetry": healthy}, "weather": Feed(), "news": Feed(), "brain": Brain()}
            sh = selfheal.SelfHeal(st)
            snap = await sh.run_checks(repair=True)
            await asyncio.sleep(0)
            alive = not st["runtime_tasks"]["telemetry"].done()
            st["runtime_tasks"]["telemetry"].cancel()
            return snap, started, alive, st
        snap, started, alive, st = self.run_(go())
        self.assertTrue(alive and started, "crashed task must be running again")
        self.assertTrue(any("restarted the 'telemetry' background task" in e["msg"] and "boom" in e["msg"] for e in snap["log"]))
        self.assertEqual(snap["checks"]["weather"]["status"], "ok")             # stale feed refreshed
        self.assertTrue(any("weather was stale; refreshed" in e["msg"] for e in snap["log"]))

    def test_no_repairs_when_auto_repair_is_off(self):
        async def go():
            st = {"hub": Hub(), "runtime_started": True, "weather": Feed(), "news": Feed()}
            sh = selfheal.SelfHeal(st)
            snap = await sh.run_checks(repair=False)
            return snap, st
        snap, st = self.run_(go())
        self.assertEqual(st["weather"].calls, 0)
        self.assertEqual(snap["checks"]["weather"]["status"], "warn")

    def test_restart_only_under_the_launcher_and_never_in_a_loop(self):
        async def go(env):
            sh = selfheal.SelfHeal({"hub": Hub()})
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch("signal.raise_signal") as sig, mock.patch("updater.request_exit") as req:
                await sh._restart("event_loop")
                await asyncio.sleep(1.2)
                first = (sig.called, req.called)
                sig.reset_mock(); req.reset_mock()
                await sh._restart("event_loop")                     # second time inside the cooldown
                await asyncio.sleep(1.2)
                return first, (sig.called, req.called), sh.log
        os.environ.pop("BUCS_LAUNCHER", None)
        first, second, log = self.run_(go({}))
        self.assertEqual(first, (False, False))                     # nothing to relaunch it → hold
        first, second, log = self.run_(go({"BUCS_LAUNCHER": "1"}))
        self.assertEqual(first, (True, True))
        self.assertEqual(second, (False, False))                    # cooldown: no restart loop
        self.assertTrue(any("already happened" in e["msg"] for e in log))

    def test_ai_diagnosis_is_grounded_in_measured_checks(self):
        async def go():
            b = Brain()
            sh = selfheal.SelfHeal({"hub": Hub(), "brain": b})
            sh.checks = {"weather": {"status": "warn", "detail": "stale: 45 min old", "value": 2700, "ts": time.time()}}
            r = await sh.diagnose()
            return r, b.prompts[0]
        r, prompt = self.run_(go())
        self.assertTrue(r["ok"])
        self.assertIn("stale: 45 min old", prompt)                   # the real measurement is in the prompt
        self.assertIn("Do not invent data", prompt)


class LiveEndpoint(unittest.TestCase):
    def test_health_endpoint_reports_real_checks(self):
        from harness import IsolatedServer
        srv = IsolatedServer().start()
        try:
            snap = srv.post("/api/health/run", {})
            for k in ("event_loop", "cpu", "memory", "tasks", "scheduler"):
                self.assertIn(k, snap["checks"])
            self.assertEqual(snap["checks"]["tasks"]["status"], "ok")
            self.assertLess(snap["checks"]["event_loop"]["value"], 2000)
            self.assertFalse(snap["under_launcher"])                 # tests never self-restart
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main()
