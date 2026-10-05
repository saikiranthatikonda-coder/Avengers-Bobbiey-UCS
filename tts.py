import asyncio
import base64
import os
import platform
import queue
import shutil
import subprocess
import threading

OS = platform.system()   # "Windows" | "Darwin" | "Linux"


class TTSPlayer:
    """Async-friendly TTS worker.

    Windows: speech runs in a separate PowerShell + System.Speech process so it
    gets its own slot in the Windows Volume Mixer (bypasses the per-app mute
    that affects long-running python.exe under uvicorn). Since 2026-10-05 that
    process is PERSISTENT — warmed once, fed one line per utterance — which cut
    per-utterance latency from ~2.8 s to ~1.4 s. If it ever fails, playback
    falls back to the original one-process-per-utterance path.

    cancel() stops speech mid-sentence (barge-in / "stop") by killing the
    current speech process and draining the queue; the next utterance
    restarts a warm process.

    Broadcasts {voice: speak} when playback starts and {voice: idle} when it
    ends, so the dashboard waveform stays in sync.
    """

    def __init__(self, hub=None, max_queue: int = 8) -> None:
        self.hub = hub
        self.q: queue.Queue = queue.Queue()
        self.max_queue = max_queue
        self.enabled = False          # engine available (set at start)
        self.muted = False            # operator mute toggle (runtime)
        self.volume = 100             # 0-100 (Windows System.Speech)
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.voice_hint = os.getenv("JARVIS_VOICE_NAME", "Microsoft David")
        self._ps: subprocess.Popen | None = None      # persistent Windows speaker
        self._ps_lock = threading.Lock()
        self._cur: subprocess.Popen | None = None     # process speaking right now
        self._persistent_ok = os.getenv("JARVIS_TTS_PERSISTENT", "1") != "0"
        self.speaking = False
        self.cancels = 0
        self.rate = self._parse_rate(os.getenv("JARVIS_VOICE_RATE", "180"))
        self._load_pref()

    # ── operator audio preference (persists across restarts) ──────
    _PREF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_pref.json")

    def _load_pref(self) -> None:
        import json
        try:
            with open(self._PREF, encoding="utf-8") as f:
                d = json.load(f)
            self.muted = bool(d.get("muted", False))
            self.volume = max(0, min(100, int(d.get("volume", 100))))
        except Exception:
            pass

    def _save_pref(self) -> None:
        import json
        try:
            with open(self._PREF, "w", encoding="utf-8") as f:
                json.dump({"muted": self.muted, "volume": self.volume}, f)
        except Exception:
            pass

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        if self.muted:
            self._drain()             # drop anything already queued
        self._save_pref()

    def set_volume(self, volume: int) -> None:
        self.volume = max(0, min(100, int(volume)))
        self._save_pref()

    def _drain(self) -> None:
        try:
            while not self.q.empty():
                item = self.q.get_nowait()
                if item and item[1] and self.loop and not item[1].done():
                    self.loop.call_soon_threadsafe(item[1].set_result, True)
        except Exception:
            pass

    def audio_state(self) -> dict:
        return {"available": self.enabled, "muted": self.muted, "volume": self.volume}

    @staticmethod
    def _parse_rate(s: str) -> int:
        # pyttsx3 used WPM (~180). System.Speech uses -10..+10.
        # Map roughly: 180 wpm → 0, 220 → +2, 140 → -2.
        try:
            wpm = int(s)
        except Exception:
            wpm = 180
        rate = int(round((wpm - 180) / 20))
        return max(-10, min(10, rate))

    def start(self) -> bool:
        # Pick a speech backend for this OS. Returns False (silent dashboard)
        # only if no engine is available.
        self.engine = self._detect_engine()
        if not self.engine:
            return False
        self.enabled = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return True

    def _detect_engine(self) -> str | None:
        if OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Add-Type -AssemblyName System.Speech; 'OK'"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=self._creation_flags())
                if r.returncode == 0 and "OK" in (r.stdout or ""):
                    return "windows"
            except Exception:
                pass
            return None
        if OS == "Darwin":
            return "macos" if shutil.which("say") else None
        # Linux
        if shutil.which("spd-say"):
            return "linux-spd"
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            return "linux-espeak"
        return None

    @staticmethod
    def _creation_flags() -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def _emit(self, event: str, **kw) -> None:
        if not (self.hub and self.loop):
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.hub.broadcast({"type": "voice", "event": event, **kw}),
                self.loop,
            )
        except Exception:
            pass

    def _build_ps_command(self, text: str) -> str:
        # Pass text as base64 to avoid PowerShell quoting/escaping pitfalls.
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        voice_part = ""
        if self.voice_hint:
            v_b64 = base64.b64encode(self.voice_hint.encode("utf-8")).decode("ascii")
            voice_part = (
                f"$vbytes = [Convert]::FromBase64String('{v_b64}'); "
                f"$vname = [System.Text.Encoding]::UTF8.GetString($vbytes); "
                f"try {{ foreach ($v in $s.GetInstalledVoices()) {{ "
                f"  if ($v.VoiceInfo.Name -like \"*$vname*\") {{ $s.SelectVoice($v.VoiceInfo.Name); break }} "
                f"}} }} catch {{}}; "
            )
        return (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Volume = {self.volume}; "
            f"$s.Rate = {self.rate}; "
            + voice_part +
            f"$bytes = [Convert]::FromBase64String('{b64}'); "
            "$text = [System.Text.Encoding]::UTF8.GetString($bytes); "
            "$s.Speak($text); "
            "$s.Dispose()"
        )

    # ── persistent Windows speaker ───────────────────────────────
    def _ps_script(self) -> str:
        voice_part = ""
        if self.voice_hint:
            v_b64 = base64.b64encode(self.voice_hint.encode("utf-8")).decode("ascii")
            voice_part = (
                f"$vname = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{v_b64}')); "
                "try { foreach ($v in $s.GetInstalledVoices()) { "
                "  if ($v.VoiceInfo.Name -like \"*$vname*\") { $s.SelectVoice($v.VoiceInfo.Name); break } "
                "} } catch {}; "
            )
        # protocol: one line per utterance "<volume>|<rate>|<base64 utf-8 text>",
        # answered by DONE once playback finishes
        return (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            + voice_part +
            "[Console]::Out.WriteLine('READY'); [Console]::Out.Flush(); "
            "while (($l = [Console]::In.ReadLine()) -ne $null) { "
            "  $p = $l.Split('|'); $s.Volume = [int]$p[0]; $s.Rate = [int]$p[1]; "
            "  $s.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p[2]))); "
            "  [Console]::Out.WriteLine('DONE'); [Console]::Out.Flush() }"
        )

    def _ensure_ps(self) -> subprocess.Popen | None:
        with self._ps_lock:
            if self._ps is not None and self._ps.poll() is None:
                return self._ps
            try:
                self._ps = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", self._ps_script()],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", creationflags=self._creation_flags())
                if (self._ps.stdout.readline() or "").strip() != "READY":
                    raise RuntimeError("speaker did not start")
                return self._ps
            except Exception:
                self._ps = None
                return None

    def warm(self) -> None:
        """Start the persistent speaker ahead of the first utterance."""
        if getattr(self, "engine", None) == "windows" and self._persistent_ok:
            threading.Thread(target=self._ensure_ps, daemon=True).start()

    def _speak_persistent(self, text: str) -> bool:
        ps = self._ensure_ps()
        if ps is None:
            return False
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        try:
            self._cur = ps
            ps.stdin.write(f"{self.volume}|{self.rate}|{b64}\n")
            ps.stdin.flush()
            ps.stdout.readline()          # returns "" when cancel() killed it — fine
            return True
        except Exception:
            return ps.poll() is not None  # killed by cancel() → handled, not a failure
        finally:
            self._cur = None

    def cancel(self) -> bool:
        """Barge-in: stop speaking now and drop anything queued."""
        was = self.speaking or not self.q.empty()
        self._drain()
        cur = self._cur
        if cur is not None and cur.poll() is None:
            try:
                cur.kill()
            except Exception:
                pass
            if cur is self._ps:
                self._ps = None
                self.warm()               # pre-warm the replacement now, not on next say
        if was:
            self.cancels += 1
        return was

    def _speak_blocking(self, text: str) -> None:
        eng = getattr(self, "engine", "windows")
        if eng == "windows":
            if self._persistent_ok and self._speak_persistent(text):
                return
            proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", self._build_ps_command(text)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=self._creation_flags())
            self._cur = proc
            try:
                proc.wait(timeout=120)
            finally:
                self._cur = None
        elif eng == "macos":
            # macOS `say` — rate in words/min (~180 default)
            wpm = os.getenv("JARVIS_VOICE_RATE", "180")
            args = ["say", "-r", str(wpm)]
            voice = os.getenv("JARVIS_MAC_VOICE", "")   # e.g. "Daniel", "Samantha"
            if voice:
                args += ["-v", voice]
            args.append(text)
            subprocess.run(args, capture_output=True, text=True, timeout=120)
        elif eng == "linux-spd":
            subprocess.run(["spd-say", "-w", "-r", "0", text],
                           capture_output=True, text=True, timeout=120)
        elif eng == "linux-espeak":
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            subprocess.run([exe, text], capture_output=True, text=True, timeout=120)

    def _worker(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                break
            text, fut = item[0], item[1]
            on_start = item[2] if len(item) > 2 else None
            self.speaking = True
            if on_start and self.loop:            # real playback start (latency metric)
                self.loop.call_soon_threadsafe(on_start)
            self._emit("speak", text=text[:200])
            try:
                self._speak_blocking(text)
            except Exception as e:
                print(f"tts play error: {e}")
            finally:
                self.speaking = False
                if self.q.empty():
                    self._emit("idle")
                if self.loop and fut and not fut.done():
                    self.loop.call_soon_threadsafe(fut.set_result, True)

    async def say(self, text: str, on_start=None) -> None:
        if not self.enabled or self.muted or self.volume == 0 or not text or not text.strip():
            return
        if text.lstrip().startswith("["):
            return  # don't speak internal error markers
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        if self.q.qsize() >= self.max_queue:
            return  # backpressure
        fut = self.loop.create_future()
        self.q.put((text, fut, on_start))
        try:
            await fut
        except Exception:
            pass

    def stop(self) -> None:
        if self.enabled:
            self.enabled = False
            self.q.put(None)
        ps = self._ps
        if ps is not None and ps.poll() is None:
            try:
                ps.kill()
            except Exception:
                pass
