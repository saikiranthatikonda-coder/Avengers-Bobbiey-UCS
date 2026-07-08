"""Multi-Agent Orchestration Layer — Roadmap Phase 2.

Jarvis coordinates the specialist agents instead of each acting alone:
  · a shared context BLACKBOARD every agent reads from (one truth, no drift)
  · DIRECTIVES derived from real conditions (telemetry, threats, calendar,
    inbox, presence, focus state) — never synthetic tasks
  · DELEGATION: each directive is routed to the specialist agent for its
    domain; the agent's live card shows the assignment
  · PRIORITY RESOLUTION: higher-priority directives preempt lower ones; a
    preempted directive re-queues instead of being lost
  · auto-resolution: when the underlying condition clears, the directive is
    marked done and the agent returns to station

Broadcasts {type: "orch", ...} summaries so the dashboard stays live.
"""

import time
import uuid
from collections import deque
from datetime import datetime

# priority: 1 = critical … 4 = routine
P_CRIT, P_HIGH, P_NORM, P_LOW = 1, 2, 3, 4

PRIORITY_LABEL = {1: "CRITICAL", 2: "HIGH", 3: "NORMAL", 4: "ROUTINE"}


class Orchestrator:
    def __init__(self, hub, team, sysmon=None, threats=None, agenda=None,
                 insights=None, memory=None, productivity=None,
                 team_memory=None) -> None:
        self.hub = hub
        self.team = team
        self.sysmon = sysmon
        self.threats = threats
        self.agenda = agenda
        self.insights = insights
        self.memory = memory
        self.productivity = productivity
        self.team_memory = team_memory        # cross-agent shared memory
        self.blackboard: dict = {}
        self.active: dict[str, dict] = {}      # key → directive
        self.recent_done: deque = deque(maxlen=12)
        self.delegations_total = 0
        self.preemptions_total = 0
        self.collabs_total = 0
        self.cycles = 0
        self._patrol_idx = 0

    # agent-to-agent collaboration: who consults whom, per domain
    CONSULT_PARTNER = {
        "widow": "vision",     # intel verifies with observation
        "stark": "hawkeye",    # engineering checks the vitals
        "hulk": "stark",       # compute consults engineering
        "captain": "thor",     # schedule aligns with announcements
        "hawkeye": "widow",    # vitals cross-checks intel
        "vision": "widow",     # synthesis pulls research
        "thor": "captain",
    }

    # ── shared context blackboard ─────────────────────────────────
    def _update_blackboard(self) -> dict:
        m = (self.sysmon.latest if self.sysmon else {}) or {}
        thr = self.threats.snapshot() if self.threats else {}
        ag = self.agenda.snapshot() if self.agenda else {}
        intel = ag.get("intel") or {}
        pres = (self.insights.presence if self.insights else {}) or {}
        events = ag.get("events") or []
        nxt = events[0] if events else None
        bb = {
            "ts": time.time(),
            "cpu": m.get("cpu", 0), "mem": m.get("mem", 0), "disk": m.get("disk", 0),
            "risk_score": thr.get("risk_score", 0),
            "risk_level": thr.get("level", "secure"),
            "open_incidents": len([e for e in (thr.get("events") or [])
                                   if e.get("stage") != "resolved"]),
            "next_meeting": (nxt or {}).get("title"),
            "next_meeting_min": (nxt or {}).get("minutes_until"),
            "conflicts": intel.get("conflicts", 0),
            "priority_mail": ag.get("priority_unread", 0),
            "presence": pres.get("state", "offline"),
            "hour": datetime.now().hour,
            "focus_active": bool(self.productivity and
                                 self.productivity.state.get("focus_active")),
            "guest_sightings": (self.memory.data["patterns"].get("guest_sightings", 0)
                                if self.memory else 0),
        }
        self.blackboard = bb
        return bb

    # ── directive generation from REAL conditions ─────────────────
    def _desired(self, bb: dict) -> list[dict]:
        """Each entry: condition key, active?, priority, agent, title, detail."""
        mins = bb.get("next_meeting_min")
        want = [
            ("risk-high", bb["risk_score"] >= 50, P_CRIT, "widow",
             f"Contain elevated risk — {bb['risk_score']}/100",
             f"{bb['open_incidents']} open incidents on the board"),
            ("risk-elevated", 20 <= bb["risk_score"] < 50, P_HIGH, "widow",
             f"Investigate risk drift — {bb['risk_score']}/100",
             "Correlating incident feed with telemetry"),
            ("cpu", bb["cpu"] > 85, P_HIGH, "stark",
             f"Diagnose CPU pressure — {bb['cpu']:.0f}%",
             "Tracing the offending process tree"),
            ("mem", bb["mem"] > 88, P_HIGH, "hulk",
             f"Reclaim memory headroom — {bb['mem']:.0f}%",
             "Ranking working sets for eviction"),
            ("disk", bb["disk"] > 92, P_CRIT, "stark",
             f"Disk critical — {bb['disk']:.0f}% full",
             "Identifying largest reclaimable artifacts"),
            ("meeting-prep", mins is not None and 0 < mins <= 30, P_HIGH, "captain",
             f"Prep briefing: \"{(bb.get('next_meeting') or '')[:40]}\" in {mins:.0f}m",
             "Compiling agenda context and attendee notes"),
            ("conflict", bb["conflicts"] > 0, P_NORM, "captain",
             f"Resolve {bb['conflicts']} calendar conflict(s)",
             "Proposing a reshuffle for the overlap"),
            ("mail", bb["priority_mail"] >= 2, P_NORM, "thor",
             f"Triage {bb['priority_mail']} priority messages",
             "Ranking inbox by sender and urgency"),
            ("guest", bb["guest_sightings"] > 0 and bb["presence"] == "active",
             P_NORM, "vision",
             "Review guest sightings log",
             f"{bb['guest_sightings']} unrecognized visitor(s) on record"),
            ("away-watch", bb["presence"] == "away" and 9 <= bb["hour"] < 19,
             P_LOW, "hawkeye",
             "Hold overwatch — operator away",
             "Perimeter and vitals under autonomous watch"),
            ("focus-guard", bb["focus_active"], P_NORM, "hawkeye",
             "Guard the focus block",
             "Suppressing non-critical interruptions"),
        ]
        out = [{"key": k, "on": on, "priority": p, "agent": a,
                "title": t, "detail": d} for k, on, p, a, t, d in want]
        return out

    _PATROLS = [
        ("stark",   "Sweep system baseline",       "Drift check on CPU/memory/disk curves"),
        ("widow",   "Scan external feeds",         "Watching headlines for security signals"),
        ("hawkeye", "Verify telemetry channels",   "Heartbeat check across all monitors"),
        ("vision",  "Consolidate observation log", "Folding recent sightings into memory"),
        ("hulk",    "Audit compute allocation",    "Reviewing top consumers for waste"),
        ("captain", "Review the day plan",         "Cross-checking agenda against readiness"),
        ("thor",    "Sample the news wire",        "Listening for announcements of note"),
    ]

    # ── delegation + lifecycle ────────────────────────────────────
    async def tick(self) -> None:
        self.cycles += 1
        bb = self._update_blackboard()
        desired = self._desired(bb)
        now = time.time()

        # resolve directives whose condition cleared (or patrols that aged out)
        on_keys = {d["key"] for d in desired if d["on"]}
        for key in list(self.active.keys()):
            d = self.active[key]
            expired = d["patrol"] and now - d["ts"] > 150
            if (not d["patrol"] and key not in on_keys) or expired:
                d["status"] = "done"
                d["done_ts"] = now
                self.recent_done.appendleft(d)
                del self.active[key]
                agent = self.team.get(d["agent"])
                if agent is not None and agent.current_task == d["title"][:60]:
                    agent.actions_completed += 1
                    agent.confidence = min(99, agent.confidence + 1)
                    await agent.set_status("idle")
                await self.hub.broadcast({
                    "type": "log", "level": "info",
                    "msg": f"orchestrator: ✓ {d['agent']} completed \"{d['title'][:60]}\"",
                })
                # completed real directives become team knowledge
                if self.team_memory is not None and not d["patrol"]:
                    await self.team_memory.write(
                        d["agent"], "directive",
                        f"completed \"{d['title']}\" — {d.get('detail', '')}")

        # raise new directives (priority resolution: replace a lower-priority
        # active directive on the same agent — the old one re-queues next tick)
        for d in sorted([x for x in desired if x["on"]], key=lambda x: x["priority"]):
            if d["key"] in self.active:
                self.active[d["key"]]["ts_seen"] = now
                continue
            holding = [a for a in self.active.values() if a["agent"] == d["agent"]]
            if holding:
                cur = holding[0]
                if cur["priority"] <= d["priority"]:
                    continue                     # busy with equal/higher priority
                # preempt the lower-priority directive
                cur["status"] = "preempted"
                self.recent_done.appendleft(cur)
                del self.active[cur["key"]]
                self.preemptions_total += 1
                await self.hub.broadcast({
                    "type": "log", "level": "warn",
                    "msg": (f"orchestrator: {d['agent']} preempted — "
                            f"\"{d['title'][:48]}\" overrides \"{cur['title'][:36]}\""),
                })
            await self._assign(d, now)

        # idle-station patrol: when nothing real is active, one agent at a time
        # runs the routine monitoring pass that the scheduler actually performs
        if not self.active:
            agent_key, title, detail = self._PATROLS[self._patrol_idx % len(self._PATROLS)]
            self._patrol_idx += 1
            await self._assign({"key": f"patrol-{self._patrol_idx}",
                                "priority": P_LOW, "agent": agent_key,
                                "title": title, "detail": detail}, now,
                               patrol=True, quiet=True)

        # Jarvis coordinates
        jarvis = self.team.get("jarvis")
        if jarvis is not None and jarvis.status != "thinking":
            n = len(self.active)
            jarvis.current_task = (f"Coordinating {n} directive(s)" if n
                                   else "Monitoring — all stations nominal")[:60]

        await self.hub.broadcast({"type": "orch", **self.summary()})

    async def _assign(self, d: dict, now: float, patrol: bool = False,
                      quiet: bool = False) -> None:
        directive = {
            "id": uuid.uuid4().hex[:8], "key": d["key"], "ts": now, "ts_seen": now,
            "priority": d["priority"], "agent": d["agent"],
            "title": d["title"][:90], "detail": d.get("detail", "")[:120],
            "status": "active", "patrol": patrol,
        }
        self.active[d["key"]] = directive
        self.delegations_total += 1
        agent = self.team.get(d["agent"])
        if agent is not None and agent.status != "thinking":
            await agent.set_status("working", note=d["title"])
        if not quiet:
            await self.hub.broadcast({
                "type": "log", "level": "info",
                "msg": (f"orchestrator: → {d['agent']} assigned "
                        f"[{PRIORITY_LABEL[d['priority']]}] \"{d['title'][:60]}\""),
            })
        # agent-to-agent collaboration: on urgent directives the assignee
        # pulls a consult from its partner specialist; the partner's live
        # domain read goes into shared team memory for everyone
        if not patrol and d["priority"] <= P_HIGH:
            await self._consult(directive)

    async def _consult(self, directive: dict) -> None:
        partner_key = self.CONSULT_PARTNER.get(directive["agent"])
        partner = self.team.get(partner_key) if partner_key else None
        if partner is None:
            return
        note = ""
        try:
            if partner.local_brain is not None:
                note = partner.local_brain.for_agent(partner_key)   # real live read
        except Exception:
            note = ""
        if not note:
            note = f"standing by on the {directive['title'][:40]} tasking"
        self.collabs_total += 1
        directive["consult"] = partner_key
        line = (f"{partner_key} → {directive['agent']} on "
                f"\"{directive['title'][:48]}\": {note}")
        partner.history.append({"q": f"[consult:{directive['agent']}]",
                                "a": note, "ts": time.time()})
        if self.team_memory is not None:
            await self.team_memory.write(partner_key, "consult", line)
        await self.hub.broadcast({
            "type": "log", "level": "info",
            "msg": f"collab: {partner_key.upper()} ⇄ {directive['agent'].upper()} — {note[:90]}",
        })

    # ── API surface ───────────────────────────────────────────────
    def summary(self) -> dict:
        return {
            "active": len(self.active),
            "delegations_total": self.delegations_total,
            "preemptions_total": self.preemptions_total,
            "collabs_total": self.collabs_total,
            "coordinator": "jarvis",
        }

    def snapshot(self) -> dict:
        acts = sorted(self.active.values(), key=lambda d: (d["priority"], -d["ts"]))
        return {
            **self.summary(),
            "cycles": self.cycles,
            "blackboard": self.blackboard,
            "directives": [
                {k: v for k, v in d.items() if k != "ts_seen"} for d in acts
            ],
            "recent_done": list(self.recent_done)[:8],
            "priority_labels": PRIORITY_LABEL,
        }
