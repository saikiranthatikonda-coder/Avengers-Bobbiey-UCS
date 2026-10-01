"""Smoke + regression tests against a real, isolated UCS server.

No credentials, no network services, no touching the operator's data (see
harness.py). Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import asyncio
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ROOT, PYTHON, IsolatedServer, copy_app  # noqa: E402

SERVER: IsolatedServer | None = None
AGENTS = {"jarvis", "captain", "stark", "widow", "hawkeye", "hulk", "thor", "vision"}


def setUpModule():
    global SERVER
    SERVER = IsolatedServer().start()


def tearDownModule():
    if SERVER:
        SERVER.stop()


class Startup(unittest.TestCase):
    def test_status_reports_agents_metrics_and_fallback_brain(self):
        s = SERVER.get("/api/status")
        self.assertEqual({a["name"] for a in s["agents"]}, AGENTS)
        self.assertEqual(s["brain_mode"], "local", "no CLI/LLM in tests → templates")
        self.assertFalse(s["tts_enabled"])

    def test_dashboard_and_assets_are_served(self):
        html = SERVER.get("/")
        self.assertIn("/static/app.js", html)
        self.assertTrue(SERVER.get("/static/app.js"))
        self.assertTrue(SERVER.get("/static/style.css"))
        self.assertIn("<", SERVER.get("/login"))

    def test_boot_log_has_no_tracebacks(self):
        self.assertNotIn("Traceback", SERVER.log())


class FrontendContract(unittest.TestCase):
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    def test_app_js_and_css_share_cache_bust_version(self):
        js = re.search(r"app\.js\?v=([\w.]+)", self.html)
        css = re.search(r"style\.css\?v=([\w.]+)", self.html)
        self.assertTrue(js and css, "?v= cache-bust missing (PROJECT_CONTEXT §5)")
        self.assertEqual(js.group(1), css.group(1))

    def test_app_js_parses(self):
        # node isn't installed here; fall back to a cheap balance check
        src = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertGreater(len(src), 1000)
        self.assertEqual(src.count("`") % 2, 0, "unbalanced template literal")


class WebSocket(unittest.TestCase):
    def test_ws_streams_json_messages(self):
        import websockets

        async def first_msg():
            async with websockets.connect(f"ws://127.0.0.1:{SERVER.port}/ws") as ws:
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=15))

        msg = asyncio.run(first_msg())
        self.assertIn("type", msg)


class Agents(unittest.TestCase):
    def test_ask_uses_template_fallback(self):
        r = SERVER.post("/api/ask", {"agent": "jarvis", "prompt": "status report"})
        self.assertEqual(r["agent"], "jarvis")
        self.assertTrue(r["reply"])
        self.assertFalse(r["reply"].startswith("[brain"), r["reply"])

    def test_unknown_agent_lists_roster(self):
        r = SERVER.post("/api/ask", {"agent": "nobody", "prompt": "x"})
        self.assertIn("error", r)
        self.assertEqual(set(r["available"]), AGENTS)


class Orchestration(unittest.TestCase):
    def test_orchestrator_snapshot(self):
        o = SERVER.get("/api/orchestrator")
        self.assertEqual(o["coordinator"], "jarvis")
        for k in ("blackboard", "directives", "priority_labels"):
            self.assertIn(k, o)

    def test_team_memory_snapshot(self):
        m = SERVER.get("/api/team-memory")
        self.assertIn("count", m)
        self.assertIsInstance(m["recent"], list)

    def test_knowledge_search_shape(self):
        k = SERVER.get("/api/knowledge/search?q=cpu")
        self.assertEqual(k["query"], "cpu")
        self.assertIsInstance(k["results"], list)


class Decisions(unittest.TestCase):
    def test_autonomy_off_by_default(self):
        d = SERVER.get("/api/decisions")
        self.assertFalse(d["autonomy"], "supervised autonomy must be opt-in")
        self.assertIsInstance(d["whitelist"], list)

    def test_unknown_proposal_is_rejected(self):
        r = SERVER.post("/api/decisions/execute", {"id": "does-not-exist"})
        self.assertFalse(r.get("ok"), r)


class Billing(unittest.TestCase):
    def test_editions_and_demo_edition_switch(self):
        b = SERVER.get("/api/billing")
        ids = {e["id"] for e in b["editions"]}
        self.assertIn("community", ids)
        self.assertEqual(b["current"]["id"] if "id" in b["current"] else "community", "community")
        target = next(i for i in ids if i != "community")
        self.assertTrue(SERVER.post("/api/billing/edition", {"edition": target}).get("ok"))
        self.assertFalse(SERVER.post("/api/billing/edition", {"edition": "bogus"}).get("ok"))


class Web3(unittest.TestCase):
    def test_disabled_by_default_and_no_invented_contract(self):
        w = SERVER.get("/api/web3")
        self.assertFalse(w["enabled"])
        self.assertIsNone(w["wallet"])
        self.assertEqual(w["config"].get("token", {}).get("contract", ""), "")


class Integrations(unittest.TestCase):
    def test_google_absent_is_honest(self):
        c = SERVER.get("/api/calendar/status")
        self.assertFalse(c["connected"])
        self.assertFalse(c["credentials_present"])
        self.assertEqual(SERVER.get("/api/agenda")["source"], "disconnected")

    def test_without_newsapi_key_news_comes_from_open_feeds(self):
        # no key → keyless open feeds (or nothing yet); never NewsAPI, never mocked
        self.assertIn(SERVER.get("/api/status")["news_source"], ("none", "open"))


class Security(unittest.TestCase):
    def test_loopback_console_is_trusted(self):
        a = SERVER.get("/api/auth/status")
        self.assertTrue(a["authenticated"])
        self.assertFalse(a["password_set"])
        self.assertEqual(a["binding"], "127.0.0.1")

    def test_remote_denied_without_password(self):
        # TestClient's peer is "testclient" (non-loopback) → exercises the gate.
        # Runs in a fresh process on its own code copy so no state is shared.
        with tempfile.TemporaryDirectory(prefix="ucs-remote-") as d:
            copy_app(Path(d))
            code = ("from fastapi.testclient import TestClient; import main;"
                    "c = TestClient(main.app);"
                    "print(c.get('/api/status').status_code,"
                    "      c.get('/api/auth/status').status_code,"
                    "      c.get('/', follow_redirects=False).status_code)")
            out = subprocess.run([PYTHON, "-c", code], cwd=d, env=SERVER.env(),
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.split(), ["403", "200", "307"], out.stderr[-800:])


class AuthManagerUnit(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import auth
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(auth, "FILE", Path(self.tmp.name) / "auth.json")
        self.patch.start()
        self.am = auth.AuthManager()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_password_session_and_token_lifecycle(self):
        self.assertFalse(self.am.password_set)
        self.assertTrue(self.am.set_password("correct horse battery"))
        self.assertTrue(self.am.verify_password("correct horse battery"))
        self.assertFalse(self.am.verify_password("wrong"))
        sid = self.am.create_session("10.0.0.5")
        self.assertTrue(self.am.verify_session(sid))
        self.assertFalse(self.am.verify_session(sid + "tampered"))
        tok = self.am.create_api_token("ci")
        self.assertTrue(self.am.verify_api_token(tok))
        self.assertFalse(self.am.verify_api_token("nope"))


if __name__ == "__main__":
    unittest.main()
