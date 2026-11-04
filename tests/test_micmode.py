"""Mic modes: ON · SLEEP (only deliberate commands) · OFF, push-to-talk, spoken switches."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import voice  # noqa: E402


def run(c):
    return asyncio.run(c)


class FakeHub:
    def __init__(self):
        self.msgs = []

    async def broadcast(self, m):
        self.msgs.append(m)

    def subscribe(self):
        return asyncio.Queue()

    def unsubscribe(self, q):
        pass


class Commands(unittest.TestCase):
    def test_spoken_mode_switches(self):
        self.assertEqual(voice.mic_command("Jarvis, go to sleep"), "sleep")
        self.assertEqual(voice.mic_command("jarvis wake up"), "on")
        self.assertEqual(voice.mic_command("Jarvis stop listening"), "off")
        self.assertIsNone(voice.mic_command("what's the weather in Hyderabad"))
        self.assertIsNone(voice.mic_command("I slept well"))


@unittest.skipUnless(__import__("importlib").util.find_spec("numpy"), "numpy (optional voice dep) not installed")
class SleepMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(voice, "MIC_PREF", Path(self.tmp.name) / "mic.json")
        self.p.start()
        self.hub = FakeHub()
        self.engine = mock.Mock(submit=mock.AsyncMock(), summon=mock.AsyncMock(), active={},
                                state={"tts": mock.Mock(speaking=False, conversation_mode=True, say=mock.AsyncMock())})
        self.v = voice.VoiceLoop(hub=self.hub, brain=None, team={k: mock.Mock() for k in ("jarvis", "stark")})
        self.v.engine = self.engine

    def tearDown(self):
        self.p.stop(); self.tmp.cleanup()

    def hear(self, text):
        import numpy as np
        with mock.patch("voice.VoiceLoop._transcribe", return_value=text):
            run(self.v._finish([np.zeros(800, dtype=np.int16)] * 10, None, np, 8))

    def test_sleep_ignores_background_talk_and_follow_up_windows(self):
        run(self.v.set_mic_mode("sleep"))
        self.v.follow_until = 10 ** 12                       # a follow-up window must not let chatter in
        self.hear("so then she said the meeting moved to friday")
        self.engine.submit.assert_not_awaited()
        self.assertIn("sleep mode", self.v.diag[-1]["outcome"])

    def test_sleep_accepts_a_named_command(self):
        run(self.v.set_mic_mode("sleep"))
        self.hear("Jarvis, what's the time")
        self.engine.submit.assert_awaited_once()
        self.assertEqual(self.engine.submit.await_args.args[0], "what's the time")

    def test_talk_arms_exactly_one_command(self):
        run(self.v.set_mic_mode("sleep"))
        run(self.v.arm())
        self.hear("open the weather panel")
        self.assertEqual(self.engine.submit.await_count, 1)
        self.v.follow_until = 0
        self.hear("and something else unaddressed")
        self.assertEqual(self.engine.submit.await_count, 1)  # the arm was used up

    def test_bare_wake_word_arms_the_next_utterance(self):
        run(self.v.set_mic_mode("sleep"))
        self.hear("Jarvis")
        self.engine.summon.assert_awaited()
        self.hear("check my calendar")
        self.engine.submit.assert_awaited_once()

    def test_spoken_sleep_and_wake_persist(self):
        self.hear("Jarvis, go to sleep")
        self.assertEqual(self.v.mic_mode, "sleep")
        self.assertEqual(json.loads(voice.MIC_PREF.read_text())["mode"], "sleep")
        self.hear("Jarvis wake up")
        self.assertEqual(self.v.mic_mode, "on")
        self.engine.submit.assert_not_awaited()                # mode switches are not sent to the LLM
        self.assertTrue(any(m.get("type") == "micmode" and m.get("mode") == "on" and m.get("source") == "voice" for m in self.hub.msgs))

    def test_talk_works_without_conversation_mode(self):
        self.engine.state["tts"].conversation_mode = False         # wake-word mode
        run(self.v.arm())
        self.hear("open the weather panel")
        self.engine.submit.assert_awaited_once()
        self.assertEqual(self.engine.submit.await_args.args[0], "open the weather panel")

    def test_off_closes_the_device_and_on_reopens_it(self):
        import threading
        import types
        import numpy as np
        events = []

        class FakeStream:
            def __init__(self, callback=None, **kw):
                self.cb, self.stop_ev = callback, threading.Event()

            def __enter__(self):
                events.append("open")
                def feed():
                    while not self.stop_ev.wait(0.01):
                        self.cb(np.zeros((800, 1), dtype=np.int16), 800, None, None)
                threading.Thread(target=feed, daemon=True).start()
                return self

            def __exit__(self, *a):
                self.stop_ev.set(); events.append("close")

        fake_sd = types.SimpleNamespace(InputStream=FakeStream)
        fake_fw = types.SimpleNamespace(WhisperModel=lambda *a, **k: object())

        async def scenario():
            with mock.patch.dict(sys.modules, {"sounddevice": fake_sd, "faster_whisper": fake_fw}):
                task = asyncio.create_task(self.v.run())
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    if events == ["open"]:
                        break
                self.assertEqual(self.v.status, "listening")
                await self.v.set_mic_mode("off")
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    if events == ["open", "close"]:
                        break
                self.assertEqual(self.v.status, "mic off")
                await self.v.set_mic_mode("sleep")
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    if len(events) == 3:
                        break
                task.cancel()
            self.assertEqual(events[:3], ["open", "close", "open"])   # OFF released the device; SLEEP reopened it
            self.assertIn({"type": "mic", "off": True, "peak": 0, "rms": 0}, self.hub.msgs)
        run(scenario())

    def test_bad_mode_is_refused(self):
        with self.assertRaises(ValueError):
            run(self.v.set_mic_mode("loud"))


if __name__ == "__main__":
    unittest.main()
