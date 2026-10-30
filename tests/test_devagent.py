"""Engineering mode + Google setup tests — throwaway git repo, fake CLI, no network."""

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import devagent  # noqa: E402
import intents  # noqa: E402
from google_sync import validate_client_config  # noqa: E402


def git(root, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root,
                          capture_output=True, text=True).stdout


class FakeHub:
    def __init__(self):
        self.msgs = []

    async def broadcast(self, m):
        self.msgs.append(m)


class DevAgentFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "app.py").write_text("x = 1\n")
        (self.root / ".gitignore").write_text(".env\n")
        (self.root / ".env").write_text("SECRET=keep-me\n")
        (self.root / "dirty.py").write_text("old\n")
        git(self.root, "init", "-q"); git(self.root, "add", "."); git(self.root, "commit", "-qm", "init")
        (self.root / "dirty.py").write_text("operator's uncommitted work\n")   # pre-existing change
        self.p = mock.patch.object(devagent, "ROOT", self.root); self.p.start()
        self.hub = FakeHub()
        self.state = {"hub": self.hub, "rbac": {"role": "commander"}}
        self.dev = devagent.DevAgent(self.state)

    def tearDown(self):
        self.p.stop(); self.tmp.cleanup()

    def run_(self, c):
        return asyncio.run(c)

    def _diagnose(self, plan):
        with mock.patch.object(self.dev, "_run_cli", mock.AsyncMock(return_value=plan)), \
             mock.patch.object(self.dev, "_live_state", mock.AsyncMock(return_value="ok")):
            return self.run_(self.dev.diagnose("the digest is empty, fix it"))

    def test_diagnosis_never_edits_and_needs_approval(self):
        r = self._diagnose("SUMMARY: digest never refreshes.\nCAUSE: x\nPROPOSED FIX: schedule it\nRISK: low")
        self.assertTrue(r["fixable"])
        self.assertEqual(self.dev.job["stage"], "proposed")
        self.assertNotIn("app.py", git(self.root, "status", "--porcelain"))
        self.assertFalse(self.run_(self.dev.undo())["ok"])                    # nothing applied yet

    def test_apply_undo_reverts_only_its_own_changes_and_restores_secrets(self):
        self._diagnose("SUMMARY: s\nPROPOSED FIX: change app.py\nRISK: low")

        async def fake_cli(prompt, tools, apply, timeout):
            assert "Edit" in tools and apply and "Bash" not in tools                # never a shell
            (self.root / "app.py").write_text("x = 2\n")
            (self.root / "new_helper.py").write_text("y = 1\n")
            (self.root / ".env").write_text("SECRET=stolen\n")                      # must be restored
            return "CHANGED: app.py, new_helper.py\nRESULT: done"
        with mock.patch.object(self.dev, "_run_cli", fake_cli), \
             mock.patch.object(self.dev, "_tests", mock.AsyncMock(return_value={"ok": True, "summary": "OK"})):
            r = self.run_(self.dev.apply())
        self.assertEqual(sorted(r["changed"]), ["app.py", "new_helper.py"])
        self.assertEqual((self.root / ".env").read_text(), "SECRET=keep-me\n")
        self.assertIn(".env", self.dev.job["restored"])
        u = self.run_(self.dev.undo())
        self.assertEqual((self.root / "app.py").read_text().strip(), "x = 1")
        self.assertFalse((self.root / "new_helper.py").exists())
        self.assertEqual((self.root / "dirty.py").read_text(), "operator's uncommitted work\n")  # untouched
        self.assertTrue(u["ok"])

    def test_ship_refused_when_tests_fail(self):
        self._diagnose("SUMMARY: s\nPROPOSED FIX: f\nRISK: low")

        async def fake_cli(*a, **k):
            (self.root / "app.py").write_text("x = 3\n"); return "done"
        with mock.patch.object(self.dev, "_run_cli", fake_cli), \
             mock.patch.object(self.dev, "_tests", mock.AsyncMock(return_value={"ok": False, "summary": "FAILED"})):
            self.run_(self.dev.apply())
        r = self.run_(self.dev.ship())
        self.assertFalse(r["ok"])
        self.assertIn("tests are failing", r["error"])

    def test_observer_cannot_drive_engineering_mode_by_any_route(self):
        self.state["rbac"]["role"] = "observer"
        for r in (self._diagnose("SUMMARY: s\nPROPOSED FIX: f\nRISK: low"),
                  self.run_(self.dev.apply()), self.run_(self.dev.ship()), self.run_(self.dev.undo())):
            self.assertFalse(r["ok"]); self.assertIn("OBSERVER", r["error"])

    def test_a_fix_that_writes_tests_is_reverted_and_never_executed(self):
        self._diagnose("SUMMARY: s\nPROPOSED FIX: change app.py\nRISK: low")
        (self.root / "tests").mkdir()

        async def fake_cli(*a, **k):
            (self.root / "app.py").write_text("x = 9\n")
            (self.root / "tests" / "test_evil.py").write_text("import os; os.system('calc')\n")
            return "done"
        tests_run = mock.AsyncMock(return_value={"ok": True, "summary": "OK"})
        with mock.patch.object(self.dev, "_run_cli", fake_cli), mock.patch.object(self.dev, "_tests", tests_run):
            r = self.run_(self.dev.apply())
        self.assertFalse(r["ok"])
        self.assertIn("tests/", r["error"])
        tests_run.assert_not_called()                                          # nothing it wrote ran
        self.assertFalse((self.root / "tests" / "test_evil.py").exists())
        self.assertEqual((self.root / "app.py").read_text().strip(), "x = 1")  # whole change reverted

    def test_deleted_protected_file_is_restored(self):
        self._diagnose("SUMMARY: s\nPROPOSED FIX: change app.py\nRISK: low")

        async def fake_cli(*a, **k):
            (self.root / ".env").unlink(); (self.root / "app.py").write_text("x = 4\n"); return "done"
        with mock.patch.object(self.dev, "_run_cli", fake_cli), \
             mock.patch.object(self.dev, "_tests", mock.AsyncMock(return_value={"ok": True, "summary": "OK"})):
            self.run_(self.dev.apply())
        self.assertEqual((self.root / ".env").read_text(), "SECRET=keep-me\n")

    def test_none_needed_is_not_fixable(self):
        r = self._diagnose("SUMMARY: works as designed.\nPROPOSED FIX: none needed — config only\nRISK: low")
        self.assertFalse(r["fixable"])


class DevIntents(unittest.TestCase):
    def match(self, q, dev):
        return asyncio.run(intents.match({"dev": dev}, q))

    def test_engineering_requests_route_to_dev(self):
        dev = mock.Mock(job=None)
        for q in ("the vision digest is not showing anything, fix it", "why is the agenda not loading",
                  "add a new panel for crypto prices"):
            self.assertEqual(self.match(q, dev)["intent"], "dev", q)
        for q in ("tell me a joke", "fix my posture", "what time is it"):
            r = self.match(q, dev)
            self.assertNotEqual(r and r["intent"], "dev", q)

    def test_apply_phrase_only_when_a_fix_is_proposed(self):
        self.assertIsNone(self.match("apply the fix", mock.Mock(job=None)))
        r = self.match("apply the fix", mock.Mock(job={"stage": "proposed"}))
        self.assertEqual(r["intent"], "dev")
        self.assertTrue(r["slow"])


class GoogleClientFile(unittest.TestCase):
    def test_validator(self):
        good = {"installed": {"client_id": "1-a.apps.googleusercontent.com", "client_secret": "s",
                              "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                              "token_uri": "https://oauth2.googleapis.com/token"}}
        self.assertIsNotNone(validate_client_config(json.dumps(good))[0])
        self.assertIsNone(validate_client_config(json.dumps({"type": "service_account"}))[0])
        self.assertIsNone(validate_client_config("not json")[0])
        bad = {"installed": {**good["installed"], "client_id": "evil"}}
        self.assertIsNone(validate_client_config(json.dumps(bad))[0])


if __name__ == "__main__":
    unittest.main()
