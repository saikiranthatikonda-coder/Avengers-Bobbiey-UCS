"""Dashboard data honesty: every card shows measured data or says it has none.

Covers the AI PERFORMANCE endpoint (/api/dashboard/ai-performance), the threat
engine's real incident lifecycle (no timer-driven "resolved", no invented matrix
confidence), and static guards against hardcoded/mock values in the dashboard.
"""

import asyncio
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ROOT, IsolatedServer  # noqa: E402

sys.path.insert(0, str(ROOT))

SERVER: IsolatedServer | None = None


def setUpModule():
    global SERVER
    SERVER = IsolatedServer().start()


def tearDownModule():
    if SERVER:
        SERVER.stop()


class AiPerformanceEndpoint(unittest.TestCase):
    KEYS = {"ts", "commands", "fast", "llm", "failed", "cancelled", "success_pct",
            "first_token_p50_ms", "routes", "replies", "insight_engine", "local_model", "sources"}

    def test_shape_and_no_invented_success_rate(self):
        d = SERVER.get("/api/dashboard/ai-performance")
        self.assertTrue(self.KEYS <= set(d), self.KEYS - set(d))
        if d["commands"] == 0:
            # nothing measured yet: the card must say so, not show a made-up percentage
            self.assertIsNone(d["success_pct"])
        self.assertIsInstance(d["routes"], dict)
        self.assertGreater(d["ts"], time.time() - 120)

    def test_success_rate_follows_real_commands(self):
        r = SERVER.post("/api/command", {"text": "what time is it", "source": "text"})
        self.assertTrue(r.get("ok"), r)
        deadline = time.time() + 30
        d = {}
        while time.time() < deadline:
            d = SERVER.get("/api/dashboard/ai-performance")
            eng = SERVER.get("/api/engine")
            if d["commands"] >= 1 and not eng.get("active"):
                break
            time.sleep(0.5)
        self.assertGreaterEqual(d["commands"], 1)
        bad = d["failed"] + d["cancelled"]
        self.assertAlmostEqual(d["success_pct"], round((d["commands"] - bad) * 100 / d["commands"], 1))


class ThreatLifecycle(unittest.TestCase):
    """threats.py: stages come from real state (telemetry, ACK, age), not a timer."""

    def make(self, mem=95.0):
        from threats import ThreatEngine
        hub = mock.Mock(broadcast=mock.AsyncMock())
        sysmon = mock.Mock(latest={"cpu": 10.0, "mem": mem, "disk": 30.0})
        return ThreatEngine(hub, sysmon=sysmon), sysmon

    def test_metric_incident_stays_active_until_telemetry_clears(self):
        eng, sysmon = self.make(mem=95.0)
        asyncio.run(eng.tick())
        snap = eng.snapshot()
        mem_ev = [e for e in snap["events"] if e["title"].startswith("Memory critical")]
        self.assertEqual(len(mem_ev), 1)
        self.assertEqual(mem_ev[0]["stage"], "active")
        self.assertTrue(mem_ev[0]["auto_check"])
        self.assertNotIn("cond", mem_ev[0], "internal condition must not leak to the API")
        self.assertEqual(snap["soc"]["resolved"], 0)
        # memory recovers → resolved, and it no longer adds to the risk score
        risk_before = snap["risk_score"]
        sysmon.latest = {"cpu": 10.0, "mem": 60.0, "disk": 30.0}
        snap = eng.snapshot()
        ev = next(e for e in snap["events"] if e["id"] == mem_ev[0]["id"])
        self.assertEqual(ev["stage"], "resolved")
        self.assertIn("back to 60%", ev.get("resolved_note", ""))
        self.assertEqual(snap["soc"]["resolved"], 1)
        self.assertLess(snap["risk_score"], risk_before)
        # still in the response queue until the operator closes it out
        self.assertIn(ev["id"], [e["id"] for e in snap["response_queue"]])

    def test_ack_and_expiry(self):
        eng, _ = self.make(mem=95.0)
        asyncio.run(eng.tick())
        ev = eng.snapshot()["events"][0]
        self.assertTrue(eng.acknowledge(ev["id"]))
        snap = eng.snapshot()
        self.assertEqual(next(e for e in snap["events"] if e["id"] == ev["id"])["stage"], "acknowledged")
        self.assertNotIn(ev["id"], [e["id"] for e in snap["response_queue"]])
        # an incident with no clear signal expires after 30 min instead of "resolving" by itself
        eng2, _ = self.make(mem=50.0)
        asyncio.run(eng2._add("external", "low", "Security headline: test", cooldown=0))
        eng2.events[0]["ts"] -= 3600
        self.assertEqual(eng2.snapshot()["events"][0]["stage"], "expired")

    def test_matrix_reports_signals_not_invented_confidence(self):
        eng, _ = self.make(mem=95.0)
        asyncio.run(eng.tick())
        mx = eng.snapshot()["matrix"]
        self.assertEqual(set(mx), {"operational", "infrastructure", "network", "ai", "resource", "security"})
        for k, v in mx.items():
            self.assertNotIn("confidence", v, k)
            self.assertIsInstance(v["signals"], int)
            self.assertTrue(v["basis"])
        self.assertGreaterEqual(mx["resource"]["signals"], 1)

    def test_threats_api_uses_real_stages(self):
        d = SERVER.get("/api/threats")
        for e in d["events"]:
            self.assertIn(e["stage"], {"active", "resolved", "acknowledged", "expired"})
        self.assertIn("by_stage", d)


class NoHardcodedDashboardValues(unittest.TestCase):
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def test_ai_performance_card_has_no_fixed_numbers(self):
        card = self.html[self.html.index('id="ai-perf-card"'):]
        card = card[:card.index("</section>") if "</section>" in card[:4000] else 4000]
        for fake in ("92<span>", "OPTIMAL", "0.32s", ">HIGH<"):
            self.assertNotIn(fake, card)

    def test_no_placeholder_status_text(self):
        for fake in ("AUTONOMOUS RESPONSE · ACTIVE", 'id="res-gpu">66%', "ALL SYSTEMS NOMINAL", "6 NODES",
                     "PERIMETER SCAN", "PHASE FLUX", "= AVENGERS"):
            self.assertNotIn(fake, self.html, fake)

    def test_world_map_has_no_fixed_activity_cities(self):
        self.assertNotIn('"NEW YORK"', self.js)
        self.assertNotIn("aiGaugeBase", self.js)
        self.assertIsNone(re.search(r"agentsOnline \|\| 8", self.js))

    def test_feature_modules_link_to_the_global_map(self):
        # map linkage is feature-detected; nothing may assume globalmap.js loaded
        self.assertIn("window.GlobalMap", self.js)
        self.assertIn('typeof gm.focus === "function"', self.js)
        dis = (ROOT / "static" / "disaster.js").read_text(encoding="utf-8")
        self.assertIn('data-map-layer="disaster"', dis)


if __name__ == "__main__":
    unittest.main()
