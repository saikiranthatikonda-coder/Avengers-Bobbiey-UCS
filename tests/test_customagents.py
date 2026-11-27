"""CUSTOM AGENTS (customagents.py) tests — stdlib unittest, no network, no credentials, and
never a real desktop action (the LLM is a stub, Windows/browser actions are patched out,
tools are fakes).

Run:  .venv\\Scripts\\python.exe -m unittest tests.test_customagents -v
"""

import asyncio
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import agentos  # noqa: E402
import customagents  # noqa: E402
import dashboard_context  # noqa: E402
import livefacts  # noqa: E402
import realtime  # noqa: E402
import voice  # noqa: E402
from agentos_providers import Adapter, ModelSpec, result  # noqa: E402
from agentos_tools import Tool  # noqa: E402
from agentos_workflows import Studio  # noqa: E402
from agents import build_team  # noqa: E402
from harness import IsolatedServer  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeHub:
    def __init__(self):
        self.sent = []

    async def broadcast(self, msg):
        self.sent.append(msg)


class StubBrain:
    """Records exactly what the reply path hands the model."""
    mode = "llm"

    def __init__(self):
        self.calls = []

    async def stream(self, prompt, system=None, agent="jarvis", on_delta=None, timeout=90.0):
        self.calls.append({"prompt": prompt, "system": system, "agent": agent})
        if on_delta:
            await on_delta("Stub answer.")
        return "Stub answer.", "claude"


class ScriptedAdapter(Adapter):
    def __init__(self, script):
        self.id, self.name, self.kind, self.local = "fake", "fake", "fake", True
        self.native_tools = True
        self.script = list(script)

    async def discover(self):
        return [ModelSpec("fake", "m", {"chat", "streaming", "tools"}, 8192, 3, 2, 1, True)]

    async def health(self):
        return {"ok": True, "detail": "fake"}

    async def chat(self, spec, messages, *, tools=None, json_mode=False, max_tokens=800, timeout=90,
                   on_delta=None, images=None):
        r = self.script.pop(0) if self.script else {"text": "all done"}
        return result(True, r.get("text", ""), r.get("tool_calls"), ms=1)


async def _no(*a, **k):
    return {}


def make(tmp, role=None, core=None):
    """Registry + real built-in team + real engine, stub LLM, temp store."""
    role = role or {"role": "commander"}
    hub, brain = FakeHub(), StubBrain()
    state = {"hub": hub, "brain": brain, "team": build_team(hub=hub, brain=brain), "rbac": role}
    studio = core.studio if core is not None else Studio(Path(tmp) / "studio", ask=_no, run_tool=_no,
                                                         request_approval=lambda *a, **k: {})
    reg = customagents.Registry(state, {"commander": lambda: role["role"] == "commander"}, studio=studio, core=core)
    state["customagents"] = reg
    reg.sync()
    engine = realtime.Engine(state)
    state["engine"] = engine
    return reg, state, engine, brain


NOVA = {"name": "Nova", "tag": "NOV", "role": "Research desk", "system": "Answer like a pirate named Nova.",
        "wake": ["research desk"], "sections": ["weather"], "notes": "The office wifi is called Hangar-7.",
        "color": "#38bdf8", "icon": "book"}


class Base(unittest.TestCase):
    def setUp(self):
        # never let a test drive the real desktop or the network
        import browser
        import win_actions
        noop = lambda *a, **k: {"ok": True, "say": "stubbed", "opened": False}  # noqa: E731
        self._patches = [mock.patch.object(win_actions, n, noop) for n in
                         ("open_app", "open_folder", "lock_screen", "media_key") if hasattr(win_actions, n)]
        self._patches.append(mock.patch.object(browser, "open_url", noop))
        self._patches.append(mock.patch.object(livefacts, "ground", mock.AsyncMock(return_value="")))
        for p in self._patches:
            p.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)          # registered first → runs last, after DB handles close
        self._triggers = list(voice.CUSTOM_TRIGGERS)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        voice.CUSTOM_TRIGGERS[:] = self._triggers


class Validation(Base):
    def test_create_edit_duplicate_disable_delete(self):
        reg, state, *_ = make(self.tmp.name)
        res = reg.save(NOVA)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["agent"]["id"], "nova")
        self.assertIn("nova", state["team"])
        self.assertTrue(state["team"]["nova"].snapshot()["custom"])
        self.assertEqual(len(state["team"]), 11)          # 10 built-ins (incl. DOOM, BOB) + Nova
        self.assertTrue(reg.save({**NOVA, "id": "nova", "role": "Intel desk"})["ok"])
        self.assertEqual(state["team"]["nova"].role, "Intel desk")
        dup = reg.duplicate("nova")
        self.assertTrue(dup["ok"], dup)
        self.assertFalse(dup["agent"]["enabled"])                       # a copy never joins the team by itself
        self.assertNotIn(dup["agent"]["id"], state["team"])
        self.assertTrue(reg.set_enabled("nova", False)["ok"])
        self.assertNotIn("nova", state["team"])
        self.assertTrue(reg.set_enabled("nova", True)["ok"])
        self.assertTrue(reg.delete("nova")["ok"])
        self.assertNotIn("nova", state["team"])
        self.assertEqual(sorted(state["team"]), sorted(customagents.BUILTIN))

    def test_builtins_cannot_be_deleted_or_replaced(self):
        reg, state, *_ = make(self.tmp.name)
        res = reg.delete("jarvis")
        self.assertFalse(res["ok"])
        self.assertIn("built-in", res["error"])
        self.assertFalse(reg.save({**NOVA, "id": "stark"})["ok"])
        self.assertIn("jarvis", state["team"])

    def test_name_and_wake_phrase_collisions_are_refused_with_a_reason(self):
        reg, *_ = make(self.tmp.name)
        for bad, why in (({"name": "Jarvis"}, "built-in agent JARVIS"), ({"name": "Black Widow"}, "built-in"),
                         ({"name": "Starck"}, "sounds too much like"), ({"name": "Stop"}, "reserved"),
                         ({"name": "Zed", "wake": ["hey tony"]}, "STARK"), ({"name": "Zed", "wake": ["open"]}, "reserved")):
            res = reg.save({"system": "x", **bad})
            self.assertFalse(res["ok"], bad)
            self.assertIn(why, res["error"])
        self.assertTrue(reg.save(NOVA)["ok"])
        self.assertIn("already called", reg.save({"name": "nova"})["error"])
        self.assertIn("already addresses", reg.save({"name": "Zed", "wake": ["Research Desk"]})["error"])

    def test_limits_charset_and_unknown_references(self):
        reg, *_ = make(self.tmp.name)
        L = customagents.LIMITS
        cases = [({"name": "A"}, "name"), ({"name": "<img src=x>"}, "name"), ({"name": "Zed", "tag": "TOOLONG"}, "tag"),
                 ({"name": "Zed", "role": "<script>alert(1)</script>"}, "plain text"),
                 ({"name": "Zed", "system": "x" * (L["system"] + 1)}, "instructions"),
                 ({"name": "Zed", "notes": "x" * (L["notes"] + 1)}, "knowledge notes"),
                 ({"name": "Zed", "wake": ["a"] * 1 + [f"word{i}" for i in range(9)]}, "wake"),
                 ({"name": "Zed", "color": "red"}, "colour"), ({"name": "Zed", "icon": "skull"}, "icon"),
                 ({"name": "Zed", "sections": ["nope"]}, "unknown dashboard section"),
                 ({"name": "Zed", "tools": ["rm_rf"]}, "tool"), ({"name": "Zed", "provider": "gpt-99"}, "provider"),
                 ({"name": "Zed", "voice": "Nobody Voice"}, "not installed")]
        for body, why in cases:
            res = reg.save(body)
            self.assertFalse(res["ok"], body)
            self.assertIn(why, res["error"], body)
        self.assertEqual(reg.records(), [])

    def test_secrets_are_refused_and_export_is_clean(self):
        reg, *_ = make(self.tmp.name)
        for field in ("system", "notes", "description"):
            res = reg.save({"name": "Zed", field: "use key sk-abcdefghijklmnopqrstuvwxyz123456 for it"})
            self.assertFalse(res["ok"])
            self.assertIn("secret", res["error"])
        self.assertTrue(reg.save(NOVA)["ok"])
        exp = reg.export("nova")["agent"]
        self.assertEqual(exp["bucs_agent"], 1)
        self.assertEqual(set(exp), set(customagents.EXPORT_KEYS) | {"bucs_agent"})   # no id, timestamps, status, keys
        blob = json.dumps(exp).lower()
        for needle in ("api_key", "token", "password", "sk-"):
            self.assertNotIn(needle, blob)

    def test_import_is_strict_and_lands_disabled(self):
        reg, state, *_ = make(self.tmp.name)
        self.assertTrue(reg.save(NOVA)["ok"])
        exp = reg.export("nova")["agent"]
        self.assertFalse(reg.import_agent({"name": "X"})["ok"])                       # not an agent file
        self.assertIn("unexpected", reg.import_agent({**exp, "name": "Imp", "enabled": True, "id": "jarvis"})["error"])
        self.assertIn("wrong type", reg.import_agent({**exp, "name": "Imp", "wake": "x"})["error"])
        self.assertIn("already called", reg.import_agent(exp)["error"])                # collision still checked
        self.assertIn("secret", reg.import_agent({**exp, "name": "Imp", "wake": [],
                                                  "system": "token=abcd1234efgh5678"})["error"])
        ok = reg.import_agent({**exp, "name": "Imported One", "wake": [], "provider": "some-other-install"})
        self.assertTrue(ok["ok"], ok)
        self.assertFalse(ok["agent"]["enabled"])
        self.assertEqual(ok["agent"]["provider"], "active")
        self.assertNotIn(ok["agent"]["id"], state["team"])

    def test_store_is_gitignored_and_holds_plain_data(self):
        reg, *_ = make(self.tmp.name)
        reg.save(NOVA)
        stored = json.loads((Path(self.tmp.name) / "studio" / "agents.json").read_text(encoding="utf-8"))
        self.assertEqual(stored[0]["system"], NOVA["system"])                          # data, verbatim, never run
        self.assertIn(".update/", (ROOT / ".gitignore").read_text(encoding="utf-8").split())
        self.assertEqual(customagents.STUDIO_DIR.relative_to(ROOT).parts[0], ".update")


class OneStoreWithAICore(Base):
    def test_ai_core_edit_keeps_the_profile_and_cannot_set_one(self):
        reg, state, *_ = make(self.tmp.name)
        reg.save(NOVA)
        st = reg.studio()
        res = st.upsert_agent({"id": "nova", "name": "Nova", "role": "Edited in AI CORE", "team": {"wake": ["jarvis"]}})
        self.assertTrue(res["ok"])
        self.assertEqual(res["agent"]["team"]["wake"], ["research desk"])              # payload cannot overwrite it
        self.assertEqual(state["team"]["nova"].role, "Edited in AI CORE")              # roster re-synced
        plain = st.upsert_agent({"name": "Plain Runner", "team": {"wake": ["jarvis"]}})
        self.assertNotIn("team", plain["agent"])                                       # …or create one
        self.assertNotIn(plain["agent"]["id"], state["team"])
        self.assertEqual([a["id"] for a in reg.overview()["aicore"]], [plain["agent"]["id"]])
        self.assertTrue(reg.save({"id": plain["agent"]["id"], "name": "Plain Runner", "system": "hi"})["ok"])
        self.assertIn(plain["agent"]["id"], state["team"])                             # promoted: same record

    def test_ai_core_rename_cannot_hijack_a_builtin_or_inject_markup(self):
        reg, state, *_ = make(self.tmp.name)
        reg.save(NOVA)
        reg.studio().upsert_agent({"id": "nova", "name": "Jarvis<b>", "role": "<i>x</i>"})
        m = state["team"]["nova"]
        self.assertNotIn("<", m.codename + m.role)
        self.assertNotIn("jarvis", [p for p, _ in voice.CUSTOM_TRIGGERS])
        self.assertIsNone(reg.resolve("Jarvis, hello"))

    def test_ai_core_delete_takes_the_agent_off_the_team(self):
        reg, state, *_ = make(self.tmp.name)
        reg.save(NOVA)
        self.assertTrue(reg.studio().delete_agent("nova")["ok"])
        self.assertNotIn("nova", state["team"])
        self.assertEqual(voice.CUSTOM_TRIGGERS, [])


class Addressing(Base):
    def test_wake_phrases_follow_the_store(self):
        reg, state, *_ = make(self.tmp.name)
        reg.save(NOVA)
        self.assertEqual(sorted(voice.CUSTOM_TRIGGERS), [("nova", "nova"), ("research desk", "nova")])
        self.assertEqual(voice.VoiceLoop._match_agent("Hey Nova, what's the weather"), ("nova", "what s the weather"))
        self.assertEqual(voice.VoiceLoop._match_agent("research desk any news")[0], "nova")
        self.assertEqual(voice.VoiceLoop._match_agent("hey jarvis tell me about nova")[0], "jarvis")
        self.assertEqual(voice.VoiceLoop._match_agent("an innovation day")[0], None)   # whole words only
        reg.set_enabled("nova", False)
        self.assertEqual(voice.CUSTOM_TRIGGERS, [])
        self.assertEqual(voice.VoiceLoop._match_agent("hey nova hello")[0], None)
        reg.set_enabled("nova", True)
        reg.delete("nova")
        self.assertEqual(voice.CUSTOM_TRIGGERS, [])
        self.assertIsNone(reg.resolve("Nova, hello"))

    def test_typed_addressing_needs_the_name_up_front(self):
        reg, *_ = make(self.tmp.name)
        reg.save(NOVA)
        self.assertEqual(reg.resolve("Nova, Check The Feed"), ("nova", "Check The Feed"))
        self.assertEqual(reg.resolve("hey nova check the feed"), ("nova", "check the feed"))
        self.assertEqual(reg.resolve("@nova hi"), ("nova", "hi"))
        self.assertEqual(reg.resolve("Research desk: status?"), ("nova", "status?"))
        self.assertIsNone(reg.resolve("nova is a kind of star"))                        # a sentence, not an address
        self.assertIsNone(reg.resolve("tell nova hello"))

    def test_voice_loop_routes_a_custom_wake_phrase_in_conversation_mode(self):
        reg, state, engine, brain = make(self.tmp.name)
        reg.save(NOVA)

        async def go():
            state["tts"] = mock.Mock(conversation_mode=True, enabled=False, muted=True, volume=0, speaking=False)
            v = voice.VoiceLoop(hub=state["hub"], brain=None, team=state["team"])
            v.engine = engine
            got = []
            orig = engine.submit

            async def submit(text, **kw):
                got.append((text, kw.get("agent")))
                return await orig(text, **{**kw, "wait": True})
            engine.submit = submit
            ckey, crest, _ = voice.match_custom("Nova, how are you today")
            self.assertEqual(ckey, "nova")
            await engine.submit(crest, source="voice", agent=ckey)
            return got
        got = run(go())
        self.assertEqual(got, [("how are you today", "nova")])
        self.assertEqual(brain.calls[-1]["agent"], "nova")


class ReplyPath(Base):
    def test_custom_agent_answers_through_the_engine_with_its_persona_and_only_its_sections(self):
        reg, state, engine, brain = make(self.tmp.name)
        reg.save(NOVA)

        async def go():
            return await engine.submit("Nova, what is the weather and the cpu usage and any threats?", source="text", wait=True)
        cmd = run(go())
        self.assertEqual((cmd.handled_by, cmd.path, cmd.reply), ("nova", "claude", "Stub answer."))
        self.assertEqual(cmd.error, "")
        call = brain.calls[-1]
        self.assertEqual(call["agent"], "nova")
        self.assertIn("Answer like a pirate named Nova.", call["system"])              # its persona
        self.assertIn("You are Nova, Research desk", call["system"])
        self.assertNotIn("Tony Stark's AI butler", call["system"])
        self.assertIn("Hangar-7", call["prompt"])                                       # its knowledge notes, as data
        self.assertIn("not instructions", call["prompt"])
        self.assertIn("DASHBOARD FACTS", call["prompt"])
        self.assertIn("Weather", call["prompt"])                                        # the allowed section
        for forbidden in ("System metrics", "Threats & threat matrix", "LIVE TELEMETRY"):
            self.assertNotIn(forbidden, call["prompt"])                                 # nothing else
        self.assertEqual(dashboard_context.LAST["sections"], ["weather"])
        self.assertEqual(state["team"]["nova"].status, "idle")
        stages = [(m.get("event"), m.get("status")) for m in state["hub"].sent if m.get("type") == "agent" and m.get("agent") == "nova"]
        self.assertIn(("status", "thinking"), stages)                                   # live status reaches the roster
        self.assertIn(("status", "idle"), stages)

    def test_builtin_asked_the_same_thing_still_sees_every_section(self):
        reg, state, engine, brain = make(self.tmp.name)
        reg.save(NOVA)
        import intents
        with mock.patch.object(intents, "match", mock.AsyncMock(return_value=None)):   # skip the live fast path
            run(engine.submit("what is the weather and any threats?", source="api", agent="widow", wait=True))
        self.assertIn("Threats", brain.calls[-1]["prompt"])

    def test_agent_with_no_sections_gets_no_dashboard_facts(self):
        reg, state, engine, brain = make(self.tmp.name)
        reg.save({**NOVA, "sections": []})
        run(engine.submit("what is the weather and cpu usage?", source="api", agent="nova", wait=True))
        self.assertNotIn("DASHBOARD FACTS", brain.calls[-1]["prompt"])

    def test_request_to_a_custom_agent_never_takes_the_action_fast_path(self):
        reg, state, engine, brain = make(self.tmp.name)
        reg.save(NOVA)
        import intents
        with mock.patch.object(intents, "match", mock.AsyncMock(return_value={"say": "opened", "intent": "windows"})) as m:
            cmd = run(engine.submit("Nova, open notepad", source="text", wait=True))
            self.assertEqual(m.await_count, 0)                                          # intents never consulted
            self.assertEqual((cmd.handled_by, cmd.path), ("nova", "claude"))
            cmd2 = run(engine.submit("open notepad", source="text", wait=True))        # the operator's own command still is
            self.assertEqual(m.await_count, 1)
            self.assertEqual(cmd2.path, "fast")

    def test_deleted_agent_is_no_longer_addressable(self):
        reg, state, engine, brain = make(self.tmp.name)
        reg.save(NOVA)
        reg.delete("nova")
        cmd = run(engine.submit("Nova, hello there friend", source="text", wait=True))
        self.assertNotEqual(cmd.handled_by, "nova")
        self.assertNotEqual(brain.calls[-1]["agent"], "nova")

    def test_unavailable_provider_falls_back_to_the_active_brain_and_says_so(self):
        reg, state, engine, brain = make(self.tmp.name)
        preg = mock.Mock()
        preg.list.return_value = [{"id": "auto", "name": "Auto"}, {"id": "ollama", "name": "Ollama (local)", "locked": False}]
        preg.get.return_value = {"id": "ollama", "locked": False}
        preg.client.return_value = mock.Mock(available=False)
        state["providers"] = preg
        self.assertTrue(reg.save({**NOVA, "provider": "ollama"})["ok"])
        cmd = run(engine.submit("hello", source="api", agent="nova", wait=True))
        self.assertEqual(cmd.path, "claude")
        self.assertTrue(state["team"]["nova"].last["fallback"])

    def test_persona_override_and_reset_for_a_builtin(self):
        reg, state, engine, brain = make(self.tmp.name)
        default = customagents.BUILTIN["thor"][4]
        self.assertTrue(reg.set_override("thor", "Speak only in haiku.")["ok"])
        self.assertEqual(state["team"]["thor"].system_prompt, "Speak only in haiku.")
        reg2 = customagents.Registry(state, {}, studio=reg.studio())                    # persisted
        self.assertEqual(reg2.load_overrides(), {"thor": "Speak only in haiku."})
        self.assertFalse(reg.set_override("nobody", "x")["ok"])
        self.assertTrue(reg.set_override("thor", None)["ok"])
        self.assertEqual(state["team"]["thor"].system_prompt, default)


class ToolTiers(Base):
    def _core(self, script):
        core = agentos.AICore({}, {}, data_dir=Path(self.tmp.name) / "core", adapters=[ScriptedAdapter(script)],
                              plugins_dir=Path(self.tmp.name) / "plugins")
        run(core.hub.discover())
        core.booted = True              # what AICore.boot() sets; agents only use tools on a started core
        self.ran = []
        for name, tier in (("ca_peek", "read"), ("ca_act", "action"), ("ca_wipe", "destructive"), ("ca_other", "read")):
            core.register_tool(Tool(name, f"fake {tier} tool", {"type": "object", "properties": {}}, tier,
                                    lambda args, tctx, n=name: self.ran.append(n) or {"done": n}, source="test"))
        self.addCleanup(core.memory.close)
        return core

    def _ask(self, role, script, tools):
        core = self._core(script)
        rb = {"role": role}
        reg, state, engine, brain = make(self.tmp.name, role=rb, core=core)
        rb["role"] = "commander"                                    # the commander builds the agent…
        self.assertTrue(reg.save({**NOVA, "tools": tools})["ok"])
        rb["role"] = role                                           # …the session role at CALL time decides
        cmd = run(engine.submit("do the thing", source="api", agent="nova", wait=True))
        return core, state["team"]["nova"], cmd

    @staticmethod
    def _call(name):
        return {"tool_calls": [{"id": "c1", "name": name, "arguments": {}}]}

    def test_read_tool_runs_and_the_answer_comes_through_the_tool_loop(self):
        core, nova, cmd = self._ask("commander", [self._call("ca_peek"), {"text": "Peeked."}], ["ca_peek"])
        self.assertEqual(self.ran, ["ca_peek"])
        self.assertEqual(cmd.reply, "Peeked.")
        self.assertTrue(cmd.path.startswith("tools:"))

    def test_action_tool_is_refused_for_an_observer_session(self):
        core, nova, cmd = self._ask("observer", [self._call("ca_act"), {"text": "Could not."}], ["ca_act"])
        self.assertEqual(self.ran, [])
        self.assertIn("commander role required", nova.last["steps"][0]["error"])

    def test_action_tool_runs_for_the_commander(self):
        self._ask("commander", [self._call("ca_act"), {"text": "Done."}], ["ca_act"])
        self.assertEqual(self.ran, ["ca_act"])

    def test_destructive_tool_is_parked_for_per_call_approval_never_run(self):
        core, nova, cmd = self._ask("commander", [self._call("ca_wipe"), {"text": "Waiting for approval."}], ["ca_wipe"])
        self.assertEqual(self.ran, [])
        self.assertEqual([a["tool"] for a in core.tools.pending()], ["ca_wipe"])
        self.assertEqual(len(nova.last["approvals"]), 1)

    def test_tool_outside_the_agents_allow_list_is_refused(self):
        core, nova, cmd = self._ask("commander", [self._call("ca_other"), {"text": "No."}], ["ca_peek"])
        self.assertEqual(self.ran, [])


class Http(unittest.TestCase):
    """The real app in an isolated temp dir (no Claude, no local LLM → rule templates answer)."""

    @classmethod
    def setUpClass(cls):
        cls.s = IsolatedServer().start()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def _names(self):
        return [a["name"] for a in self.s.get("/api/status")["agents"]]

    def test_1_overview_lists_real_registries_and_templates(self):
        d = self.s.get("/api/customagents")
        self.assertTrue(d["commander"])
        self.assertEqual([b["key"] for b in d["builtins"]], list(customagents.BUILTIN))
        m = d["meta"]
        self.assertIn("weather", [x["section"] for x in m["sections"]])
        self.assertEqual(m["providers"][0]["id"], "active")
        self.assertEqual(len(m["templates"]), 5)
        tools = {t["name"]: t["tier"] for t in m["tools"]}
        self.assertEqual(tools.get("computer_use_task"), "destructive")
        self.assertNotIn("api_key", json.dumps(d))
        html = self.s.get("/")
        for ref in ('id="customagents-mount"', "/static/customagents.js?v=", "/static/customagents.css?v="):
            self.assertIn(ref, html)

    def test_2_create_joins_the_team_broadcasts_and_is_addressable(self):
        got = self.s.ws_during(lambda: self.s.post("/api/customagents", NOVA), "sync")
        self.assertEqual(got[0]["topic"], "customagents")
        self.assertIn("nova", self._names())
        snap = next(a for a in self.s.get("/api/status")["agents"] if a["name"] == "nova")
        self.assertEqual((snap["custom"], snap["codename"], snap["tag"]), (True, "NOVA", "NOV"))
        self.assertTrue(self.s.post("/api/command", {"text": "hello", "agent": "nova"})["ok"])
        r = self.s.post("/api/ask", {"prompt": "Nova, introduce yourself please"})       # typed "<name>, …"
        self.assertEqual(r["agent"], "nova")
        self.assertTrue(r["reply"])
        t = self.s.post("/api/customagents/nova/test", {"prompt": "how is the weather?"})
        self.assertTrue(t["ok"], t)
        self.assertEqual(t["agent"], "nova")
        self.assertTrue(t["reply"] and t["path"])
        self.assertIsInstance(t["ms"], int)
        self.assertEqual(t["sections"], ["weather"])
        audit = json.dumps(self.s.get("/api/audit")) if self._has("/api/audit") else "customagents.create"
        self.assertIn("customagents.create", audit)

    def _has(self, path):
        try:
            self.s.get(path)
            return True
        except Exception:
            return False

    def test_3_observer_can_view_but_not_change(self):
        self.s.post("/api/role", {"role": "observer"})
        try:
            self.assertFalse(self.s.get("/api/customagents")["commander"])
            self.assertTrue(self.s.get("/api/customagents/nova/export")["ok"])
            for path, body in (("/api/customagents", {"name": "Sneaky"}), ("/api/customagents/nova/delete", {}),
                               ("/api/customagents/nova/enable", {"on": False}), ("/api/customagents/nova/duplicate", {}),
                               ("/api/customagents/import", {"agent": {"bucs_agent": 1, "name": "Sneaky"}}),
                               ("/api/customagents/builtin/thor", {"persona": "x"}),
                               ("/api/customagents/nova/test", {"prompt": "hi"})):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    self.s.post(path, body)
                self.assertEqual(cm.exception.code, 403, path)
        finally:
            self.s.post("/api/role", {"role": "commander"})
        self.assertEqual([a["id"] for a in self.s.get("/api/customagents")["agents"]], ["nova"])

    def test_4_persists_across_restart(self):
        self.s.post("/api/customagents/builtin/thor", {"persona": "Speak only in haiku."})
        self.s.restart()
        d = self.s.get("/api/customagents")
        self.assertEqual([(a["id"], a["wake"], a["sections"], a["enabled"]) for a in d["agents"]],
                         [("nova", ["research desk"], ["weather"], True)])
        self.assertTrue(next(b for b in d["builtins"] if b["key"] == "thor")["overridden"])
        for _ in range(40):                                          # start() syncs right after boot
            if "nova" in self._names():
                break
            import time
            time.sleep(0.25)
        self.assertIn("nova", self._names())
        self.assertEqual(self.s.post("/api/ask", {"prompt": "Research desk: are you there?"})["agent"], "nova")
        self.assertTrue(self.s.post("/api/customagents/builtin/thor", {"reset": True})["ok"])

    def test_5_validation_over_http(self):
        self.assertIn("built-in agent", self.s.post("/api/customagents", {"name": "Hulk"})["error"])
        self.assertIn("already called", self.s.post("/api/customagents", {"name": "NOVA"})["error"])
        self.assertIn("built-in", self.s.post("/api/customagents/jarvis/delete", {})["error"])
        self.assertIn("jarvis", self._names())

    def test_6_disable_then_delete_removes_it_everywhere(self):
        self.assertTrue(self.s.post("/api/customagents/nova/enable", {"on": False})["ok"])
        self.assertNotIn("nova", self._names())
        self.assertFalse(self.s.post("/api/command", {"text": "hello", "agent": "nova"})["ok"])
        self.assertIn("disabled", self.s.post("/api/customagents/nova/test", {"prompt": "hi"})["error"])
        self.assertTrue(self.s.post("/api/customagents/nova/enable", {"on": True})["ok"])
        got = self.s.ws_during(lambda: self.s.post("/api/customagents/nova/delete", {}), "sync")
        self.assertEqual(got[0]["topic"], "customagents")
        self.assertEqual(self.s.get("/api/customagents")["agents"], [])
        self.assertNotIn("nova", self._names())
        self.assertNotEqual(self.s.post("/api/ask", {"prompt": "Nova, are you still there?"})["agent"], "nova")


if __name__ == "__main__":
    unittest.main()
