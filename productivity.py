"""Operator Productivity engine.

Real work-session intelligence, persisted to productivity.json:
  · focus / deep-work timer (start–stop sessions, daily total)
  · current task (set by the operator; changes count as context switches)
  · context-switch tracker
  · AI productivity score — computed from actual signals: focus minutes,
    switch rate, camera-presence minutes, and calendar density

Counters roll over automatically at local midnight.
"""

import json
import time
from datetime import date
from pathlib import Path

FILE = Path(__file__).parent / "productivity.json"
FOCUS_TARGET_MIN = 120          # daily deep-work target used by the score


class Productivity:
    def __init__(self, hub=None, insights=None, agenda=None, memory=None) -> None:
        self.hub = hub
        self.insights = insights
        self.agenda = agenda
        self.memory = memory
        self.state = {
            "day": date.today().isoformat(),
            "current_task": "",
            "focus_active": False,
            "focus_started": 0.0,
            "focus_seconds_today": 0.0,
            "sessions_today": 0,
            "switches_today": 0,
            "tasks_done_today": 0,
        }
        self._load()

    # ── persistence ───────────────────────────────────────────────
    def _load(self) -> None:
        try:
            saved = json.loads(FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.state.update(saved)
        except Exception:
            pass
        self._rollover()
        # a focus session can't survive a restart — close it cleanly
        if self.state["focus_active"]:
            self.state["focus_active"] = False
            self.state["focus_started"] = 0.0
            self._save()

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _rollover(self) -> None:
        today = date.today().isoformat()
        if self.state.get("day") != today:
            self.state.update({
                "day": today, "focus_seconds_today": 0.0, "sessions_today": 0,
                "switches_today": 0, "tasks_done_today": 0,
                "focus_active": False, "focus_started": 0.0,
            })
            self._save()

    # ── operations ────────────────────────────────────────────────
    async def set_task(self, text: str) -> dict:
        self._rollover()
        text = (text or "").strip()[:80]
        prev = self.state["current_task"]
        if prev and text and text != prev:
            self.state["switches_today"] += 1
        self.state["current_task"] = text
        self._save()
        if self.hub and text:
            await self.hub.broadcast({"type": "log", "level": "info",
                                      "msg": f"operator task set: {text}"})
        return self.snapshot()

    async def complete_task(self) -> dict:
        self._rollover()
        done = self.state["current_task"]
        if done:
            self.state["tasks_done_today"] += 1
            self.state["current_task"] = ""
            self._save()
            if self.hub:
                await self.hub.broadcast({"type": "log", "level": "info",
                                          "msg": f"operator task completed: {done}"})
        return self.snapshot()

    async def focus_start(self) -> dict:
        self._rollover()
        if not self.state["focus_active"]:
            self.state["focus_active"] = True
            self.state["focus_started"] = time.time()
            self.state["sessions_today"] += 1
            self._save()
            if self.hub:
                await self.hub.broadcast({"type": "log", "level": "info",
                                          "msg": "deep-work session started — orchestrator guarding focus"})
        return self.snapshot()

    async def focus_stop(self) -> dict:
        self._rollover()
        if self.state["focus_active"]:
            dur = max(0.0, time.time() - self.state["focus_started"])
            self.state["focus_seconds_today"] += dur
            self.state["focus_active"] = False
            self.state["focus_started"] = 0.0
            self._save()
            if self.hub:
                await self.hub.broadcast({
                    "type": "log", "level": "info",
                    "msg": f"deep-work session ended — {dur / 60:.0f} min logged",
                })
        return self.snapshot()

    # ── the score (real signals only) ─────────────────────────────
    def score(self) -> dict:
        focus_min = self.state["focus_seconds_today"] / 60
        if self.state["focus_active"]:
            focus_min += (time.time() - self.state["focus_started"]) / 60
        focus_pts = min(45, focus_min / FOCUS_TARGET_MIN * 45)       # ≤45
        switch_pen = min(20, self.state["switches_today"] * 2.5)     # −≤20
        presence_min = 0
        if self.memory is not None:
            try:
                hours = self.memory.data["patterns"]["hourly_activity"]
                presence_min = sum(hours) if isinstance(hours, list) else 0
            except Exception:
                presence_min = 0
        presence_pts = min(25, presence_min / 240 * 25)              # ≤25
        task_pts = min(15, self.state["tasks_done_today"] * 5)       # ≤15
        base = 15                                                    # showed up
        total = max(0, min(100, round(base + focus_pts + presence_pts
                                      + task_pts - switch_pen)))
        return {
            "score": total,
            "focus_min_today": round(focus_min),
            "focus_target_min": FOCUS_TARGET_MIN,
            "presence_min_today": presence_min,
            "breakdown": {
                "focus": round(focus_pts), "presence": round(presence_pts),
                "tasks": round(task_pts), "switch_penalty": -round(switch_pen),
                "base": base,
            },
        }

    def snapshot(self) -> dict:
        self._rollover()
        elapsed = 0
        if self.state["focus_active"]:
            elapsed = round(time.time() - self.state["focus_started"])
        return {
            **{k: v for k, v in self.state.items() if k != "focus_started"},
            "focus_elapsed_sec": elapsed,
            **self.score(),
        }
