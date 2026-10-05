"""Real-time engine tests — fake brain/TTS/hub, no network, no credentials."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import intents  # noqa: E402
import win_actions  # noqa: E402
from realtime import Engine, SentenceChunker  # noqa: E402


class FakeHub:
    def __init__(self):
        self.msgs = []

    async def broadcast(self, m):
        self.msgs.append(m)

    def stages(self, cmd_id):
        return [m["stage"] for m in self.msgs if m.get("type") == "cmd" and m.get("id") == cmd_id]


class FakeTTS:
    enabled, muted, volume, speaking = True, False, 100, False

    def __init__(self):
        self.said, self.cancelled = [], 0

    async def say(self, t, on_start=None):
        self.said.append(t)
        if on_start:
            on_start()

    def cancel(self):
        self.cancelled += 1
        return True


class FakeAgent:
    def __init__(self, name, chunks=("Hello there. ", "All systems ", "are nominal."), delay=0.0):
        self.name, self.chunks, self.delay = name, chunks, delay
        self.prompts, self.emitted = [], []

    async def handle_stream(self, prompt, on_delta=None):
        self.prompts.append(prompt)
        for c in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            await on_delta(c)
        return "".join(self.chunks), "claude"

    async def _emit(self, event, **kw):
        self.emitted.append((event, kw))


def make_engine(**agents):
    team = {k: FakeAgent(k) for k in ("jarvis", "stark", "captain", "widow", "hulk", "hawkeye", "thor", "vision")}
    team.update(agents)
    hub, tts = FakeHub(), FakeTTS()
    audit = mock.Mock(log=mock.AsyncMock())
    state = {"hub": hub, "tts": tts, "team": team, "audit": audit, "rbac": {}}
    return Engine(state), hub, tts, team, audit


def run(coro):
    return asyncio.run(coro)


class Chunker(unittest.TestCase):
    def test_first_sentence_goes_out_immediately(self):
        c = SentenceChunker()
        self.assertEqual(c.feed("Yes. All sys"), ["Yes."])                 # first: no minimum wait
        self.assertEqual(c.feed("tems nominal. Ok. Memory is holding steady at 88%. "),
                         ["All systems nominal. Ok.", "Memory is holding steady at 88%."])
        self.assertEqual(c.feed("Next"), [])
        self.assertEqual(c.flush(), ["Next"])

    def test_run_on_text_is_broken_up(self):
        c = SentenceChunker(max_len=40)
        out = c.feed("word " * 20)
        self.assertTrue(out and all(len(s) <= 41 for s in out))


class FastPath(unittest.TestCase):
    def test_time_answered_without_any_agent_or_llm(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            cmd = await e.submit("what time is it", source="voice", wait=True)
            return cmd, hub, tts, team
        cmd, hub, tts, team = run(go())
        self.assertEqual((cmd.path, cmd.intent), ("fast", "time"))
        self.assertIn("It's", cmd.reply)
        self.assertEqual(team["jarvis"].prompts, [])                    # no LLM
        self.assertEqual(tts.said, [cmd.reply])
        self.assertEqual(hub.stages(cmd.id)[:3], ["received", "ack", "routed"])
        self.assertEqual(hub.stages(cmd.id)[-1], "done")
        self.assertIn("total_ms", cmd.marks)

    def test_windows_action_runs_and_is_audited(self):
        async def go():
            e, hub, tts, team, audit = make_engine()
            with mock.patch.object(win_actions, "open_app", return_value={"ok": True, "say": "Opening Notepad."}) as op:
                cmd = await e.submit("open notepad", source="text", wait=True)
            return cmd, op, audit
        cmd, op, audit = run(go())
        op.assert_called_once_with("notepad")
        self.assertEqual(cmd.reply, "Opening Notepad.")
        self.assertEqual(cmd.intent, "windows")
        self.assertTrue(any("action.win.open_app" in str(c) for c in audit.log.await_args_list))

    def test_slow_action_acknowledges_first_then_reports(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            net = mock.Mock(last_latency_ms=12.0)
            net.speed_test = mock.AsyncMock(return_value={"ok": True, "down_mbps": 480.0, "up_mbps": 90.0})
            e.state["net"] = net
            cmd = await e.submit("run a speed test", source="voice", wait=True)
            return cmd, tts, hub
        cmd, tts, hub = run(go())
        self.assertTrue(tts.said[0].startswith("Running a speed test"))  # instant ack spoken first
        self.assertIn("480 megabits down", cmd.reply)
        self.assertIn("progress", hub.stages(cmd.id))


class Reasoning(unittest.TestCase):
    def test_stream_is_spoken_sentence_by_sentence(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            cmd = await e.submit("tell me something reassuring", source="text", wait=True)
            return cmd, hub, tts
        cmd, hub, tts = run(go())
        self.assertEqual(cmd.path, "claude")
        self.assertEqual(tts.said, ["Hello there.", "All systems are nominal."])
        self.assertIn("delta", hub.stages(cmd.id))
        self.assertIn("first_token_ms", cmd.marks)
        self.assertIn("first_speech_ms", cmd.marks)          # reported by the speaker itself

    def test_unaddressed_request_is_delegated_with_handoff(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            cmd = await e.submit("write a script to automate my backups", wait=True)
            return cmd, hub, team
        cmd, hub, team = run(go())
        self.assertEqual(cmd.handled_by, "stark")
        self.assertTrue(team["stark"].prompts and not team["jarvis"].prompts)
        hand = [m for m in hub.msgs if m.get("stage") == "handoff"]
        self.assertEqual((hand[0]["from"], hand[0]["to"]), ("jarvis", "stark"))

    def test_explicit_agent_is_respected(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            return await e.submit("write a script for backups", agent="widow", wait=True), team
        cmd, team = run(go())
        self.assertEqual(cmd.handled_by, "widow")

    def test_context_carries_across_sources(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            await e.submit("my favourite colour is teal", source="voice", wait=True)
            await e.submit("what did I just tell you", source="text", wait=True)
            return team
        team = run(go())
        self.assertIn("my favourite colour is teal", team["jarvis"].prompts[-1])


class Cancellation(unittest.TestCase):
    def test_cancel_stops_task_and_speech(self):
        async def go():
            e, hub, tts, team, _ = make_engine(jarvis=FakeAgent("jarvis", chunks=("a. ",) * 50, delay=0.05))
            cmd = await e.submit("long story please")
            await asyncio.sleep(0.15)
            n = await e.cancel()
            await asyncio.wait_for(cmd.done.wait(), 2)
            return cmd, n, tts, hub
        cmd, n, tts, hub = run(go())
        self.assertGreaterEqual(n, 1)
        self.assertEqual(hub.stages(cmd.id)[-1], "cancelled")
        self.assertEqual(tts.cancelled, 1)

    def test_spoken_stop_cancels_running_command(self):
        async def go():
            e, hub, tts, team, _ = make_engine(jarvis=FakeAgent("jarvis", chunks=("a. ",) * 50, delay=0.05))
            long = await e.submit("long story please")
            await asyncio.sleep(0.1)
            stop = await e.submit("stop", source="voice", wait=True)
            await asyncio.wait_for(long.done.wait(), 2)
            return long, stop, hub
        long, stop, hub = run(go())
        self.assertEqual(stop.intent, "cancel")
        self.assertEqual(hub.stages(long.id)[-1], "cancelled")

    def test_failures_are_reported_not_raised(self):
        class Boom(FakeAgent):
            async def handle_stream(self, prompt, on_delta=None):
                raise RuntimeError("model exploded")
        async def go():
            e, hub, tts, team, _ = make_engine(jarvis=Boom("jarvis"))
            return await e.submit("hello there friend", wait=True), hub
        cmd, hub = run(go())
        self.assertEqual(hub.stages(cmd.id)[-1], "failed")
        self.assertIn("model exploded", cmd.error)


class Supersede(unittest.TestCase):
    def test_new_operator_command_stops_old_speech_only(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            tts.speaking = True                       # still reading an earlier answer
            cmd = await e.submit("what time is it", source="voice", wait=True)
            return cmd, tts
        cmd, tts = run(go())
        self.assertEqual(tts.cancelled, 1)
        self.assertEqual(cmd.stage, "done")

    def test_background_commands_do_not_interrupt_speech(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            tts.speaking = True
            await e.submit("what time is it", source="agent", wait=True)
            return tts
        self.assertEqual(run(go()).cancelled, 0)


class Summon(unittest.TestCase):
    def test_summon_greets_and_is_measured_as_control(self):
        async def go():
            e, hub, tts, team, _ = make_engine()
            return await e.summon("jarvis"), tts, e
        cmd, tts, e = run(go())
        self.assertEqual(tts.said, ["Yes, sir? I'm listening."])
        self.assertEqual(cmd.intent, "summon")
        self.assertEqual(e.snapshot()["totals"]["commands"], 1)


@unittest.skipUnless(__import__("importlib").util.find_spec("numpy"), "numpy (optional voice dep) not installed")
class VoiceOnEngine(unittest.TestCase):
    def _loop(self, active=False, speaking=False):
        import numpy as np
        from voice import VoiceLoop
        hub = FakeHub()
        engine = mock.Mock(submit=mock.AsyncMock(), summon=mock.AsyncMock(),
                           active={"x": 1} if active else {},
                           state={"tts": mock.Mock(speaking=speaking)})
        v = VoiceLoop(hub=hub, brain=None, team={k: mock.Mock() for k in ("jarvis", "stark")})
        v.engine = engine
        return v, engine, np.zeros(800, dtype=np.int16)

    def _hear(self, v, audio, text):
        with mock.patch("voice.VoiceLoop._transcribe", return_value=text):
            import numpy as np
            run(v._finish([audio] * 10, None, np, 8))

    def test_hey_jarvis_lets_engine_delegate(self):
        v, engine, a = self._loop()
        self._hear(v, a, "Hey Jarvis, write a backup script")
        kw = engine.submit.await_args.kwargs
        self.assertEqual((kw["source"], kw["agent"]), ("voice", None))

    def test_hey_stark_addresses_stark(self):
        v, engine, a = self._loop()
        self._hear(v, a, "Hey Stark, check the disk")
        self.assertEqual(engine.submit.await_args.kwargs["agent"], "stark")

    def test_bare_wake_word_opens_conversation(self):
        v, engine, a = self._loop()
        self._hear(v, a, "Hey Jarvis")
        engine.summon.assert_awaited()

    def test_stop_without_wake_word_while_busy(self):
        v, engine, a = self._loop(active=True)
        self._hear(v, a, "stop")
        self.assertEqual(engine.submit.await_args.args[0], "stop")


class IntentRouting(unittest.TestCase):
    def test_route_table(self):
        cases = {"open notepad": "windows", "open youtube": "browser", "volume up": "windows",
                 "open threat intelligence": "dashboard", "what's the cpu load": "system",
                 "lock my computer": "windows"}
        for text, want in cases.items():
            with mock.patch.object(win_actions, "open_app"), mock.patch.object(win_actions, "lock_screen"):
                res = run(intents.match({"sysmon": mock.Mock(latest={"cpu": 5, "mem": 40, "disk": 30})}, text))
            self.assertIsNotNone(res, text)
            self.assertEqual(res["intent"], want, text)

    def test_free_form_reaches_the_llm(self):
        self.assertIsNone(run(intents.match({}, "tell me a joke about robots")))

    def test_cancel_phrases(self):
        for t in ("stop", "never mind", "cancel.", "shut up"):
            self.assertTrue(intents.is_cancel(t), t)
        self.assertFalse(intents.is_cancel("stop the music in five minutes please"))


if __name__ == "__main__":
    unittest.main()
