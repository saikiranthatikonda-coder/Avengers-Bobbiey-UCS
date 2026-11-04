"""AUTONOMOUS EVOLUTION tests — analyzers, backlog, state machine, scrubbing, customer
restrictions, persistence, the devagent sandbox/deploy/revert path (throwaway git repo,
fake CLI) and the HTTP surface on an isolated server."""

import asyncio
import base64
import json
import logging
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import devagent  # noqa: E402
import evolution  # noqa: E402
import evolution_core as core  # noqa: E402
import evolution_sandbox as sbx  # noqa: E402

SECRETS = ["sk-live1234567890abcdef", "ghp_abcdefghijklmnopqrstuvwxyz12", "hunter2pass", "me@example.com",
           "BUCS-eyJjdXN0b21lciI6InRlc3QifQ.c2lnbmF0dXJl", "0123456789abcdef0123456789abcdef"]


def run(c):
    return asyncio.run(c)


def git(root, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root,
                          capture_output=True, text=True).stdout


class Scrubbing(unittest.TestCase):
    def test_scrub_removes_credentials_and_personal_data(self):
        text = ("api_key=sk-live1234567890abcdef token: ghp_abcdefghijklmnopqrstuvwxyz12 password=hunter2pass "
                "mail me@example.com key BUCS-eyJjdXN0b21lciI6InRlc3QifQ.c2lnbmF0dXJl hash "
                "0123456789abcdef0123456789abcdef Authorization: Bearer abc.def.ghi https://u:pw@host/x "
                r"C:\Users\Saikiran\secret.txt")
        out = core.scrub(text, 2000)
        for s in SECRETS + ["pw@host", "Saikiran", "abc.def.ghi"]:
            self.assertNotIn(s, out)
        self.assertIn("<redacted>", out)

    def test_error_recorder_counts_by_signature_and_never_keeps_secrets(self):
        rec = core.ErrorRecorder(Path(__file__).resolve().parent.parent)
        lg = logging.getLogger("evo-test-logger")
        lg.addHandler(rec)
        lg.propagate = False
        try:
            for _ in range(3):
                try:
                    raise ValueError("refresh failed token=sk-live1234567890abcdef for me@example.com")
                except ValueError:
                    lg.exception("feed failed password=%s", "hunter2pass")
            lg.error("plain failure with secret=%s", "hunter2pass")
            lg.warning("just a warning")                      # below ERROR, no exc → ignored
        finally:
            lg.removeHandler(rec)
        snap = rec.snapshot()
        self.assertEqual(len(snap), 2)
        exc = next(e for e in snap if e["type"] == "ValueError")
        self.assertEqual(exc["count"], 3)
        self.assertIn("tests/test_evolution.py:", exc["where"])
        self.assertTrue(exc["frames"])
        dump = json.dumps(snap)
        for s in SECRETS:
            self.assertNotIn(s, dump)

    def test_diffs_shown_to_the_commander_are_scrubbed(self):
        d = "+++ b/x.py\n+API_KEY = 'sk-live1234567890abcdef'\n- old"
        self.assertNotIn("sk-live", evolution.scrub_diff(d))
        self.assertIn("+    return x", evolution.scrub_diff("+    return x"))     # indentation preserved


class Analyzers(unittest.TestCase):
    def test_error_spike_is_detected_with_measured_evidence(self):
        now = time.time()
        errs = [{"sig": "KeyError@weather.py:40", "type": "KeyError", "where": "weather.py:40", "count": 6,
                 "first": now - 3 * 86400, "last": now, "msg": "'temp'", "frames": ["weather.py:40 in get"],
                 "times": [now - 5 * 3600] + [now - i * 60 for i in range(5)]}]
        c = core.analyze_errors(errs, now)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["key"], "error:KeyError@weather.py:40")
        self.assertTrue(c[0]["code_level"])
        self.assertIn("5 in the last hour", c[0]["evidence"][0])
        self.assertIn("spike", " ".join(c[0]["evidence"]))
        quiet = [{**errs[0], "times": [now - 20 * 3600], "first": now - 5 * 86400, "count": 1}]
        self.assertEqual(core.analyze_errors(quiet, now), [])

    def test_perf_budget(self):
        s, now = core.Signals(), time.time()
        for i in range(8):
            s.add("endpoint:/api/slow", 900 + i, now - i * 60)
            s.add("endpoint:/api/fast", 20, now - i * 60)
        keys = [c["key"] for c in core.analyze_perf(s, now)]
        self.assertEqual(keys, ["perf:/api/slow"])

    def test_check_regression_and_flaky(self):
        s, now = core.Signals(), time.time()
        for i, v in enumerate([0, 0, 0, 0, 0, 2]):
            s.add("check:news", v, now - 60 * (6 - i), "stale")
        for i, v in enumerate([0, 1, 0, 1, 0, 1, 0]):
            s.add("check:google", v, now - 60 * (7 - i))
        keys = {c["key"] for c in core.analyze_checks(s, now)}
        self.assertEqual(keys, {"regression:check:news", "flaky:check:google"})

    def test_tests_regression(self):
        last = {"ts": time.time(), "ran": 100, "failing": ["test_x.A.test_a", "test_y.B.test_b"]}
        prev = {"ts": time.time() - 3600, "ran": 100, "failing": ["test_y.B.test_b"]}
        c = core.analyze_tests(last, prev)
        self.assertEqual(c[0]["kind"], "regression")
        self.assertEqual(c[0]["impact"], 5)
        self.assertIn("test_x.A.test_a", " ".join(c[0]["evidence"]))
        self.assertEqual(core.analyze_tests({"skipped": "no tests"}, None), [])

    def test_voice_feedback_security_updates(self):
        utt = [{"outcome": "ignored (empty / phantom)", "peak": 400, "stt_ms": 3000}] * 7 + [{"outcome": "ok", "peak": 900, "stt_ms": 2800}] * 2
        keys = {c["key"] for c in core.analyze_voice({"utterances": utt})}
        self.assertEqual(keys, {"voice:ignored", "voice:slow-stt"})
        now = time.time()
        fb = [{"ts": now, "vote": "down", "area": "voice", "note": "keeps mishearing me"}] * 2
        f = core.analyze_feedback(fb, now)
        self.assertEqual(f[0]["key"], "feedback:voice")
        self.assertTrue(f[0]["code_level"])
        sec = core.analyze_security([{"id": "x1", "severity": "high", "title": "t", "evidence": "e", "fix": "f",
                                      "task": "bump"}, {"id": "x2", "severity": "low", "title": "t"}])
        self.assertEqual([c["key"] for c in sec], ["sec:x1"])
        up = core.analyze_updates({"available": True, "latest": "9.9.9", "version": "1.0"},
                                  [{"name": "httpx", "installed": "0.28.1", "latest": "0.29.0"}])
        self.assertEqual({c["key"] for c in up}, {"update:9.9.9", "dep:httpx"})


class BacklogAndStateMachine(unittest.TestCase):
    def c(self, key="error:A", value=3):
        return core.cand(key, "error", "t " + key, ["ev"], 4, 2, 2, 0.9, code_level=True, task="fix it", value=value)

    def test_dedupe_trend_and_auto_resolve(self):
        b = core.Backlog()
        b.upsert_all([self.c(), self.c()], now=1000)
        b.upsert_all([self.c(value=5)], now=2000)
        self.assertEqual(len(b.items), 1)
        it = next(iter(b.items.values()))
        self.assertEqual(it["seen"], 2)
        self.assertEqual([v for _, v in it["trend"]], [3, 5])
        for t in (3000, 4000):
            b.upsert_all([], now=t)
        self.assertEqual(it["stage"], "identified")
        b.upsert_all([], now=5000)
        self.assertEqual(it["stage"], "resolved")
        b.upsert_all([self.c()], now=6000)                    # comes back → a NEW item, history kept
        self.assertEqual(len(b.items), 2)

    def test_dismiss_snoozes(self):
        b = core.Backlog()
        b.upsert_all([self.c()], now=1000)
        it = next(iter(b.items.values()))
        core.transition(it, "dismissed")
        it["snooze_until"] = time.time() + 3600
        b.upsert_all([self.c()])
        self.assertEqual(it["stage"], "dismissed")
        self.assertEqual(len(b.items), 1)

    def test_cannot_skip_approval_or_deploy_without_passing_sandbox(self):
        it = {"stage": "identified"}
        with self.assertRaises(core.TransitionError):
            core.transition(it, "deploying")
        for s in ("proposing", "proposed", "sandboxing"):
            core.transition(it, s)
        with self.assertRaises(core.TransitionError):          # failing sandbox can't go to approval
            it["sandbox"] = {"id": "s1", "ok": False}
            core.transition(it, "awaiting_approval")
        it["sandbox"] = {"id": "s1", "ok": True}
        core.transition(it, "awaiting_approval")
        with self.assertRaises(core.TransitionError):          # no approval
            core.transition(it, "deploying")
        it["approval"] = {"decision": "approved", "by": "sai", "sandbox_id": "other"}
        with self.assertRaises(core.TransitionError):          # approval for a different result
            core.transition(it, "deploying")
        it["approval"]["sandbox_id"] = "s1"
        it["sandbox"]["ok"] = False
        with self.assertRaises(core.TransitionError):          # sandbox no longer passing
            core.transition(it, "deploying")
        it["sandbox"]["ok"] = True
        core.transition(it, "deploying")
        self.assertEqual(it["stage"], "deploying")

    def test_verdict(self):
        self.assertEqual(core.verdict(100, 200, "down", min_abs=5), "regressed")
        self.assertEqual(core.verdict(200, 100, "down", min_abs=5), "improved")
        self.assertEqual(core.verdict(100, 105, "down", min_abs=5), "neutral")
        self.assertEqual(core.verdict(None, 5), "insufficient-data")


class FakeDev:
    def __init__(self, sandbox_ok=True):
        self.lock = asyncio.Lock()
        self.job = None
        self.sandbox_ok = sandbox_ok
        self.calls = []

    async def diagnose(self, req):
        self.calls.append("diagnose")
        self.job = {"id": "job1", "stage": "proposed", "plan": "SUMMARY: s\nPROPOSED FIX: x", "request": req}
        return {"ok": True, "summary": "s", "fixable": True, "id": "job1"}

    async def sandbox(self):
        self.calls.append("sandbox")
        ok = self.sandbox_ok
        self.job["stage"] = "sandboxed" if ok else "proposed"
        sb = {"id": "sb1", "ts": time.time(), "ok": ok, "changed": ["app.py"], "diff": "+x = 2 token=sk-live1234567890abcdef",
              "tests": {"ok": ok}, "boot": {"ok": True}, "reason": "" if ok else "unit tests failed"}
        return {"ok": True, "sandbox": sb, "patch": {"id": "sb1", "files": {"app.py": {"new": "eA==", "orig_sha": None}}} if ok else None}

    async def deploy_patch(self, patch, approved_by=""):
        self.calls.append("deploy")
        self.job = {"id": patch["id"], "stage": "applied"}
        return {"ok": True, "id": patch["id"], "changed": ["app.py"], "tests": {"ok": True}, "backup_dir": "b", "hashes": {}}

    async def revert_deploy(self, record, reason="", auto=False):
        self.calls.append("revert-auto" if auto else "revert")
        return {"ok": True, "reverted": ["app.py"], "skipped": []}


class EvolutionEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audits = []

        async def audit(a, d=""):
            self.audits.append((a, d))
        self.ctx = {"audit": audit}
        self.lic = types.SimpleNamespace(owner=True, paid=True)
        self.dev = FakeDev()
        self.state = {"licensing": self.lic, "rbac": {"role": "commander", "operator": "sai"}, "dev": self.dev}

    def tearDown(self):
        self.tmp.cleanup()

    def evo(self):
        return evolution.Evolution(self.state, self.ctx, store_path=self.root / "evolution.json", root=self.root,
                                   recorder=core.ErrorRecorder(self.root))

    def item(self, e, key="error:A"):
        e.backlog.upsert_all([core.cand(key, "error", "Recurring error A", ["3 in the last hour"], 4, 2, 2, 0.9,
                                        metric={"key": "errors:A", "dir": "down", "agg": "sum"}, code_level=True,
                                        task="fix A")])
        return next(i for i in e.backlog.items.values() if i["key"] == key)

    async def settle(self, e):
        while e._tasks:
            await asyncio.gather(*list(e._tasks))

    def test_full_loop_requires_sandbox_then_approval(self):
        async def go():
            e = self.evo()
            it = self.item(e)
            self.assertFalse((await e.approve(it["id"], "sai"))["ok"])        # can't approve an identified item
            r = await e.propose(it["id"], "sai")
            self.assertTrue(r["ok"])
            await self.settle(e)
            self.assertEqual(it["stage"], "awaiting_approval")
            self.assertEqual(self.dev.calls, ["diagnose", "sandbox"])       # sandbox BEFORE approval, no deploy
            self.assertNotIn("sk-live", it["sandbox"]["diff"])
            self.assertFalse((await e.reject(it["id"], "sai", ""))["ok"])    # reason required
            r = await e.approve(it["id"], "sai")
            self.assertTrue(r["ok"], r)
            self.assertEqual(self.dev.calls[-1], "deploy")
            self.assertEqual(it["stage"], "evaluating")
            self.assertEqual(it["approval"]["by"], "sai")
            self.assertIn("evolution.approve", [a for a, _ in self.audits])
            r = await e.revert(it["id"], "sai")
            self.assertEqual(it["stage"], "reverted")
            self.assertEqual(self.dev.calls[-1], "revert")
        run(go())

    def test_failed_sandbox_never_reaches_approval(self):
        async def go():
            self.dev.sandbox_ok = False
            e = self.evo()
            it = self.item(e)
            await e.propose(it["id"], "sai")
            await self.settle(e)
            self.assertEqual(it["stage"], "sandbox_failed")
            self.assertFalse((await e.approve(it["id"], "sai"))["ok"])
            self.assertNotIn("deploy", self.dev.calls)
        run(go())

    def test_customer_install_is_observe_and_analyse_only(self):
        async def go():
            self.lic.owner = False
            e = self.evo()
            it = self.item(e)
            for r in (await e.propose(it["id"], "sai"), await e.sandbox(it["id"]), await e.approve(it["id"], "sai"),
                      await e.run_tests()):
                self.assertFalse(r["ok"])
            self.assertEqual(self.dev.calls, [])
            await e._maybe_auto_propose()
            self.assertEqual(self.dev.calls, [])
            self.assertFalse(e.snapshot()["owner"])
            r = await e.beat("test")                                      # observation still works
            self.assertTrue(r["ok"])
        run(go())

    def test_evaluation_flags_regression_and_catastrophe_auto_reverts(self):
        async def go():
            e = self.evo()
            e.settings["eval_window_min"] = 5
            it = self.item(e)
            await e.propose(it["id"], "sai")
            await self.settle(e)
            await e.approve(it["id"], "sai")
            ev = it["eval"]
            self.assertEqual(ev["live_since"], None)                      # python change: awaits restart
            dep = it["deploy"]["ts"]
            ev.update(live_since=dep - 1000, baseline=1.0, window_s=300)
            for t in range(10):
                e.signals.add("errors:A", 5, dep - 900 + t * 60)
            sh = types.SimpleNamespace(catastrophic=lambda: [])
            self.state["selfheal"] = sh
            await e.evaluate()
            self.assertEqual(it["stage"], "regressed")
            self.assertNotIn("revert", self.dev.calls)                   # flagged, NOT reverted without approval
            # catastrophic health failure → automatic revert + guarded restart request
            it2 = self.item(e, key="error:B")
            self.dev.job = None
            await e.propose(it2["id"], "sai")
            await self.settle(e)
            await e.approve(it2["id"], "sai")
            it2["eval"]["live_since"] = time.time()
            restarts = []

            async def restart(why):
                restarts.append(why)
            self.state["selfheal"] = types.SimpleNamespace(catastrophic=lambda: ["event_loop"], restart_guarded=restart)
            await e.evaluate()
            self.assertEqual(it2["stage"], "reverted")
            self.assertEqual(self.dev.calls[-1], "revert-auto")
            self.assertEqual(len(restarts), 1)
        run(go())

    def test_persistence_survives_restart(self):
        async def go():
            e = self.evo()
            it = self.item(e)
            await e.add_feedback("down", "voice cut off token=sk-live1234567890abcdef", "voice", "sai")
            e.signals.add("endpoint:/api/status", 12.5)
            e.settings["heartbeat_min"] = 7
            it["stage"] = "proposing"                                    # in flight when BUCS stopped
            e.save()
            raw = (self.root / "evolution.json").read_text()
            self.assertNotIn("sk-live", raw)
            e2 = self.evo()
            self.assertIn(it["id"], e2.backlog.items)
            self.assertEqual(e2.backlog.items[it["id"]]["stage"], "failed")
            self.assertEqual(len(e2.feedback), 1)
            self.assertEqual(e2.settings["heartbeat_min"], 7)
            self.assertEqual(e2.signals.values("endpoint:/api/status"), [12.5])
        run(go())

    def test_corrupt_settings_are_clamped_and_customer_autonomy_is_observe(self):
        (self.root / "evolution.json").write_text(json.dumps({"v": 1, "settings": {
            "autonomy": "deploy-everything", "heartbeat_min": 0, "eval_window_min": "x", "tests_every_h": 9999}}))
        e = self.evo()
        self.assertEqual(e.settings["autonomy"], "propose")            # unknown value → safe default
        self.assertEqual(e.settings["heartbeat_min"], 2)               # 0 would spin the loop
        self.assertEqual(e.settings["eval_window_min"], evolution.DEFAULT_SETTINGS["eval_window_min"])
        self.assertEqual(e.settings["tests_every_h"], 168)
        self.assertEqual(e.snapshot()["autonomy_effective"], "propose")
        self.lic.owner = False
        self.assertEqual(e.snapshot()["autonomy_effective"], "observe")  # UI never claims auto-propose on a customer install

    def test_changed_interval_reschedules_now(self):
        e = self.evo()
        e.heartbeat["last"] = 1000.0
        e.tests["last"] = {"ts": time.time()}
        e.settings.update(heartbeat_min=5, tests_every_h=2)
        e.reschedule()
        self.assertEqual(e.heartbeat["next"], 1000.0 + 300)
        self.assertAlmostEqual(e.tests["next"], e.tests["last"]["ts"] + 7200, delta=1)

    def test_feedback_validation_and_heartbeat_observes(self):
        async def go():
            e = self.evo()
            self.assertFalse((await e.add_feedback("meh", "", "voice", "sai"))["ok"])
            sh = types.SimpleNamespace(snapshot=lambda: {"checks": {"event_loop": {"status": "ok", "detail": "lag 3 ms", "value": 3}},
                                                         "overall": "ok", "log": []})
            self.state["selfheal"] = sh
            r = await e.beat("test")
            self.assertTrue(r["ok"])
            self.assertEqual(e.signals.values("selfheal:event_loop_ms"), [3.0])
            self.assertEqual(e.heartbeat["beats"], 1)
            loop = e.snapshot()["loop"]
            self.assertEqual([s["stage"] for s in loop], core.LOOP)
        run(go())


class DevAgentSandbox(unittest.TestCase):
    """Real DevAgent against a throwaway git repo; fake CLI edits only the sandbox copy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "app.py").write_text("x = 1\n")
        (self.root / ".gitignore").write_text(".env\n.update/\n")
        (self.root / ".env").write_text("SECRET=keep-me\n")
        git(self.root, "init", "-q"); git(self.root, "add", "."); git(self.root, "commit", "-qm", "init")
        self.p = mock.patch.object(devagent, "ROOT", self.root); self.p.start()
        self.state = {"rbac": {"role": "commander"}, "licensing": types.SimpleNamespace(owner=True)}
        self.dev = devagent.DevAgent(self.state)
        self.dev.job = {"id": "j1", "request": "make x 2", "stage": "proposed", "plan": "set x = 2", "summary": "s"}
        self.ok_tests = mock.patch.object(sbx, "run_tests", mock.AsyncMock(return_value={"ok": True, "modules": []}))
        self.ok_boot = mock.patch.object(sbx, "boot_check", mock.AsyncMock(return_value={"ok": True, "probes": []}))
        self.ok_tests.start(); self.ok_boot.start()

    def tearDown(self):
        self.ok_tests.stop(); self.ok_boot.stop(); self.p.stop(); self.tmp.cleanup()

    def sandbox(self, edit):
        seen = {}

        async def fake_cli(prompt, tools, apply, timeout, cwd=None):
            assert "Bash" not in tools and cwd is not None and Path(cwd) != self.root
            seen["cwd"] = Path(cwd)
            self.assertFalse((Path(cwd) / ".env").exists())               # secrets never copied
            edit(Path(cwd))
            return "CHANGED: app.py"
        with mock.patch.object(self.dev, "_run_cli", fake_cli):
            r = run(self.dev.sandbox())
        return r, seen

    def test_sandbox_never_touches_the_repo_and_deploy_revert_roundtrip(self):
        r, seen = self.sandbox(lambda box: (box / "app.py").write_text("x = 2\n"))
        sb = r["sandbox"]
        self.assertTrue(sb["ok"], sb)
        self.assertEqual(sb["changed"], ["app.py"])
        self.assertIn("+x = 2", sb["diff"])
        self.assertEqual((self.root / "app.py").read_text(), "x = 1\n")     # real tree untouched
        self.assertFalse(seen["cwd"].exists())                             # sandbox cleaned up
        self.assertEqual(self.dev.job["stage"], "sandboxed")
        self.assertNotIn("patch", self.dev.snapshot()["job"])
        with mock.patch.object(self.dev, "_tests", mock.AsyncMock(return_value={"ok": True, "summary": "OK"})):
            d = run(self.dev.deploy_patch(r["patch"], "sai"))
        self.assertTrue(d["ok"], d)
        self.assertEqual((self.root / "app.py").read_text(), "x = 2\n")
        self.assertEqual(self.dev.job["stage"], "applied")                  # existing KEEP · UNDO · SHIP path
        self.assertEqual(self.dev.job["revertable"], ["app.py"])
        rv = run(self.dev.revert_deploy(d))
        self.assertEqual(rv["reverted"], ["app.py"])
        self.assertEqual((self.root / "app.py").read_text(), "x = 1\n")
        self.assertEqual((self.root / ".env").read_text(), "SECRET=keep-me\n")

    def test_stale_patch_is_refused(self):
        r, _ = self.sandbox(lambda box: (box / "app.py").write_text("x = 2\n"))
        (self.root / "app.py").write_text("x = 99  # operator edit\n")
        d = run(self.dev.deploy_patch(r["patch"], "sai"))
        self.assertFalse(d["ok"])
        self.assertIn("changed since the sandbox", d["error"])
        self.assertEqual((self.root / "app.py").read_text(), "x = 99  # operator edit\n")

    def test_sandbox_change_to_tests_is_refused(self):
        def edit(box):
            (box / "app.py").write_text("x = 2\n")
            (box / "tests").mkdir(exist_ok=True)
            (box / "tests" / "test_evil.py").write_text("import os\n")
        r, _ = self.sandbox(edit)
        self.assertFalse(r["sandbox"]["ok"])
        self.assertIn("protected", r["sandbox"]["reason"])
        self.assertIsNone(r["patch"])
        sbx.run_tests.assert_not_called()                                  # nothing it wrote ran

    def test_forged_patch_is_refused_and_revert_keeps_later_operator_edits(self):
        bad = {"id": "x", "files": {"tests/test_x.py": {"new": base64.b64encode(b"x").decode(), "orig_sha": None}}}
        self.assertFalse(run(self.dev.deploy_patch(bad))["ok"])
        bad = {"id": "x", "files": {"../outside.py": {"new": base64.b64encode(b"x").decode(), "orig_sha": None}}}
        self.assertFalse(run(self.dev.deploy_patch(bad))["ok"])
        r, _ = self.sandbox(lambda box: (box / "app.py").write_text("x = 2\n"))
        with mock.patch.object(self.dev, "_tests", mock.AsyncMock(return_value={"ok": True, "summary": "OK"})):
            d = run(self.dev.deploy_patch(r["patch"], "sai"))
        (self.root / "app.py").write_text("x = 3  # operator kept editing\n")
        rv = run(self.dev.revert_deploy(d))
        self.assertEqual(rv["skipped"], ["app.py"])
        self.assertEqual((self.root / "app.py").read_text(), "x = 3  # operator kept editing\n")

    def test_not_owner_or_observer_cannot_sandbox_or_deploy(self):
        self.state["licensing"].owner = False
        self.assertFalse(run(self.dev.sandbox())["ok"])
        self.assertFalse(run(self.dev.deploy_patch({"id": "x", "files": {"app.py": {}}}))["ok"])
        self.state["licensing"].owner = True
        self.state["rbac"]["role"] = "observer"
        self.assertIn("OBSERVER", run(self.dev.sandbox())["error"])


class PrivateDataProtection(unittest.TestCase):
    def test_update_dir_and_private_files_are_protected(self):
        for f in (".update/phone.json", ".update/phone.db", ".update/devices.json", ".update/secagent.json",
                  ".update/mic_pref.json"):
            self.assertIn(f, devagent.PROTECTED)
        for f in (".update/agentos/memory.db", ".update/phone.json", ".env", "tests/test_x.py"):
            self.assertTrue(devagent.DevAgent._unsafe_path(f), f)
        self.assertFalse(devagent.DevAgent._unsafe_path("weather.py"))
        self.assertIn("Read(./.update/**)", devagent.DENY_RULES)
        self.assertIn("Edit(./.update/**)", devagent.DENY_RULES)
        self.assertIn("Read(./.env)", devagent.DENY_RULES)

    def test_cli_is_started_with_the_deny_rules(self):
        dev = devagent.DevAgent({})
        seen = {}

        async def fake_exec(*args, **kw):
            seen["args"] = args
            raise FileNotFoundError
        with mock.patch.object(asyncio, "create_subprocess_exec", fake_exec):
            with self.assertRaises(FileNotFoundError):
                run(dev._run_cli("p", devagent.DIAG_TOOLS, apply=False, timeout=5))
        args = list(seen["args"])
        self.assertIn("--disallowedTools", args)
        self.assertIn("Read(./.update/**)", args[args.index("--disallowedTools") + 1])
        self.assertNotIn("Bash", args[args.index("--tools") + 1])


class SandboxHelpers(unittest.TestCase):
    def test_copy_excludes_protected_and_parse_unittest(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "a.py").write_text("1")
            (root / "credentials.json").write_text("{}")
            (root / "tests").mkdir()
            (root / "tests" / "test_a.py").write_text("")
            box, files = sbx.make_copy(root, devagent.PROTECTED)
            try:
                self.assertIn("a.py", files)
                self.assertNotIn("credentials.json", files)
                self.assertEqual(sbx.related_tests(box, ["a.py"]), ["test_a"])
            finally:
                sbx.cleanup(box)
        out = "FAIL: test_x (test_mod.Cls.test_x)\nERROR: test_y (test_mod.Cls.test_y)\nRan 12 tests in 1.5s\n\nFAILED (failures=1, errors=1)"
        p = sbx.parse_unittest(out)
        self.assertEqual(p["ran"], 12)
        self.assertEqual(p["failing"], ["test_mod.Cls.test_x", "test_mod.Cls.test_y"])


class Endpoints(unittest.TestCase):
    """The router on a real isolated server (owner install)."""

    @classmethod
    def setUpClass(cls):
        from harness import IsolatedServer
        cls.srv = IsolatedServer().start(timeout=240)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_snapshot_capabilities_heartbeat_feedback(self):
        s = self.srv.get("/api/evolution")
        self.assertEqual([x["stage"] for x in s["loop"]], core.LOOP)
        self.assertTrue(s["owner"])
        caps = self.srv.get("/api/evolution/capabilities")
        self.assertGreater(caps["summary"]["loaded"], 20)
        names = {c["name"]: c for c in caps["capabilities"]}
        self.assertTrue(names["Autonomous evolution loop (approval-gated)"]["loaded"])
        rm = self.srv.get("/api/roadmap")
        self.assertIn("version", rm)
        self.assertIn("loaded", rm["phases"][0]["features"][0])
        hb = self.srv.post("/api/evolution/heartbeat", {})
        self.assertTrue(hb["ok"], hb)
        s = self.srv.get("/api/evolution")
        self.assertEqual(s["heartbeat"]["beats"], 1)
        self.assertIn("endpoints", s["observe"]["sources"])
        self.assertIn("sampled", s["observe"]["sources"]["endpoints"])
        fb = self.srv.post("/api/evolution/feedback", {"vote": "down", "note": "slow sk-live1234567890abcdef", "area": "voice"})
        self.assertTrue(fb["ok"])
        self.assertNotIn("sk-live", json.dumps(fb))
        self.assertFalse(self.srv.post("/api/evolution/items/nope/approve", {})["ok"])
        sig = self.srv.get("/api/evolution/signals?prefix=endpoint:")
        self.assertIn("endpoint:/api/status", sig["keys"])

    def test_settings_save_broadcast_and_survive_restart(self):
        body = {"autonomy": "observe", "heartbeat_min": 15, "eval_window_min": 45, "tests_every_h": 6}
        got = self.srv.ws_during(lambda: self.srv.post("/api/evolution/settings", body), "evolution")
        self.assertTrue(any(m.get("event") == "settings" and m["settings"]["heartbeat_min"] == 15 for m in got), got)
        s = self.srv.get("/api/evolution")                                 # what other tabs' polling reads
        self.assertEqual({k: s["settings"][k] for k in body}, body)
        self.assertEqual(s["autonomy_effective"], "observe")
        self.assertEqual(s["setting_bounds"]["heartbeat_min"], [2, 240])
        r = self.srv.post("/api/evolution/settings", {"heartbeat_min": 1, "eval_window_min": 99999})
        self.assertEqual((r["settings"]["heartbeat_min"], r["settings"]["eval_window_min"]), (2, 1440))
        self.assertFalse(self.srv.post("/api/evolution/settings", {"autonomy": "deploy"})["ok"])
        self.srv.post("/api/evolution/settings", {"heartbeat_min": 15, "eval_window_min": 45})
        self.srv.restart(timeout=240)
        s = self.srv.get("/api/evolution")
        self.assertEqual({k: s["settings"][k] for k in body}, body)
        self.srv.post("/api/evolution/settings", {"autonomy": "propose"})  # leave the shared server as it was

    def test_observer_is_locked_out_of_actions(self):
        self.srv.post("/api/role", {"role": "observer"})
        try:
            for path in ("/api/evolution/heartbeat", "/api/evolution/items/x/approve", "/api/evolution/items/x/revert",
                         "/api/evolution/settings"):
                r = self.srv.post(path, {})
                self.assertFalse(r["ok"], path)
                self.assertIn("OBSERVER", r["error"])
        finally:
            self.srv.post("/api/role", {"role": "commander"})


if __name__ == "__main__":
    unittest.main()
