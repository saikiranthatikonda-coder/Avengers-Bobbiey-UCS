"""Voice routing regression tests (no mic, no model download needed)."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice import VoiceLoop  # noqa: E402


class WakeWords(unittest.TestCase):
    def test_exact_and_fuzzy_matches(self):
        cases = {
            "Hey Stark, what is the current CPU load?": ("stark", "what is the current cpu load"),
            "hey starck what is the cpu": ("stark", "what is the cpu"),
            "Hey Cap, open threat intelligence": ("captain", "open threat intelligence"),
            "hey black widow status": ("widow", "status"),
            "Jarvis": ("jarvis", None),
        }
        for text, want in cases.items():
            self.assertEqual(VoiceLoop._match_agent(text), want, text)

    def test_no_wake_word_is_ignored(self):
        self.assertEqual(VoiceLoop._match_agent("good morning everyone"), (None, None))


class Intents(unittest.TestCase):
    def _loop(self):
        hub = mock.Mock(broadcast=mock.AsyncMock())
        agent = mock.Mock(speaker=None, _emit=mock.AsyncMock())
        return VoiceLoop(hub=hub, brain=None, team={}), hub, agent

    def test_open_section_drives_dashboard(self):
        v, hub, agent = self._loop()
        self.assertTrue(asyncio.run(v._try_intent(agent, "open threat intelligence")))
        hub.broadcast.assert_any_await({"type": "dash-cmd", "target": "threats"})

    def test_vision_request_is_forwarded(self):
        v, hub, agent = self._loop()
        self.assertTrue(asyncio.run(v._try_intent(agent, "what do you see")))
        hub.broadcast.assert_any_await({"type": "vision-request"})

    def test_empty_calendar_is_honest(self):
        v, _, agent = self._loop()
        v.services = {"agenda": mock.Mock(snapshot=lambda: {"events": [], "intel": {}})}
        self.assertTrue(asyncio.run(v._try_intent(agent, "what meetings do i have today")))
        said = agent._emit.await_args.kwargs["a"]
        self.assertIn("clear", said.lower())

    def test_free_form_falls_through_to_llm(self):
        v, _, agent = self._loop()
        self.assertFalse(asyncio.run(v._try_intent(agent, "tell me a joke")))


if __name__ == "__main__":
    unittest.main()
