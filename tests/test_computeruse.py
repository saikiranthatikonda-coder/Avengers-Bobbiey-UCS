"""Computer Use (computeruse*.py): action schema, sensitive-action gate (pauses / refuses
secrets), coordinate scaling, pause/stop/takeover state machine, commander + host lock,
adapter selection, Anthropic toolset mapping. No real screen, no real model, no input."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import computeruse  # noqa: E402
import computeruse_core as core  # noqa: E402
import computeruse_models as models  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CARD = "4111 1111 1111 1111"          # the public Visa test number (passes Luhn)


def run(coro):
    return asyncio.run(coro)


# ═══ schema ══════════════════════════════════════════════════════════════════

class SchemaTests(unittest.TestCase):
    def test_click_and_aliases(self):
        a = core.validate_action({"type": "left_click", "coordinate": [10, 20]}, 100, 100)
        self.assertEqual(a, {"type": "click", "x": 10, "y": 20, "modifiers": []})
        a = core.validate_action({"action": "double_click", "x": "5", "y": 6.4}, 100, 100)
        self.assertEqual((a["type"], a["x"], a["y"]), ("double_click", 5, 6))

    def test_out_of_bounds_and_garbage(self):
        for bad in ({"type": "click", "x": 100, "y": 1}, {"type": "click", "x": -1, "y": 1},
                    {"type": "click"}, {"type": "rm -rf"}, "click", {"type": "click", "x": True, "y": 1},
                    {"type": "click", "x": float("nan"), "y": 1}):
            with self.assertRaises(core.ActionError, msg=bad):
                core.validate_action(bad, 100, 100)

    def test_keys(self):
        self.assertEqual(core.parse_keys("Ctrl + S"), ["ctrl", "s"])
        self.assertEqual(core.parse_keys("Return"), ["enter"])
        self.assertEqual(core.parse_keys(["cmd", "space"]), ["win", "space"])
        self.assertEqual(core.parse_keys("ctrl++"), ["ctrl", "+"])
        for bad in ("ctrl+alt+del", "ctrl+alt+backspace", "win+l", "hyperdrive", ""):
            with self.assertRaises(core.ActionError, msg=bad):
                core.parse_keys(bad)

    def test_type_limits(self):
        with self.assertRaises(core.ActionError):
            core.validate_action({"type": "type", "text": "x" * 501}, 10, 10)
        with self.assertRaises(core.ActionError):
            core.validate_action({"type": "type", "text": "a\x1bb"}, 10, 10)
        self.assertEqual(core.validate_action({"type": "type", "text": "hi\n"}, 10, 10)["text"], "hi\n")

    def test_misc_actions(self):
        self.assertEqual(core.validate_action({"type": "scroll", "direction": "down"}, 10, 10)["amount"], 3)
        self.assertEqual(core.validate_action({"type": "wait", "seconds": 999}, 10, 10)["seconds"], 30.0)
        with self.assertRaises(core.ActionError):
            core.validate_action({"type": "open_url", "url": "file:///etc/passwd"}, 10, 10)
        with self.assertRaises(core.ActionError):
            core.validate_action({"type": "open_app", "app": "calc & del *"}, 10, 10)
        d = core.validate_action({"type": "drag", "start_coordinate": [1, 2], "coordinate": [3, 4]}, 10, 10)
        self.assertEqual((d["x"], d["y"], d["x2"], d["y2"]), (1, 2, 3, 4))

    def test_parse_model_reply(self):
        txt = 'Sure!\n```json\n{"reasoning": "the Save button {top}", "action": {"type": "click", "x": 3, "y": 4}}\n```'
        p = core.parse_model_reply(txt, 10, 10)
        self.assertEqual(p["action"]["type"], "click")
        self.assertIn("Save", p["reasoning"])
        p = core.parse_model_reply('{"action": "key", "keys": "ctrl+s", "sensitive": true, "sensitive_reason": "x"}', 10, 10)
        self.assertEqual(p["action"]["keys"], ["ctrl", "s"])
        self.assertTrue(p["sensitive"])
        with self.assertRaises(core.ActionError):
            core.parse_model_reply("I can't see anything", 10, 10)
        with self.assertRaises(core.ActionError):         # zoom is native-only
            core.parse_model_reply('{"action": {"type": "zoom", "region": [0,0,5,5]}}', 10, 10)


# ═══ coordinates ═════════════════════════════════════════════════════════════

class CoordinateTests(unittest.TestCase):
    def test_fit_size(self):
        self.assertEqual(core.fit_size(1920, 1200, 1280), (1280, 800))
        self.assertEqual(core.fit_size(800, 600, 1280), (800, 600))           # never upscales
        w, h = core.fit_size(3840, 2160, 1568)
        self.assertLessEqual(w * h, 1_150_000)

    def test_roundtrip_dpi_and_offsets(self):
        # 4K physical monitor (150 % scaling, DPI-aware → physical px) left of the primary
        f = core.Frame(mon_left=-3840, mon_top=0, mon_w=3840, mon_h=2160, img_w=1280, img_h=720)
        self.assertEqual(f.to_screen(0, 0), (-3840 + 1, 1))
        self.assertEqual(f.to_screen(640, 360), (-3840 + 1921, 1081))
        self.assertEqual(f.to_screen(1279, 719), (-2, 2158))                  # pixel centre, inside
        for x, y in ((0, 0), (100, 200), (1279, 719), (640, 360)):
            self.assertEqual(f.to_frame(*f.to_screen(x, y)), (x, y))

    def test_retina_points(self):
        # macOS: mon rect in points (1440x900), capture 2880x1800, model sees 1280x800
        f = core.Frame(0, 0, 1440, 900, 1280, 800, cap_w=2880, cap_h=1800)
        x, y = f.to_screen(1280 / 2, 400)
        self.assertAlmostEqual(x, 720, delta=1)
        self.assertAlmostEqual(y, 450, delta=1)

    def test_corners(self):
        prim = (0, 0, 1920, 1200)
        self.assertTrue(core.in_corner(0, 0, prim))
        self.assertTrue(core.in_corner(1919, 1199, prim))
        self.assertFalse(core.in_corner(960, 0, prim))
        self.assertEqual(core.avoid_corners(1, 1199, prim), (6, 1193))
        self.assertEqual(core.avoid_corners(500, 500, prim), (500, 500))


# ═══ gate ════════════════════════════════════════════════════════════════════

def gate(action, reasoning="", **kw):
    a = core.validate_action(action, 1000, 1000)
    return core.check_action(a, reasoning, **kw)


class GateTests(unittest.TestCase):
    def test_plain_actions_allowed(self):
        self.assertEqual(gate({"type": "type", "text": "BUCS computer use test"}, "Type the text into Notepad").verdict, core.ALLOW)
        self.assertEqual(gate({"type": "click", "x": 5, "y": 5}, "Click the Notepad text area").verdict, core.ALLOW)
        self.assertEqual(gate({"type": "open_app", "app": "notepad"}, "Open Notepad").verdict, core.ALLOW)
        self.assertEqual(gate({"type": "scroll", "direction": "down"}, "scroll to the delete button").verdict, core.ALLOW)

    def test_payment_pauses(self):
        g = gate({"type": "click", "x": 5, "y": 5}, "Click 'Place your order' to complete checkout")
        self.assertEqual((g.verdict, g.category), (core.APPROVE, "payment"))
        self.assertEqual(gate({"type": "open_url", "url": "https://shop.example/checkout"}).category, "payment")

    def test_password_and_card_refused(self):
        g = gate({"type": "type", "text": "hunter2"}, "Type the password into the password field.")
        self.assertEqual((g.verdict, g.category), (core.REFUSE, "secret"))
        self.assertEqual(gate({"type": "type", "text": CARD}, "fill the form").verdict, core.REFUSE)
        self.assertEqual(gate({"type": "type", "text": "Tr0ub4dor&3"}, "type it").verdict, core.REFUSE)
        self.assertEqual(gate({"type": "type", "text": "sk-" + "a" * 30}).verdict, core.REFUSE)
        self.assertEqual(gate({"type": "type", "text": "123-45-6789"}).verdict, core.REFUSE)
        # an ordinary number that fails Luhn is fine
        self.assertEqual(gate({"type": "type", "text": "Order 1234 5678 9012 3456"}, "note").verdict, core.ALLOW)

    def test_signin_click_pauses(self):
        self.assertEqual(gate({"type": "click", "x": 1, "y": 1}, "Click Sign in").category, "credentials")

    def test_delete_and_move_pause(self):
        self.assertEqual(gate({"type": "click", "x": 1, "y": 1}, "Click Delete to remove the file").category, "delete")
        self.assertEqual(gate({"type": "drag", "x": 1, "y": 1, "x2": 9, "y2": 9},
                              "Move the report file into the Archive folder").category, "delete")
        self.assertEqual(gate({"type": "key", "keys": "shift+delete"}).category, "delete")

    def test_send_pauses(self):
        self.assertEqual(gate({"type": "click", "x": 1, "y": 1}, "Click Send to email Bob").category, "send")
        self.assertEqual(gate({"type": "key", "keys": "enter"}, "Press Enter to post the tweet").category, "send")
        self.assertEqual(gate({"type": "key", "keys": "ctrl+enter"}).verdict, core.APPROVE)

    def test_install_system_irreversible(self):
        self.assertEqual(gate({"type": "click", "x": 1, "y": 1}, "Run the installer setup.exe").category, "install")
        self.assertEqual(gate({"type": "open_app", "app": "Settings"}).category, "system")
        self.assertEqual(gate({"type": "key", "keys": "win+r"}).category, "system")
        g = gate({"type": "click", "x": 1, "y": 1}, "Click Don't Save to close Notepad without saving")
        self.assertEqual((g.verdict, g.category), (core.APPROVE, "irreversible"))

    def test_declared_and_step_mode(self):
        g = gate({"type": "click", "x": 1, "y": 1}, "click it", sensitive=True, sensitive_reason="submits the form")
        self.assertEqual((g.verdict, g.category), (core.APPROVE, "send"))
        self.assertEqual(gate({"type": "click", "x": 1, "y": 1}, "ok", approve_all=True).category, "step")
        self.assertEqual(gate({"type": "wait", "seconds": 1}, "pay later", approve_all=True).verdict, core.ALLOW)

    def test_aadhaar_checksum(self):
        base = "23456789012"
        good = next(base + d for d in "0123456789" if core.verhoeff_ok(base + d))
        spaced = f"{good[:4]} {good[4:8]} {good[8:]}"
        self.assertEqual(gate({"type": "type", "text": spaced}).verdict, core.REFUSE)
        bad = next(base + d for d in "0123456789" if not core.verhoeff_ok(base + d))
        self.assertEqual(gate({"type": "type", "text": bad}).verdict, core.ALLOW)

    def test_luhn(self):
        self.assertTrue(core.luhn_ok("4111111111111111"))
        self.assertFalse(core.luhn_ok("4111111111111112"))


# ═══ state machine & rate limit ═════════════════════════════════════════════

class ControlTests(unittest.TestCase):
    def test_pause_resume_stop(self):
        async def go():
            c = core.TaskControl()
            c.status = "thinking"
            self.assertTrue(c.pause())
            self.assertFalse(c.pause())
            waiter = asyncio.create_task(c.checkpoint())
            await asyncio.sleep(0.02)
            self.assertFalse(waiter.done())             # blocked while paused
            self.assertTrue(c.resume())
            await asyncio.wait_for(waiter, 1)
            self.assertTrue(c.stop("x"))
            with self.assertRaises(core.Stopped):
                await c.checkpoint()
            self.assertFalse(c.resume())
        run(go())

    def test_takeover_handback_and_stop_while_paused(self):
        async def go():
            c = core.TaskControl()
            c.status = "acting"
            self.assertTrue(c.takeover())
            self.assertEqual(c.status, "taken_over")
            w = asyncio.create_task(c.checkpoint())
            await asyncio.sleep(0.02)
            c.stop("emergency")
            with self.assertRaises(core.Stopped):
                await asyncio.wait_for(w, 1)
        run(go())

    def test_approval_flow(self):
        async def go():
            c = core.TaskControl()
            c.status = "thinking"
            t = asyncio.create_task(c.ask("approval", {"action": "click"}, 5))
            await asyncio.sleep(0.01)
            self.assertEqual(c.status, "awaiting_approval")
            self.assertTrue(c.decide("approve"))
            self.assertEqual((await t)["decision"], "approve")
            self.assertEqual(c.status, "thinking")
            t = asyncio.create_task(c.ask("approval", {}, 5))
            await asyncio.sleep(0.01)
            c.decide("takeover")
            self.assertEqual((await t)["decision"], "takeover")
            self.assertEqual(c.status, "taken_over")
            c.resume()
            r = await c.ask("approval", {}, 0.05)          # timeout = deny
            self.assertEqual(r["decision"], "timeout")
            t = asyncio.create_task(c.ask("approval", {}, 5))
            await asyncio.sleep(0.01)
            c.stop("bye")
            with self.assertRaises(core.Stopped):
                await t
        run(go())

    def test_rate_limiter(self):
        now = [100.0]
        rl = core.RateLimiter(0.5, 3, clock=lambda: now[0])
        self.assertEqual(rl.delay(), 0)
        rl.stamps = [100.0]
        self.assertAlmostEqual(rl.delay(), 0.5)
        rl.stamps = [99.0, 99.5, 99.9]
        now[0] = 100.0
        self.assertAlmostEqual(rl.delay(), 59.0)


# ═══ adapters ════════════════════════════════════════════════════════════════

class AdapterTests(unittest.TestCase):
    def state(self, brain_mode="llm", providers=None, cloud=True):
        reg = SimpleNamespace(cloud_allowed=lambda: cloud, list=lambda: providers or [])
        return {"providers": reg, "brain": SimpleNamespace(mode=brain_mode, cloud_locked=False),
                "local_llm": SimpleNamespace(base_url="http://127.0.0.1:11434/v1")}

    def discover(self, state, env=None, ollama=([], "none")):
        async def fake_ollama(_):
            return ollama
        with mock.patch.dict(os.environ, env or {}, clear=False), \
                mock.patch.object(models, "ollama_vision_models", fake_ollama):
            if not env:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            return run(models.discover(state))

    def test_order_and_selection(self):
        provs = [{"id": "gpt", "name": "OpenAI", "model": "gpt-4o-mini", "builtin": False, "locked": False},
                 {"id": "ds", "name": "DeepSeek", "model": "deepseek-chat", "builtin": False, "locked": False}]
        c = self.discover(self.state(providers=provs), {"ANTHROPIC_API_KEY": "x"}, (["qwen2.5vl:7b"], ""))
        ids = [x["id"] for x in c]
        self.assertEqual(ids[0], "anthropic")
        self.assertLess(ids.index("provider:gpt"), ids.index("ollama:qwen2.5vl:7b"))
        self.assertLess(ids.index("ollama:qwen2.5vl:7b"), ids.index("claude-cli"))
        self.assertEqual(models.pick(c)["id"], "anthropic")
        ds = next(x for x in c if x["id"] == "provider:ds")
        self.assertFalse(ds["auto"])                     # text-only model never auto-picked
        self.assertEqual(models.pick(c, "provider:ds")["id"], "provider:ds")   # but selectable

    def test_fallback_to_cli_and_none(self):
        c = self.discover(self.state())
        self.assertEqual(models.pick(c)["id"], "claude-cli")
        c = self.discover(self.state(brain_mode="local"))
        self.assertIsNone(models.pick(c))
        self.assertIsNone(models.pick(c, "anthropic"))
        a = next(x for x in c if x["id"] == "anthropic")
        self.assertIn("ANTHROPIC_API_KEY", a["reason"])

    def test_licence_lock(self):
        c = self.discover(self.state(cloud=False), {"ANTHROPIC_API_KEY": "x"})
        self.assertFalse(next(x for x in c if x["id"] == "anthropic")["available"])

    def test_native_mapping(self):
        f = core.Frame(0, 0, 1920, 1200, 1280, 800)
        self.assertEqual(models.map_native("left_click", {"coordinate": [5, 6], "text": "shift"}, f, None),
                         {"type": "click", "x": 5, "y": 6, "modifiers": ["shift"]})
        self.assertEqual(models.map_native("left_click", {}, f, (7, 8))["x"], 7)
        self.assertEqual(models.map_native("key", {"text": "ctrl+s", "repeat": 2}, f, None)["keys"], ["ctrl", "s"])
        self.assertEqual(models.map_native("scroll", {"scroll_direction": "up", "scroll_amount": 2}, f, None)["amount"], 2)
        self.assertEqual(models.map_native("bucs_done", {"summary": "ok"}, f, None)["type"], "done")
        with self.assertRaises(core.ActionError):
            models.map_native("bash", {}, f, None)

    def test_anthropic_adapter_roundtrip(self):
        calls = []

        class Blk(dict):
            def to_dict(self):
                return dict(self)

        class Fake:
            def __init__(self):
                self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
                self.messages = SimpleNamespace(create=self.create)

            async def create(self, **kw):
                calls.append(kw)
                return SimpleNamespace(stop_reason="tool_use", content=[
                    Blk(type="thinking", thinking="Notepad is open; click the text area"),
                    Blk(type="tool_use", id="t1", name="left_click", toolset_name="computer", input={"coordinate": [10, 10]})])

        a = models.AnthropicAdapter(model="claude-opus-5-5", client=Fake())
        f = core.Frame(0, 0, 1920, 1200, 1280, 800, jpeg=b"\xff\xd8x")
        job = models.Job("do it", 1, 10, "Windows", [], [], [], [])
        st = run(a.plan(job, f))
        self.assertEqual(calls[0]["tools"][0], {"type": "computer_toolset_20260801"})
        self.assertNotIn("betas", {k for k in calls[0] if k == "betas" and "computer" in str(calls[0][k])})
        self.assertEqual(st.actions[0]["native"], "left_click")
        self.assertIn("text area", st.reasoning)
        a.add_results([{"tool_id": "t1", "toolset": "computer", "ok": True, "text": "OK"}], f, [])
        last = a.messages[-1]["content"][0]
        self.assertEqual((last["type"], last["toolset_name"], last["tool_use_id"]), ("tool_result", "computer", "t1"))


# ═══ engine, router, commander lock ══════════════════════════════════════════

class ScriptAdapter(models.Adapter):
    id, name = "script", "script"

    def __init__(self, replies):
        self.replies = list(replies)

    async def plan(self, job, frame):
        if not self.replies:
            return models.Step(final="nothing left")
        return models._json_step(self.replies.pop(0), frame)


def make_engine(tmp, replies, executed):
    st = {"hub": None}
    audit_log = []

    async def audit(a, d=""):
        audit_log.append((a, d))
    ctx = {"state": st, "audit": audit, "commander": lambda: True, "host_only": lambda r: True}
    eng = computeruse.Engine(ctx, Path(tmp))
    eng.settings["min_interval"] = 0.2

    async def cap(step):
        return core.Frame(0, 0, 1920, 1200, 1280, 800, step=step, jpeg=b"x"), None

    async def exe(a, frame):
        executed.append(a)
        return True, "OK", None
    eng.capture_fn, eng.executor = cap, exe
    eng.adapter_factory = lambda ch: ScriptAdapter(replies)

    async def cands(force=False):
        eng.candidates = [{"id": "claude-cli", "name": "fake", "available": True, "auto": True, "kind": "json"}]
        return eng.candidates
    eng.refresh_candidates = cands
    return eng, audit_log


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        computeruse.SETTLE = 0.0
        computeruse.LAUNCH_SETTLE = 0.0

    def test_happy_path_with_approval(self):
        replies = ['{"reasoning":"open it","action":{"type":"open_app","app":"notepad"}}',
                   '{"reasoning":"type","action":{"type":"type","text":"BUCS computer use test"}}',
                   '{"reasoning":"Click Don\'t Save","action":{"type":"click","x":10,"y":10}}',
                   '{"reasoning":"closed","action":{"type":"done","summary":"all good"}}']
        executed = []

        async def go():
            eng, audit = make_engine(self.tmp.name, replies, executed)
            r = await eng.start_task("test")
            self.assertTrue(r["ok"], r)
            for _ in range(200):
                await asyncio.sleep(0.02)
                if eng.ctl.pending:
                    break
            self.assertEqual(eng.status(), "awaiting_approval")
            self.assertEqual(eng.ctl.pending["category"], "irreversible")
            self.assertEqual(len(executed), 2)              # the click waits
            self.assertTrue(eng.decide("approve")["ok"])
            await asyncio.wait_for(eng.runner, 5)
            self.assertEqual(eng.task["status"], "done")
            self.assertEqual(eng.task["summary"], "all good")
            self.assertEqual([a["type"] for a in executed], ["open_app", "type", "click"])
            acts = [a for a, _ in audit]
            self.assertIn("computeruse.approval.approve", acts)
            self.assertEqual(acts.count("computeruse.action"), 3)
            self.assertEqual(eng.history[0]["status"], "done")
        run(go())

    def test_deny_secret_refusal_and_takeover(self):
        replies = ['{"reasoning":"Type the password into the password box","action":{"type":"type","text":"x"}}',
                   '{"reasoning":"Click Pay now","action":{"type":"click","x":1,"y":1}}',
                   '{"action":{"type":"done","summary":"stopped short"}}']
        executed = []

        async def go():
            eng, audit = make_engine(self.tmp.name, replies, executed)
            await eng.start_task("buy a thing")
            for _ in range(200):
                await asyncio.sleep(0.02)
                if eng.ctl.status == "taken_over":
                    break
            self.assertEqual(eng.ctl.status, "taken_over")   # secret → human takes over
            self.assertEqual(executed, [])
            self.assertTrue(eng.control("handback")["ok"])
            for _ in range(200):
                await asyncio.sleep(0.02)
                if eng.ctl.pending:
                    break
            self.assertEqual(eng.ctl.pending["category"], "payment")
            eng.decide("deny")
            await asyncio.wait_for(eng.runner, 5)
            self.assertEqual(executed, [])                  # nothing sensitive ever ran
            self.assertEqual(eng.task["status"], "done")
            self.assertIn("computeruse.refused", [a for a, _ in audit])
            self.assertTrue(any("denied" in s["result"].lower() or "deny" in s["result"] for s in eng.task["steps"]))
        run(go())

    def test_stop_is_immediate_and_pause_blocks(self):
        replies = ['{"action":{"type":"wait","seconds":30}}'] * 5
        executed = []

        async def go():
            eng, _ = make_engine(self.tmp.name, replies, executed)

            async def slow(a, frame):
                executed.append(a)
                await asyncio.sleep(30)
                return True, "OK", None
            eng.executor = slow
            await eng.start_task("wait forever")
            await asyncio.sleep(0.1)
            self.assertEqual(len(executed), 1)
            self.assertTrue(eng.control("stop")["ok"])
            await asyncio.wait_for(asyncio.shield(eng.runner), 2)
            self.assertEqual(eng.task["status"], "stopped")
            # pause before the first action: nothing executes until resume
            executed.clear()
            eng.executor = make_engine(self.tmp.name, [], executed)[0].executor
            eng.adapter_factory = lambda ch: ScriptAdapter(['{"action":{"type":"click","x":1,"y":1}}'])
            await eng.start_task("click once")
            eng.control("pause")
            await asyncio.sleep(0.2)
            self.assertEqual(executed, [])
            eng.control("resume")
            await asyncio.wait_for(eng.runner, 5)
            self.assertEqual(len(executed), 1)
        run(go())

    def test_privacy_guard_blocks_capture(self):
        self.assertEqual(core.private_window(["Untitled - Notepad", ".env - Notepad"]), ".env")
        self.assertEqual(core.private_window(["Bitwarden"]), "Bitwarden")
        self.assertIsNone(core.private_window(["report.env.md - Word", "Settings", "Inbox - Chrome"]))
        titles = [["credentials.json - Notepad"]]
        planned = []

        class Spy(ScriptAdapter):
            async def plan(self, job, frame):
                planned.append(frame.step)
                return await super().plan(job, frame)

        async def go():
            eng, audit = make_engine(self.tmp.name, [], [])
            eng.adapter_factory = lambda ch: Spy(['{"action":{"type":"done","summary":"ok"}}'])

            async def tf():
                return titles[0]
            eng.titles_fn = tf
            await eng.start_task("look")
            for _ in range(100):
                await asyncio.sleep(0.02)
                if eng.ctl.status == "taken_over":
                    break
            self.assertEqual(eng.ctl.status, "taken_over")
            self.assertEqual(planned, [])                   # the frame never reached a model
            self.assertIn("computeruse.privacy_guard", [a for a, _ in audit])
            titles[0] = ["Untitled - Notepad"]
            eng.control("handback")
            await asyncio.wait_for(eng.runner, 5)
            self.assertEqual(eng.task["status"], "done")
            self.assertEqual(len(planned), 1)
        run(go())

    def test_max_steps_and_invalid_replies(self):
        async def go():
            eng, _ = make_engine(self.tmp.name, ['{"action":{"type":"scroll","direction":"down"}}'] * 10, [])
            await eng.start_task("scroll", max_steps=3)
            await asyncio.wait_for(eng.runner, 5)
            self.assertEqual(eng.task["status"], "failed")
            self.assertIn("step limit", eng.task["error"])
            eng.adapter_factory = lambda ch: ScriptAdapter(["nonsense"] * 5)
            await eng.start_task("garbage")
            await asyncio.wait_for(eng.runner, 5)
            self.assertEqual(eng.task["status"], "failed")
            self.assertIn("invalid", eng.task["error"])
        run(go())

    def test_submit_task_contract(self):
        executed = []

        async def go():
            eng, audit = make_engine(self.tmp.name, ['{"reasoning":"Click Send","action":{"type":"click","x":1,"y":1}}',
                                                     '{"action":{"type":"done","summary":"ok"}}'], executed)
            eng.ctx["commander"] = lambda: False
            r = await computeruse.submit_task(eng.ctx | {"state": {"computeruse": eng}}, "send it")
            self.assertEqual((r["ok"], r["status"]), (False, "refused"))
            eng.ctx["commander"] = lambda: True
            r = await eng.submit_task("send it", source="agentos")
            self.assertTrue(r["ok"] and r["task_id"])
            for _ in range(100):
                await asyncio.sleep(0.02)
                if eng.ctl.pending:
                    break
            self.assertEqual(eng.ctl.pending["category"], "send")      # same gate as the dashboard
            self.assertEqual(executed, [])
            eng.decide("deny")
            await asyncio.wait_for(eng.runner, 5)
            self.assertEqual(eng.history[0]["source"], "agentos")
            self.assertIn("computeruse.submit", [a for a, _ in audit])
        run(go())

    def test_router_locks(self):
        st = {"hub": None}
        role = {"commander": False, "host": True}
        ctx = {"state": st, "audit": lambda a, d="": asyncio.sleep(0), "commander": lambda: role["commander"],
               "host_only": lambda r: role["host"]}
        with mock.patch.object(computeruse, "DATA", Path(self.tmp.name)):
            st["computeruse"] = computeruse.Engine(ctx, Path(self.tmp.name))
            app = FastAPI()
            app.include_router(computeruse.build_router(ctx))
            cl = TestClient(app)
            r = cl.post("/api/computeruse/task", json={"task": "open notepad"}).json()
            self.assertIn("OBSERVER", r["error"])
            role["commander"], role["host"] = True, False
            self.assertIn("host machine", cl.post("/api/computeruse/task", json={"task": "x"}).json()["error"])
            self.assertIn("host machine", cl.post("/api/computeruse/approve", json={"decision": "approve"}).json()["error"])
            self.assertEqual(cl.get("/api/computeruse/frame/abcdef-1/1").status_code, 403)
            # STOP is never locked (answers "no task" rather than "locked")
            role["commander"] = False
            self.assertEqual(cl.post("/api/computeruse/control", json={"action": "stop"}).json()["error"], "no task is running")
            self.assertIn("OBSERVER", cl.post("/api/computeruse/control", json={"action": "resume"}).json()["error"])


class SweepTests(unittest.TestCase):
    def test_age_based_sweep(self):
        with tempfile.TemporaryDirectory() as d:
            eng = computeruse.Engine({"state": {}, "audit": None, "commander": lambda: True, "host_only": lambda r: True}, Path(d))
            fresh, old, ended = (eng.frames / n for n in ("fresh-task1", "old-task01", "ended-task"))
            for x in (fresh, old, ended):
                x.mkdir(parents=True)
                (x / "0001.jpg").write_bytes(b"x")
            past = computeruse._now() - computeruse.FRAME_TTL - 5
            for f in (old, old / "0001.jpg"):
                os.utime(f, (past, past))
            eng.ended_at["ended-task"] = past
            self.assertEqual(eng.sweep(), 2)
            self.assertTrue(fresh.is_dir())                # a running task elsewhere is never wiped
            self.assertFalse(old.exists() or ended.exists())


class FileAccessTests(unittest.TestCase):
    def test_safe_path(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "notes.txt").write_text("hello", encoding="utf-8")
            (Path(d) / ".env").write_text("SECRET=1", encoding="utf-8")
            allowed = [str(Path(d).resolve())]
            self.assertEqual(computeruse.read_file(str(Path(d) / "notes.txt"), allowed), "hello")
            self.assertIn("notes.txt", computeruse.list_dir(d, allowed))
            for bad in (str(Path(d) / ".env"), str(Path(d) / ".." / "x.txt"), "C:\\Windows\\win.ini"):
                with self.assertRaises(core.ActionError):
                    computeruse.read_file(bad, allowed)
            with self.assertRaises(core.ActionError):
                computeruse.read_file(str(Path(d) / "notes.txt"), [])

    def test_settings_sanitised(self):
        s, errs = computeruse.sanitize_settings({"max_steps": 999, "adapter": "rm -rf", "allowed_dirs":
                                                 [str(computeruse.ROOT), "relative/path", "C:\\"]})
        self.assertEqual(s["max_steps"], 100)
        self.assertEqual(s["adapter"], "auto")
        self.assertEqual(s["allowed_dirs"], [])
        self.assertEqual(len(errs), 3)


if __name__ == "__main__":
    unittest.main()
