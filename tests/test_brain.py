"""Brain provider-chain regression tests (stdlib unittest, no credentials).

Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
Live Claude CLI check (spends a real call):  set UCS_LIVE_AI=1 first.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brain  # noqa: E402
from brain import Brain, resolve_claude_bin, BRAIN_CWD  # noqa: E402
from local_brain import LocalBrain  # noqa: E402


class ResolveClaudeBin(unittest.TestCase):
    def test_path_hit_is_kept(self):
        with mock.patch.object(brain.shutil, "which", return_value="/x/claude"):
            self.assertEqual(resolve_claude_bin("claude"), "claude")

    def test_native_install_location_is_found_off_path(self):
        with tempfile.TemporaryDirectory() as home:
            exe = Path(home) / ".local" / "bin" / "claude.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            with mock.patch.object(brain.shutil, "which", return_value=None), \
                 mock.patch.object(brain.Path, "home", return_value=Path(home)):
                self.assertEqual(resolve_claude_bin("claude"), str(exe))

    def test_unknown_custom_name_is_left_alone(self):
        with mock.patch.object(brain.shutil, "which", return_value=None):
            self.assertEqual(resolve_claude_bin("my-llm"), "my-llm")

    def test_brain_cwd_is_outside_repo(self):
        self.assertNotIn(str(ROOT).lower(), str(BRAIN_CWD.resolve()).lower())


class FallbackChain(unittest.TestCase):
    def test_missing_cli_falls_back_to_templates(self):
        b = Brain(claude_bin="definitely-not-a-real-claude-bin",
                  local_brain=LocalBrain(), local_llm=None)
        self.assertEqual(asyncio.run(b.probe()), "local")
        reply = asyncio.run(b.think("status?", agent="jarvis"))
        self.assertTrue(reply and not reply.startswith("[brain"))

    def test_no_providers_is_offline(self):
        b = Brain(claude_bin="definitely-not-a-real-claude-bin")
        self.assertEqual(asyncio.run(b.probe()), "offline")


class CliArgs(unittest.TestCase):
    """The CLI call must run tool-less (text) or Read-only (vision), outside the repo."""

    def _capture(self, **kw):
        seen = {}

        async def fake_exec(*args, **opts):
            seen["args"], seen["cwd"] = list(args), opts.get("cwd")
            raise FileNotFoundError

        with mock.patch.object(brain.asyncio, "create_subprocess_exec", fake_exec):
            asyncio.run(Brain(claude_bin="claude")._llm_call("hi", None, 5, **kw))
        return seen

    def test_text_call_disables_tools(self):
        s = self._capture()
        i = s["args"].index("--tools")
        self.assertEqual(s["args"][i + 1], "")
        self.assertNotIn("--dangerously-skip-permissions", s["args"])
        self.assertEqual(s["cwd"], str(BRAIN_CWD))

    def test_vision_call_is_read_only(self):
        s = self._capture(allow_read=True)
        i = s["args"].index("--tools")
        self.assertEqual(s["args"][i + 1], "Read")

    def test_prompt_is_not_swallowed_by_tools_list(self):
        s = self._capture()
        self.assertEqual(s["args"][-2:], ["--", "hi"])


class AgentTelemetryContext(unittest.TestCase):
    def test_real_metrics_are_injected(self):
        from agents import Avenger
        sysmon = mock.Mock(latest={"cpu": 12.3, "mem": 45.0, "disk": 60.0,
                                   "net_up": 1.5, "net_down": 2.5})
        a = Avenger(name="stark", codename="STARK", role="r", color="#fff",
                    hub=None, brain=None, local_brain=mock.Mock(sysmon=sysmon))
        line = a._live_telemetry()
        self.assertIn("CPU 12%", line)
        self.assertIn("memory 45%", line)

    def test_no_sample_means_no_numbers(self):
        from agents import Avenger
        a = Avenger(name="stark", codename="STARK", role="r", color="#fff",
                    hub=None, brain=None, local_brain=mock.Mock(sysmon=mock.Mock(latest={})))
        self.assertEqual(a._live_telemetry(), "")


@unittest.skipUnless(os.getenv("UCS_LIVE_AI") == "1", "set UCS_LIVE_AI=1 for the live CLI test")
class LiveClaude(unittest.TestCase):
    def test_live_reply_without_dev_context(self):
        b = Brain()
        self.assertEqual(asyncio.run(b.probe()), "llm", "Claude CLI not reachable")
        reply = asyncio.run(b.think(
            "One line: is any CLAUDE.md project instruction file loaded in your "
            "context? Start your answer with YES or NO.", timeout=90))
        self.assertFalse(reply.startswith("["), reply)
        self.assertTrue(reply.strip().upper().startswith("NO"), reply)


if __name__ == "__main__":
    unittest.main()
