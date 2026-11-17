"""Core dashboard editing (coreedit.py + corestore.py): every create/edit/delete endpoint
works, is validated, commander-gated, audited, broadcast to other tabs and survives a
restart. Also: the core dashboard scripts contain no Math.random-driven data.

Runs against tests/harness.IsolatedServer (temp copy + temp state files). Nothing here
performs a desktop action: no command, browser-open or Windows-action endpoint is called.
"""
import asyncio
import json
import re
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from harness import IsolatedServer  # noqa: E402

import corestore  # noqa: E402
import decisions  # noqa: E402
import threats  # noqa: E402

SERVER: IsolatedServer | None = None


def setUpModule():
    global SERVER
    SERVER = IsolatedServer().start()


def tearDownModule():
    if SERVER:
        SERVER.stop()


def post(path, body=None):
    return SERVER.post(path, body if body is not None else {})


def delete(path):
    return SERVER._req("DELETE", path)


def synced(test, action, topic):
    got = SERVER.ws_during(action, "sync")
    test.assertTrue(got, f"no sync broadcast for {topic}")
    test.assertEqual(got[0].get("topic"), topic)


def audited(action):
    return any(e.get("action") == action for e in SERVER.get("/api/enterprise")["audit"]["recent"])


class Prefs(unittest.TestCase):
    def test_defaults_are_the_shipped_layout(self):
        p = SERVER.get("/api/prefs")
        self.assertTrue(all(corestore.valid_tz(c["tz"]) for c in p["world_clocks"]))
        self.assertIn("system-scan", [c["action"] for c in p["quick_catalog"]])
        self.assertIn("BBC World", p["news"]["builtin"])

    def test_world_clocks_add_reorder_remove_and_validation(self):
        clocks = [{"tz": "Asia/Dubai", "label": "DXB"}, {"tz": "utc"}, {"tz": "Europe/Paris"}]
        synced(self, lambda: post("/api/prefs", {"world_clocks": clocks}), "prefs")
        got = SERVER.get("/api/prefs")["world_clocks"]
        self.assertEqual([c["tz"] for c in got], ["Asia/Dubai", "UTC", "Europe/Paris"])
        self.assertEqual(got[0]["label"], "DXB")
        self.assertEqual(got[2]["label"], "Paris")               # default label from the zone
        r = post("/api/prefs", {"world_clocks": list(reversed(got))[:2]})
        self.assertEqual([c["tz"] for c in r["prefs"]["world_clocks"]], ["Europe/Paris", "UTC"])
        bad = post("/api/prefs", {"world_clocks": [{"tz": "Mars/Olympus"}]})
        self.assertFalse(bad["ok"])
        self.assertIn("not a time zone", bad["error"])
        self.assertFalse(post("/api/prefs", {"world_clocks": [{"tz": "UTC"}, {"tz": "UTC"}]})["ok"])
        self.assertFalse(post("/api/prefs", {"world_clocks": [{"tz": "UTC"}] * 9})["ok"])
        self.assertEqual(len(SERVER.get("/api/prefs")["world_clocks"]), 2)   # refused edits change nothing
        self.assertTrue(audited("prefs.update"))

    def test_quick_access_builtin_and_custom(self):
        items = [{"action": "briefing"}, {"label": "disk report", "command": "@stark disk usage"},
                 {"action": "speedtest"}]
        r = post("/api/prefs", {"quick_access": items})
        self.assertTrue(r["ok"])
        q = r["prefs"]["quick_access"]
        self.assertEqual([x.get("action") for x in q], ["briefing", None, "speedtest"])
        self.assertEqual(q[1], {"label": "DISK REPORT", "command": "@stark disk usage"})
        self.assertEqual(q[0]["label"], "DAILY BRIEFING")
        self.assertFalse(post("/api/prefs", {"quick_access": [{"action": "format-disk"}]})["ok"])
        self.assertFalse(post("/api/prefs", {"quick_access": [{"label": "x"}]})["ok"])

    def test_weather_location_override_and_clear(self):
        r = post("/api/prefs", {"weather_location": {"name": "Pune, India", "lat": 18.5204, "lon": 73.8567}})
        self.assertEqual(r["prefs"]["weather_location"], {"name": "Pune, India", "lat": 18.5204, "lon": 73.8567})
        self.assertFalse(post("/api/prefs", {"weather_location": {"name": "x", "lat": 123, "lon": 0}})["ok"])
        self.assertFalse(post("/api/prefs", {"weather_location": {"name": "", "lat": 1, "lon": 1}})["ok"])
        self.assertIsNone(post("/api/prefs", {"weather_location": None})["prefs"]["weather_location"])
        self.assertFalse(SERVER.get("/api/weather/search?q=a")["ok"])        # too short: no lookup made

    def test_news_sources_select_and_custom_feed_rules(self):
        r = post("/api/prefs", {"news": {"disabled": ["BBC World", "No Such Source"],
                                         "custom": [{"name": "Example", "url": "https://example.org/feed.xml"}]}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["prefs"]["news"]["disabled"], ["BBC World"])       # unknown names are dropped
        self.assertEqual(r["prefs"]["news"]["custom"][0]["name"], "Example")
        for url in ("http://example.org/feed", "https://127.0.0.1/feed", "https://localhost/feed",
                    "https://printer.local/feed", "file:///etc/passwd"):
            bad = post("/api/prefs", {"news": {"custom": [{"name": "Bad", "url": url}]}})
            self.assertFalse(bad["ok"], url)
        self.assertFalse(post("/api/prefs", {"news": {"custom": [{"name": "BBC World", "url": "https://example.org/x"}]}})["ok"])
        post("/api/prefs", {"news": {"disabled": [], "custom": []}})

    def test_empty_patch_is_refused(self):
        self.assertFalse(post("/api/prefs", {"theme": "x"})["ok"])


class Notes(unittest.TestCase):
    def _add(self, text):
        post("/api/notes", {"text": text})
        return next(n for n in SERVER.get("/api/notes")["notes"] if n["text"] == text)

    def test_edit_pin_reorder(self):
        a, b = self._add("note alpha"), self._add("note beta")
        synced(self, lambda: post(f"/api/notes/{a['id']}", {"text": "note alpha edited"}), "notes")
        notes = SERVER.get("/api/notes")["notes"]
        got = next(n for n in notes if n["id"] == a["id"])
        self.assertEqual(got["text"], "note alpha edited")
        self.assertIn("edited", got)
        self.assertFalse(post(f"/api/notes/{a['id']}", {"text": "   "})["ok"])
        self.assertFalse(post("/api/notes/nope", {"text": "x"})["ok"])
        self.assertTrue(post(f"/api/notes/{a['id']}", {"pinned": True})["ok"])
        self.assertEqual(SERVER.get("/api/notes")["notes"][0]["id"], a["id"])     # pinned floats to the top
        ids = [n["id"] for n in SERVER.get("/api/notes")["notes"]]
        self.assertTrue(post("/api/notes/reorder", {"ids": list(reversed(ids))})["ok"])
        self.assertEqual([n["id"] for n in SERVER.get("/api/notes")["notes"]], list(reversed(ids)))
        self.assertFalse(post("/api/notes/reorder", {"ids": ids[:-1]})["ok"])
        self.assertTrue(audited("note.reorder"))
        self.assertTrue(delete(f"/api/notes/{b['id']}")["ok"])


class Tasks(unittest.TestCase):
    def test_create_edit_complete_delete_and_agenda_merge(self):
        got = SERVER.ws_during(lambda: post("/api/tasks", {"title": "ship the audit", "priority": "high"}), "sync")
        self.assertEqual(got[0]["topic"], "agenda")
        t = next(x for x in SERVER.get("/api/tasks")["tasks"] if x["title"] == "ship the audit")
        self.assertEqual((t["priority"], t["status"]), ("high", "open"))
        row = next(x for x in SERVER.get("/api/agenda")["tasks"] if x.get("id") == t["id"])
        self.assertTrue(row["editable"])
        self.assertEqual(row["source"], "local")
        r = post(f"/api/tasks/{t['id']}", {"title": "ship the audit v2", "status": "doing", "due": "2027-01-15"})
        self.assertEqual((r["task"]["title"], r["task"]["status"], r["task"]["due"]),
                         ("ship the audit v2", "doing", "2027-01-15"))
        self.assertEqual(r["task"]["priority"], "high")                       # untouched fields stay
        for bad in ({"priority": "urgent"}, {"status": "maybe"}, {"due": "tomorrow"}, {"title": " "}):
            self.assertFalse(post(f"/api/tasks/{t['id']}", bad)["ok"], bad)
        self.assertFalse(post("/api/tasks", {"title": ""})["ok"])
        self.assertFalse(post("/api/tasks/nope", {"status": "done"})["ok"])
        done = post(f"/api/tasks/{t['id']}", {"status": "done"})["task"]
        self.assertTrue(done["done_ts"])
        self.assertFalse(any(x.get("id") == t["id"] for x in SERVER.get("/api/agenda")["tasks"]))  # done leaves the open list
        self.assertTrue(delete(f"/api/tasks/{t['id']}")["ok"])
        self.assertFalse(any(x["id"] == t["id"] for x in SERVER.get("/api/tasks")["tasks"]))
        self.assertFalse(delete(f"/api/tasks/{t['id']}")["ok"])
        for a in ("task.add", "task.edit", "task.delete"):
            self.assertTrue(audited(a), a)

    def test_reorder(self):
        ids = [post("/api/tasks", {"title": f"order {i}"})["task"]["id"] for i in range(3)]
        cur = [t["id"] for t in SERVER.get("/api/tasks")["tasks"]]
        want = list(reversed(cur))
        self.assertTrue(post("/api/tasks/reorder", {"ids": want})["ok"])
        self.assertEqual([t["id"] for t in SERVER.get("/api/tasks")["tasks"]], want)
        self.assertFalse(post("/api/tasks/reorder", {"ids": want + ["x"]})["ok"])
        for i in ids:
            delete(f"/api/tasks/{i}")


class LocalEvents(unittest.TestCase):
    def test_create_edit_delete_and_shows_in_agenda(self):
        start = time.time() + 3 * 3600
        got = SERVER.ws_during(lambda: post("/api/events", {"title": "design review", "start_ts": start,
                                                           "duration_min": 45, "location": "Room 2"}), "sync")
        self.assertEqual(got[0]["topic"], "agenda")
        ag = SERVER.get("/api/agenda")
        ev = next(e for e in ag["events"] if e["title"] == "design review")
        self.assertTrue(ev["editable"] and ev["id"].startswith("loc-"))
        self.assertTrue(ev["source"].startswith("Local"))
        self.assertGreaterEqual(ag["local_events"], 1)
        self.assertNotEqual(ag["source"], "google")                           # Google is still honestly not connected
        r = post(f"/api/events/{ev['id']}", {"title": "design review (moved)", "priority": "high"})
        self.assertEqual((r["event"]["title"], r["event"]["priority"], r["event"]["duration_min"]),
                         ("design review (moved)", "high", 45))
        self.assertTrue(any(e["title"] == "design review (moved)" for e in SERVER.get("/api/agenda")["events"]))
        for bad in ({"duration_min": 2}, {"priority": "low"}, {"start": "not a date"}, {"title": ""}):
            self.assertFalse(post(f"/api/events/{ev['id']}", bad)["ok"], bad)
        self.assertFalse(post("/api/events", {"title": "no start"})["ok"])
        self.assertFalse(post("/api/events/gcal-123", {"title": "x"})["ok"])   # Google events are read-only here
        self.assertTrue(delete(f"/api/events/{ev['id']}")["ok"])
        self.assertFalse(any(e.get("id") == ev["id"] for e in SERVER.get("/api/agenda")["events"]))
        self.assertTrue(audited("event.delete"))

    def test_iso_start_is_accepted(self):
        r = post("/api/events", {"title": "iso start", "start": "2027-03-01T09:30"})
        self.assertTrue(r["ok"], r)
        delete(f"/api/events/{r['event']['id']}")


class Operators(unittest.TestCase):
    def test_rename_role_remove_and_guards(self):
        self.assertTrue(post("/api/operators", {"name": "ce-alice", "role": "observer"})["ok"])
        synced(self, lambda: post("/api/operators/ce-alice", {"name": "ce-alicia"}), "enterprise")
        names = {o["name"]: o["role"] for o in SERVER.get("/api/enterprise")["operators"]}
        self.assertIn("ce-alicia", names)
        self.assertNotIn("ce-alice", names)
        self.assertTrue(post("/api/operators/ce-alicia", {"role": "commander"})["ok"])
        self.assertFalse(post("/api/operators/ce-alicia", {"role": "admin"})["ok"])
        self.assertFalse(post("/api/operators/ce-alicia", {})["ok"])          # nothing to change
        self.assertFalse(post("/api/operators/nobody", {"name": "x"})["ok"])
        active = SERVER.get("/api/enterprise")["operator"]
        self.assertFalse(post("/api/operators/ce-alicia", {"name": active})["ok"])   # no duplicate names
        self.assertFalse(delete("/api/operators/" + active)["ok"])            # active operator stays
        self.assertTrue(delete("/api/operators/ce-alicia")["ok"])
        self.assertNotIn("ce-alicia", [o["name"] for o in SERVER.get("/api/enterprise")["operators"]])
        only = [o for o in SERVER.get("/api/enterprise")["operators"] if o["role"] == "commander"]
        if len(only) == 1:
            self.assertFalse(post("/api/operators/" + only[0]["name"], {"role": "observer"})["ok"])
        self.assertTrue(audited("operator.remove"))


class ProductivityGoal(unittest.TestCase):
    def test_goal_edit_and_validation(self):
        synced(self, lambda: post("/api/productivity", {"action": "goal", "minutes": 200}), "productivity")
        self.assertEqual(SERVER.get("/api/productivity")["focus_target_min"], 200)
        self.assertFalse(post("/api/productivity", {"action": "goal", "minutes": 5}).get("ok", True))
        self.assertFalse(post("/api/productivity", {"action": "goal"}).get("ok", True))
        self.assertEqual(SERVER.get("/api/productivity")["focus_target_min"], 200)


class DecisionsApi(unittest.TestCase):
    def test_whitelist_can_only_be_narrowed(self):
        d = SERVER.get("/api/decisions")
        key = d["whitelist_all"][0]
        synced(self, lambda: post("/api/decisions/whitelist", {"key": key, "on": False}), "decisions")
        self.assertNotIn(key, SERVER.get("/api/decisions")["whitelist"])
        self.assertIn(key, SERVER.get("/api/decisions")["whitelist_all"])
        self.assertFalse(post("/api/decisions/whitelist", {"key": "disk_directive", "on": True})["ok"])
        self.assertFalse(post("/api/decisions/restore", {"key": "nothing"})["ok"])


class DecisionsUnit(unittest.TestCase):
    def test_dismiss_then_restore(self):
        with tempfile.TemporaryDirectory() as td:
            old = decisions.FILE
            decisions.FILE = Path(td) / "decisions.json"
            try:
                eng = decisions.DecisionEngine()
                eng.proposals["briefing"] = {"id": "p1", "key": "briefing", "title": "Deliver the briefing",
                                             "risk": "low", "ts": time.time()}
                self.assertTrue(asyncio.run(eng.dismiss("p1"))["ok"])
                snap = eng.snapshot()
                self.assertEqual(snap["dismissed"][0]["title"], "Deliver the briefing")
                self.assertTrue(asyncio.run(eng.restore("briefing"))["ok"])
                self.assertEqual(eng.snapshot()["dismissed"], [])
                asyncio.run(eng.set_whitelist("ack_resolved", False))
                self.assertEqual(decisions.DecisionEngine().snapshot()["whitelist"], ["switch_claude"])   # persisted
            finally:
                decisions.FILE = old


class _Hub:
    async def broadcast(self, msg):
        pass


class IncidentLifecycle(unittest.TestCase):
    def setUp(self):
        self.eng = threats.ThreatEngine(_Hub())
        asyncio.run(self.eng._add("network", "high", "packet loss", "12% loss"))
        self.id = self.eng.events[0]["id"]

    def stage(self):
        return next(e for e in self.eng.snapshot()["events"] if e["id"] == self.id)

    def test_resolve_reopen_ack_note(self):
        self.assertEqual(self.stage()["stage"], "active")
        r = self.eng.set_state(self.id, "resolve", "router rebooted", by="sai")
        self.assertEqual(r["stage"], "resolved")
        ev = self.stage()
        self.assertEqual(ev["note"], "router rebooted")
        self.assertIn("resolved by sai", ev["resolved_note"])
        self.assertEqual(self.eng.set_state(self.id, "reopen", "it came back")["stage"], "active")
        self.assertEqual(self.stage()["note"], "it came back")
        self.assertEqual(self.eng.set_state(self.id, "ack")["stage"], "acknowledged")
        self.assertEqual(self.eng.set_state(self.id, "reopen")["stage"], "active")
        self.assertEqual(self.eng.set_state(self.id, "note", "watching")["note"], "watching")
        self.assertFalse(self.eng.set_state(self.id, "note", " ")["ok"])
        self.assertFalse(self.eng.set_state(self.id, "delete")["ok"])
        self.assertFalse(self.eng.set_state("nope", "ack")["ok"])

    def test_reopened_old_incident_is_active_again_not_expired(self):
        self.eng.events[0]["ts"] -= 4000
        self.assertEqual(self.stage()["stage"], "expired")
        self.eng.set_state(self.id, "reopen")
        self.assertEqual(self.stage()["stage"], "active")

    def test_endpoint_rejects_unknown_incident(self):
        self.assertFalse(post("/api/threats/incident/nope", {"action": "resolve"})["ok"])


class CommanderGate(unittest.TestCase):
    """An OBSERVER session can read everything and change nothing."""

    def test_observer_is_refused_everywhere(self):
        t = post("/api/tasks", {"title": "gate probe"})["task"]
        self.assertTrue(post("/api/role", {"role": "observer"})["ok"])
        try:
            before = json.dumps([SERVER.get("/api/prefs"), SERVER.get("/api/tasks"), SERVER.get("/api/notes")], sort_keys=True)
            calls = [
                ("POST", "/api/prefs", {"world_clocks": [{"tz": "UTC"}]}),
                ("POST", "/api/notes/reorder", {"ids": []}),
                ("POST", "/api/notes/x", {"text": "y"}),
                ("POST", "/api/tasks", {"title": "nope"}),
                ("POST", f"/api/tasks/{t['id']}", {"status": "done"}),
                ("POST", "/api/tasks/reorder", {"ids": [t["id"]]}),
                ("DELETE", f"/api/tasks/{t['id']}", None),
                ("POST", "/api/events", {"title": "nope", "start_ts": time.time() + 3600}),
                ("POST", "/api/events/loc-x", {"title": "nope"}),
                ("DELETE", "/api/events/loc-x", None),
                ("POST", "/api/threats/incident/x", {"action": "resolve"}),
                ("POST", "/api/decisions/whitelist", {"key": "switch_claude", "on": False}),
                ("POST", "/api/decisions/restore", {"key": "briefing"}),
                ("POST", "/api/operators/operator-1", {"name": "pwned"}),
                ("DELETE", "/api/operators/operator-1", None),
                ("POST", "/api/productivity", {"action": "goal", "minutes": 60}),
            ]
            for method, path, body in calls:
                r = SERVER._req(method, path, body)
                self.assertFalse(r.get("ok", True), f"{method} {path} was not refused")
                self.assertIn("OBSERVER", r.get("error", ""), f"{method} {path}")
            after = json.dumps([SERVER.get("/api/prefs"), SERVER.get("/api/tasks"), SERVER.get("/api/notes")], sort_keys=True)
            self.assertEqual(before, after)
        finally:
            self.assertTrue(post("/api/role", {"role": "commander"})["ok"])
            delete(f"/api/tasks/{t['id']}")


class StoreUnit(unittest.TestCase):
    def test_corrupt_or_foreign_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            p.write_text('{"world_clocks": "oops", "tasks": [], "weather_location": 5}', encoding="utf-8")
            s = corestore.CoreStore(p)
            self.assertEqual(s.data["world_clocks"], corestore.DEFAULT_CLOCKS)
            self.assertIsNone(s.data["weather_location"])
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(corestore.CoreStore(p).data["quick_access"], corestore.DEFAULT_QUICK)

    def test_old_local_events_are_pruned_on_load(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            s = corestore.CoreStore(p)
            s.add_event({"title": "soon", "start_ts": time.time() + 60})
            s.data["events"].append({"id": "loc-old", "title": "old", "start_ts": time.time() - 9 * 86400,
                                     "duration_min": 30})
            s._save()
            self.assertEqual([e["title"] for e in corestore.CoreStore(p).events()], ["soon"])


class NoRandomData(unittest.TestCase):
    """Dashboard values must be measured, never generated. Math.random is allowed only for
    purely visual effects, each whitelisted here by a distinctive snippet of its line."""

    # visual-only uses (e.g. particle jitter) go here with a comment saying what they animate.
    VISUAL_ONLY: tuple[str, ...] = ()

    def test_core_scripts_have_no_random_driven_values(self):
        for name in ("app.js", "core_edit.js"):
            src = (ROOT / "static" / name).read_text(encoding="utf-8")
            for n, line in enumerate(src.splitlines(), 1):
                if "Math.random" in line and not any(w in line for w in self.VISUAL_ONLY):
                    self.fail(f"static/{name}:{n} uses Math.random outside the visual-only whitelist: {line.strip()[:120]}")

    def test_no_placeholder_fallback_labels(self):
        src = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('|| "mock")', src)
        self.assertNotIn('"HYDERABAD WEATHER"', src)

    def test_core_edit_script_is_loaded_with_the_same_cache_bust(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app = re.search(r"app\.js\?v=([\w.]+)", html)
        ce = re.search(r"core_edit\.js\?v=([\w.]+)", html)
        self.assertTrue(app and ce)
        self.assertEqual(app.group(1), ce.group(1))
        self.assertLess(html.index("app.js?v="), html.index("core_edit.js?v="))   # needs app.js globals
        with urllib.request.urlopen(SERVER.base + "/static/core_edit.js", timeout=10) as r:
            self.assertEqual(r.status, 200)

    def test_store_is_private_and_kept_across_updates(self):
        import updater
        self.assertIn("dashboard_store.json", updater.PRESERVE)
        self.assertIn("dashboard_store.json", (ROOT / ".gitignore").read_text(encoding="utf-8"))


class ZPersistence(unittest.TestCase):
    """Runs last (name order): every edit survives a server restart."""

    def test_edits_survive_restart(self):
        post("/api/prefs", {"world_clocks": [{"tz": "Asia/Singapore", "label": "SIN"}],
                            "quick_access": [{"action": "health-check"}, {"label": "hello", "command": "status report"}],
                            "weather_location": {"name": "Chennai, India", "lat": 13.0827, "lon": 80.2707},
                            "news": {"disabled": ["Al Jazeera"], "custom": []}})
        post("/api/notes", {"text": "persist me"})
        nid = next(n["id"] for n in SERVER.get("/api/notes")["notes"] if n["text"] == "persist me")
        post(f"/api/notes/{nid}", {"text": "persisted and edited", "pinned": True})
        tid = post("/api/tasks", {"title": "persistent task", "priority": "low"})["task"]["id"]
        post(f"/api/tasks/{tid}", {"status": "doing"})
        eid = post("/api/events", {"title": "persistent event", "start_ts": time.time() + 7200})["event"]["id"]
        post("/api/operators", {"name": "ce-persist", "role": "observer"})
        post("/api/operators/ce-persist", {"name": "ce-persisted"})
        post("/api/productivity", {"action": "goal", "minutes": 90})
        post("/api/decisions/whitelist", {"key": "switch_claude", "on": False})

        SERVER.restart()

        p = SERVER.get("/api/prefs")
        self.assertEqual(p["world_clocks"], [{"label": "SIN", "tz": "Asia/Singapore"}])
        self.assertEqual([q.get("action") or q["command"] for q in p["quick_access"]], ["health-check", "status report"])
        self.assertEqual(p["weather_location"]["name"], "Chennai, India")
        self.assertEqual(p["news"]["disabled"], ["Al Jazeera"])
        note = next(n for n in SERVER.get("/api/notes")["notes"] if n["id"] == nid)
        self.assertEqual((note["text"], note["pinned"]), ("persisted and edited", True))
        task = next(t for t in SERVER.get("/api/tasks")["tasks"] if t["id"] == tid)
        self.assertEqual((task["status"], task["priority"]), ("doing", "low"))
        ag = SERVER.get("/api/agenda")
        self.assertTrue(any(e.get("id") == eid for e in ag["events"]))
        self.assertTrue(any(t.get("id") == tid for t in ag["tasks"]))
        self.assertIn("ce-persisted", [o["name"] for o in SERVER.get("/api/enterprise")["operators"]])
        self.assertEqual(SERVER.get("/api/productivity")["focus_target_min"], 90)
        self.assertNotIn("switch_claude", SERVER.get("/api/decisions")["whitelist"])


if __name__ == "__main__":
    unittest.main()
