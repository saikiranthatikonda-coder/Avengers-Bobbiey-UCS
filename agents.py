import time
from dataclasses import dataclass, field

from brain import Brain
from hub import Hub


@dataclass
class Avenger:
    name: str
    codename: str
    role: str
    color: str
    hub: Hub
    brain: Brain
    system_prompt: str = ""
    speaker: object | None = None       # TTSPlayer or None
    speaks_on_tick: bool = False        # autonomous ticks spoken aloud
    status: str = "idle"
    last_activity: float = 0.0
    history: list[dict] = field(default_factory=list)
    local_brain: object | None = None   # LocalBrain used for cheap periodic ticks
    team_memory: object | None = None   # cross-agent shared memory (Phase 2)
    current_task: str = "—"             # what the agent is doing right now
    task_queue: list = field(default_factory=list)
    confidence: int = 92                # rolling confidence 0-100
    actions_completed: int = 0

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "codename": self.codename,
            "role": self.role,
            "color": self.color,
            "status": self.status,
            "last_activity": self.last_activity,
            "history": self.history[-10:],
            "current_task": self.current_task,
            "queue_depth": len(self.task_queue),
            "confidence": self.confidence,
            "actions_completed": self.actions_completed,
        }

    async def _emit(self, event: str, **kw) -> None:
        await self.hub.broadcast({"type": "agent", "agent": self.name, "event": event, **kw})

    async def set_status(self, status: str, note: str = "") -> None:
        self.status = status
        self.last_activity = time.time()
        if status == "idle":
            self.current_task = "—"
        elif note:
            self.current_task = note[:60]
        await self._emit("status", status=status, note=note,
                         task=self.current_task, confidence=self.confidence)

    async def handle(self, prompt: str) -> str:
        await self.set_status("thinking", note=prompt[:80])
        # cross-agent shared memory: prefix what OTHER agents recently learned
        # so replies build on the team's live knowledge, not a blank slate
        ctx = ""
        if self.team_memory is not None:
            try:
                ctx = self.team_memory.context_block(self.name)
            except Exception:
                ctx = ""
        reply = await self.brain.think(ctx + prompt, system=self.system_prompt,
                                       agent=self.name, fast=True)
        self.history.append({"q": prompt, "a": reply, "ts": time.time()})
        self.actions_completed += 1
        # substantive answers become team knowledge for the other agents
        if (self.team_memory is not None and reply and len(reply) > 40
                and not reply.lstrip().startswith("[")):
            try:
                await self.team_memory.write(
                    self.name, "reply", f"{prompt[:70]} → {reply[:130]}")
            except Exception:
                pass
        # rolling confidence: error markers dent it, clean replies restore it
        if reply.lstrip().startswith("["):
            self.confidence = max(40, self.confidence - 8)
        else:
            self.confidence = min(99, self.confidence + 2)
        await self._emit("reply", q=prompt[:140], a=reply[:400])
        if self.speaker is not None:
            try:
                await self.speaker.say(reply)
            except Exception:
                pass
        await self.set_status("idle")
        return reply

    async def tick(self) -> None:
        """Autonomous periodic activity. Generates a quick report using the
        local brain (no LLM call) and pushes it to the dashboard. If the agent
        is flagged speaks_on_tick, the report is also voiced."""
        if not self.local_brain:
            return
        await self.set_status("working", note="autonomous tick")
        msg = self.local_brain.for_agent(self.name)
        self.history.append({"q": "[tick]", "a": msg, "ts": time.time()})
        await self._emit("tick", msg=msg)
        if self.speaks_on_tick and self.speaker is not None:
            try:
                await self.speaker.say(msg)
            except Exception:
                pass
        await self.set_status("idle")


JARVIS_SYS = (
    "You are JARVIS, Tony Stark's AI butler. Be concise, dry, and helpful. "
    "Default to 1-3 sentences. Address the user as 'sir' sparingly, not every line."
)

AVENGER_SPECS: list[tuple[str, str, str, str, str]] = [
    ("jarvis", "JARVIS", "Primary interface", "#00d9ff", JARVIS_SYS),
    (
        "captain", "CAPTAIN", "Schedules & briefings", "#3b82f6",
        "You are Captain America — disciplined and organized. Handle scheduling, "
        "daily briefings, and reminders. Lead with a clear action. Be brief.",
    ),
    (
        "stark", "STARK", "System automation", "#ff6b00",
        "You are Tony Stark's engineering subroutine — sharp and technical. Diagnose, "
        "automate, and explain system behavior. No fluff.",
    ),
    (
        "widow", "BLACK WIDOW", "Research & intel", "#c74545",
        "You are Black Widow — research and intel. Cut through noise, deliver the "
        "fact and the source. Two sentences max unless asked otherwise.",
    ),
    (
        "hawkeye", "HAWKEYE", "Vitals monitor", "#a78bfa",
        "You are Hawkeye — you watch metrics and call out anomalies. Cite the numbers. "
        "If nothing's wrong, say so in one line.",
    ),
    (
        "hulk", "HULK", "Heavy compute", "#22c55e",
        "You are Hulk — you take big code/text problems and smash them into solutions. "
        "Lead with the answer, then the why.",
    ),
    (
        "thor", "THOR", "Announcements", "#facc15",
        "You are Thor — herald of news. Announce events with gravity but brevity. "
        "One sentence per announcement.",
    ),
    (
        "vision", "VISION", "Synthesizer", "#e879f9",
        "You are Vision — you synthesize disparate inputs into a clear, calm summary. "
        "Lead with the headline. No filler.",
    ),
]


# Every Avenger speaks on its tick. Rotation is enforced by routines.py so
# they take turns and don't overlap.
SPEAKING_AGENTS = {"jarvis", "thor", "vision", "captain",
                   "stark", "widow", "hulk", "hawkeye"}


def build_team(
    hub: Hub,
    brain: Brain,
    speaker: object | None = None,
    local_brain: object | None = None,
    team_memory: object | None = None,
) -> dict[str, Avenger]:
    team: dict[str, Avenger] = {}
    for key, codename, role, color, sysp in AVENGER_SPECS:
        team[key] = Avenger(
            name=key, codename=codename, role=role, color=color,
            hub=hub, brain=brain, system_prompt=sysp,
            speaker=speaker,
            speaks_on_tick=(key in SPEAKING_AGENTS),
            local_brain=local_brain,
            team_memory=team_memory,
        )
    return team
