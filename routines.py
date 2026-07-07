"""Autonomous routines.

All 8 Avengers speak on a 3-minute round-robin, staggered ~22.5 s apart so the
user hears one agent talk every ~22 s without overlap. A separate silent
anomaly-alert job runs every 60 s for Hawkeye so the dashboard still flags
emergencies between speaking turns.
"""

from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


# (agent_key, initial_delay_seconds). Each agent fires every 180 s.
SPEECH_ROTATION: list[tuple[str, float]] = [
    ("jarvis",   2.0),
    ("stark",   24.5),
    ("captain", 47.0),
    ("widow",   69.5),
    ("hulk",    92.0),
    ("vision", 114.5),
    ("thor",   137.0),
    ("hawkeye", 159.5),
]

CYCLE_SECONDS = 180


def schedule_all(team, news, sysmon, hub, agenda=None, tts=None, insights=None,
                 threats=None, gcal=None, memory=None) -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    now = datetime.now()

    # ── round-robin voiced ticks ─────────────────────────────────
    for key, delay_s in SPEECH_ROTATION:
        agent = team.get(key)
        if not agent:
            continue
        sched.add_job(
            agent.tick,
            IntervalTrigger(seconds=CYCLE_SECONDS),
            next_run_time=now + timedelta(seconds=delay_s),
            id=f"tick-{key}",
            max_instances=1,
            coalesce=True,
        )

    # ── shared services ──────────────────────────────────────────
    async def pull_news():
        await news.fetch_top()

    async def hawkeye_anomaly_alert():
        m = sysmon.latest
        if not m:
            return
        flags = []
        if m["cpu"] > 90:  flags.append(f"CPU {m['cpu']:.0f}%")
        if m["mem"] > 90:  flags.append(f"MEM {m['mem']:.0f}%")
        if m["disk"] > 95: flags.append(f"DISK {m['disk']:.0f}%")
        if flags:
            msg = "Vitals critical: " + " / ".join(flags)
            await team["hawkeye"]._emit("alert", msg=msg, level="warn")
            await hub.broadcast({"type": "log", "level": "warn", "msg": msg})

    sched.add_job(pull_news, IntervalTrigger(minutes=15),
                  next_run_time=now + timedelta(seconds=8))
    sched.add_job(hawkeye_anomaly_alert, IntervalTrigger(seconds=30))

    if agenda is not None:
        async def agenda_check():
            await agenda.check_reminders(tts=tts)
        sched.add_job(agenda_check, IntervalTrigger(seconds=30),
                      next_run_time=now + timedelta(seconds=15),
                      max_instances=1, coalesce=True)

    if insights is not None:
        async def insights_tick():
            await insights.generate()
        sched.add_job(insights_tick, IntervalTrigger(seconds=90),
                      next_run_time=now + timedelta(seconds=20),
                      max_instances=1, coalesce=True)

        # daily executive briefing at 08:30 local
        async def daily_briefing():
            await insights.briefing(speak=True)
        sched.add_job(daily_briefing, CronTrigger(hour=8, minute=30),
                      max_instances=1, coalesce=True)

    if threats is not None:
        async def threat_tick():
            await threats.tick()
        sched.add_job(threat_tick, IntervalTrigger(seconds=20),
                      next_run_time=now + timedelta(seconds=8),
                      max_instances=1, coalesce=True)

    if memory is not None:
        # behavioural clock: log an active minute whenever the operator is present
        async def memory_activity_tick():
            try:
                if insights and insights.presence.get("state") in ("active", "idle"):
                    memory.add_active_minute()
            except Exception:
                pass
        sched.add_job(memory_activity_tick, IntervalTrigger(seconds=60),
                      max_instances=1, coalesce=True)

        # distill recent observations into stable long-term facts
        async def memory_synthesis():
            await memory.synthesize()
        sched.add_job(memory_synthesis, IntervalTrigger(minutes=30),
                      next_run_time=now + timedelta(minutes=10),
                      max_instances=1, coalesce=True)

    if gcal is not None and agenda is not None:
        async def calendar_sync():
            if not gcal.token_present():
                return            # not connected yet — keep mock data
            events = await gcal.sync()
            if events is not None:
                agenda.set_events(events)
                await hub.broadcast({
                    "type": "log", "level": "info",
                    "msg": f"calendar synced — {len(events)} events from Google",
                })
        sched.add_job(calendar_sync, IntervalTrigger(minutes=5),
                      next_run_time=now + timedelta(seconds=6),
                      max_instances=1, coalesce=True)

    return sched
