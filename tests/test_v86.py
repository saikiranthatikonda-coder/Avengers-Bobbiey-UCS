"""v8.6: agents see and drive the dashboard, shared commander memory, mute-all, a separate
hands-free switch, Computer Use by voice, the map's TODAY mode. Offline (fake fetch, no mic)."""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import intents  # noqa: E402

run = asyncio.run


class Mem:
    def __init__(self):
        self.f = []

    async def add_fact(self, t, source=""):
        self.f.append((t, source))

    async def remove_facts(self, q):
        gone = [t for t, _ in self.f if q.lower().split()[-1] in t.lower()]
        self.f = [x for x in self.f if x[0] not in gone]
        return gone


class TTS:
    muted = False

    def set_muted(self, m):
        self.muted = m

    def audio_state(self):
        return {"muted": self.muted}


def ctx(**kw):
    return {"memory": Mem(), "tts": TTS(), **kw}


class SharedMemoryIntents(unittest.TestCase):
    def test_remember_shares_and_keeps_commander_words(self):
        c = ctx()
        r = run(intents.match(c, "Jarvis, remember that I prefer short answers"))
        self.assertEqual(r["intent"], "remember")
        self.assertEqual(c["memory"].f[0][0], 'Commander said: "I prefer short answers"')
        self.assertIn("whole team", r["say"])
        run(intents.match(c, "remember the office wifi is on floor 3"))
        self.assertEqual(c["memory"].f[1][0], "The office wifi is on floor 3")

    def test_not_a_memory_request(self):
        c = ctx()
        for q in ("I remember that day", "remember to call mom tomorrow", "do you remember the plan"):
            r = run(intents.match(c, q))
            self.assertNotEqual((r or {}).get("intent"), "remember", q)
        self.assertEqual(c["memory"].f, [])

    def test_forget(self):
        c = ctx()
        run(intents.match(c, "remember that I prefer short answers"))
        r = run(intents.match(c, "forget that I prefer short answers"))
        self.assertIn("forgotten", r["say"])
        self.assertEqual(c["memory"].f, [])


class MemoryEngine(unittest.TestCase):
    def test_remove_and_shared_block(self):
        import memory
        with tempfile.TemporaryDirectory() as d, mock.patch.object(memory, "MEMORY_FILE", Path(d) / "m.json"):
            m = memory.OperatorMemory(hub=None) if "hub" in memory.OperatorMemory.__init__.__code__.co_varnames else memory.OperatorMemory()
            m._announce = mock.AsyncMock()
            run(m.add_fact("Prefers short answers"))
            run(m.add_fact("Works from Hyderabad on weekdays"))
            self.assertIn("Hyderabad", m.shared_block())
            self.assertIn("SHARED TEAM MEMORY", m.shared_block())
            self.assertEqual(run(m.remove_facts("short answers")), ["Prefers short answers"])
            self.assertNotIn("short", m.shared_block())

    def test_every_agent_gets_the_shared_block(self):
        import agents
        om = SimpleNamespace(shared_block=lambda: "[SHARED TEAM MEMORY ABOUT THE COMMANDER — x: likes tea]\n")
        with mock.patch.dict(agents.COMMANDER, {"operator": om, "memory": None}):
            team = agents.build_team(hub=mock.Mock(), brain=mock.Mock())
            for name in ("jarvis", "doom", "widow"):
                self.assertIn("likes tea", team[name]._commander_context("hello"))


class VoiceSettingIntents(unittest.TestCase):
    def test_mute_and_unmute_all_agents(self):
        c = ctx()
        self.assertEqual(run(intents.match(c, "mute all agents"))["intent"], "agentvoice")
        self.assertTrue(c["tts"].muted)
        run(intents.match(c, "unmute the agents"))
        self.assertFalse(c["tts"].muted)

    def test_handsfree_switch(self):
        v = SimpleNamespace(set_handsfree=mock.AsyncMock(return_value={"ok": True}))
        c = ctx(voice=v)
        run(intents.match(c, "turn off hands free"))
        v.set_handsfree.assert_awaited_with(enabled=False)
        run(intents.match(c, "hands-free on"))
        v.set_handsfree.assert_awaited_with(enabled=True)


class DashboardControl(unittest.TestCase):
    def ui(self, q):
        r = run(intents.match(ctx(), q))
        return (r or {}).get("ui", [{}])[0] if r and r.get("ui") else None

    def test_commands_become_ui_messages(self):
        self.assertEqual(self.ui("switch the map to live satellite"), {"type": "ui", "action": "map-mode", "mode": "sat:live"})
        self.assertEqual(self.ui("change the map to 3d globe"), {"type": "ui", "action": "map-mode", "mode": "3d"})
        self.assertEqual(self.ui("switch the map to dark")["mode"], "sat:dark")
        self.assertEqual(self.ui("show me my location")["action"], "map-locate")
        self.assertEqual(self.ui("show Tokyo on the map"), {"type": "ui", "action": "map-fly", "q": "tokyo"})
        self.assertEqual(self.ui("switch theme to doom"), {"type": "ui", "action": "theme", "theme": "doom"})
        self.assertEqual(self.ui("make the camera full screen")["action"], "camfs")
        self.assertEqual(self.ui("scroll down")["to"], "down")

    def test_ordinary_questions_are_not_hijacked(self):
        for q in ("what is the cpu load", "tell me about the satellite industry", "fly me to the moon lyrics"):
            r = run(intents.match(ctx(), q))
            self.assertNotEqual((r or {}).get("intent"), "ui", q)


class ComputerUseByVoice(unittest.TestCase):
    def test_natural_phrasings_queue_a_confirmation(self):
        eng = mock.Mock()
        for q in ("On my computer, open notepad and type hello", "open excel and start a budget sheet on my laptop for me"):
            c = ctx(computeruse=eng)
            with mock.patch.object(intents, "_current_cmd", return_value=SimpleNamespace(host=True, source="voice")):
                r = run(intents.match(c, q))
            self.assertEqual(r["intent"], "computeruse", q)
            self.assertIn("Say confirm", r["say"])                    # safety: still a spoken confirm
            self.assertIn("computeruse_chat_pending", c)


class Screen(unittest.TestCase):
    def test_screen_report_routes_panel_questions(self):
        import dashboard_context as dc
        st = {}
        dc.set_screen(st, {"panels": [{"title": "WORLD CLOCKS", "visible": True, "text": "NYC 07:40 TOK 21:40"},
                                      {"title": "VISION DIGEST", "visible": False, "text": "operator at desk"}],
                           "map": {"view": "sat", "sat_mode": "live"}, "theme": "doom"})
        self.assertEqual([p["title"] for p in dc.screen_match(st, "what does the world clocks panel say?")], ["WORLD CLOCKS"])
        out = run(dc.r_screen(st, "what does the world clocks panel say?", True))
        self.assertIn("TOK 21:40", out["panel_text"]["WORLD CLOCKS"])
        self.assertEqual(out["map"]["sat_mode"], "live")
        self.assertIn("screen", dc.route("what map view is on my screen?"))
        with mock.patch.object(dc, "read_sections", mock.AsyncMock(return_value=[])) as rs:
            run(dc.context_for(st, "what does world clocks show", commander=True))
        self.assertIn("screen", rs.await_args.args[1])

    def test_stale_screen_is_unavailable(self):
        import dashboard_context as dc
        st = {"dashboard_screen": {"ts": time.time() - dc.SCREEN_TTL - 5, "panels": []}}
        with self.assertRaises(dc.Unavailable):
            run(dc.r_screen(st, "anything", True))

    def test_secrets_are_scrubbed(self):
        import dashboard_context as dc
        st = {}
        dc.set_screen(st, {"panels": [{"title": "KEYS", "visible": True, "text": "token sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"}]})
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", json.dumps(st["dashboard_screen"]))


class HandsFreeSwitch(unittest.TestCase):
    def setUp(self):
        import voice
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(voice, "HF_FILE", Path(self.tmp.name) / "handsfree.json")
        p.start(); self.addCleanup(p.stop)

    def _loop(self):
        import numpy as np
        from voice import VoiceLoop
        engine = mock.Mock(submit=mock.AsyncMock(), summon=mock.AsyncMock(), active={},
                           state={"tts": mock.Mock(speaking=False, conversation_mode=True)})
        v = VoiceLoop(hub=mock.Mock(broadcast=mock.AsyncMock()), brain=None, team={k: mock.Mock() for k in ("jarvis", "doom")})
        v.engine = engine
        return v, engine, np

    def _hear(self, v, np, text):
        with mock.patch("voice.VoiceLoop._transcribe", return_value=text):
            run(v._finish([np.zeros(800, dtype=np.int16)] * 10, None, np, 8))

    def test_handsfree_off_needs_a_name_but_conversation_stays_on(self):
        import voice
        v, engine, np = self._loop()
        run(v.set_handsfree(enabled=False))
        self.assertFalse(voice._load_handsfree()["enabled"])
        self._hear(v, np, "what's the weather")                 # no name, hands-free off → not answered
        engine.submit.assert_not_awaited()
        self._hear(v, np, "doom, scan my network")               # a name anywhere still works (wake-word mode)
        self.assertEqual(engine.submit.await_args.kwargs["agent"], "doom")
        run(v.set_handsfree(enabled=True))
        self._hear(v, np, "and the router too")
        self.assertEqual(engine.submit.await_count, 2)


class Today(unittest.TestCase):
    def test_daily_mosaic_date_comes_from_gibs(self):
        import globalmap_live as gl
        dom = "<Domains><Domain>2026-11-21/2026-11-27/P1D</Domain></Domains>"

        async def fetch(url, headers):
            return (200, dom.encode()) if "VIIRS_NOAA20" in url else (404, b"")
        m = gl.LiveMap(fetch=fetch, now=lambda: 1795770000.0, key_file=Path(tempfile.mkdtemp()) / "k.json")
        t = run(m.today())
        self.assertTrue(t["ok"])
        self.assertEqual(t["date"], "2026-11-27")
        self.assertIn("/2026-11-27/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg", t["tiles"][0])


if __name__ == "__main__":
    unittest.main()
