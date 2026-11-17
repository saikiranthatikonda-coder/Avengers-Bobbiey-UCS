"""Core dashboard store: everything the operator edits on the core dashboard that is
not owned by another module. One JSON file (dashboard_store.json, gitignored, kept across
updates) written atomically.

  world_clocks      [{label, tz}]                   ordered; shown in the left rail + header clock
  quick_access      [{action}|{label, command}]     ordered QUICK ACCESS buttons
  weather_location  {name, lat, lon} | None         override for the weather card (None = auto)
  news              {disabled: [source], custom: [{name, url}]}
  tasks             [{id, title, priority, status, due, created, updated}]
  events            [{id, title, start_ts, duration_min, location, priority, created, updated}]

Nothing here is invented: defaults are only the layout the dashboard shipped with, and
every value is validated before it is stored (the dashboard also escapes on render).
"""

import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from atomicio import atomic_write_text

FILE = Path(__file__).parent / "dashboard_store.json"

DEFAULT_CLOCKS = [
    {"label": "NYC", "tz": "America/New_York"},
    {"label": "LA", "tz": "America/Los_Angeles"},
    {"label": "LON", "tz": "Europe/London"},
    {"label": "MUM", "tz": "Asia/Kolkata"},
    {"label": "TOK", "tz": "Asia/Tokyo"},
]
# Built-in quick actions: each maps to a real endpoint/handler in the dashboard.
QUICK_ACTIONS = {
    "system-scan": "SYSTEM SCAN",
    "briefing": "DAILY BRIEFING",
    "network-diag": "NET DIAGNOSTIC",
    "security-sweep": "SECURITY SWEEP",
    "worldmonitor": "WORLDMONITOR",
    "refresh-news": "REFRESH FEED",
    "speedtest": "SPEED TEST",
    "health-check": "SELF-CHECK",
    "focus-toggle": "DEEP WORK",
    "refresh-weather": "REFRESH WEATHER",
}
DEFAULT_QUICK = [{"action": a} for a in ("system-scan", "briefing", "network-diag",
                                         "security-sweep", "worldmonitor", "refresh-news")]
MAX_CLOCKS, MAX_QUICK, MAX_TASKS, MAX_EVENTS, MAX_FEEDS = 8, 12, 100, 100, 6
PRIORITIES = ("low", "normal", "high")
STATUSES = ("open", "doing", "done")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class StoreError(ValueError):
    """A rejected edit; the message is safe to show the operator."""


def _text(v, limit: int) -> str:
    return _CTRL.sub(" ", str(v or "")).strip()[:limit]


def valid_tz(tz: str) -> bool:
    try:
        ZoneInfo(tz)
        return True
    except Exception:
        return False


def clean_feed_url(url) -> str:
    """Custom news feeds: public https hosts only (no IP literals / local names), so the
    server is never pointed at something on the operator's own network."""
    u = urlparse(_text(url, 300))
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or "." not in host:
        raise StoreError("feed URL must be a public https:// address")
    if (re.fullmatch(r"[\d.]+", host) or ":" in host
            or host.endswith((".local", ".localhost", ".internal", ".lan", ".home"))):
        raise StoreError("feed URL must be a public host name, not an IP or local address")
    return u.geturl()


class CoreStore:
    def __init__(self, path: Path = FILE) -> None:
        self.path = Path(path)
        self.data = {
            "world_clocks": [dict(c) for c in DEFAULT_CLOCKS],
            "quick_access": [dict(q) for q in DEFAULT_QUICK],
            "weather_location": None,
            "news": {"disabled": [], "custom": []},
            "tasks": [],
            "events": [],
        }
        self._load()

    # ── persistence ───────────────────────────────────────────────
    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(saved, dict):
            return
        for k, cur in self.data.items():
            if k not in saved:
                continue
            if k == "weather_location":
                if saved[k] is None or isinstance(saved[k], dict):
                    self.data[k] = saved[k]
            elif isinstance(saved[k], type(cur)):
                self.data[k] = saved[k]
        # local events that ended more than a week ago are dropped
        cutoff = time.time() - 7 * 86400
        self.data["events"] = [e for e in self.data["events"] if isinstance(e, dict)
                               and e.get("start_ts", 0) + e.get("duration_min", 0) * 60 > cutoff]

    def _save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")

    # ── preferences ───────────────────────────────────────────────
    def prefs(self) -> dict:
        return {
            "world_clocks": self.data["world_clocks"],
            "quick_access": [self._quick_out(q) for q in self.data["quick_access"]],
            "quick_catalog": [{"action": a, "label": lbl} for a, lbl in QUICK_ACTIONS.items()],
            "weather_location": self.data["weather_location"],
            "news": self.data["news"],
            "limits": {"world_clocks": MAX_CLOCKS, "quick_access": MAX_QUICK, "feeds": MAX_FEEDS},
        }

    @staticmethod
    def _quick_out(q: dict) -> dict:
        return {**q, "label": QUICK_ACTIONS.get(q["action"], q["action"])} if "action" in q else q

    def set_world_clocks(self, clocks) -> list[dict]:
        if not isinstance(clocks, list) or len(clocks) > MAX_CLOCKS:
            raise StoreError(f"up to {MAX_CLOCKS} clocks")
        out, seen = [], set()
        for c in clocks:
            is_d = isinstance(c, dict)
            tz = _text(c.get("tz") if is_d else c, 60)
            if tz.upper() == "UTC":
                tz = "UTC"
            if not tz or not valid_tz(tz):
                raise StoreError(f'"{tz}" is not a time zone (use Region/City, e.g. Asia/Dubai)')
            if tz in seen:
                raise StoreError(f"{tz} is already in the list")
            seen.add(tz)
            label = _text(c.get("label") if is_d else "", 14) or tz.split("/")[-1].replace("_", " ")[:14]
            out.append({"label": label, "tz": tz})
        self.data["world_clocks"] = out
        self._save()
        return out

    def set_quick_access(self, items) -> list[dict]:
        if not isinstance(items, list) or len(items) > MAX_QUICK:
            raise StoreError(f"up to {MAX_QUICK} quick buttons")
        out, seen = [], set()
        for q in items:
            if not isinstance(q, dict):
                raise StoreError("bad quick button")
            if q.get("action"):
                a = str(q["action"])
                if a not in QUICK_ACTIONS:
                    raise StoreError(f"unknown quick action: {a[:40]}")
                if a in seen:
                    continue
                seen.add(a)
                out.append({"action": a})
            else:
                label, cmd = _text(q.get("label"), 22), _text(q.get("command"), 200)
                if not label or not cmd:
                    raise StoreError("a custom button needs a label and a command")
                out.append({"label": label.upper(), "command": cmd})
        self.data["quick_access"] = out
        self._save()
        return out

    def set_weather_location(self, loc) -> dict | None:
        if not loc:
            self.data["weather_location"] = None
        else:
            try:
                lat, lon = float(loc["lat"]), float(loc["lon"])
            except Exception:
                raise StoreError("a location needs numeric lat and lon")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise StoreError("lat/lon out of range")
            name = _text(loc.get("name"), 60)
            if not name:
                raise StoreError("a location needs a name")
            self.data["weather_location"] = {"name": name, "lat": round(lat, 4), "lon": round(lon, 4)}
        self._save()
        return self.data["weather_location"]

    def set_news(self, disabled=None, custom=None, known: list[str] | None = None) -> dict:
        known = list(known or [])
        news = dict(self.data["news"])
        if custom is not None:
            if not isinstance(custom, list) or len(custom) > MAX_FEEDS:
                raise StoreError(f"up to {MAX_FEEDS} custom feeds")
            out, names = [], {n.lower() for n in known}
            for f in custom:
                f = f if isinstance(f, dict) else {}
                name = _text(f.get("name"), 30)
                if not name:
                    raise StoreError("a feed needs a name")
                if name.lower() in names:
                    raise StoreError(f'a source named "{name}" already exists')
                names.add(name.lower())
                out.append({"name": name, "url": clean_feed_url(f.get("url"))})
            news["custom"] = out
        allowed = set(known) | {f["name"] for f in news["custom"]}
        if disabled is not None:
            if not isinstance(disabled, list):
                raise StoreError("bad source list")
            news["disabled"] = sorted({str(d) for d in disabled if str(d) in allowed})
        else:
            news["disabled"] = [d for d in news["disabled"] if d in allowed]
        self.data["news"] = news
        self._save()
        return news

    # ── tasks ─────────────────────────────────────────────────────
    def tasks(self) -> list[dict]:
        return list(self.data["tasks"])

    @staticmethod
    def _task_fields(patch: dict, base: dict) -> dict:
        t = dict(base)
        if "title" in patch:
            title = _text(patch["title"], 120)
            if not title:
                raise StoreError("a task needs a title")
            t["title"] = title
        if "priority" in patch:
            if patch["priority"] not in PRIORITIES:
                raise StoreError("priority must be low, normal or high")
            t["priority"] = patch["priority"]
        if "status" in patch:
            if patch["status"] not in STATUSES:
                raise StoreError("status must be open, doing or done")
            if patch["status"] != t.get("status"):
                t["done_ts"] = time.time() if patch["status"] == "done" else None
            t["status"] = patch["status"]
        if "due" in patch:
            due = patch["due"] or None
            if due is not None:
                due = str(due)
                try:
                    if not _DATE.match(due):
                        raise ValueError
                    datetime.strptime(due, "%Y-%m-%d")
                except ValueError:
                    raise StoreError("due date must be YYYY-MM-DD")
            t["due"] = due
        return t

    def add_task(self, patch: dict) -> dict:
        if len(self.data["tasks"]) >= MAX_TASKS:
            raise StoreError(f"task list is full ({MAX_TASKS}); delete finished tasks first")
        now = time.time()
        fields = {"title": patch.get("title")}
        fields.update({k: patch[k] for k in ("priority", "status", "due") if patch.get(k) is not None})
        t = self._task_fields(fields, {"id": uuid.uuid4().hex[:8], "title": "", "priority": "normal",
                                       "status": "open", "due": None, "created": now, "updated": now,
                                       "done_ts": None})
        self.data["tasks"].insert(0, t)
        self._save()
        return t

    def update_task(self, tid: str, patch: dict) -> dict:
        for i, t in enumerate(self.data["tasks"]):
            if t["id"] == tid:
                new = self._task_fields(patch, t)
                new["updated"] = time.time()
                self.data["tasks"][i] = new
                self._save()
                return new
        raise StoreError("unknown task")

    def delete_task(self, tid: str) -> dict:
        for t in self.data["tasks"]:
            if t["id"] == tid:
                self.data["tasks"].remove(t)
                self._save()
                return t
        raise StoreError("unknown task")

    def reorder_tasks(self, ids) -> list[dict]:
        by = {t["id"]: t for t in self.data["tasks"]}
        if not isinstance(ids, list) or len(ids) != len(by) or {str(i) for i in ids} != set(by):
            raise StoreError("reorder needs every task id exactly once")
        self.data["tasks"] = [by[str(i)] for i in ids]
        self._save()
        return self.data["tasks"]

    # ── local calendar events ─────────────────────────────────────
    def events(self) -> list[dict]:
        return list(self.data["events"])

    @staticmethod
    def _event_fields(patch: dict, base: dict) -> dict:
        e = dict(base)
        if "title" in patch:
            title = _text(patch["title"], 120)
            if not title:
                raise StoreError("an event needs a title")
            e["title"] = title
        if "start_ts" in patch or "start" in patch:
            try:
                ts = (float(patch["start_ts"]) if patch.get("start_ts") is not None
                      else datetime.fromisoformat(str(patch["start"])).timestamp())
                datetime.fromtimestamp(ts)
            except Exception:
                raise StoreError("start must be a date and time")
            if not (time.time() - 366 * 86400 < ts < time.time() + 5 * 366 * 86400):
                raise StoreError("start is out of range")
            e["start_ts"] = ts
        if "duration_min" in patch:
            try:
                d = int(patch["duration_min"])
            except Exception:
                raise StoreError("duration must be minutes")
            if not 5 <= d <= 1440:
                raise StoreError("duration must be 5 to 1440 minutes")
            e["duration_min"] = d
        if "location" in patch:
            e["location"] = _text(patch["location"], 80)
        if "priority" in patch:
            if patch["priority"] not in ("normal", "high"):
                raise StoreError("priority must be normal or high")
            e["priority"] = patch["priority"]
        return e

    def add_event(self, patch: dict) -> dict:
        if len(self.data["events"]) >= MAX_EVENTS:
            raise StoreError(f"local calendar is full ({MAX_EVENTS} events)")
        if patch.get("start_ts") is None and not patch.get("start"):
            raise StoreError("start must be a date and time")
        now = time.time()
        e = self._event_fields({k: v for k, v in patch.items() if v is not None},
                               {"id": "loc-" + uuid.uuid4().hex[:8], "title": "", "start_ts": 0.0,
                                "duration_min": 30, "location": "", "priority": "normal",
                                "created": now, "updated": now})
        if not e["title"]:
            raise StoreError("an event needs a title")
        self.data["events"].append(e)
        self._save()
        return e

    def update_event(self, eid: str, patch: dict) -> dict:
        for i, e in enumerate(self.data["events"]):
            if e["id"] == eid:
                new = self._event_fields({k: v for k, v in patch.items() if v is not None}, e)
                new["updated"] = time.time()
                self.data["events"][i] = new
                self._save()
                return new
        raise StoreError("unknown event (only local events can be edited here)")

    def delete_event(self, eid: str) -> dict:
        for e in self.data["events"]:
            if e["id"] == eid:
                self.data["events"].remove(e)
                self._save()
                return e
        raise StoreError("unknown event (only local events can be deleted here)")
