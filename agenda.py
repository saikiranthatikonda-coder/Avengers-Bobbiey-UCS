"""Calendar + Inbox with mock data and voice-reminder logic.

The data is synthetic so you can demo the full pipeline (UI cards + JARVIS
voice alerts) without setting up Google OAuth. To wire real Gmail/Calendar:
  1. Create a Google Cloud project, enable Gmail + Calendar APIs.
  2. Create OAuth Desktop credentials, drop credentials.json next to this file.
  3. Replace `_seed()` with calls to the Google Python SDK
     (`google-api-python-client`, `google-auth-oauthlib`).
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class CalendarEvent:
    title: str
    start: datetime
    duration_min: int
    attendees: list[str] = field(default_factory=list)
    location: str = ""
    priority: str = "normal"  # "high" | "normal"
    notified_10: bool = False
    notified_1: bool = False

    def minutes_until(self) -> float:
        return (self.start - datetime.now()).total_seconds() / 60.0


@dataclass
class Email:
    subject: str
    sender: str
    received: datetime
    priority: str = "normal"  # "priority" | "normal"
    snippet: str = ""
    notified: bool = False


class Agenda:
    def __init__(self, hub=None) -> None:
        self.hub = hub
        # REAL DATA ONLY: starts empty; populated by Google Calendar + Gmail
        # sync (google_sync.py). No mock/seed data anywhere.
        self.events: list[CalendarEvent] = []
        self.emails: list[Email] = []
        self.source = "disconnected"   # "disconnected" | "google"

    # ── real-calendar ingestion (Google sync) ────────────────────
    def set_events(self, raw: list[dict]) -> None:
        """Replace events with synced calendar data, preserving reminder flags
        so reconnects/syncs don't re-fire notifications."""
        old_flags = {(e.title, e.start.isoformat()): (e.notified_10, e.notified_1)
                     for e in self.events}
        new_events: list[CalendarEvent] = []
        for r in raw:
            ev = CalendarEvent(
                title=r["title"], start=r["start"],
                duration_min=r.get("duration_min", 30),
                attendees=r.get("attendees") or [],
                location=r.get("location") or "",
                priority=r.get("priority", "normal"),
            )
            flags = old_flags.get((ev.title, ev.start.isoformat()))
            if flags:
                ev.notified_10, ev.notified_1 = flags
            new_events.append(ev)
        self.events = new_events
        self.source = "google"

    # ── calendar intelligence ─────────────────────────────────────
    def intelligence(self) -> dict:
        now = datetime.now()
        today = [e for e in self.events
                 if e.start.date() == now.date()
                 and e.start + timedelta(minutes=e.duration_min) > now]
        today.sort(key=lambda e: e.start)

        total_min = sum(e.duration_min for e in today)
        # conflicts: overlapping pairs
        conflicts = 0
        for i in range(len(today) - 1):
            if today[i + 1].start < today[i].start + timedelta(minutes=today[i].duration_min):
                conflicts += 1
        # largest free block between now and 19:00
        day_end = now.replace(hour=19, minute=0, second=0, microsecond=0)
        cursor, largest_block = now, 0
        for e in today:
            if e.start > cursor:
                largest_block = max(largest_block,
                                    int((e.start - cursor).total_seconds() // 60))
            cursor = max(cursor, e.start + timedelta(minutes=e.duration_min))
        if day_end > cursor:
            largest_block = max(largest_block,
                                int((day_end - cursor).total_seconds() // 60))
        # readiness: time + prep margin before the next meeting
        nxt = today[0] if today else None
        readiness = 100
        if nxt:
            mins = nxt.minutes_until()
            if mins < 5:    readiness = 40
            elif mins < 15: readiness = 60
            elif mins < 45: readiness = 80
            if nxt.priority == "high" and mins < 60:
                readiness -= 15
            if conflicts:
                readiness -= 10 * conflicts
            readiness = max(10, min(100, readiness))
        density = ("heavy" if total_min > 240 else
                   "moderate" if total_min > 120 else "light")
        return {
            "meetings_today": len(today),
            "meeting_minutes": total_min,
            "density": density,
            "conflicts": conflicts,
            "largest_free_block_min": largest_block,
            "next_title": nxt.title if nxt else None,
            "next_in_min": round(nxt.minutes_until(), 1) if nxt else None,
            "readiness": readiness,
            "source": self.source,
        }

    # ── real-Gmail ingestion ──────────────────────────────────────
    def set_emails(self, raw: list[dict]) -> None:
        """Replace inbox with synced Gmail data, preserving notified flags so
        re-syncs don't re-announce the same priority mail."""
        old_notified = {(m.sender, m.subject) for m in self.emails if m.notified}
        new_mails: list[Email] = []
        for r in raw:
            em = Email(
                subject=r.get("subject") or "(no subject)",
                sender=r.get("sender") or "unknown",
                received=datetime.fromtimestamp((r.get("received_ms") or 0) / 1000)
                         if r.get("received_ms") else datetime.now(),
                priority=r.get("priority", "normal"),
                snippet=r.get("snippet") or "",
            )
            if (em.sender, em.subject) in old_notified:
                em.notified = True
            new_mails.append(em)
        self.emails = new_mails
        self.source = "google"

    def snapshot(self) -> dict:
        now = datetime.now()
        events = []
        for e in self.events:
            if e.start + timedelta(minutes=e.duration_min) <= now:
                continue
            events.append({
                "title": e.title,
                "start_ts": e.start.timestamp(),
                "start_iso": e.start.isoformat(),
                "minutes_until": round(e.minutes_until(), 1),
                "duration_min": e.duration_min,
                "attendees": e.attendees,
                "location": e.location,
                "priority": e.priority,
            })
        events.sort(key=lambda x: x["start_ts"])
        emails = sorted(self.emails, key=lambda m: m.received, reverse=True)
        return {
            "events": events[:6],
            "emails": [{
                "subject": m.subject,
                "sender": m.sender,
                "snippet": m.snippet,
                "ts": m.received.timestamp(),
                "priority": m.priority,
            } for m in emails][:6],
            "priority_unread": sum(1 for m in self.emails if m.priority == "priority"),
            "events_today": sum(
                1 for e in self.events
                if e.start.date() == now.date()
                and e.start > now
            ),
            "source": self.source,
            "intel": self.intelligence(),
        }

    async def check_reminders(self, tts=None) -> None:
        """Fire voice reminders for upcoming meetings and new priority emails."""
        # ── meetings ────────────────────────────────────────
        for ev in self.events:
            m = ev.minutes_until()
            if not ev.notified_10 and 9.0 <= m <= 10.5:
                ev.notified_10 = True
                msg = (
                    f"Sir, you have {ev.title} in 10 minutes"
                    + (f" with {', '.join(ev.attendees[:2])}." if ev.attendees else ".")
                )
                if self.hub:
                    await self.hub.broadcast({
                        "type": "agenda-alert",
                        "event": ev.title, "in_minutes": 10, "msg": msg,
                    })
                    await self.hub.broadcast({
                        "type": "alert", "severity": "warning",
                        "title": f"{ev.title} — in 10 minutes",
                        "detail": msg, "source": "calendar",
                        "action": "Wrap the current task and prepare.",
                    })
                if tts:
                    await tts.say(msg)
            elif not ev.notified_1 and -0.5 <= m <= 1.2:
                ev.notified_1 = True
                msg = f"Reminder: {ev.title} starts now."
                if self.hub:
                    await self.hub.broadcast({
                        "type": "agenda-alert",
                        "event": ev.title, "in_minutes": 0, "msg": msg,
                    })
                    await self.hub.broadcast({
                        "type": "alert",
                        "severity": "critical" if ev.priority == "high" else "warning",
                        "title": f"{ev.title} — starting now",
                        "detail": msg, "source": "calendar",
                        "action": "Join the meeting.",
                    })
                if tts:
                    await tts.say(msg)

        # ── priority emails (only recent ones) ──────────────
        now = datetime.now()
        for em in self.emails:
            if em.priority != "priority" or em.notified:
                continue
            age_min = (now - em.received).total_seconds() / 60.0
            if age_min < 5:
                em.notified = True
                msg = f"New priority email from {em.sender.split('@')[0]}: {em.subject}."
                if self.hub:
                    await self.hub.broadcast({
                        "type": "inbox-alert",
                        "from": em.sender, "subject": em.subject, "msg": msg,
                    })
                if tts:
                    await tts.say(msg)
            else:
                em.notified = True  # too old to announce; mark to skip future cycles
