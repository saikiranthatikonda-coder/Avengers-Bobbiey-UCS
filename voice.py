"""Continuous speech-to-text wake-phrase loop.

Accuracy upgrades over v1:
  * base.en whisper model (~150 MB, far more accurate than tiny.en)
  * initial_prompt biases whisper toward Avenger names — big recall boost
  * beam_size=5, best_of=5 for higher-quality decoding
  * 300 ms pre-roll buffer captures the "hey" at speech onset
  * 1.2 s end-of-speech silence threshold (handles natural pauses)
  * Fuzzy agent-name matching (difflib) absorbs transcription noise
  * Self-mutes while TTS speaks so JARVIS doesn't trigger on its own voice
"""

import asyncio
import os
import re
from collections import deque
from difflib import SequenceMatcher


# Wake phrases → agent key. Order matters: longer first so "stark" doesn't
# shadow "hey stark".
AGENT_TRIGGERS: list[tuple[str, str]] = [
    ("hey jarvis", "jarvis"),
    ("hey stark", "stark"),
    ("hey iron man", "stark"),
    ("hey tony", "stark"),
    ("iron man", "stark"),
    ("hey captain america", "captain"),
    ("hey captain", "captain"),
    ("hey cap", "captain"),
    ("hey steve", "captain"),
    ("hey black widow", "widow"),
    ("hey widow", "widow"),
    ("hey natasha", "widow"),
    ("black widow", "widow"),
    ("hey hawkeye", "hawkeye"),
    ("hey clint", "hawkeye"),
    ("hey barton", "hawkeye"),
    ("hey hulk", "hulk"),
    ("hey banner", "hulk"),
    ("hey bruce", "hulk"),
    ("hey thor", "thor"),
    ("hey vision", "vision"),
    # bare names
    ("jarvis", "jarvis"),
    ("stark", "stark"),
    ("captain", "captain"),
    ("hawkeye", "hawkeye"),
    ("widow", "widow"),
    ("hulk", "hulk"),
    ("thor", "thor"),
    ("vision", "vision"),
]

# Used by the fuzzy fallback when exact substring matching misses.
FUZZY_NAMES: dict[str, str] = {
    "jarvis": "jarvis",
    "stark": "stark", "tony": "stark", "ironman": "stark",
    "captain": "captain", "cap": "captain", "steve": "captain", "rogers": "captain",
    "widow": "widow", "natasha": "widow", "romanoff": "widow",
    "hawkeye": "hawkeye", "clint": "hawkeye", "barton": "hawkeye",
    "hulk": "hulk", "banner": "hulk", "bruce": "hulk",
    "thor": "thor",
    "vision": "vision",
}

# Whisper's `initial_prompt` biases the language model. We seed it with every
# wake phrase plus a few Indian-English context words so Whisper picks the
# right tokens even when the audio is mushy or the accent is Indian English.
INITIAL_PROMPT = (
    "Indian English speaker at Stark Industries Hyderabad. "
    "Hey Jarvis, hey Stark, hey Tony, Iron Man, "
    "hey Captain, hey Cap, hey Steve, Captain America, "
    "hey Widow, hey Natasha, Black Widow, "
    "hey Hawkeye, hey Clint, hey Barton, "
    "hey Hulk, hey Banner, hey Bruce, "
    "hey Thor, hey Vision. "
    "Sir asks Stark about CPU, memory, disk, network, system status, "
    "weather in Hyderabad, latest news, schedule, meetings, AISIN, "
    "open WorldMonitor, brief me, what is the time, kindly check, "
    "do the needful, please proceed, fire it up."
)


class VoiceLoop:
    def __init__(self, hub, brain, team, services: dict | None = None) -> None:
        self.hub = hub
        self.brain = brain
        self.team = team
        self.services = services or {}   # agenda / threats / insights handles
        self.muted = False
        self.convo: list[tuple[str, str]] = []   # rolling (question, answer) memory

    async def run(self) -> None:
        try:
            import sounddevice as sd
            import numpy as np
            from faster_whisper import WhisperModel
        except ImportError as e:
            await self.hub.broadcast({
                "type": "log", "level": "warn",
                "msg": f"voice disabled: missing dep ({e.name}). "
                       "Run: .\\.venv\\Scripts\\pip install faster-whisper sounddevice numpy",
            })
            return

        asyncio.create_task(self._hub_listener())

        # Default to `small` (multilingual). Indian-accented English transcribes
        # noticeably better than with the English-only models because the
        # multilingual training set contains much more accent diversity.
        model_name = os.getenv("JARVIS_WHISPER_MODEL", "small")
        await self.hub.broadcast({
            "type": "log", "level": "info",
            "msg": f"loading whisper {model_name} (first run downloads model — ~500 MB for 'small')…",
        })

        try:
            stt = WhisperModel(model_name, device="cpu", compute_type="int8")
        except Exception as e:
            await self.hub.broadcast({
                "type": "log", "level": "warn",
                "msg": f"whisper init failed ({model_name}): {e}",
            })
            return

        await self.hub.broadcast({
            "type": "log", "level": "info",
            "msg": "voice listener online — say 'Hey Stark', 'Hey Cap', 'Hey Widow', etc.",
        })
        await self.hub.broadcast({"type": "voice", "event": "ready"})

        sample_rate = 16000
        chunk_samples = int(sample_rate * 0.05)  # 50 ms
        threshold = float(os.getenv("JARVIS_VOICE_THRESHOLD", "300"))
        end_silence_chunks = 14    # 0.7 s trailing silence ends an utterance (snappier)
        max_utterance_chunks = 220 # 11 s cap per utterance
        min_utterance_chunks = 8   # 0.4 s floor (reject blips)
        preroll_chunks = 6         # 300 ms pre-roll captured before speech onset

        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()

        def cb(indata, frames, time_info, status):
            try:
                loop.call_soon_threadsafe(q.put_nowait, indata.copy())
            except Exception:
                pass

        try:
            with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16",
                                blocksize=chunk_samples, callback=cb):
                buffer: list = []
                preroll: deque = deque(maxlen=preroll_chunks)
                in_speech = False
                silence_count = 0

                while True:
                    pkt = await q.get()
                    if self.muted:
                        buffer = []
                        preroll.clear()
                        in_speech = False
                        silence_count = 0
                        continue

                    samples = pkt[:, 0]
                    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

                    if in_speech:
                        buffer.append(samples)
                        if rms < threshold:
                            silence_count += 1
                            if silence_count >= end_silence_chunks:
                                await self._finish(buffer, stt, np, min_utterance_chunks)
                                buffer, in_speech, silence_count = [], False, 0
                                preroll.clear()
                        else:
                            silence_count = 0
                        if len(buffer) >= max_utterance_chunks:
                            await self._finish(buffer, stt, np, min_utterance_chunks)
                            buffer, in_speech, silence_count = [], False, 0
                            preroll.clear()
                    else:
                        preroll.append(samples)
                        if rms > threshold:
                            in_speech = True
                            buffer = list(preroll) + [samples]  # prepend pre-roll
                            silence_count = 0
                            await self.hub.broadcast({"type": "voice", "event": "listening"})
        except Exception as e:
            await self.hub.broadcast({
                "type": "log", "level": "error",
                "msg": f"voice loop crashed: {e}",
            })

    async def _hub_listener(self) -> None:
        q = self.hub.subscribe()
        try:
            while True:
                msg = await q.get()
                if msg.get("type") != "voice":
                    continue
                ev = msg.get("event")
                if ev == "speak":
                    self.muted = True
                elif ev == "idle":
                    self.muted = False
        except Exception:
            pass
        finally:
            self.hub.unsubscribe(q)

    async def _finish(self, buffer, stt, np, min_chunks) -> None:
        if len(buffer) < min_chunks:
            await self.hub.broadcast({"type": "voice", "event": "idle"})
            return
        audio = np.concatenate(buffer).astype("float32") / 32768.0
        await self.hub.broadcast({"type": "voice", "event": "processing"})

        loop = asyncio.get_running_loop()
        try:
            text = await loop.run_in_executor(None, self._transcribe, stt, audio)
        except Exception as e:
            await self.hub.broadcast({
                "type": "log", "level": "warn",
                "msg": f"transcribe error: {e}",
            })
            await self.hub.broadcast({"type": "voice", "event": "idle"})
            return

        if not text or len(text) < 2:
            await self.hub.broadcast({"type": "voice", "event": "idle"})
            return

        await self.hub.broadcast({"type": "voice", "event": "heard", "text": text})

        agent_key, command = self._match_agent(text)
        if not agent_key:
            await self.hub.broadcast({
                "type": "log", "level": "info",
                "msg": f"no wake-phrase in: \"{text}\"",
            })
            await self.hub.broadcast({"type": "voice", "event": "idle"})
            return

        agent = self.team.get(agent_key)
        if not agent:
            await self.hub.broadcast({"type": "voice", "event": "idle"})
            return

        await self.hub.broadcast({
            "type": "voice", "event": "routed",
            "agent": agent_key, "command": command or "(no command)",
        })
        try:
            mem = self.services.get("memory")
            if mem and command:
                mem.record_command(command)   # memory learns command habits
        except Exception:
            pass

        if not command:
            command = "Sir is summoning you. Greet briefly and ask what he needs."

        # ── tool layer: answer from live data / control the dashboard ──
        handled = await self._try_intent(agent, command)
        if handled:
            return

        # ── LLM path with conversation memory ──────────────────────
        # Instant spoken acknowledgment so the operator hears feedback in <1s
        # while the model composes the full reply (masks LLM latency).
        if agent.speaker:
            import random as _r
            ack = _r.choice(["On it, sir.", "One moment.", "Right away.", "Working on it, sir."])
            try:
                await agent.speaker.say(ack)
            except Exception:
                pass
        prompt = command
        if self.convo:
            ctx = " | ".join(f"Q:{q[:60]} A:{a[:80]}" for q, a in self.convo[-3:])
            prompt = f"(recent conversation: {ctx}) New request: {command}"
        reply = await agent.handle(prompt)
        self.convo = (self.convo + [(command, reply)])[-6:]

    # ── voice tool-calling: real data, instant answers ───────────
    async def _try_intent(self, agent, command: str) -> bool:
        low = command.lower()
        agenda = self.services.get("agenda")
        threats = self.services.get("threats")

        async def respond(text: str) -> None:
            await agent._emit("reply", q=command[:140], a=text[:400])
            self.convo = (self.convo + [(command, text)])[-6:]
            if agent.speaker:
                try:
                    await agent.speaker.say(text)
                except Exception:
                    pass

        # memory: "what do you know about me" / "who am I"
        memory = self.services.get("memory")
        if memory and re.search(r"\b(what do you know about me|who am i|remember about me|my memory)\b", low):
            await respond(memory.summary_text())
            return True

        # vision: "what do you see" / "what am I doing" / "look at me"
        if re.search(r"\b(what do you see|what am i doing|look at me|see me|analys?e me|how do i look)\b", low):
            await self.hub.broadcast({"type": "vision-request"})
            await respond("Let me take a look, sir.")
            return True

        # dashboard control: "open/show <section>"
        if re.search(r"\b(open|show|bring up|display)\b", low):
            targets = {
                "threat": "threats", "security": "threats",
                "map": "map", "global": "map",
                "agenda": "agenda", "calendar": "agenda", "schedule": "agenda",
                "insight": "insights", "intel": "insights",
                "news": "news", "feed": "news",
                "camera": "camera", "operator": "camera",
                "roster": "roster", "agent": "roster",
                "readiness": "readiness", "mission": "readiness",
            }
            for word, target in targets.items():
                if word in low:
                    await self.hub.broadcast({"type": "dash-cmd", "target": target})
                    await respond(f"Bringing up {target} now, sir.")
                    return True

        # calendar queries
        if agenda and re.search(r"\b(meeting|meetings|schedule|agenda|calendar|today)\b", low) \
                and re.search(r"\b(what|how many|next|today|do i have|upcoming)\b", low):
            snap = agenda.snapshot()
            intel = snap.get("intel", {})
            evs = snap.get("events") or []
            if not evs:
                await respond("Your calendar is clear, sir. No upcoming meetings.")
                return True
            nxt = evs[0]
            parts = [f"You have {intel.get('meetings_today', len(evs))} meetings today."]
            parts.append(f"Next is {nxt['title']} in {max(0, round(nxt['minutes_until']))} minutes.")
            if intel.get("conflicts"):
                parts.append(f"Warning: {intel['conflicts']} scheduling conflict detected.")
            if intel.get("largest_free_block_min"):
                parts.append(f"Your largest focus block is {intel['largest_free_block_min']} minutes.")
            await respond(" ".join(parts))
            return True

        # inbox summary
        if agenda and re.search(r"\b(inbox|email|emails|mail)\b", low):
            snap = agenda.snapshot()
            mails = snap.get("emails") or []
            prio = [m for m in mails if m.get("priority") == "priority"]
            if not mails:
                await respond("Inbox is clear, sir.")
                return True
            line = f"{len(mails)} messages in view, {len(prio)} priority."
            if prio:
                line += f" Top priority: {prio[0]['subject']} from {prio[0]['sender'].split('@')[0]}."
            await respond(line)
            return True

        # threat / security status
        if threats and re.search(r"\b(threat|threats|security|risk)\b", low):
            await respond(threats.summary_text())
            return True

        return False

    @staticmethod
    def _transcribe(stt, audio) -> str:
        # Speed-optimised decode: greedy search, single temperature, no fallback.
        # Lossy vs beam=5 in absolute accuracy, but on Indian-accented English
        # the bigger gain comes from using the multilingual `small` model with
        # an Indian-context initial_prompt — this combo is ~4-5× faster than
        # the prior beam=5,best_of=5,temperature=[0,0.2,0.4] setup.
        segments, _info = stt.transcribe(
            audio, language="en",
            beam_size=1, best_of=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 200},
            initial_prompt=INITIAL_PROMPT,
            condition_on_previous_text=False,
            no_speech_threshold=0.5,
            temperature=0.0,
        )
        return " ".join(s.text for s in segments).strip()

    @staticmethod
    def _match_agent(text: str) -> tuple[str | None, str | None]:
        low = re.sub(r"[^\w\s]", " ", text.lower()).strip()
        low = re.sub(r"\s+", " ", low)

        # 1) exact substring match, longest phrases first
        for trigger, agent_key in AGENT_TRIGGERS:
            if trigger in low:
                idx = low.find(trigger)
                cmd = low[idx + len(trigger):].strip()
                return agent_key, cmd or None

        # 2) fuzzy token match — handles "starck", "stork", "thoor", "wido", etc.
        tokens = low.split()
        best_agent: str | None = None
        best_ratio = 0.0
        best_idx = -1
        for i, tok in enumerate(tokens):
            if len(tok) < 3:
                continue
            for name, agent in FUZZY_NAMES.items():
                r = SequenceMatcher(None, tok, name).ratio()
                if r > best_ratio:
                    best_ratio = r
                    best_agent = agent
                    best_idx = i
        if best_ratio >= 0.72:
            cmd = " ".join(tokens[best_idx + 1:]).strip()
            return best_agent, cmd or None
        return None, None
