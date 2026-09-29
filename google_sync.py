"""Google sync engine — ONE sign-in (the G button) covers Calendar, Gmail and Tasks.

Setup (one-time, ~5 min):
  1. console.cloud.google.com → create project → enable "Google Calendar API",
     "Gmail API" and "Google Tasks API"
  2. OAuth consent screen → External → add yourself as test user
  3. Credentials → Create OAuth client ID → Desktop app → download JSON
  4. Save as  credentials.json  in the project root (next to main.py)
  5. Click G in the dashboard (POST /api/calendar/connect) → browser opens →
     approve once → token.json (with a refresh token) keeps you signed in.
     Clicking G again just re-syncs; it only re-asks when a scope is missing.

Read-only everywhere. Without credentials the Agenda stays empty (never mocked).
"""

import asyncio
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
]

CAL_WINDOW_DAYS = 7
MAX_CALENDARS = 15
MAX_EVENTS_PER_CAL = 25
_VIDEO_URL = re.compile(
    r"https://[\w.-]*(zoom\.us|teams\.microsoft\.com|teams\.live\.com|"
    r"meet\.google\.com|webex\.com)/[^\s\"<>]+")


def _provider(url: str) -> str:
    for key, name in (("zoom.us", "Zoom"), ("teams.", "Microsoft Teams"),
                      ("meet.google", "Google Meet"), ("webex", "Webex")):
        if key in url:
            return name
    return "Video call"


def _join_link(it: dict) -> tuple[str, str]:
    """(url, provider) for an event's video meeting, from conferenceData,
    hangoutLink, or a Zoom/Teams/Meet URL in the location/description."""
    conf = it.get("conferenceData") or {}
    for ep in conf.get("entryPoints") or []:
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            name = ((conf.get("conferenceSolution") or {}).get("name") or "").strip()
            return ep["uri"], name or _provider(ep["uri"])
    if it.get("hangoutLink"):
        return it["hangoutLink"], "Google Meet"
    m = _VIDEO_URL.search(f"{it.get('location') or ''} {it.get('description') or ''}")
    if m:
        return m.group(0), _provider(m.group(0))
    return "", ""


def parse_event(it: dict, calendar: str = "") -> dict | None:
    """Google Calendar event resource → agenda dict (None if unusable)."""
    start_raw = (it.get("start") or {}).get("dateTime") or (it.get("start") or {}).get("date")
    end_raw = (it.get("end") or {}).get("dateTime") or (it.get("end") or {}).get("date")
    if not start_raw or it.get("status") == "cancelled":
        return None
    try:
        start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        if start.tzinfo:
            start = start.astimezone().replace(tzinfo=None)
        if end_raw:
            end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            if end.tzinfo:
                end = end.astimezone().replace(tzinfo=None)
            duration = max(5, int((end - start).total_seconds() // 60))
        else:
            duration = 30
    except Exception:
        return None
    title = it.get("summary") or "(untitled)"
    people = it.get("attendees") or []
    me = next((a for a in people if a.get("self")), None)
    attendees = [a.get("email", "") for a in people if not a.get("self")]
    low = title.lower()
    priority = "high" if (len(attendees) >= 3
                          or any(w in low for w in HIGH_PRIORITY_WORDS)) else "normal"
    link, provider = _join_link(it)
    return {
        "title": title, "start": start, "duration_min": duration,
        "attendees": attendees[:5],
        "attendee_count": len(attendees),
        "location": it.get("location") or "",
        "priority": priority,
        "calendar": calendar,
        "meet_link": link, "meet_provider": provider,
        "organizer": (it.get("organizer") or {}).get("email", ""),
        "response": (me or {}).get("responseStatus", ""),  # accepted|declined|tentative|needsAction
        "all_day": "date" in (it.get("start") or {}),
        "uid": it.get("iCalUID") or it.get("id") or "",
    }


def merge_events(per_calendar: list[tuple[str, list[dict]]]) -> list[dict]:
    """Parse, de-duplicate (same invite on several calendars) and sort."""
    seen, out = set(), []
    for cal_name, items in per_calendar:
        for it in items:
            ev = parse_event(it, cal_name)
            if not ev:
                continue
            key = (ev["uid"], ev["start"])
            if ev["uid"] and key in seen:
                continue
            seen.add(key)
            out.append(ev)
    out.sort(key=lambda e: e["start"])
    return out

HIGH_PRIORITY_WORDS = ("investor", "board", "demo", "review", "interview",
                       "pitch", "client", "aisin", "deadline")


class GoogleCalendar:
    def __init__(self, root: Path, hub=None) -> None:
        self.root = Path(root)
        self.credentials_path = self.root / "credentials.json"
        self.token_path = self.root / "token.json"
        self.hub = hub
        self.connected = False
        self.last_sync: float | None = None
        self.last_error: str | None = None
        self.event_count = 0
        self.account: str | None = None      # signed-in Google address
        self.calendars: list[str] = []       # calendar names included in sync
        self.task_count = 0
        self.tasks_error: str | None = None

    # ── status helpers ────────────────────────────────────────────
    def credentials_present(self) -> bool:
        return self.credentials_path.exists()

    def token_present(self) -> bool:
        return self.token_path.exists()

    def status(self) -> dict:
        return {
            "credentials_present": self.credentials_present(),
            "token_present": self.token_present(),
            "connected": self.connected,
            "last_sync": self.last_sync,
            "last_error": self.last_error,
            "event_count": self.event_count,
            "account": self.account,
            "calendars": self.calendars,
            "task_count": self.task_count,
            "tasks_error": self.tasks_error,
            "scopes_ok": self._scopes_ok(),
        }

    def _scopes_ok(self) -> bool:
        """True when the saved token already grants every scope we use."""
        try:
            import json as _json
            info = _json.loads(self.token_path.read_text(encoding="utf-8"))
            return set(SCOPES) <= set(info.get("scopes") or [])
        except Exception:
            return False

    # ── auth ──────────────────────────────────────────────────────
    def _load_creds(self):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
        except ImportError:
            self.last_error = "google libraries not installed"
            return None
        creds = None
        if self.token_present():
            try:
                # no scopes arg: keep what the token actually granted, so a token
                # from before a scope was added still refreshes (Google rejects a
                # refresh that asks for more than was consented)
                creds = Credentials.from_authorized_user_file(str(self.token_path))
            except Exception as e:
                # Older/web tokens may lack refresh_token → from_authorized_user_file
                # raises. Try to build creds from whatever fields we have so a
                # still-valid access token keeps working until it expires.
                try:
                    import json as _json
                    info = _json.loads(self.token_path.read_text(encoding="utf-8"))
                    if info.get("token"):
                        creds = Credentials(
                            token=info.get("token"),
                            refresh_token=info.get("refresh_token"),
                            token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
                            client_id=info.get("client_id"),
                            client_secret=info.get("client_secret"),
                            scopes=info.get("scopes", SCOPES))
                    else:
                        raise ValueError("no access token")
                except Exception:
                    self.last_error = (f"token load failed: {e}. "
                                       "Reconnect via the G button to grant offline access.")
                    return None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self.token_path.write_text(creds.to_json(), encoding="utf-8")
            except Exception as e:
                self.last_error = f"token refresh failed: {e}"
                return None
        return creds if (creds and creds.valid) else None

    async def connect(self, force: bool = False) -> dict:
        """Sign in once. With a valid token that already grants every scope this
        just reports success (the caller re-syncs). A browser consent only runs
        on first connect, when a scope was added, or with force=True."""
        if not self.credentials_present():
            return {"ok": False, "error": "credentials.json not found — see google_sync.py docstring"}
        if not force and self._scopes_ok() and self._load_creds():
            self.connected = True
            return {"ok": True, "reused": True}
        # drop any stale token so re-consent issues a fresh one WITH a refresh_token
        try:
            self.token_path.unlink(missing_ok=True)
        except Exception:
            pass

        def _free_port(candidates):
            import socket
            for p in candidates:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.bind(("127.0.0.1", p))
                    s.close()
                    return p
                except OSError:
                    continue
            return None

        def _flow():
            import json
            from google_auth_oauthlib.flow import InstalledAppFlow
            cfg = json.loads(self.credentials_path.read_text(encoding="utf-8"))
            if "web" in cfg:
                # "web"-type clients need an EXACT pre-registered redirect URI.
                # Scan a small pinned range; register these in Google console:
                #   http://localhost:8766/   http://localhost:8767/   http://localhost:8768/
                port = _free_port([8766, 8767, 8768])
                if port is None:
                    raise RuntimeError(
                        "ports 8766-8768 all busy — restart JARVIS to clear a stuck flow")
            else:
                port = 0   # Desktop clients accept any localhost port
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path), SCOPES)
            # access_type=offline + prompt=consent → Google issues a refresh_token
            # (without these, web clients return a token with no refresh_token and
            # every later load fails with "missing fields refresh_token").
            creds = flow.run_local_server(port=port, open_browser=True,
                                          authorization_prompt_message="",
                                          access_type="offline", prompt="consent",
                                          include_granted_scopes="true",
                                          timeout_seconds=180)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
            return True

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _flow)
            self.connected = True
            self.last_error = None
            if self.hub:
                await self.hub.broadcast({"type": "log", "level": "info",
                                          "msg": "Google Calendar connected — syncing real events"})
            return {"ok": True}
        except Exception as e:
            self.last_error = str(e)
            return {"ok": False, "error": str(e)}

    # ── sync ──────────────────────────────────────────────────────
    async def sync(self) -> list[dict] | None:
        """Fetch upcoming events (next 7 days). Returns None when not connected."""
        creds = self._load_creds()
        if not creds:
            self.connected = False
            return None

        def _fetch():
            from googleapiclient.discovery import build
            svc = build("calendar", "v3", credentials=creds,
                        cache_discovery=False)
            return fetch_all_calendars(svc)

        try:
            loop = asyncio.get_running_loop()
            per_cal = await loop.run_in_executor(None, _fetch)
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            if self.hub:
                await self.hub.broadcast({"type": "log", "level": "warn",
                                          "msg": f"calendar sync failed: {e}"})
            return None

        events = merge_events(per_cal)
        self.calendars = [name for name, _ in per_cal]

        self.connected = True
        self.last_sync = time.time()
        self.last_error = None
        self.event_count = len(events)
        return events

    # ── Gmail (same OAuth, readonly) ──────────────────────────────
    async def fetch_emails(self, max_results: int = 8) -> list[dict] | None:
        """Recent inbox mail (last 3 days). Returns None when not connected."""
        creds = self._load_creds()
        if not creds:
            return None

        def _fetch():
            from googleapiclient.discovery import build
            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
            try:
                self.account = svc.users().getProfile(userId="me").execute().get("emailAddress")
            except Exception:
                pass
            resp = svc.users().messages().list(
                userId="me", q="in:inbox newer_than:3d",
                maxResults=max_results).execute()
            out = []
            for m in resp.get("messages", [])[:max_results]:
                msg = svc.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From", "Subject"]).execute()
                headers = {h["name"].lower(): h["value"]
                           for h in msg.get("payload", {}).get("headers", [])}
                labels = msg.get("labelIds", []) or []
                sender = headers.get("from", "unknown")
                if "<" in sender:
                    sender = sender.split("<", 1)[1].rstrip(">")
                out.append({
                    "sender": sender[:80],
                    "subject": (headers.get("subject") or "(no subject)")[:140],
                    "snippet": (msg.get("snippet") or "")[:160],
                    "received_ms": int(msg.get("internalDate", "0")),
                    "priority": "priority" if ("IMPORTANT" in labels
                                               or "STARRED" in labels) else "normal",
                    "unread": "UNREAD" in labels,
                })
            return out

        try:
            loop = asyncio.get_running_loop()
            mails = await loop.run_in_executor(None, _fetch)
            return mails
        except Exception as e:
            self.last_error = f"gmail: {e}"
            if self.hub:
                await self.hub.broadcast({"type": "log", "level": "warn",
                                          "msg": f"gmail sync failed: {e}"})
            return None

    # ── Google Tasks (same OAuth, readonly) ───────────────────────
    async def fetch_tasks(self, max_results: int = 20) -> list[dict] | None:
        """Open tasks across all task lists. None when not connected or when the
        Tasks API/scope isn't available (the reason is kept in tasks_error)."""
        creds = self._load_creds()
        if not creds:
            return None

        def _fetch():
            from googleapiclient.discovery import build
            svc = build("tasks", "v1", credentials=creds, cache_discovery=False)
            return fetch_open_tasks(svc, max_results)

        try:
            loop = asyncio.get_running_loop()
            tasks = await loop.run_in_executor(None, _fetch)
            self.task_count = len(tasks)
            self.tasks_error = None
            return tasks
        except Exception as e:
            msg = str(e)
            if "insufficient" in msg.lower() or "scope" in msg.lower():
                msg = "Tasks not granted yet — click G to approve the new permission"
            elif "has not been used" in msg or "disabled" in msg:
                msg = "enable the Google Tasks API in your Cloud project"
            self.tasks_error = msg[:200]
            return None


def fetch_all_calendars(svc) -> list[tuple[str, list[dict]]]:
    """Events from every calendar the user shows in Google Calendar (primary +
    shared/work/team), for the next CAL_WINDOW_DAYS days."""
    now = datetime.utcnow()
    tmin = (now - timedelta(hours=1)).isoformat() + "Z"
    tmax = (now + timedelta(days=CAL_WINDOW_DAYS)).isoformat() + "Z"
    cals = svc.calendarList().list(minAccessRole="reader").execute().get("items", [])
    chosen = [c for c in cals if c.get("primary") or
              (c.get("selected") and not c.get("hidden"))][:MAX_CALENDARS]
    if not chosen:
        chosen = [{"id": "primary", "primary": True}]
    out = []
    for c in chosen:
        try:
            items = svc.events().list(
                calendarId=c["id"], timeMin=tmin, timeMax=tmax,
                singleEvents=True, orderBy="startTime",
                maxResults=MAX_EVENTS_PER_CAL,
            ).execute().get("items", [])
        except Exception:
            continue          # one unreadable shared calendar must not sink the sync
        name = "Primary" if c.get("primary") else (
            c.get("summaryOverride") or c.get("summary") or c["id"])
        out.append((name, items))
    return out


def fetch_open_tasks(svc, max_results: int = 20) -> list[dict]:
    """Open (not completed) tasks from every task list, soonest due first."""
    out = []
    for tl in svc.tasklists().list(maxResults=20).execute().get("items", []):
        items = svc.tasks().list(tasklist=tl["id"], showCompleted=False,
                                 maxResults=max_results).execute().get("items", [])
        for t in items:
            if t.get("status") == "completed" or not (t.get("title") or "").strip():
                continue
            due = None
            if t.get("due"):
                try:
                    due = datetime.fromisoformat(t["due"].replace("Z", "+00:00")).date().isoformat()
                except Exception:
                    due = None
            out.append({"title": t["title"][:140], "list": tl.get("title", ""),
                        "due": due, "notes": (t.get("notes") or "")[:160]})
    out.sort(key=lambda t: (t["due"] is None, t["due"] or ""))
    return out[:max_results]
