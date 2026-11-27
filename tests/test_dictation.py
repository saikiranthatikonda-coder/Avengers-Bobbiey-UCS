"""Voice dictation (v8.5): WAV decoding, agent addressing, the Whisper call (fake model — no
microphone, no model download), the hands-free hold, and the HTTP endpoints."""
import asyncio
import io
import struct
import sys
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np  # noqa: E402

import dictation  # noqa: E402


def wav(seconds=1.0, rate=16000, ch=1, amp=0.3, width=2):
    n = int(seconds * rate)
    t = np.arange(n) / rate
    x = (amp * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    if ch == 2:
        x = np.repeat(x, 2)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(ch); w.setsampwidth(width); w.setframerate(rate)
        w.writeframes(x.tobytes() if width == 2 else bytes(len(x)))
    return b.getvalue()


import agents  # noqa: E402
TEAM = [s[0] for s in agents.AVENGER_SPECS]


class FakeModel:
    def __init__(self, text="open the threat board", lang="hi", prob=0.93):
        self.calls, self.text, self.lang, self.prob = [], text, lang, prob

    def transcribe(self, audio, **kw):
        self.calls.append((audio, kw))
        seg = SimpleNamespace(text=" " + self.text, no_speech_prob=0.01, avg_logprob=-0.2, compression_ratio=1.2)
        return iter([seg]), SimpleNamespace(language=kw.get("language") or self.lang, language_probability=self.prob)


class Decode(unittest.TestCase):
    def test_mono_16k(self):
        a = dictation.decode_wav(wav(1.0))
        self.assertEqual(a.dtype, np.float32)
        self.assertEqual(a.size, 16000)

    def test_stereo_48k_is_downmixed_and_resampled(self):
        a = dictation.decode_wav(wav(1.0, rate=48000, ch=2))
        self.assertAlmostEqual(a.size, 16000, delta=2)

    def test_rejects_bad_input(self):
        for data, msg in ((b"", "no audio"), (b"x" * 100, "not a WAV"), (wav(0.1), "too short")):
            with self.assertRaises(dictation.AudioError) as c:
                dictation.decode_wav(data)
            self.assertIn(msg, str(c.exception))

    def test_rejects_too_long(self):
        with self.assertRaises(dictation.AudioError):
            dictation.decode_wav(wav(dictation.MAX_SECONDS + 5, rate=8000))


class Addressing(unittest.TestCase):
    def test_leading_name_picks_agent_and_keeps_casing(self):
        self.assertEqual(dictation.addressed("Doom, scan my network for open ports.", TEAM),
                         ("doom", "Scan my network for open ports."))
        self.assertEqual(dictation.addressed("hey Jarvis what's on my calendar", TEAM)[0], "jarvis")
        self.assertEqual(dictation.addressed("Captain America, brief me", TEAM)[0], "captain")

    def test_mid_sentence_name_does_not_switch(self):
        self.assertEqual(dictation.addressed("tell me about the vision for doomsday", TEAM),
                         (None, "tell me about the vision for doomsday"))

    def test_agent_must_be_on_the_team(self):
        self.assertEqual(dictation.addressed("Doom, status", ["jarvis"])[0], None)


class Transcribe(unittest.TestCase):
    def run_(self, coro):
        return asyncio.run(coro)

    def test_auto_language_context_and_agent(self):
        m = FakeModel(text="Doom, check for ransomware")
        d = dictation.Dictation({"team": {k: 1 for k in TEAM}}, model_factory=lambda name: m)
        d.model_name = "small"
        r = self.run_(d.transcribe(wav(1.2), lang="auto", context="earlier: Hyderabad office"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "Doom, check for ransomware")
        self.assertEqual((r["agent"], r["agent_text"]), ("doom", "Check for ransomware"))
        self.assertEqual(r["language"], "hi")
        self.assertEqual(r["language_probability"], 0.93)
        kw = m.calls[0][1]
        self.assertIsNone(kw["language"])                          # auto-detect
        self.assertEqual(kw["task"], "transcribe")
        self.assertIn("Hyderabad office", kw["initial_prompt"])    # context carried into the prompt

    def test_explicit_language_and_translate(self):
        m = FakeModel()
        d = dictation.Dictation({}, model_factory=lambda name: m)
        d.model_name = "small"
        r = self.run_(d.transcribe(wav(1.0), lang="te", task="translate"))
        self.assertEqual(m.calls[0][1]["language"], "te")
        self.assertEqual(m.calls[0][1]["task"], "translate")
        self.assertTrue(r["translated"])

    def test_english_only_model_forces_english(self):
        m = FakeModel()
        d = dictation.Dictation({}, model_factory=lambda name: m)
        d.model_name = "base.en"
        self.run_(d.transcribe(wav(1.0), lang="hi", task="translate"))
        self.assertEqual((m.calls[0][1]["language"], m.calls[0][1]["task"]), ("en", "transcribe"))
        self.assertEqual(d.status()["languages"], [{"code": "en", "name": "English"}])

    def test_silence_is_not_sent_to_the_model(self):
        m = FakeModel()
        d = dictation.Dictation({}, model_factory=lambda name: m)
        r = self.run_(d.transcribe(wav(1.0, amp=0.0)))
        self.assertEqual(r["text"], "")
        self.assertIn("no speech", r["note"])
        self.assertEqual(m.calls, [])

    def test_unknown_language_rejected(self):
        d = dictation.Dictation({}, model_factory=lambda name: FakeModel())
        with self.assertRaises(dictation.AudioError):
            self.run_(d.transcribe(wav(1.0), lang="klingon"))

    def test_reuses_the_voice_loops_model(self):
        shared = FakeModel(text="shared model")
        d = dictation.Dictation({"voice": SimpleNamespace(stt=shared)})
        r = self.run_(d.transcribe(wav(1.0)))
        self.assertEqual(r["text"], "shared model")
        self.assertTrue(d.status()["shared_with_voice_loop"])


class Hold(unittest.TestCase):
    def tearDown(self):
        dictation.set_hold(False)

    def test_hold_expires_and_voice_loop_sees_it(self):
        import voice
        now = time.time()
        dictation.set_hold(True, now)
        self.assertTrue(dictation.holding(now + 1))
        self.assertTrue(voice._dictation_holding())
        self.assertFalse(dictation.holding(now + dictation.HOLD_MAX + 1))   # never sticks forever
        dictation.set_hold(False)
        self.assertFalse(voice._dictation_holding())


class Endpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from harness import IsolatedServer
        cls.srv = IsolatedServer(owner=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _j(self, r):
        return r.json() if hasattr(r, "json") else r

    def test_status_lists_languages(self):
        d = self._j(self.srv.get("/api/dictate/status"))
        self.assertTrue(d["ok"])
        self.assertIn("auto", [l["code"] for l in d["languages"]])
        self.assertEqual(d["max_seconds"], dictation.MAX_SECONDS)

    def test_hold_roundtrip(self):
        self.assertTrue(self._j(self.srv.post("/api/dictate/hold", {"on": True}))["hold"])
        self.assertFalse(self._j(self.srv.post("/api/dictate/hold", {"on": False}))["hold"])

    def test_garbage_audio_is_refused_without_touching_the_model(self):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.srv.base + "/api/dictate?lang=auto", data=b"not audio at all" * 4,
                                     headers={"content-type": "audio/wav"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(req, timeout=30)
        self.assertIn(c.exception.code, (400, 503))


if __name__ == "__main__":
    unittest.main()
