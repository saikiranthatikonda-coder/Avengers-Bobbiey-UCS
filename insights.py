"""JARVIS Insights engine.

Continuously ingests live telemetry (system metrics, agenda, inbox, news,
agent statuses, camera presence) and produces one sharp observation per cycle.

Source priority: local LLM (Ollama etc.) → claude CLI brain → rule templates.
Every insight is broadcast over the hub as {type: "insight", ...} and kept in
a rolling buffer served by /api/insights.
"""

import asyncio
import json
import random
import re
import time
from datetime import datetime

INSIGHT_SYS = (
    "You are JARVIS, the AI core of sir's command center in Hyderabad. "
    "You receive one telemetry snapshot and produce ONE sharp, butler-toned "
    "observation. Reply with STRICT JSON only, no prose around it: "
    '{"insight": "<one sentence>", "recommendation": "<one short action>", '
    '"severity": "info"|"warn"|"critical", "confidence": <int 0-100>}'
)

GREETINGS = [
    "Welcome back, sir.",
    "Good to see you, sir. Systems are nominal.",
    "Operator detected. Resuming full telemetry.",
]


class InsightsEngine:
    def __init__(self, hub, sysmon, news, agenda, team,
                 local_llm=None, brain=None, tts=None, threats=None) -> None:
        self.hub = hub
        self.sysmon = sysmon
        self.news = news
        self.agenda = agenda
        self.team = team
        self.local_llm = local_llm
        self.brain = brain
        self.tts = tts
        self.threats = threats   # ThreatEngine (set after construction is fine)
        self.recent: list[dict] = []
        self.presence = {"state": "offline", "motion": 0.0, "since": time.time()}
        self._last_greet = 0.0
        self._recent_template_keys: list[str] = []
        self.status: dict = {
            "source": "template", "model": None, "last_run": None,
            "latency_ms": None, "state": "idle", "confidence": None,
        }

    # ── telemetry snapshot ────────────────────────────────────────
    def snapshot(self) -> dict:
        m = (self.sysmon.latest if self.sysmon else {}) or {}
        ag = self.agenda.snapshot() if self.agenda else {"events": [], "emails": []}
        events = ag.get("events") or []
        nxt = events[0] if events else None
        busy = sum(1 for a in self.team.values() if a.status != "idle") if self.team else 0
        headlines = [a.get("title") or "" for a in (self.news.recent if self.news else [])][:3]
        intel = ag.get("intel") or {}
        thr = self.threats.snapshot() if self.threats else {}
        return {
            "cpu": m.get("cpu", 0), "mem": m.get("mem", 0), "disk": m.get("disk", 0),
            "net_up": m.get("net_up", 0), "net_down": m.get("net_down", 0),
            "agents_busy": busy, "agents_total": len(self.team or {}),
            "next_meeting": (nxt or {}).get("title"),
            "next_meeting_min": (nxt or {}).get("minutes_until"),
            "priority_mail": ag.get("priority_unread", 0),
            "headlines": headlines,
            "presence": self.presence.get("state", "offline"),
            "presence_minutes": round((time.time() - self.presence.get("since", time.time())) / 60),
            "hour": datetime.now().hour,
            "cal_density": intel.get("density"),
            "cal_conflicts": intel.get("conflicts", 0),
            "cal_focus_block": intel.get("largest_free_block_min"),
            "cal_readiness": intel.get("readiness"),
            "cal_source": intel.get("source", "mock"),
            "risk_score": thr.get("risk_score", 0),
            "risk_level": thr.get("level", "secure"),
            "threat_count": len(thr.get("events") or []),
        }

    def _prompt(self, s: dict) -> str:
        meeting = (
            f'"{s["next_meeting"]}" in {s["next_meeting_min"]:.0f} min'
            if s.get("next_meeting") and s.get("next_meeting_min") is not None
            else "none scheduled"
        )
        heads = "; ".join(h[:70] for h in s["headlines"]) or "none"
        return (
            f"TELEMETRY {datetime.now():%H:%M}: CPU {s['cpu']:.0f}%, MEM {s['mem']:.0f}%, "
            f"DISK {s['disk']:.0f}%, NET up {s['net_up']} / down {s['net_down']} KB/s. "
            f"AGENTS: {s['agents_busy']} busy of {s['agents_total']}. "
            f"OPERATOR PRESENCE: {s['presence']}. NEXT MEETING: {meeting}. "
            f"PRIORITY MAIL: {s['priority_mail']}. HEADLINES: {heads}. "
            "Give the single most useful observation for sir right now."
        )

    # ── parsing ───────────────────────────────────────────────────
    @staticmethod
    def _parse(raw: str) -> dict | None:
        try:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0) if m else raw)
            sev = str(data.get("severity", "info")).lower()
            if sev not in ("info", "warn", "critical"):
                sev = "info"
            conf = data.get("confidence", 70)
            try:
                conf = max(5, min(99, int(conf)))
            except Exception:
                conf = 70
            insight = str(data.get("insight", "")).strip()
            if not insight:
                return None
            return {
                "insight": insight[:280],
                "recommendation": str(data.get("recommendation", "")).strip()[:200],
                "severity": sev,
                "confidence": conf,
            }
        except Exception:
            text = (raw or "").strip()
            if len(text) < 8:
                return None
            return {"insight": text[:280], "recommendation": "",
                    "severity": "info", "confidence": 60}

    # ── rule-based fallback ───────────────────────────────────────
    def _template_insight(self, s: dict) -> dict:
        rules: list[tuple[str, bool, str, str, str, int]] = [
            ("disk", s["disk"] > 92, "critical",
             f"Storage critical at {s['disk']:.0f}% — paging and update failures become likely.",
             "Run a disk cleanup or archive old project folders.", 92),
            ("mem", s["mem"] > 88, "warn",
             f"Memory pressure at {s['mem']:.0f}% — responsiveness will degrade under new loads.",
             "Close idle browser tabs or background apps.", 88),
            ("cpu", s["cpu"] > 85, "warn",
             f"CPU sustained at {s['cpu']:.0f}% — thermal throttling possible on this chassis.",
             "Check Task Manager for the offending process.", 85),
            ("meeting", bool(s.get("next_meeting")) and (s.get("next_meeting_min") or 999) < 30, "info",
             f"\"{s.get('next_meeting')}\" begins in {(s.get('next_meeting_min') or 0):.0f} minutes.",
             "Wrap the current task and review the agenda item.", 90),
            ("mail", s["priority_mail"] >= 2, "info",
             f"{s['priority_mail']} priority emails are awaiting your attention.",
             "Triage the inbox before the next meeting block.", 82),
            ("away", s["presence"] == "away" and s["cpu"] > 40, "info",
             "Workload is running while you are away from the console.",
             "I will continue monitoring and alert you on anomalies.", 75),
            ("night", s["hour"] >= 23 or s["hour"] < 5, "info",
             "It is rather late, sir — overnight monitoring is fully autonomous.",
             "Consider resting; I will hold the fort.", 70),
            ("long-session",
             s["presence"] in ("active", "idle") and s["presence_minutes"] >= 120, "info",
             f"You have been at the console for {s['presence_minutes'] // 60}h {s['presence_minutes'] % 60}m.",
             "A short break would sharpen the next block.", 80),
            ("long-away",
             s["presence"] == "away" and s["presence_minutes"] >= 30
             and 9 <= s["hour"] < 19, "info",
             f"No activity detected for {s['presence_minutes']} minutes.",
             "Systems remain under autonomous watch.", 78),
            ("cal-heavy", s.get("cal_density") == "heavy", "warn",
             f"Heavy meeting load today with conflicts: {s['cal_conflicts']}; "
             f"largest focus block only {s.get('cal_focus_block') or 0} minutes.",
             "Decline or shorten one low-value meeting.", 84),
            ("cal-conflict", s.get("cal_conflicts", 0) > 0, "warn",
             f"{s['cal_conflicts']} scheduling conflict(s) detected on today's calendar.",
             "Resolve the overlap before it collides.", 88),
            ("risk-high", s.get("risk_score", 0) >= 50, "critical",
             f"Operational risk elevated at {s['risk_score']}/100 with {s['threat_count']} incidents.",
             "Open the Threat Intelligence board and triage.", 90),
        ]
        applicable = [r for r in rules if r[1]]
        fresh = [r for r in applicable if r[0] not in self._recent_template_keys]
        pool = fresh or applicable
        if pool:
            key, _, sev, insight, rec, conf = random.choice(pool)
        else:
            key, sev, conf = "nominal", "info", 76
            insight = random.choice([
                f"All systems nominal — CPU {s['cpu']:.0f}%, memory {s['mem']:.0f}%, eight agents on station.",
                "Telemetry steady across the board. No intervention required.",
                f"Quiet cycle: {s['agents_busy']} agents active, perimeter clean, feeds current.",
            ])
            rec = "No action needed."
        self._recent_template_keys = ([key] + self._recent_template_keys)[:2]
        return {"insight": insight, "recommendation": rec,
                "severity": sev, "confidence": conf}

    # ── main cycle ────────────────────────────────────────────────
    async def generate(self) -> dict:
        self.status["state"] = "analyzing"
        await self.hub.broadcast({"type": "ai-state", "state": "analyzing"})
        s = self.snapshot()

        data, source, model = None, None, None
        if self.local_llm and self.local_llm.available:
            raw = await self.local_llm.chat(self._prompt(s), system=INSIGHT_SYS, timeout=45)
            if raw:
                data = self._parse(raw)
                source, model = "local-llm", self.local_llm.model
        if data is None and self.brain is not None and getattr(self.brain, "mode", "") == "llm":
            raw = await self.brain.think(
                self._prompt(s) + " Reply with STRICT JSON only as specified.",
                system=INSIGHT_SYS, agent="vision", timeout=60)
            # Reject only error markers ("[brain ...]") — a legitimate reply that
            # happens to start with "[" (e.g. a JSON array) is still parsed.
            if raw and raw.strip() and not raw.lstrip().startswith("[brain"):
                data = self._parse(raw)
                source, model = "claude", "claude-cli"
        if data is None:
            data = self._template_insight(s)
            source, model = "template", "rules"

        item = {**data, "source": source, "ts": time.time()}
        self.recent = ([item] + self.recent)[:12]
        self.status.update(
            source=source, model=model, last_run=time.time(), state="idle",
            latency_ms=(self.local_llm.last_latency_ms
                        if source == "local-llm" and self.local_llm else None),
            confidence=item["confidence"],
        )
        await self.hub.broadcast({"type": "insight", **item})
        await self.hub.broadcast({"type": "ai-state", "state": "idle"})
        return item

    # ── executive briefing ────────────────────────────────────────
    async def briefing(self, speak: bool = True) -> dict:
        """Compile a daily/executive briefing from every input and deliver it."""
        s = self.snapshot()
        meeting = (f"next meeting \"{s['next_meeting']}\" in {s['next_meeting_min']:.0f} min"
                   if s.get("next_meeting") and s.get("next_meeting_min") is not None
                   else "no meetings ahead")
        facts = (
            f"Time {datetime.now():%H:%M}. System: CPU {s['cpu']:.0f}%, MEM {s['mem']:.0f}%, "
            f"DISK {s['disk']:.0f}%. Calendar ({s['cal_source']}): {meeting}, density {s.get('cal_density')}, "
            f"conflicts {s['cal_conflicts']}, focus block {s.get('cal_focus_block') or 0} min, "
            f"readiness {s.get('cal_readiness')}%. Priority mail: {s['priority_mail']}. "
            f"Risk: {s['risk_score']}/100 ({s['risk_level']}), {s['threat_count']} incidents. "
            f"Presence: {s['presence']}. Headlines: "
            + ("; ".join(h[:60] for h in s["headlines"]) or "none")
        )
        text = None
        if self.local_llm and self.local_llm.available:
            text = await self.local_llm.chat(
                "Compose a crisp 3-sentence executive briefing from this snapshot, "
                "JARVIS butler tone, lead with what matters most:\n" + facts,
                system="You are JARVIS delivering a spoken executive briefing. Plain text only.",
                timeout=45)
        if not text and self.brain is not None and getattr(self.brain, "mode", "") == "llm":
            raw = await self.brain.think(
                "Compose a crisp 3-sentence executive briefing, JARVIS tone, "
                "from this snapshot:\n" + facts,
                system="You are JARVIS delivering a spoken executive briefing. Plain text only.",
                agent="captain", timeout=60)
            if raw and not raw.lstrip().startswith("[brain"):
                text = raw
        if not text:
            text = (f"Briefing, sir. Systems at CPU {s['cpu']:.0f} and memory {s['mem']:.0f} percent — "
                    f"{'stable' if s['risk_score'] < 20 else s['risk_level']} posture, "
                    f"risk {s['risk_score']} of 100. Calendar shows "
                    f"{meeting} with {s['cal_conflicts']} conflicts and a "
                    f"{s.get('cal_focus_block') or 0}-minute focus block. "
                    f"{s['priority_mail']} priority messages await your attention.")
        text = text.strip()[:600]
        item = {"insight": text, "recommendation": "", "severity": "info",
                "confidence": 85, "source": "briefing", "ts": time.time()}
        self.recent = ([item] + self.recent)[:12]
        await self.hub.broadcast({"type": "insight", **item})
        await self.hub.broadcast({"type": "digest", "msg": text, "kind": "briefing"})
        if speak and self.tts:
            asyncio.create_task(self.tts.say(text))
        return item

    # ── camera presence ───────────────────────────────────────────
    async def update_presence(self, state: str, motion: float = 0.0) -> dict:
        prev = self.presence.get("state", "offline")
        safe_motion = round(max(0.0, min(100.0, float(motion))), 1)
        self.presence = {"state": state, "motion": safe_motion,
                         "since": time.time()}
        await self.hub.broadcast({
            "type": "presence", "state": state, "motion": self.presence["motion"],
            "prev": prev,
        })
        greeted = False
        if (state == "active" and prev in ("away", "offline", "no-user")
                and time.time() - self._last_greet > 300):
            self._last_greet = time.time()
            greeted = True
            phrase = random.choice(GREETINGS)
            # personalize with the operator's enrolled name when memory knows it
            mem = getattr(self, "memory", None)
            if mem is not None and getattr(mem, "name", None):
                phrase = phrase.replace("sir", mem.name).replace(
                    "Operator detected", f"{mem.name} detected")
            await self.hub.broadcast({
                "type": "log", "level": "info",
                "msg": f"presence: operator arrived — \"{phrase}\"",
            })
            if self.tts:
                asyncio.create_task(self.tts.say(phrase))
        return {"prev": prev, "greeted": greeted}

    # ── command recommendations (permanent panel feed) ────────────
    def recommendations(self, productivity=None, orchestrator=None) -> list[dict]:
        """EVERY currently-applicable recommendation, priority-ordered — the
        commander's standing briefing. Same real signals as insights, but a
        complete list instead of one observation."""
        s = self.snapshot()
        recs: list[dict] = []

        def add(sev, rank, title, detail):
            recs.append({"severity": sev, "rank": rank,
                         "title": title[:90], "detail": detail[:120]})

        if s.get("next_meeting") and s.get("next_meeting_min") is not None:
            m = s["next_meeting_min"]
            if 0 < m <= 20:
                add("critical", 1, f"Prepare: \"{s['next_meeting']}\" in {m:.0f} min",
                    "Wrap the current task and review the agenda item.")
            elif m <= 60:
                add("warn", 3, f"Next: \"{s['next_meeting']}\" in {m:.0f} min",
                    "A prep block now beats a scramble later.")
        if s["disk"] > 92:
            add("critical", 1, f"Storage critical — {s['disk']:.0f}%",
                "Run a cleanup; saves and updates may fail.")
        if s.get("risk_score", 0) >= 50:
            add("critical", 1, f"Threat level HIGH — {s['risk_score']}/100",
                "Open the incident board and acknowledge the queue.")
        elif s.get("risk_score", 0) >= 20:
            add("warn", 2, f"Threat level elevated — {s['risk_score']}/100",
                "Review the response queue when convenient.")
        if s["cpu"] > 85:
            add("warn", 2, f"High CPU — {s['cpu']:.0f}%",
                "Check the top process before it throttles the machine.")
        if s["mem"] > 88:
            add("warn", 2, f"Memory pressure — {s['mem']:.0f}%",
                "Close idle apps or browser tabs.")
        if s["priority_mail"] >= 1:
            add("warn", 3, f"Inbox: {s['priority_mail']} priority message(s)",
                "Triage before the next meeting block.")
        if s.get("cal_conflicts", 0) > 0:
            add("warn", 2, f"{s['cal_conflicts']} calendar conflict(s) today",
                "Resolve the overlap before it collides.")
        if s.get("cal_density") == "heavy" and (s.get("cal_focus_block") or 0) < 60:
            add("info", 4, "Heavy meeting day, thin focus time",
                f"Largest free block only {s.get('cal_focus_block') or 0} min — guard it.")
        if productivity is not None:
            try:
                p = productivity.snapshot()
                if not p["focus_active"] and p["focus_min_today"] < 30 \
                        and 9 <= s["hour"] < 19 and s["presence"] in ("active", "idle"):
                    blk = s.get("cal_focus_block") or 0
                    if blk >= 45:
                        add("info", 4, f"A {blk}-min focus window is open",
                            "Start a deep-work session while it lasts.")
                if p["switches_today"] >= 6:
                    add("info", 4, f"{p['switches_today']} context switches today",
                        "Batch similar tasks to recover momentum.")
            except Exception:
                pass
        if orchestrator is not None:
            try:
                n = orchestrator.summary()["active"]
                if n >= 3:
                    add("info", 4, f"{n} directives in flight",
                        "The agent roster is at elevated tasking.")
            except Exception:
                pass
        if s["presence"] in ("active", "idle") and s["presence_minutes"] >= 120:
            add("info", 4, f"{s['presence_minutes'] // 60}h {s['presence_minutes'] % 60}m at the console",
                "A short break would sharpen the next block.")
        if s["hour"] >= 23 or s["hour"] < 5:
            add("info", 5, "Late hours — autonomous watch is on",
                "Consider resting; I will hold the fort.")
        if not recs:
            add("info", 5, "All clear — no action required",
                f"CPU {s['cpu']:.0f}%, risk {s.get('risk_score', 0)}/100, "
                "calendar under control.")
        recs.sort(key=lambda r: r["rank"])
        return recs[:8]

    # ── consolidated status for /api/aiops ────────────────────────
    def aiops_status(self) -> dict:
        m = (self.sysmon.latest if self.sysmon else {}) or {}
        cpu, mem, disk = m.get("cpu", 0), m.get("mem", 0), m.get("disk", 0)
        readiness = max(0, min(100, round(
            100 - max(0, cpu - 70) * 0.8 - max(0, mem - 80) * 1.2
            - max(0, disk - 85) * 1.6)))
        anomaly = min(100, round(
            max(0, cpu - 85) * 1.5 + max(0, mem - 88) * 2.0
            + max(0, disk - 92) * 2.5))
        return {
            "insights": self.recent[:8],
            "engine": self.status,
            "presence": self.presence,
            "readiness": readiness,
            "anomaly": anomaly,
        }
