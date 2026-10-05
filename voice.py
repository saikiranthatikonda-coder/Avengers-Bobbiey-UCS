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
import time
from collections import deque

# Whisper's well-known phantom outputs on noise/silence (trained on subtitles).
# Ignored when they are the WHOLE utterance — never filtered inside a sentence.
PHANTOMS = {"thank you", "thanks", "thank you very much", "thanks for watching", "you",
            "bye", "okay", "ok", "so", "uh", "um", "hmm", "mm", "oh", "ah", "yeah",
            "subtitles by the amaraorg community", "please subscribe", "the end", "i", "a"}


def is_phantom(text: str, seconds: float) -> bool:
    norm = re.sub(r"[^a-z ]", "", (text or "").lower()).strip()
    return (not norm) or (norm in PHANTOMS and seconds < 2.5)
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
# Short vocabulary hint. A long, oddly-phrased prompt makes Whisper
# hallucinate its words into silence/noise, so keep it to names + commands.
HOTWORDS = ("Jarvis Stark Tony Captain Cap Steve Widow Natasha Hawkeye Clint "
            "Hulk Banner Bruce Thor Vision CPU memory disk network threat "
            "calendar meetings inbox weather news briefing")

INITIAL_PROMPT_LEGACY = (
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
INITIAL_PROMPT = "Hey Jarvis. Hey Stark, what's the CPU load? Hey Cap, open threat intelligence."


class ClapDetector:
    """Double-clap detector on 50 ms int16 blocks.

    A clap is an impulsive transient: a block whose peak is far above both an
    absolute floor and the room's noise floor, with a SUDDEN ONSET (the block
    before was quiet) and a FAST DECAY (the next block's RMS falls below
    `decay` of the clap block's). Speech fails at least one: plosives keep
    sustained energy, and the end of a loud sentence has no sudden onset.
    Two claps 0.2–0.9 s apart = summon. Tunable via JARVIS_CLAP_PEAK."""

    def __init__(self, peak_floor: float | None = None, gap=(0.2, 0.9), decay=0.35,
                 onset=0.3) -> None:
        self.peak_floor = float(peak_floor or os.getenv("JARVIS_CLAP_PEAK", "7000"))
        self.gap = gap
        self.decay = decay
        self.onset = onset
        self._pending = None          # (t, rms) of a spike awaiting its decay check
        self._last_clap: float | None = None
        self._prev_rms = 0.0
        self.last_peak = 0.0

    def feed(self, peak: float, rms: float, noise_rms: float, now: float) -> bool:
        """Returns True when a double clap completes on this block."""
        fired = False
        if self._pending is not None:
            t0, r0 = self._pending
            self._pending = None
            if rms < r0 * self.decay:                          # sharp decay → clap
                if (self._last_clap is not None
                        and self.gap[0] <= t0 - self._last_clap <= self.gap[1]):
                    fired = True
                    self._last_clap = None
                else:
                    self._last_clap = t0
        if (peak >= self.peak_floor and peak >= noise_rms * 25
                and self._prev_rms < rms * self.onset                # sudden onset
                and (self._last_clap is None or now - self._last_clap > 0.12)):
            self._pending = (now, rms)
            self.last_peak = peak
        self._prev_rms = rms
        return fired


class VoiceLoop:
    def __init__(self, hub, brain, team, services: dict | None = None) -> None:
        self.hub = hub
        self.brain = brain
        self.team = team
        self.services = services or {}   # agenda / threats / insights handles
        self.muted = False
        self.convo: list[tuple[str, str]] = []   # rolling (question, answer) memory
        # conversation mode: after a clap summon or an answer, the next
        # utterance goes to this agent without needing a wake word
        self.follow_agent: str | None = None
        self.follow_until = 0.0
        self.noise_rms = 60.0                    # adaptive room noise floor
        self.clap = ClapDetector() if os.getenv("JARVIS_CLAP", "1") != "0" else None
        self.engine = None                       # realtime.Engine, set by main.py
        self.diag: deque = deque(maxlen=30)      # per-utterance diagnostics (/api/voice/diagnostics)
        self.echo_rms = 0.0                      # learned level of our own voice in the mic
        self.barge_ins = 0

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
        # transcription runs in its own worker so capture NEVER pauses: before
        # this, the mic loop waited on Whisper (1-14 s) — readings stalled,
        # audio backed up and phrases were processed late, in bursts
        self._utt_q: asyncio.Queue = asyncio.Queue()
        asyncio.create_task(self._stt_worker())

        # Default to `small` (multilingual). Indian-accented English transcribes
        # noticeably better than with the English-only models because the
        # multilingual training set contains much more accent diversity.
        model_name = os.getenv("JARVIS_WHISPER_MODEL", "small")
        self.model_name = model_name
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
            "msg": ("voice listener online — say 'Hey Jarvis', 'Hey Stark', 'Hey Cap'…"
                    + (" · double-clap to summon JARVIS" if self.clap else "")),
        })
        await self.hub.broadcast({"type": "voice", "event": "ready"})

        sample_rate = 16000
        chunk_samples = int(sample_rate * 0.05)  # 50 ms
        # JARVIS_VOICE_THRESHOLD pins a fixed trigger level; otherwise it adapts
        # to the room: 4.5× the learned noise floor, never below 120 (a fixed
        # 300 missed soft speech and clipped the "hey" on a quiet laptop mic)
        fixed_thr = os.getenv("JARVIS_VOICE_THRESHOLD")
        fixed_thr = float(fixed_thr) if fixed_thr and fixed_thr.strip() else None
        end_silence_chunks = 20    # 1.0 s trailing silence ends an utterance
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
                barge = 0
                preroll: deque = deque(maxlen=preroll_chunks)
                in_speech = False
                silence_count = 0

                while True:
                    pkt = await q.get()
                    if self.muted:
                        # mic is muted while BUCS speaks (no self-triggering), but
                        # two barge-ins stay live:
                        #  · double clap
                        #  · Conversation Mode: sustained speech clearly LOUDER than
                        #    our own voice's echo (the laptop's echo cancellation only
                        #    partly removes it: measured p95 ~410, peaks ~700 RMS)
                        f32 = pkt[:, 0].astype(np.float32)
                        b_rms = float(np.sqrt(np.mean(f32 ** 2)))
                        preroll.append(pkt[:, 0])
                        if self.engine is not None:
                            if self.clap and self.clap.feed(float(np.max(np.abs(f32))), b_rms,
                                                            self.noise_rms, time.time()):
                                await self.engine.cancel(reason="double clap (barge-in)")
                                self.muted = False
                                barge = 0
                                continue
                            if self._conversation():
                                self.echo_rms = 0.95 * self.echo_rms + 0.05 * min(b_rms, 4000)
                                bar = max(900.0, self.echo_rms * 3.5)
                                barge = barge + 1 if b_rms > bar else 0
                                if barge >= 6:                  # 0.3 s of loud speech
                                    self.barge_ins += 1
                                    await self.engine.cancel(reason="operator spoke (barge-in)")
                                    self.muted = False
                                    in_speech, silence_count = True, 0
                                    buffer = list(preroll)        # keep the words that interrupted
                                    barge = 0
                                    await self.hub.broadcast({"type": "voice", "event": "listening"})
                                    continue
                        buffer = []
                        in_speech = False
                        silence_count = 0
                        continue
                    barge = 0

                    samples = pkt[:, 0]
                    f32 = samples.astype(np.float32)
                    rms = float(np.sqrt(np.mean(f32 ** 2)))
                    peak = float(np.max(np.abs(f32)))
                    if not in_speech:     # learn the room while nobody talks
                        self.noise_rms = 0.97 * self.noise_rms + 0.03 * min(rms, self.noise_rms * 3)
                    threshold = fixed_thr or max(120.0, self.noise_rms * 4.5)
                    now_t = time.time()
                    self._lvl_peak = max(getattr(self, "_lvl_peak", 0.0), peak)
                    if now_t - getattr(self, "_lvl_t", 0.0) > 0.25:
                        self._lvl_t = now_t
                        await self.hub.broadcast({"type": "mic", "peak": round(self._lvl_peak),
                                                  "rms": round(rms), "noise": round(self.noise_rms),
                                                  "trigger": round(threshold)})
                        self._lvl_peak = 0.0

                    if self.clap and self.clap.feed(peak, rms, self.noise_rms, time.time()):
                        buffer, in_speech, silence_count = [], False, 0
                        preroll.clear()
                        await self._summon()
                        continue

                    if in_speech:
                        buffer.append(samples)
                        if rms < threshold:
                            silence_count += 1
                            if silence_count >= end_silence_chunks:
                                self._enqueue(buffer, stt, np, min_utterance_chunks)
                                buffer, in_speech, silence_count = [], False, 0
                                preroll.clear()
                        else:
                            silence_count = 0
                        if len(buffer) >= max_utterance_chunks:
                            self._enqueue(buffer, stt, np, min_utterance_chunks)
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

    def _enqueue(self, buffer, stt, np, min_chunks) -> None:
        q = getattr(self, "_utt_q", None)
        if q is None:                                   # not running (tests): process inline
            asyncio.create_task(self._finish(buffer, stt, np, min_chunks))
            return
        while q.qsize() >= 2:                           # real-time: stale phrases are dropped
            try:
                q.get_nowait()
                self.dropped_stale = getattr(self, "dropped_stale", 0) + 1
            except asyncio.QueueEmpty:
                break
        q.put_nowait((list(buffer), stt, np, min_chunks))

    async def _stt_worker(self) -> None:
        while True:
            buffer, stt, np, min_chunks = await self._utt_q.get()
            try:
                await self._finish(buffer, stt, np, min_chunks)
            except Exception as e:
                await self.hub.broadcast({"type": "log", "level": "warn", "msg": f"voice worker: {e}"})

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
        # auto-gain: laptop array mics often deliver quiet speech that Whisper's
        # VAD drops; normalise to ~0.9 peak (gain capped at 20x so pure noise
        # isn't blown up into words)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        level = {"peak": round(peak * 32768), "gain": 1.0}
        if 0 < peak < 0.9:
            g = min(20.0, 0.9 / peak)
            audio = audio * g
            level["gain"] = round(g, 1)
        self.last_level = level
        await self.hub.broadcast({"type": "voice", "event": "processing"})

        seconds = round(len(audio) / 16000, 2)
        loop = asyncio.get_running_loop()
        t_stt = time.perf_counter()
        try:
            text = await loop.run_in_executor(None, self._transcribe, stt, audio)
        except Exception as e:
            await self.hub.broadcast({
                "type": "log", "level": "warn",
                "msg": f"transcribe error: {e}",
            })
            await self.hub.broadcast({"type": "voice", "event": "idle"})
            return

        entry = {"ts": time.time(), "text": text or "", "seconds": seconds,
                 "stt_ms": round((time.perf_counter() - t_stt) * 1000),
                 "peak": level["peak"], "gain": level["gain"],
                 "noise_rms": round(self.noise_rms), "model": getattr(self, "model_name", "?"),
                 "outcome": ""}
        self.diag.append(entry)
        if not text or len(text) < 2 or is_phantom(text, seconds):
            entry["outcome"] = "ignored (empty / phantom)"
            await self.hub.broadcast({"type": "voice", "event": "idle"})
            return

        await self.hub.broadcast({"type": "voice", "event": "heard", "text": text,
                                  "level": getattr(self, "last_level", None),
                                  "seconds": seconds, "stt_ms": entry["stt_ms"]})

        # "stop" / "cancel" / "never mind" need no wake word while BUCS is busy
        if self.engine is not None:
            from intents import is_cancel
            if is_cancel(text) and (self.engine.active or getattr(self.engine.state.get("tts"), "speaking", False)):
                entry["outcome"] = "cancel"
                await self.engine.submit(text, source="voice")
                return
            # Conversation Mode: one agent (JARVIS). Say "Jarvis" anywhere to
            # start; then no wake word is needed while the conversation is
            # live (ENGAGED_S after each reply / summon). Outside that window
            # other people's chatter is ignored instead of answered.
            if self._conversation():
                named, rest = self._mentions_jarvis(text)
                engaged = time.time() < self.follow_until
                if not (named or engaged):
                    entry["outcome"] = "ignored (not addressed — say 'Jarvis')"
                    await self.hub.broadcast({"type": "voice", "event": "unrouted", "text": text,
                                              "hint": "say “Jarvis …” to start talking"})
                    await self.hub.broadcast({"type": "voice", "event": "idle"})
                    return
                if named and not rest:                       # just "Jarvis"
                    entry["outcome"] = "summon"
                    await self.engine.summon("jarvis", source="voice")
                    self._open_followup("jarvis", self.ENGAGED_S)
                    return
                req = rest if named else text.strip()
                entry["outcome"] = "→ jarvis (" + ("named" if named else "engaged") + ")"
                await self.hub.broadcast({"type": "voice", "event": "routed",
                                          "agent": "jarvis", "command": req})
                await self.engine.submit(req, source="voice", agent="jarvis",
                                         on_done=lambda c: self._open_followup("jarvis", self.ENGAGED_S))
                return

        agent_key, command = self._match_agent(text)
        entry["outcome"] = f"→ {agent_key}" if agent_key else "no wake word"
        if not agent_key and self.follow_agent and time.time() < self.follow_until:
            agent_key, command = self.follow_agent, text.strip()   # conversation mode
        if not agent_key:
            await self.hub.broadcast({
                "type": "log", "level": "info",
                "msg": f"no wake-phrase in: \"{text}\"",
            })
            await self.hub.broadcast({"type": "voice", "event": "unrouted", "text": text})
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

        # ── real-time engine: one pipeline for voice/text/UI ─────────
        if self.engine is not None:
            if not command:                                    # bare "Hey Jarvis"
                await self.engine.summon(agent_key, source="voice")
                self._open_followup(agent_key, 15.0)
                return
            # JARVIS is the coordinator: "Hey Jarvis …" lets the engine delegate
            # to the right specialist; "Hey Stark …" addresses Stark directly
            explicit = None if agent_key == "jarvis" else agent_key
            await self.engine.submit(
                command, source="voice", agent=explicit,
                on_done=lambda c, k=agent_key: self._open_followup(c.handled_by or k))
            return

        # ── legacy path (engine unavailable) ──────────────────────────
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
        self._open_followup(agent_key)

    # ── voice tool-calling: real data, instant answers ───────────
    async def _try_intent(self, agent, command: str) -> bool:
        low = command.lower()
        agenda = self.services.get("agenda")
        threats = self.services.get("threats")

        async def respond(text: str) -> None:
            await agent._emit("reply", q=command[:140], a=text[:400])
            self.convo = (self.convo + [(command, text)])[-6:]
            self._open_followup(agent.name)
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

    ENGAGED_S = 20.0

    @staticmethod
    def _mentions_jarvis(text: str) -> tuple[bool, str]:
        """("Jarvis" said anywhere, the request without the name). Tolerant of
        the usual mis-hearings ("Jarvis," "Jervis", "jarvis's")."""
        low = re.sub(r"[^\w\s']", " ", (text or "").lower())
        m = re.search(r"\b(hey |ok |okay |hi )?(jarvis|jervis|javis|jarvas|travis)('s)?\b", low)
        if not m:
            return False, ""
        rest = (low[:m.start()] + " " + low[m.end():]).strip()
        return True, re.sub(r"\s+", " ", rest).strip()

    def _conversation(self) -> bool:
        tts = self.engine.state.get("tts") if self.engine is not None else None
        return bool(getattr(tts, "conversation_mode", False))

    def diagnostics(self) -> dict:
        return {"model": getattr(self, "model_name", None), "noise_rms": round(self.noise_rms),
                "echo_rms": round(self.echo_rms), "barge_ins": self.barge_ins,
                "dropped_stale": getattr(self, "dropped_stale", 0),
                "conversation_mode": self._conversation(),
                "utterances": list(self.diag)[::-1]}

    def _open_followup(self, agent_key: str, seconds: float = 12.0) -> None:
        self.follow_agent = agent_key
        self.follow_until = time.time() + seconds

    async def _summon(self) -> None:
        """Double clap → JARVIS pops up on the dashboard and answers."""
        await self.hub.broadcast({"type": "voice", "event": "summon", "agent": "jarvis",
                                  "peak": round(self.clap.last_peak) if self.clap else None})
        self._open_followup("jarvis", 15.0)
        if self.engine is not None:
            await self.engine.summon("jarvis", source="clap")
            return
        jarvis = self.team.get("jarvis")
        greeting = "Yes, sir? I'm listening."
        if jarvis is not None:
            await jarvis._emit("reply", q="(double clap)", a=greeting)
            if jarvis.speaker:
                try:
                    await jarvis.speaker.say(greeting)
                except Exception:
                    pass

    @staticmethod
    def _transcribe(stt, audio) -> str:
        # Accuracy-first decode (operator request, 2026-09-30): beam search 5
        # with a short hint + hotwords; temperature fallback rescues low-
        # confidence segments. On the Core Ultra 9 CPU a 2-4 s command decodes
        # in ~1-2 s with `small`.
        #
        # 2026-10-05 field data: weak mic input (peaks 1-8% of full scale) made
        # Whisper re-decode at higher temperatures (up to 14 s for 2 s of audio)
        # and invent sentences ("It's going to be OK" x3). So: one temperature,
        # and segments Whisper itself isn't confident about are DROPPED rather
        # than answered — silence beats a wrong command.
        kw = dict(
            language="en",
            beam_size=5, best_of=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            initial_prompt=INITIAL_PROMPT,
            condition_on_previous_text=False,
            temperature=0.0,
        )
        try:
            segments, _info = stt.transcribe(audio, hotwords=HOTWORDS, **kw)
        except TypeError:          # older faster-whisper without `hotwords`
            segments, _info = stt.transcribe(audio, **kw)
        return " ".join(s.text for s in segments if VoiceLoop._confident(s)).strip()

    @staticmethod
    def _confident(seg) -> bool:
        """Whisper's own signals: likely-silence, low token confidence, or
        looping/repetitive output (compression ratio) mean "don't trust it"."""
        if getattr(seg, "no_speech_prob", 0.0) > 0.6:
            return False
        if getattr(seg, "avg_logprob", 0.0) < -1.0:
            return False
        if getattr(seg, "compression_ratio", 1.0) > 2.4:
            return False
        return True

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
