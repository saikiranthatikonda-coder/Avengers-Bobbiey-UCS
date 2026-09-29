"""Google sync tests with fake API responses — no network, no credentials."""

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import google_sync as g  # noqa: E402
from agenda import Agenda  # noqa: E402


def _iso(minutes: int) -> str:
    return (datetime.now().astimezone() + timedelta(minutes=minutes)).isoformat()


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeCalendar:
    """Just enough of googleapiclient's calendar v3 surface."""

    def __init__(self, calendars, events_by_cal):
        self._cals, self._events = calendars, events_by_cal

    def calendarList(self):
        return mock.Mock(list=lambda **kw: _Exec({"items": self._cals}))

    def events(self):
        return mock.Mock(list=lambda calendarId, **kw: _Exec(self._events[calendarId]))


class ParseEvent(unittest.TestCase):
    def test_meet_link_organizer_rsvp(self):
        ev = g.parse_event({
            "summary": "Investor sync", "iCalUID": "u1",
            "start": {"dateTime": _iso(30)}, "end": {"dateTime": _iso(60)},
            "organizer": {"email": "boss@corp.com"},
            "attendees": [{"email": "me@x.com", "self": True, "responseStatus": "tentative"},
                          {"email": "a@corp.com"}, {"email": "b@corp.com"}],
            "conferenceData": {"conferenceSolution": {"name": "Google Meet"},
                               "entryPoints": [{"entryPointType": "video",
                                                "uri": "https://meet.google.com/abc-defg-hij"}]},
        }, "Work")
        self.assertEqual(ev["meet_link"], "https://meet.google.com/abc-defg-hij")
        self.assertEqual(ev["meet_provider"], "Google Meet")
        self.assertEqual(ev["organizer"], "boss@corp.com")
        self.assertEqual(ev["response"], "tentative")
        self.assertEqual(ev["attendee_count"], 2)
        self.assertEqual(ev["calendar"], "Work")
        self.assertEqual(ev["priority"], "high")        # "investor" keyword
        self.assertEqual(ev["duration_min"], 30)

    def test_zoom_link_found_in_description(self):
        ev = g.parse_event({"summary": "Client call", "start": {"dateTime": _iso(5)},
                            "description": "Join: https://us02web.zoom.us/j/8812?pwd=Qx now"})
        self.assertEqual(ev["meet_provider"], "Zoom")
        self.assertTrue(ev["meet_link"].startswith("https://us02web.zoom.us/j/8812"))

    def test_cancelled_and_all_day(self):
        self.assertIsNone(g.parse_event({"status": "cancelled", "start": {"dateTime": _iso(5)}}))
        day = datetime.now().date().isoformat()
        ev = g.parse_event({"summary": "Holiday", "start": {"date": day}, "end": {"date": day}})
        self.assertTrue(ev["all_day"])


class AllCalendars(unittest.TestCase):
    def test_reads_every_selected_calendar_and_dedupes(self):
        shared = {"summary": "Team standup", "iCalUID": "same",
                  "start": {"dateTime": _iso(20)}, "end": {"dateTime": _iso(35)}}
        svc = FakeCalendar(
            calendars=[{"id": "primary", "primary": True, "summary": "me@x.com"},
                       {"id": "team", "summary": "Team", "selected": True},
                       {"id": "hidden", "summary": "Old", "selected": True, "hidden": True},
                       {"id": "broken", "summary": "Broken", "selected": True}],
            events_by_cal={
                "primary": {"items": [shared]},
                "team": {"items": [shared, {"summary": "Design review", "iCalUID": "d1",
                                            "start": {"dateTime": _iso(90)}}]},
                "hidden": {"items": [{"summary": "never", "start": {"dateTime": _iso(1)}}]},
                "broken": RuntimeError("403 forbidden"),
            })
        per_cal = g.fetch_all_calendars(svc)
        self.assertEqual([n for n, _ in per_cal], ["Primary", "Team"])   # hidden skipped, broken tolerated
        events = g.merge_events(per_cal)
        self.assertEqual([e["title"] for e in events], ["Team standup", "Design review"])


class Tasks(unittest.TestCase):
    def test_open_tasks_sorted_by_due(self):
        svc = mock.Mock()
        svc.tasklists.return_value.list.return_value = _Exec({"items": [{"id": "L", "title": "Work"}]})
        svc.tasks.return_value.list.return_value = _Exec({"items": [
            {"title": "later", "due": "2030-01-02T00:00:00.000Z"},
            {"title": "no due"},
            {"title": "done", "status": "completed"},
            {"title": "soon", "due": "2030-01-01T00:00:00.000Z"},
        ]})
        self.assertEqual([t["title"] for t in g.fetch_open_tasks(svc)], ["soon", "later", "no due"])


class SignInOnce(unittest.TestCase):
    def test_clicking_g_again_reuses_the_saved_login(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "credentials.json").write_text("{}")
            (root / "token.json").write_text(json.dumps({"scopes": g.SCOPES}))
            gc = g.GoogleCalendar(root=root)
            with mock.patch.object(gc, "_load_creds", return_value=object()):
                r = asyncio.run(gc.connect())
            self.assertEqual(r, {"ok": True, "reused": True})
            self.assertTrue((root / "token.json").exists(), "saved login must not be deleted")

    def test_missing_scope_triggers_consent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "token.json").write_text(json.dumps({"scopes": g.SCOPES[:2]}))
            self.assertFalse(g.GoogleCalendar(root=root)._scopes_ok())


class AgendaCarriesDetails(unittest.TestCase):
    def test_snapshot_exposes_join_link_and_tasks(self):
        a = Agenda()
        a.set_events([g.parse_event({
            "summary": "Board", "start": {"dateTime": _iso(15)}, "end": {"dateTime": _iso(45)},
            "hangoutLink": "https://meet.google.com/xyz"}, "Work")])
        a.set_tasks([{"title": "Send deck", "due": "2030-01-01", "list": "Work"}])
        snap = a.snapshot()
        self.assertEqual(snap["events"][0]["meet_link"], "https://meet.google.com/xyz")
        self.assertEqual(snap["events"][0]["calendar"], "Work")
        self.assertEqual(snap["tasks"][0]["title"], "Send deck")
        self.assertEqual(snap["source"], "google")


if __name__ == "__main__":
    unittest.main()
