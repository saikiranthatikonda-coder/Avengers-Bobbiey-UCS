"""Real-Time Interaction & Action Engine — the single command pipeline of BUCS.

Every command — spoken, typed, clicked, clapped, or raised by another
subsystem — flows through Engine.submit():

    RECEIVED → ACK (instant) → ROUTE → [HANDOFF] → REASON (streamed) / ACT
             → RESPOND (spoken sentence-by-sentence) → DONE | FAILED | CANCELLED

and every stage is broadcast on the hub as {"type": "cmd", "stage": …} with
timings, so the dashboard shows exactly what BUCS is doing, live.

Design rules (see CLAUDE.md §6b):
* Fast path first: intents.py answers from live data / runs allow-listed
  actions without an LLM (ms). Only unmatched requests reach an agent.
* Stream everything: Claude/Ollama tokens are pushed as they arrive and
  spoken per sentence, so audio starts long before the reply is finished.
* Never block, always cancellable: each command is its own task; "stop",
  a double clap during speech, or the UI cancel button kills it (incl. the
  CLI subprocess and queued speech).
* One context: voice, text and UI share the conversation memory, so a
  follow-up typed after a spoken question still has context.
* Measure: per-path latency (p50/p95) is kept and exposed at /api/engine.
* Graceful: any failure degrades to the existing behaviour (agent.handle /
  templates); the engine never takes BUCS down.
"""

import asyncio
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

import intents

SENTENCE_END = re.compile(r"([.!?…])(\s+|$)")
SPOKEN_SOURCES = {"voice", "clap", "text", "ui"}


@dataclass
class Command:
    text: str
    source: str = "text"                 # voice | text | ui | clap | api | agent
    agent: str | None = None             # explicit target (wake word / @mention)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created: float = field(default_factory=time.perf_counter)
    wall: float = field(default_factory=time.time)
    stage: str = "received"
    path: str = ""                       # fast | claude | local-llm | template | control
    intent: str = ""
    handled_by: str = ""
    reply: str = ""
    error: str = ""
    marks: dict = field(default_factory=dict)   # stage → ms since created
    task: asyncio.Task | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def mark(self, key: str) -> float:
        ms = round((time.perf_counter() - self.created) * 1000, 1)
        self.marks.setdefault(key, ms)
        return ms

    def public(self) -> dict:
        return {"id": self.id, "text": self.text[:200], "source": self.source, "stage": self.stage,
                "agent": self.handled_by or self.agent, "path": self.path, "intent": self.intent,
                "reply": self.reply[:600], "error": self.error, "marks": self.marks, "ts": self.wall}


class SentenceChunker:
    """Turns a token stream into speakable sentences (first one ASAP)."""

    def __init__(self, min_len: int = 24, max_len: int = 220, first_min: int = 4) -> None:
        # the FIRST sentence goes out as soon as it's complete (time-to-first-
        # audio); later short ones merge so speech doesn't sound choppy
        self.buf = ""
        self.min_len, self.max_len, self.first_min = min_len, max_len, first_min
        self.sent = 0

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            m = None
            need = self.first_min if (self.sent + len(out)) == 0 else self.min_len
            for mm in SENTENCE_END.finditer(self.buf):
                if mm.end() >= need:
                    m = mm
                    break
            if m:
                out.append(self.buf[:m.end()].strip())
                self.buf = self.buf[m.end():]
                continue
            if len(self.buf) > self.max_len:                     # run-on: break at a comma/space
                cut = max(self.buf.rfind(", ", 0, self.max_len), self.buf.rfind(" ", 0, self.max_len))
                cut = cut if cut > self.min_len else self.max_len
                out.append(self.buf[:cut + 1].strip())
                self.buf = self.buf[cut + 1:]
                continue
            out = [s for s in out if s]
            self.sent += len(out)
            return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


class Engine:
    def __init__(self, state: dict, context_turns: int = 8) -> None:
        self.state = state
        self.active: dict[str, Command] = {}
        self.recent: deque = deque(maxlen=40)
        self.context: deque = deque(maxlen=context_turns)     # shared (q, a, agent)
        self.latency: dict[str, deque] = {}                    # path → deque of total ms
        self.first_token: deque = deque(maxlen=50)
        self.first_speech: deque = deque(maxlen=50)
        self.totals = {"commands": 0, "fast": 0, "llm": 0, "cancelled": 0, "failed": 0}
        self.listeners: list = []                              # callables(cmd) on finish

    # ── public API ────────────────────────────────────────────────
    @property
    def hub(self):
        return self.state.get("hub")

    async def submit(self, text: str, source: str = "text", agent: str | None = None,
                     wait: bool = False, speak: bool | None = None, on_done=None) -> Command:
        cmd = Command(text=(text or "").strip(), source=source,
                      agent=(agent or None) and agent.lower())
        self.totals["commands"] += 1
        speak = (source in SPOKEN_SOURCES) if speak is None else speak
        # a new request from the operator supersedes speech still playing for an
        # older one (people interrupt each other); running work is not touched
        tts = self.state.get("tts")
        if source in SPOKEN_SOURCES and tts is not None and hasattr(tts, "cancel"):
            busy = getattr(tts, "speaking", False) or (hasattr(tts, "q") and not tts.q.empty())
            if busy:
                tts.cancel()
        self.active[cmd.id] = cmd
        cmd.task = asyncio.create_task(self._run(cmd, speak, on_done))
        if wait:
            await cmd.done.wait()
        return cmd

    async def cancel(self, cmd_id: str | None = None, reason: str = "operator") -> int:
        """Cancel one command (or all active ones) and stop speech now."""
        targets = [self.active[cmd_id]] if cmd_id in self.active else \
                  ([] if cmd_id else list(self.active.values()))
        for c in targets:
            if c.task and not c.task.done():
                c.task.cancel()
        tts = self.state.get("tts")
        stopped_speech = bool(tts and getattr(tts, "cancel", None) and tts.cancel())
        if self.hub and (targets or stopped_speech):
            await self.hub.broadcast({"type": "cmd", "stage": "interrupt", "reason": reason,
                                      "ids": [c.id for c in targets], "speech": stopped_speech})
        return len(targets) + int(stopped_speech)

    async def summon(self, agent: str = "jarvis", source: str = "clap") -> Command:
        """Open a conversation without a request (double clap / bare wake word)."""
        cmd = Command(text="", source=source, agent=agent)
        self.totals["commands"] += 1
        self.active[cmd.id] = cmd
        await self._emit(cmd, "received")
        greeting = "Yes, sir? I'm listening."
        cmd.path, cmd.intent, cmd.handled_by, cmd.reply = "control", "summon", agent, greeting
        await self._agent_reply(agent, "(summoned)", greeting)
        await self._finish(cmd, "done")
        await self._say(greeting, cmd)
        return cmd

    def snapshot(self) -> dict:
        def pct(xs, p):                       # linear-interpolated percentile
            xs = sorted(xs)
            if not xs:
                return None
            k = p * (len(xs) - 1); lo = int(k); hi = min(lo + 1, len(xs) - 1)
            return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)
        return {
            "active": [c.public() for c in self.active.values()],
            "recent": [c.public() for c in list(self.recent)[-15:]][::-1],
            "latency": {p: {"n": len(v), "p50_ms": pct(v, 0.5), "p95_ms": pct(v, 0.95)}
                        for p, v in self.latency.items()},
            "first_token_p50_ms": pct(self.first_token, 0.5),
            "first_speech_p50_ms": pct(self.first_speech, 0.5),
            "totals": self.totals,
            "context_turns": len(self.context),
            "actions": __import__("win_actions").catalog(),
        }

    # ── pipeline ──────────────────────────────────────────────────
    async def _run(self, cmd: Command, speak: bool, on_done) -> None:
        try:
            await self._emit(cmd, "received")
            await self._emit(cmd, "ack")                       # instant visual acknowledgement
            if not cmd.text:
                return await self._finish(cmd, "done")
            if intents.is_cancel(cmd.text):
                n = await self.cancel(reason=f"{cmd.source}: {cmd.text}")
                cmd.path, cmd.intent, cmd.handled_by = "control", "cancel", "jarvis"
                cmd.reply = "Standing down." if n else "Nothing to stop, sir."
                return await self._finish(cmd, "done")

            t = time.perf_counter()
            res = await intents.match(self.state, cmd.text)
            cmd.marks["route_ms"] = round((time.perf_counter() - t) * 1000, 2)
            if res:
                await self._fast(cmd, res, speak)
            else:
                await self._reason(cmd, speak)
            await self._finish(cmd, "done")
        except asyncio.CancelledError:
            cmd.error = "cancelled"
            self.totals["cancelled"] += 1
            await self._finish(cmd, "cancelled")
        except Exception as e:                                # never take BUCS down
            cmd.error = str(e)[:300]
            self.totals["failed"] += 1
            await self._finish(cmd, "failed")
        finally:
            if on_done:
                try:
                    on_done(cmd)
                except Exception:
                    pass

    async def _fast(self, cmd: Command, res: dict, speak: bool) -> None:
        cmd.path, cmd.intent = "fast", res.get("intent", "")
        self.totals["fast"] += 1
        agent = cmd.agent if cmd.agent and not res.get("agent") else (res.get("agent") or cmd.agent or "jarvis")
        cmd.handled_by = agent
        await self._emit(cmd, "routed", route="fast", intent=cmd.intent)
        say = res.get("say")
        if res.get("slow") and res.get("run"):
            if say:                                           # instant ack for long work
                await self._agent_reply(agent, cmd.text, say)
                if speak:
                    await self._say(say, cmd)

            async def progress(msg):
                await self._emit(cmd, "progress", detail=msg)
            await self._emit(cmd, "acting", detail=res.get("action", {}).get("name", ""))
            say = await res["run"](progress)
        elif res.get("do"):
            await self._emit(cmd, "acting", detail=res.get("action", {}).get("name", ""))
            out = await asyncio.get_running_loop().run_in_executor(None, res["do"])
            say = out.get("say") or ("Done." if out.get("ok") else "That didn't work, sir.")
            res.setdefault("action", {})["ok"] = bool(out.get("ok"))
        if res.get("action"):
            await self._audit(f"action.{res['action'].get('name', 'unknown')}",
                              f"{cmd.source}: {cmd.text[:80]} → {'ok' if res['action'].get('ok', True) else 'failed'}")
        for msg in res.get("ui") or []:
            await self.hub.broadcast(msg)
        cmd.reply = say or ""
        await self._agent_reply(agent, cmd.text, cmd.reply)
        if speak and cmd.reply and not res.get("slow"):
            await self._say(cmd.reply, cmd)
        elif speak and res.get("slow") and cmd.reply:
            await self._say(cmd.reply, cmd)

    async def _reason(self, cmd: Command, speak: bool) -> None:
        team = self.state.get("team") or {}
        target = cmd.agent if cmd.agent in team else None
        delegated = None
        if not target:
            delegated = intents.delegate(cmd.text)
            target = delegated if delegated in team else "jarvis"
        agent = team.get(target)
        if agent is None:
            raise RuntimeError(f"no agent '{target}'")
        cmd.handled_by = target
        await self._emit(cmd, "routed", route="agent", agent=target)
        if delegated and target != "jarvis":
            await self._emit(cmd, "handoff", **{"from": "jarvis", "to": target})
        if speak and cmd.source == "voice":
            await self._say(self._ack_phrase(), cmd)          # sub-second audible ack

        prompt = cmd.text
        if self.context:
            ctx = " | ".join(f"Q:{q[:70]} A:{a[:90]}" for q, a, _ in list(self.context)[-3:])
            prompt = f"(recent conversation: {ctx}) New request: {cmd.text}"

        chunker = SentenceChunker()
        pending = {"buf": "", "t": 0.0}

        async def on_delta(txt: str):
            cmd.mark("first_token_ms")
            cmd.reply += txt                                   # partial survives a cancel
            pending["buf"] += txt
            now = time.perf_counter()
            if now - pending["t"] > 0.08:                       # throttle UI deltas (~12/s)
                await self._emit(cmd, "delta", text=pending["buf"])
                pending["buf"], pending["t"] = "", now
            if speak:
                for sentence in chunker.feed(txt):
                    await self._say(sentence, cmd)

        await self._emit(cmd, "thinking", agent=target)
        reply, path = await agent.handle_stream(prompt, on_delta=on_delta)
        if pending["buf"]:
            await self._emit(cmd, "delta", text=pending["buf"])
        if speak:
            for sentence in chunker.flush():
                await self._say(sentence, cmd)
        cmd.path, cmd.reply = path, reply
        self.totals["llm"] += 1

    # ── helpers ───────────────────────────────────────────────────
    _ACKS = ["On it, sir.", "One moment.", "Right away.", "Working on it."]

    def _ack_phrase(self) -> str:
        return self._ACKS[self.totals["commands"] % len(self._ACKS)]

    async def _say(self, text: str, cmd: Command) -> None:
        tts = self.state.get("tts")
        if not (tts and text and not text.lstrip().startswith("[")):
            return
        if not (tts.enabled and not tts.muted and tts.volume > 0):
            return
        cmd.mark("speech_queued_ms")
        try:   # first_speech_ms = the moment playback really starts (TTS worker)
            asyncio.create_task(tts.say(text, on_start=lambda: cmd.mark("first_speech_ms")))
        except TypeError:                                      # older TTS without on_start
            asyncio.create_task(tts.say(text))

    async def _agent_reply(self, agent_key: str, q: str, a: str) -> None:
        agent = (self.state.get("team") or {}).get(agent_key)
        if agent is not None and a:
            await agent._emit("reply", q=q[:140], a=a[:400])

    async def _audit(self, action: str, detail: str) -> None:
        a = self.state.get("audit")
        if a is not None:
            rb = self.state.get("rbac", {})
            try:
                await a.log(action, detail, operator=rb.get("operator", "operator"),
                            role=rb.get("role", "commander"))
            except Exception:
                pass

    async def _emit(self, cmd: Command, stage: str, **kw) -> None:
        cmd.stage = stage
        ms = cmd.mark(f"{stage}_ms") if stage not in ("delta", "progress") else \
            round((time.perf_counter() - cmd.created) * 1000, 1)
        if self.hub:
            await self.hub.broadcast({"type": "cmd", "id": cmd.id, "stage": stage, "t_ms": ms,
                                      "source": cmd.source, "text": cmd.text[:200],
                                      "agent": cmd.handled_by or cmd.agent, **kw})

    async def _finish(self, cmd: Command, stage: str) -> None:
        total = cmd.mark("total_ms")
        if stage == "done" and cmd.text:
            self.latency.setdefault(cmd.path or "unknown", deque(maxlen=50)).append(total)
            if "first_token_ms" in cmd.marks:
                self.first_token.append(cmd.marks["first_token_ms"])
            if "first_speech_ms" in cmd.marks:
                self.first_speech.append(cmd.marks["first_speech_ms"])
            if cmd.reply and cmd.path != "control":
                self.context.append((cmd.text, cmd.reply, cmd.handled_by))
        await self._emit(cmd, stage, reply=cmd.reply[:600], path=cmd.path, intent=cmd.intent,
                         marks=cmd.marks, error=cmd.error)
        self.active.pop(cmd.id, None)
        self.recent.append(cmd)
        cmd.done.set()
        for fn in self.listeners:
            try:
                fn(cmd)
            except Exception:
                pass
