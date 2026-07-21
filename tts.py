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

    Each utterance spawns a fresh PowerShell + System.Speech process so it gets
    its own slot in the Windows Volume Mixer — bypasses the per-app mute that
    affects long-running python.exe under uvicorn.

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

    def _speak_blocking(self, text: str) -> None:
        eng = getattr(self, "engine", "windows")
        if eng == "windows":
            subprocess.run(["powershell", "-NoProfile", "-Command", self._build_ps_command(text)],
                           capture_output=True, text=True, timeout=120,
                           creationflags=self._creation_flags())
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
            text, fut = item
            self._emit("speak", text=text[:200])
            try:
                self._speak_blocking(text)
            except Exception as e:
                print(f"tts play error: {e}")
            finally:
                self._emit("idle")
                if self.loop and fut and not fut.done():
                    self.loop.call_soon_threadsafe(fut.set_result, True)

    async def say(self, text: str) -> None:
        if not self.enabled or self.muted or self.volume == 0 or not text or not text.strip():
            return
        if text.lstrip().startswith("["):
            return  # don't speak internal error markers
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        if self.q.qsize() >= self.max_queue:
            return  # backpressure
        fut = self.loop.create_future()
        self.q.put((text, fut))
        try:
            await fut
        except Exception:
            pass

    def stop(self) -> None:
        if self.enabled:
            self.enabled = False
            self.q.put(None)
