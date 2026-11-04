"""Dashboard sync: a change made in one place reaches every open dashboard (WebSocket
broadcast) and the real settings survive a restart.

Runs against tests/harness.IsolatedServer (temp copy, temp state files), never the
operator's live server or private files.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import IsolatedServer  # noqa: E402

import intents  # noqa: E402
import voice  # noqa: E402

SERVER: IsolatedServer | None = None


def setUpModule():
    global SERVER
    SERVER = IsolatedServer().start()


def tearDownModule():
    if SERVER:
        SERVER.stop()


def _cmd(text):
    return lambda: SERVER.post("/api/command", {"text": text, "source": "text", "speak": False})


class Broadcasts(unittest.TestCase):
    """Every shared-setting change tells the other tabs (same refresh function in app.js)."""

    def sync(self, action, topic):
        got = SERVER.ws_during(action, "sync")
        self.assertTrue(got, f"no sync broadcast for {topic}")
        self.assertEqual(got[0].get("topic"), topic)

    def test_provider_save_and_delete_broadcast_models_updated(self):
        body = {"name": "Sync Test LLM", "base_url": "http://127.0.0.1:9/v1", "model": "m1"}
        self.assertTrue(SERVER.ws_during(lambda: SERVER.post("/api/llm/providers", body), "models-updated"))
        pid = next(p["id"] for p in SERVER.get("/api/llm/providers")["providers"] if p["name"] == "Sync Test LLM")
        self.assertTrue(SERVER.ws_during(lambda: SERVER.post(f"/api/llm/providers/{pid}/delete", {}),
                                         "models-updated"))

    def test_active_brain_reports_a_custom_provider(self):
        # before the fix /api/models said "claude"/"rules" while a custom provider answered
        r = SERVER.post("/api/llm/providers", {"name": "Brain Truth", "base_url": "http://127.0.0.1:9/v1",
                                               "model": "m2"})
        try:
            self.assertTrue(SERVER.post("/api/llm/active", {"id": r["id"]})["ok"])
            self.assertEqual(SERVER.get("/api/models")["active_brain"], "provider:" + r["id"])
        finally:
            SERVER.post("/api/llm/active", {"id": "auto"})
            SERVER.post(f"/api/llm/providers/{r['id']}/delete", {})

    def test_notes(self):
        self.sync(lambda: SERVER.post("/api/notes", {"text": "sync note"}), "notes")

    def test_autonomy(self):
        try:
            self.sync(lambda: SERVER.post("/api/decisions/autonomy", {"on": True}), "decisions")
        finally:
            SERVER.post("/api/decisions/autonomy", {"on": False})

    def test_productivity(self):
        try:
            self.sync(lambda: SERVER.post("/api/productivity", {"action": "focus_start"}), "productivity")
        finally:
            SERVER.post("/api/productivity", {"action": "focus_stop"})

    def test_role(self):
        self.sync(lambda: SERVER.post("/api/role", {"role": "commander"}), "enterprise")

    def test_selfheal_auto(self):
        cur = SERVER.get("/api/health").get("auto", False)
        try:
            self.assertTrue(SERVER.ws_during(lambda: SERVER.post("/api/health/auto", {"on": not cur}), "selfheal"))
        finally:
            SERVER.post("/api/health/auto", {"on": cur})

    def test_typed_brain_switch_broadcasts_and_switches(self):
        try:
            self.assertTrue(SERVER.ws_during(_cmd("Jarvis, switch the brain to ollama"), "models-updated"))
            self.assertEqual(SERVER.get("/api/llm/providers")["active"], "ollama")
        finally:
            SERVER.post("/api/llm/active", {"id": "auto"})


class MicModesRemoved(unittest.TestCase):
    """The mic ON/SLEEP/OFF/TALK feature was removed (owner decision, v8.1.0): the mic always
    listens for its wake words, and nothing in the UI or the API can switch it."""

    def test_no_mic_mode_code_left(self):
        self.assertFalse(hasattr(voice.VoiceLoop, "set_mic_mode"))
        self.assertFalse(hasattr(voice, "mic_command"))
        self.assertFalse(hasattr(intents, "i_micmode"))
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for s in ("mic-modes", "mic-talk"):
            self.assertNotIn(s, html)
        self.assertNotIn("MicMode", js)
        self.assertNotIn("/api/voice/mic", (ROOT / "main.py").read_text(encoding="utf-8"))


class Persistence(unittest.TestCase):
    """Real settings survive a restart (state files in the isolated temp dir)."""

    def test_settings_survive_restart(self):
        SERVER.post("/api/audio", {"muted": True, "volume": 37})
        SERVER.post("/api/conversation", {"on": False})
        SERVER.post("/api/llm/active", {"id": "ollama"})
        SERVER.post("/api/decisions/autonomy", {"on": True})
        SERVER.post("/api/notes", {"text": "persist me"})
        SERVER.restart()
        try:
            a = SERVER.get("/api/audio")
            self.assertEqual((a["muted"], a["volume"]), (True, 37))
            self.assertFalse(SERVER.get("/api/conversation")["on"])
            self.assertEqual(SERVER.get("/api/llm/providers")["active"], "ollama")
            self.assertTrue(SERVER.get("/api/models")["force_local"])
            self.assertTrue(SERVER.get("/api/decisions")["autonomy"])
            self.assertIn("persist me", [n["text"] for n in SERVER.get("/api/notes")["notes"]])
        finally:
            SERVER.post("/api/audio", {"muted": False, "volume": 100})
            SERVER.post("/api/conversation", {"on": True})
            SERVER.post("/api/llm/active", {"id": "auto"})
            SERVER.post("/api/decisions/autonomy", {"on": False})


if __name__ == "__main__":
    unittest.main()
