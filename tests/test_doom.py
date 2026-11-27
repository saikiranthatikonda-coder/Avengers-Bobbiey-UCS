"""DOOM agent (v8.4): roster, wake words (whole-word only), delegation of cyber requests, the
standing security brief in the dashboard facts, the measured-only local tick, and the hands-free
API gating. Offline — no microphone, no LLM, no network."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import agents  # noqa: E402
import intents  # noqa: E402
from voice import VoiceLoop  # noqa: E402


class Roster(unittest.TestCase):
    def test_doom_is_a_builtin_with_a_persona(self):
        spec = {s[0]: s for s in agents.AVENGER_SPECS}["doom"]
        self.assertEqual(spec[1], "DOOM")
        self.assertIn("PROPOSE", spec[4])                       # proposes, operator approves
        self.assertIn("never claim you ran", spec[4])
        self.assertIn("doom", agents.SPEAKING_AGENTS)
        team = agents.build_team(hub=mock.Mock(), brain=mock.Mock())
        self.assertEqual(team["doom"].system_prompt, agents.DOOM_SYS)


class WakeWords(unittest.TestCase):
    def test_doom_triggers(self):
        for text, cmd in (("Hey Doom, scan my network", "scan my network"),
                          ("Doctor Doom what is my posture", "what is my posture"),
                          ("dr doom status", "status")):
            self.assertEqual(VoiceLoop._match_agent(text), ("doom", cmd), text)

    def test_names_only_match_whole_words(self):
        self.assertNotEqual(VoiceLoop._match_agent("the author of this book")[0], "thor")
        self.assertNotEqual(VoiceLoop._match_agent("doomsday is on december eighteenth")[0], "doom")


class Delegation(unittest.TestCase):
    def test_cyber_requests_go_to_doom(self):
        for q in ("was I hacked?", "check for ransomware", "any CVEs in my packages",
                  "how do I harden my router", "give me a sitrep"):
            self.assertEqual(intents.delegate(q), "doom", q)

    def test_research_and_news_stay_with_widow(self):
        self.assertEqual(intents.delegate("research the history of Hyderabad"), "widow")
        self.assertEqual(intents.delegate("latest news please"), "widow")


class StandingBrief(unittest.TestCase):
    def test_doom_gets_security_sections_when_question_routes_nowhere(self):
        import dashboard_context as dc
        seen = {}

        async def fake_read(state, secs, q, cmd):
            seen["secs"] = list(secs)
            return []
        with mock.patch.object(dc, "read_sections", fake_read), mock.patch.object(dc, "render_block", lambda e: "B"):
            asyncio.run(dc.context_for({"x": 1}, "how are we doing", commander=True, meta={"agent": "doom"}))
        self.assertEqual(seen["secs"], ["sentinel", "secagent", "privacy", "threats"])     # v8.9: DEFCON first
        seen.clear()
        with mock.patch.object(dc, "read_sections", fake_read), mock.patch.object(dc, "render_block", lambda e: "B"):
            asyncio.run(dc.context_for({"x": 1}, "how are we doing", commander=True, meta={"agent": "jarvis"}))
        self.assertEqual(seen, {})                               # other agents unchanged


class LocalTick(unittest.TestCase):
    def test_tick_quotes_measured_posture_only(self):
        from local_brain import LocalBrain
        lb = LocalBrain()
        self.assertIn("Doom", lb.for_agent("doom"))              # nothing bound → honest standby
        sa = mock.Mock(snapshot=lambda: {"score": 86, "counts": {"high": 1},
                                         "last_scan": {"full": 1.0, "light": None}})
        lb.state = {"secagent": sa}
        self.assertIn("Posture 86 of 100, 1 high-severity", lb.for_agent("doom"))
        sa2 = mock.Mock(snapshot=lambda: {"score": None, "counts": {}, "last_scan": {}})
        lb.state = {"secagent": sa2}
        self.assertIn("has not finished", lb.for_agent("doom"))


class HandsFreeApi(unittest.TestCase):
    def test_owner_sees_handsfree_and_voice_off_is_reported(self):
        from harness import IsolatedServer
        srv = IsolatedServer(owner=True).start()
        try:
            d = srv.get("/api/conversation")
            d = d.json() if hasattr(d, "json") else d
            self.assertEqual(d.get("mode"), "hands-free")
            self.assertIn("doom", d["handsfree"]["agents"])
            r = srv.post("/api/conversation", {"agent": "doom"})    # no microphone loop in tests
            r = r.json() if hasattr(r, "json") else r
            self.assertFalse(r.get("ok", True))
            self.assertIn("voice is not running", r.get("error", ""))
        finally:
            srv.stop()

    def test_observer_cannot_change(self):
        from harness import IsolatedServer
        srv = IsolatedServer(owner=False).start()
        self.addCleanup(srv.stop)
        import urllib.error
        if True:
            try:
                r = srv.post("/api/conversation", {"on": False})
                r = r.json() if hasattr(r, "json") else r
                self.assertFalse(r.get("ok", True))
            except urllib.error.HTTPError as e:
                self.assertGreaterEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main()
