"""Edit abilities added to the feature modules (v8.3.1): device edit, cyber-analyst scan schedule,
Computer Use task templates. Real server in a temp copy; persistence across restart; observer gating;
secrets never returned. No test touches a real device, the desktop or a phone line."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from harness import IsolatedServer  # noqa: E402

import secagent  # noqa: E402


def js(r):
    return r.json() if hasattr(r, "json") else r


def refused(fn, *a) -> bool:
    """True when the server refuses: an ok:false body, or an HTTP error status (403 / 402 / 404…)."""
    import urllib.error
    try:
        return not js(fn(*a)).get("ok", False)
    except urllib.error.HTTPError as e:
        return e.code >= 400


class Owner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = IsolatedServer(owner=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    # ── devices ──
    def test_device_edit_keeps_secret_and_persists(self):
        add = js(self.srv.post("/api/devices", {"kind": "shelly", "name": "Desk plug", "host": "192.168.1.250",
                                                "password": "s3cret-pass-123"}))
        self.assertTrue(add.get("ok"), add)
        did = add["device"]["id"]
        self.assertNotIn("s3cret-pass-123", str(add))
        masked = add["device"].get("password")
        r = js(self.srv.post(f"/api/devices/{did}", {"name": "Desk lamp", "room": "Study", "password": masked}))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual((r["device"]["name"], r["device"]["room"]), ("Desk lamp", "Study"))
        self.assertTrue(r["device"].get("has_password"))           # stored secret kept, not wiped
        self.assertNotIn("s3cret-pass-123", str(r))
        self.assertTrue(refused(self.srv.post, f"/api/devices/{did}", {"host": "8.8.8.8"}))   # LAN only
        self.srv.restart()
        d = next(x for x in js(self.srv.get("/api/devices"))["devices"] if x["id"] == did)
        self.assertEqual((d["name"], d.get("room"), d.get("host")), ("Desk lamp", "Study", "192.168.1.250"))
        self.assertTrue(js(self.srv.post(f"/api/devices/{did}/remove", {})).get("ok"))
        self.assertTrue(refused(self.srv.post, "/api/devices/nope", {"name": "x"}))

    # ── cyber analyst schedule ──
    def test_scan_schedule_clamps_and_persists(self):
        r = js(self.srv.post("/api/secagent/schedule", {"full_h": 999, "light_min": 1, "paused": True}))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual((r["schedule"]["full_h"], r["schedule"]["light_min"], r["schedule"]["paused"]),
                         (secagent.FULL_H_RANGE[1], secagent.LIGHT_MIN_RANGE[0], True))
        self.srv.restart()
        s = js(self.srv.get("/api/secagent"))["schedule"]
        self.assertEqual((s["full_h"], s["light_min"], s["paused"]), (48, 10, True))
        self.assertTrue(js(self.srv.post("/api/secagent/schedule", {"full_h": 6, "light_min": 30, "paused": False})).get("ok"))

    # ── computer use templates ──
    def test_templates_crud_and_persist(self):
        r = js(self.srv.post("/api/computeruse/templates", {"name": "Morning notes", "task": "Open Notepad and type: good morning"}))
        self.assertTrue(r.get("ok"), r)
        tid = r["template"]["id"]
        r2 = js(self.srv.post("/api/computeruse/templates", {"id": tid, "name": "Morning notes", "task": "Open Notepad and type: hi"}))
        self.assertEqual(len([t for t in r2["templates"] if t["name"] == "Morning notes"]), 1)   # updated, not duplicated
        self.assertTrue(refused(self.srv.post, "/api/computeruse/templates", {"name": "", "task": "x"}))
        self.srv.restart()
        tpls = js(self.srv.get("/api/computeruse"))["templates"]
        self.assertEqual([(t["name"], t["task"]) for t in tpls if t["id"] == tid], [("Morning notes", "Open Notepad and type: hi")])
        self.assertTrue(js(self.srv.post(f"/api/computeruse/templates/{tid}/delete", {})).get("ok"))
        self.assertTrue(refused(self.srv.post, f"/api/computeruse/templates/{tid}/delete", {}))


class Observer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = IsolatedServer(owner=False).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_observer_cannot_edit(self):
        self.assertTrue(refused(self.srv.post, "/api/devices/abc", {"name": "x"}))
        self.assertTrue(refused(self.srv.post, "/api/secagent/schedule", {"paused": True}))
        self.assertTrue(refused(self.srv.post, "/api/computeruse/templates", {"name": "a", "task": "b"}))


class ScheduleUnit(unittest.TestCase):
    def test_clean_schedule_bounds(self):
        s = secagent.clean_schedule({"full_h": 0, "light_min": 10_000})
        self.assertEqual((s["full_h"], s["light_min"]), (1, 240))
        with self.assertRaises((TypeError, ValueError)):
            secagent.clean_schedule({"full_h": "lots"})


if __name__ == "__main__":
    unittest.main()
