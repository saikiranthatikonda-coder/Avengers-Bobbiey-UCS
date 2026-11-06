"""Dashboard context (dashboard_context.py): every section reader returns real-shaped data or a
clean 'unavailable' entry, keyword routing, secrets never leak, observer redaction, the real-time
engine puts the facts into the prompt the agent receives, and Computer Use from chat goes through
submit_task with commander + host gating. Plus an end-to-end check on an IsolatedServer."""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from unittest import mock  # noqa: E402
import dashboard_context as dc  # noqa: E402
import intents  # noqa: E402
import realtime  # noqa: E402

SECRET = "sk-ant-api03-SUPERSECRETKEY0123456789"
LICKEY = "BUCS-eyJ0byI6ImN1c3RvbWVyIn0.c2lnbmF0dXJlc2lnbmF0dXJl"


def run(coro):
    return asyncio.run(coro)


class Snap:
    def __init__(self, data):
        self.data = data

    def snapshot(self, *a, **k):
        return self.data


class FakeCU:
    def __init__(self):
        self.calls = []
        self.task = {"id": "t1", "task": "open notepad and type hello", "status": "thinking", "n": 2,
                     "max_steps": 30, "started": time.time(), "steps": [{"desc": "click start", "result": "ok", "ok": True}],
                     "adapter_name": "test-vision", "source": "chat", "summary": "", "error": ""}
        self.ctl = None
        self.history = []
        self.candidates = [{"name": "test-vision", "available": True}]
        self.stopped = False

    def status(self):
        return self.task["status"] if self.task else "idle"

    async def submit_task(self, instruction, source="agentos", **kw):
        self.calls.append((instruction, source))
        return {"ok": True, "task_id": "t2", "status": "starting", "adapter": "test-vision"}

    def control(self, action, reason=""):
        self.stopped = action == "stop"
        return {"ok": True}


class FakeHub:
    def __init__(self):
        self.msgs = []

    async def broadcast(self, msg):
        self.msgs.append(msg)


def rich_state(role="commander"):
    return {
        "hub": FakeHub(),
        "rbac": {"role": role, "operator": "op"},
        "sysmon": SimpleNamespace(latest={"cpu": 37.0, "mem": 61.0, "disk": 70.0, "net_up": 5, "net_down": 40}),
        "threats": Snap({"risk_score": 42, "level": "ELEVATED", "matrix": {"cyber": {"score": 30}},
                         "events": [{"title": "Disk pressure", "severity": "medium", "category": "operations",
                                     "stage": "active", "ts": time.time()}],
                         "by_severity": {"medium": 1}, "by_stage": {"active": 1}, "response_queue": [], "soc": {}}),
        "secagent": Snap({"score": 81, "counts": {"high": 1}, "running": False,
                          "last_scan": {"full": time.time(), "light": None},
                          "findings": [{"severity": "high", "title": "Outdated urllib3 (CVE-2099-1)",
                                        "category": "deps", "fix": "pip install -U urllib3",
                                        "api_key": SECRET}],
                          "brief": f"one high finding; leaked token={SECRET}", "new": [], "resolved": [], "accepted": []}),
        "licensing": Snap({"edition": "pro", "paid": True, "licensed": True, "key": LICKEY[:9] + "…",
                           "install_id": "abcd-1234", "expires_at": time.time() + 86400 * 30, "days_left": 30,
                           "entitlements": {"engineering": True}}),
        "computeruse": FakeCU(),
        "phone_agent": SimpleNamespace(
            live={}, setup=lambda: {"ready": True},
            store=SimpleNamespace(
                analytics=lambda: {"calls": 1},
                calls=lambda n: [{"direction": "outbound", "to_number": "+919876543210", "from_number": "+15550001111",
                                  "status": "completed", "summary": "Booked the dentist for Friday", "sid": "CA" + "0" * 32,
                                  "created": time.time()}],
                followups=lambda status=None: [])),
        "disaster": SimpleNamespace(
            location=lambda *a: {"lat": 17.4, "lon": 78.5, "label": "Hyderabad"},
            snapshot=lambda *a: {"generated": time.time(), "near_km": 1000, "location": {"label": "Hyderabad"},
                                 "counts": {"total": 2, "near": 1},
                                 "items": [{"title": "M4.6 quake near Ongole", "type": "earthquake", "sev_label": "MODERATE",
                                            "severity": 2, "distance_km": 260, "near": True, "source": "USGS", "ts": time.time()},
                                           {"title": "M6.1 quake Japan", "type": "earthquake", "sev_label": "HIGH",
                                            "severity": 3, "distance_km": 6200, "near": False, "source": "USGS",
                                            "ts": time.time()}]}),
        "globalmap": SimpleNamespace(
            snapshot=lambda: {"generated": time.time(), "layers": {"quakes": {"count": 2, "status": "live"}},
                              "items": [{"title": "M4.6 Ongole", "layer": "quakes", "lat": 15.5, "lon": 80.0, "source": "USGS",
                                         "time": time.time(), "sev_label": "MODERATE"},
                                        {"title": "M6.1 Honshu", "layer": "quakes", "lat": 38.0, "lon": 141.0,
                                         "source": "USGS", "time": time.time()}],
                              "space": None, "opensky": {}},
            layer=lambda k: {"rows": []}),
    }


class ReaderTests(unittest.TestCase):
    def test_every_section_unavailable_cleanly_on_empty_state(self):
        for sec in dc.SECTIONS:
            e = run(dc.read_section({}, sec, "status?"))
            self.assertEqual(e["section"], sec)
            self.assertIn("as_of", e)
            self.assertIn("source", e)
            if not e["ok"]:
                self.assertTrue(e["unavailable"], sec)

    def test_real_shaped_sections(self):
        st = rich_state()
        for sec in ("system", "threats", "secagent", "licence", "computeruse", "phone", "disaster", "globalmap"):
            e = run(dc.read_section(st, sec, "what's near India?"))
            self.assertTrue(e["ok"], (sec, e))
            self.assertTrue(e["source"] and e["as_of"])
        self.assertEqual(run(dc.read_section(st, "system"))["cpu_pct"], 37.0)
        self.assertEqual(run(dc.read_section(st, "threats"))["open_incidents"], 1)
        g = run(dc.read_section(st, "globalmap", "what's on the global map near India?"))
        self.assertEqual(g["region"]["name"], "india")
        self.assertEqual(g["in_region"], 1)              # Ongole yes, Honshu no
        d = run(dc.read_section(st, "disaster", "how many quakes near me?"))
        self.assertEqual((d["filter"], d["near_you"]), ("earthquake", 1))

    def test_unknown_section_and_failures_are_isolated(self):
        st = rich_state()
        st["threats"] = SimpleNamespace(snapshot=lambda: 1 / 0)
        out = run(dc.read_sections(st, ["threats", "system", "nope"], ""))
        self.assertEqual([e["section"] for e in out], ["threats", "system"])
        self.assertFalse(out[0]["ok"])
        self.assertTrue(out[1]["ok"])
        self.assertFalse(run(dc.read_section(st, "nope"))["ok"])

    def test_slow_section_hits_its_budget(self):
        async def slow(state, q, cmd):
            await asyncio.sleep(5)
        orig = dc.SECTIONS["weather"]
        dc.SECTIONS["weather"] = (orig[0], slow, orig[2])
        try:
            t0 = time.time()
            out = run(dc.read_sections(rich_state(), ["weather", "system"], "", total_budget=0.5))
            self.assertLess(time.time() - t0, 2.0)
            self.assertFalse(out[0]["ok"])
            self.assertTrue(out[1]["ok"])
        finally:
            dc.SECTIONS["weather"] = orig


class RoutingTests(unittest.TestCase):
    CASES = {
        "what threats are open?": "threats",
        "how many quakes near me?": "disaster",
        "what did the cyber analyst find?": "secagent",
        "what's my CPU?": "system",
        "status of computer use task": "computeruse",
        "last phone call summary": "phone",
        "what's on the global map near India?": "globalmap",
        "which model are you using?": "brain",
        "is my licence expiring?": "licence",
        "what meetings do I have today": "agenda",
        "what is the evolution loop doing": "evolution",
        "any open decisions?": "decisions",
        "what's the self-heal status?": "selfheal",
    }

    def test_questions_pick_the_right_section(self):
        for q, sec in self.CASES.items():
            self.assertIn(sec, dc.route(q), q)
            self.assertLessEqual(len(dc.route(q)), dc.MAX_SECTIONS)

    def test_unrelated_requests_get_nothing(self):
        for q in ("tell me a joke", "write a poem about rain", ""):
            self.assertEqual(dc.route(q), [], q)

    def test_overview(self):
        self.assertEqual(dc.route("give me a dashboard overview"), list(dc.OVERVIEW_SET))

    def test_tool_enum_matches_sections(self):
        import agentos_tools
        tool = next(t for t in agentos_tools.builtin_tools({}) if t.name == "dashboard_read")
        self.assertEqual(tool.tier, "read")
        self.assertEqual(set(tool.parameters["properties"]["section"]["enum"]) - {"auto"}, set(dc.SECTIONS))


class SecurityTests(unittest.TestCase):
    def test_block_never_contains_secrets(self):
        st = rich_state()
        entries = run(dc.read_sections(st, ["secagent", "licence", "phone", "computeruse"], ""))
        block = dc.render_block(entries)
        self.assertIn("DASHBOARD FACTS", block)
        for bad in (SECRET, LICKEY[:9], "abcd-1234", "9876543210", "CA" + "0" * 32):
            self.assertNotIn(bad, block)
        self.assertIn("…3210", block)                      # masked number only
        self.assertLessEqual(len(block), dc.BLOCK_CHARS + 10)

    def test_sanitize(self):
        out = dc.sanitize({"api_key": "x", "token": "y", "password": "z", "nested": {"client_secret": "s",
                           "note": f"Authorization: Bearer {SECRET}", "gh": "ghp_" + "a" * 30}})
        txt = json.dumps(out)
        for bad in ("api_key", "\"token\"", "password", "client_secret", SECRET, "ghp_aaaa"):
            self.assertNotIn(bad, txt)

    def test_observer_gets_no_operator_detail(self):
        st = rich_state("observer")
        cu = run(dc.read_section(st, "computeruse"))
        self.assertNotIn("task", cu.get("task", {}))
        self.assertNotIn("open notepad", json.dumps(cu))
        ph = run(dc.read_section(st, "phone"))
        self.assertNotIn("dentist", json.dumps(ph))
        self.assertNotIn("3210", json.dumps(ph))
        cmd = run(dc.read_section(rich_state(), "computeruse"))
        self.assertIn("open notepad", json.dumps(cmd))


class FakeAgent:
    def __init__(self):
        self.prompts = []

    async def handle_stream(self, prompt, on_delta=None):
        self.prompts.append(prompt)
        return "Per the cyber analyst panel: one high finding.", "stub"

    async def _emit(self, *a, **k):
        pass


class EngineTests(unittest.TestCase):
    def setUp(self):
        # never let a test drive the real desktop: every Windows/browser action becomes a no-op
        import browser
        import win_actions
        noop = lambda *a, **k: {"ok": True, "say": "stubbed", "opened": False}  # noqa: E731
        self._patches = [mock.patch.object(win_actions, n, noop) for n in
                         ("open_app", "open_folder", "lock_screen", "media_key") if hasattr(win_actions, n)]
        self._patches.append(mock.patch.object(browser, "open_url", noop))
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _engine(self, st):
        agent = FakeAgent()
        st["team"] = {"jarvis": agent, "widow": agent}
        return realtime.Engine(st), agent

    def test_every_agent_prompt_gets_the_section_facts(self):
        async def go():
            eng, agent = self._engine(rich_state())
            cmd = await eng.submit("what did the cyber analyst find?", source="api", wait=True)
            return cmd, agent
        cmd, agent = run(go())
        self.assertEqual(cmd.stage, "done", cmd.error)
        self.assertIn("DASHBOARD FACTS", agent.prompts[0])
        self.assertIn("Cyber analyst findings", agent.prompts[0])
        self.assertIn("urllib3", agent.prompts[0])
        self.assertNotIn(SECRET, agent.prompts[0])
        self.assertEqual(dc.LAST.get("cmd_id"), cmd.id)

    def test_computeruse_needs_confirm_commander_and_host(self):
        async def go(role, host, texts):
            st = rich_state(role)
            eng, _ = self._engine(st)
            replies = []
            for t in texts:
                c = await eng.submit(t, source="text", host=host, wait=True, speak=False)
                replies.append(c.reply)
            return st["computeruse"], replies
        ask = ["Jarvis, use the computer to open notepad and type hello", "confirm"]
        cu, rep = run(go("commander", True, ask))
        self.assertIn("confirm", rep[0].lower())
        self.assertEqual(cu.calls, [("open notepad and type hello", "chat")])
        self.assertIn("running", rep[1].lower())
        cu, rep = run(go("commander", False, ask))          # remote browser
        self.assertEqual(cu.calls, [])
        self.assertIn("host machine", rep[0])
        cu, rep = run(go("observer", True, ask))            # observer session
        self.assertEqual(cu.calls, [])
        self.assertIn("commander", rep[0])
        cu, rep = run(go("commander", True, [ask[0], "no"]))  # declined
        self.assertEqual(cu.calls, [])
        cu, rep = run(go("observer", False, ["stop computer use"]))   # STOP always works
        self.assertTrue(cu.stopped)
        cu, rep = run(go("commander", True, ["what's the computer use status?"]))
        self.assertIn("step 2", rep[0])

    def test_voice_is_host_agents_are_not(self):
        self.assertTrue(realtime.Command(text="x", source="voice", host="voice" in realtime.HOST_SOURCES).host)
        async def go():
            eng, _ = self._engine(rich_state())
            a = await eng.submit("hi", source="agent", wait=True, speak=False)
            v = await eng.submit("", source="voice", wait=True, speak=False)
            return a.host, v.host
        self.assertEqual(run(go()), (False, True))

    def test_real_submit_task_refuses_observer(self):
        import computeruse
        with tempfile.TemporaryDirectory() as d:
            eng = computeruse.Engine({"state": {}, "audit": lambda *a: None, "commander": lambda: False,
                                      "host_only": lambda r: True}, data_dir=Path(d))
            r = run(eng.submit_task("open notepad", source="chat"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "refused")


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from harness import IsolatedServer
        cls.srv = IsolatedServer().start(timeout=180)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_sections_and_typed_question_reach_the_reply_path(self):
        srv = self.srv
        cat = srv.get("/api/dashboard/context?q=what%20did%20the%20cyber%20analyst%20find%3F")
        self.assertEqual({s["section"] for s in cat["sections"]}, set(dc.SECTIONS))
        self.assertEqual(cat["route"], ["secagent"])
        for sec in dc.SECTIONS:                            # real objects: ok or clean unavailable, no secrets
            e = srv.get(f"/api/dashboard/context/read/{sec}", timeout=20)
            self.assertEqual(e["section"], sec)
            self.assertTrue(e["ok"] or e.get("unavailable"), (sec, e))
            txt = json.dumps(e)
            self.assertNotIn("install_id", txt)
            self.assertNotIn("BUCS-", txt)
        r = srv.post("/api/command", {"text": "what did the cyber analyst find?", "speak": False})
        self.assertTrue(r["ok"])
        last = {}
        for _ in range(60):
            last = srv.get("/api/dashboard/context/last")
            if last.get("cmd_id") == r["id"]:
                break
            time.sleep(0.25)
        self.assertEqual(last.get("cmd_id"), r["id"])
        self.assertIn("secagent", last["sections"])
        self.assertIn("DASHBOARD FACTS", last["block"])
        self.assertIn("Cyber analyst", last["block"])


if __name__ == "__main__":
    unittest.main()
