"""Orchestrator tick regression tests — runs real ticks with a real roster."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents import build_team  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402


def _hub():
    return mock.Mock(broadcast=mock.AsyncMock())


def _orch(metrics=None, agenda_events=None):
    hub = _hub()
    team = build_team(hub=hub, brain=mock.Mock())
    sysmon = mock.Mock(latest=metrics or {"cpu": 5, "mem": 40, "disk": 30})
    agenda = mock.Mock(snapshot=lambda: {"events": agenda_events or [], "intel": {},
                                         "priority_unread": 0})
    return Orchestrator(hub=hub, team=team, sysmon=sysmon, agenda=agenda), team


class Tick(unittest.TestCase):
    def test_tick_survives_no_calendar(self):
        # regression: f"{None:.0f}" in an inactive rule crashed every tick
        # whenever Google Calendar wasn't connected
        o, _ = _orch()
        for _ in range(3):
            asyncio.run(o.tick())
        self.assertEqual(o.cycles, 3)

    def test_memory_pressure_delegates_to_hulk(self):
        o, team = _orch(metrics={"cpu": 5, "mem": 95, "disk": 30})
        asyncio.run(o.tick())
        active = {d["key"]: d for d in o.active.values()}
        self.assertIn("mem", active)
        self.assertEqual(active["mem"]["agent"], "hulk")

    def test_imminent_meeting_raises_prep_directive(self):
        o, _ = _orch(agenda_events=[{"title": "Board sync", "minutes_until": 12}])
        asyncio.run(o.tick())
        d = next(d for d in o.active.values() if d["key"] == "meeting-prep")
        self.assertIn("in 12m", d["title"])


if __name__ == "__main__":
    unittest.main()
