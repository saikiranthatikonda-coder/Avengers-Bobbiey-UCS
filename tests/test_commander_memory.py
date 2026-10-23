"""Commander Memory tests — synthetic profile (no real personal data), temp files."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import commander_memory as cmm  # noqa: E402

DOC = """TEST COMMANDER MEMORY PROTOCOL
Version: 1.0
PART I — COMMANDER IDENTITY
1. Identity Profile
Preferred designation: Commander Testy
Primary operating region: Springfield
Core Characteristics
The Commander values execution over endless planning.
PART II — PROFESSIONAL INTELLIGENCE
2. Career Background
Associate in innovation programs since 2025.
Education
Master's in Data Science; undergraduate in Commerce.
PART III — PROJECTS
5. TicketDesk — QR-Based Ticketing Platform
Frontend on a static host, Supabase backend, QR ticket submission.
NON-NEGOTIABLE SYSTEM RULES:
* Never reset production data.
* Never casually recreate production tables.
* Back up important data before consequential migrations.
6. Corporate Programs
Healthcare accelerator with four startups; milestone tracking and demo days.
""" + ("Filler paragraph about weekly operations and reporting cadence.\n\n" * 40)


class Sectioning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cm = cmm.CommanderMemory(Path(self.tmp.name) / "commander_memory.md")

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_document_is_kept_not_truncated(self):
        r = self.cm.save(DOC)
        self.assertTrue(r["ok"])
        self.assertEqual(r["chars"], len(DOC.strip()))                 # old limit: 240 chars
        self.assertGreaterEqual(r["sections"], 7)
        for t in ("Identity Profile", "Education", "Corporate Programs"):
            self.assertTrue(any(t in x for x in r["titles"]), t)

    def test_relevant_sections_are_retrieved(self):
        self.cm.save(DOC)
        top = self.cm.search("is it safe to recreate the ticketdesk production tables?", k=2)
        self.assertTrue(any("Never reset production data" in s["text"] for s in top))
        edu = self.cm.search("what did I study", k=1) or self.cm.search("education degree", k=1)
        self.assertIn("Data Science", edu[0]["text"])

    def test_rules_outrank_description(self):
        self.cm.save(DOC)
        top = self.cm.search("can I recreate the ticketdesk supabase tables", k=1)
        self.assertIn("Never reset production data", top[0]["text"])

    def test_context_block_has_core_identity_and_respects_budget(self):
        self.cm.save(DOC)
        ctx = self.cm.context_for("what are my corporate programs?", budget=1500)
        self.assertIn("Commander Testy", ctx)                            # always-on core
        self.assertIn("Healthcare accelerator", ctx)                     # relevant section
        self.assertLess(len(ctx), 1500 + 400)                            # bounded (+ wrapper)

    def test_append_replace_clear_and_limit(self):
        self.cm.save("PART I — A\nalpha")
        self.cm.save("PART II — B\nbeta", mode="append")
        self.assertIn("alpha", self.cm.text); self.assertIn("beta", self.cm.text)
        self.cm.save("PART I — C\ngamma")
        self.assertNotIn("alpha", self.cm.text)
        self.assertFalse(self.cm.save("x" * (cmm.MAX_CHARS + 1))["ok"])
        self.cm.clear()
        self.assertEqual(self.cm.context_for("anything"), "")

    def test_persists_across_restart(self):
        self.cm.save(DOC)
        again = cmm.CommanderMemory(self.cm.path)
        self.assertEqual(again.meta()["sections"], self.cm.meta()["sections"])


class Wiring(unittest.TestCase):
    def test_agents_receive_relevant_profile_context(self):
        import agents
        with tempfile.TemporaryDirectory() as d:
            cm = cmm.CommanderMemory(Path(d) / "c.md"); cm.save(DOC)
            with mock.patch.dict(agents.COMMANDER, {"memory": cm}):
                a = agents.Avenger(name="jarvis", codename="JARVIS", role="r", color="#fff", hub=None, brain=None)
                ctx = a._commander_context("tell me about my ticketdesk project")
        self.assertIn("COMMANDER CONTEXT", ctx)
        self.assertIn("TicketDesk", ctx)

    def test_profile_is_private(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("commander_memory.md", ignore)
        import devagent
        self.assertIn("commander_memory.md", devagent.PROTECTED)


if __name__ == "__main__":
    unittest.main()
