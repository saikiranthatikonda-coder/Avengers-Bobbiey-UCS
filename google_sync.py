"""Google Calendar sync engine.

Setup (one-time, ~5 min):
  1. console.cloud.google.com → create project → enable "Google Calendar API"
  2. OAuth consent screen → External → add yourself as test user
  3. Credentials → Create OAuth client ID → Desktop app → download JSON
  4. Save as  C:\\Users\\sai\\Desktop\\jarvis\\credentials.json
  5. POST /api/calendar/connect (or click CONNECT GOOGLE in the dashboard)
     → browser opens → approve → token.json saved → real events flow.

Falls back silently when credentials are absent; the Agenda keeps mock data.
"""

import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

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
        }

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
                creds = Credentials.from_authorized_user_file(
                    str(self.token_path), SCOPES)
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

    async def connect(self) -> dict:
        """Run the OAuth installed-app flow (opens a browser on this machine)."""
        if not self.credentials_present():
            return {"ok": False, "error": "credentials.json not found — see google_sync.py docstring"}
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
            now = datetime.utcnow()
            resp = svc.events().list(
                calendarId="primary",
                timeMin=(now - timedelta(hours=1)).isoformat() + "Z",
                timeMax=(now + timedelta(days=7)).isoformat() + "Z",
                singleEvents=True, orderBy="startTime", maxResults=25,
            ).execute()
            return resp.get("items", [])

        try:
            loop = asyncio.get_running_loop()
            items = await loop.run_in_executor(None, _fetch)
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            if self.hub:
                await self.hub.broadcast({"type": "log", "level": "warn",
                                          "msg": f"calendar sync failed: {e}"})
            return None

        events: list[dict] = []
        for it in items:
            start_raw = (it.get("start") or {}).get("dateTime") \
                        or (it.get("start") or {}).get("date")
            end_raw = (it.get("end") or {}).get("dateTime") \
                      or (it.get("end") or {}).get("date")
            if not start_raw:
                continue
            try:
                start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                start = start.astimezone().replace(tzinfo=None)
                if end_raw:
                    end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    end = end.astimezone().replace(tzinfo=None)
                    duration = max(5, int((end - start).total_seconds() // 60))
                else:
                    duration = 30
            except Exception:
                continue
            title = it.get("summary") or "(untitled)"
            attendees = [a.get("email", "") for a in (it.get("attendees") or [])
                         if not a.get("self")][:5]
            low = title.lower()
            priority = "high" if (len(attendees) >= 3
                                  or any(w in low for w in HIGH_PRIORITY_WORDS)) else "normal"
            events.append({
                "title": title, "start": start, "duration_min": duration,
                "attendees": attendees,
                "location": it.get("location") or
                            (it.get("hangoutLink") or ""),
                "priority": priority,
            })

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
