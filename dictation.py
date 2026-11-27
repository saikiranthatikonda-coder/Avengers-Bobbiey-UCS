"""VOICE DICTATION (v8.5) · speech → editable text for the dashboard's command bar.

The browser records the microphone (only while the operator holds the mic button on),
encodes 16-bit PCM WAV and POSTs it here. Whisper transcribes it locally (the same
faster-whisper model the voice loop uses, shared when it is already loaded), and the
text goes back into the command box where the operator can edit it before sending.
Audio is never stored, logged or sent anywhere else; transcripts are not audited.

  GET  /api/dictate/status   engine availability, model, languages
  POST /api/dictate          body: audio/wav · query: lang=auto|<code>, task=transcribe|translate
                             header X-Dictate-Context (optional, ≤300 chars): text already in the box,
                             used only as Whisper's prompt so names and context carry over
  POST /api/dictate/hold     {"on": bool} · while on, the hands-free voice loop ignores what it hears
                             (the operator is dictating, not commanding); expires by itself
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import time
import wave
from typing import Callable
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

MAX_SECONDS = 240
MAX_BYTES = 16000 * 2 * MAX_SECONDS + 4096          # 16 kHz mono int16 + header
MIN_SECONDS = 0.3
HOLD_MAX = MAX_SECONDS + 30
MAX_QUEUE = 2

# Whisper's multilingual set (subset shown in the picker; "auto" detects any of Whisper's 99)
LANGUAGES = [
    ("auto", "Auto-detect"), ("en", "English"), ("hi", "Hindi · हिन्दी"), ("te", "Telugu · తెలుగు"),
    ("ta", "Tamil · தமிழ்"), ("kn", "Kannada · ಕನ್ನಡ"), ("ml", "Malayalam · മലയാളം"), ("mr", "Marathi · मराठी"),
    ("bn", "Bengali · বাংলা"), ("gu", "Gujarati · ગુજરાતી"), ("pa", "Punjabi · ਪੰਜਾਬੀ"), ("ur", "Urdu · اردو"),
    ("es", "Spanish · Español"), ("fr", "French · Français"), ("de", "German · Deutsch"),
    ("it", "Italian · Italiano"), ("pt", "Portuguese · Português"), ("nl", "Dutch · Nederlands"),
    ("ru", "Russian · Русский"), ("uk", "Ukrainian · Українська"), ("pl", "Polish · Polski"),
    ("tr", "Turkish · Türkçe"), ("ar", "Arabic · العربية"), ("fa", "Persian · فارسی"),
    ("he", "Hebrew · עברית"), ("zh", "Chinese · 中文"), ("ja", "Japanese · 日本語"),
    ("ko", "Korean · 한국어"), ("vi", "Vietnamese · Tiếng Việt"), ("th", "Thai · ไทย"),
    ("id", "Indonesian · Bahasa Indonesia"), ("ms", "Malay · Bahasa Melayu"), ("sw", "Swahili · Kiswahili"),
]
LANG_NAMES = dict(LANGUAGES)

_hold = {"until": 0.0}


def holding(now: float | None = None) -> bool:
    """True while a dashboard is dictating (the voice loop then ignores what it hears)."""
    return (now if now is not None else time.time()) < _hold["until"]


def set_hold(on: bool, now: float | None = None) -> float:
    now = now if now is not None else time.time()
    _hold["until"] = now + HOLD_MAX if on else 0.0
    return _hold["until"]


# ───────────────────────── audio ─────────────────────────

class AudioError(ValueError):
    pass


def decode_wav(data: bytes):
    """16-bit PCM WAV (any rate, mono/stereo) → float32 mono 16 kHz numpy array."""
    import numpy as np
    if not data or len(data) < 44:
        raise AudioError("no audio received")
    if len(data) > MAX_BYTES * 3:                      # stereo / 48 kHz uploads are larger; hard ceiling
        raise AudioError(f"recording too long (max {MAX_SECONDS // 60} minutes)")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError) as e:
        raise AudioError(f"not a WAV recording ({e})") from None
    if width != 2:
        raise AudioError("expected 16-bit PCM audio")
    if not 8000 <= rate <= 96000 or ch not in (1, 2):
        raise AudioError("unsupported sample rate / channel count")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch == 2:
        a = a[: len(a) // 2 * 2].reshape(-1, 2).mean(axis=1)
    if rate != 16000 and a.size:
        x_new = np.arange(0, a.size * 16000 / rate) * rate / 16000
        a = np.interp(x_new, np.arange(a.size), a).astype(np.float32)
    secs = a.size / 16000
    if secs < MIN_SECONDS:
        raise AudioError("recording too short")
    if secs > MAX_SECONDS + 1:
        raise AudioError(f"recording too long (max {MAX_SECONDS // 60} minutes)")
    return a


# ───────────────────────── addressing ─────────────────────────

_LEAD = r"^\W*(?:(?:hey|ok|okay|hi|yo|so|and|now|please)[\s,]+)*"


def addressed(text: str, team) -> tuple[str | None, str]:
    """If the dictation OPENS with an agent's name ("Doom, scan my network…"), return
    (agent, the rest with original casing/punctuation). Names mid-sentence don't count."""
    try:
        from voice import AGENT_TRIGGERS, match_custom
    except Exception:
        return None, text
    team = set(team or ())
    ckey, crest, cpos = match_custom(text)
    if ckey and ckey in team:
        lead = re.match(_LEAD, (text or "").lower())
        if cpos <= (lead.end() if lead else 0) and crest is not None:
            return ckey, crest.strip()
    aliases = {(t[4:] if t.startswith("hey ") else t, k) for t, k in AGENT_TRIGGERS}
    aliases |= {("jervis", "jarvis"), ("javis", "jarvis")}
    for phrase, key in sorted(aliases, key=lambda x: -len(x[0])):
        if key not in team:
            continue
        words = r"[\s\-]+".join(re.escape(w) for w in phrase.split())
        m = re.match(_LEAD + words + r"(?:'s)?(?!\w)[\s,.:;!?\-–—]*", text or "", re.I)
        if m:
            rest = (text or "")[m.end():].strip()
            return key, (rest[:1].upper() + rest[1:]) if rest else ""
    return None, text


# ───────────────────────── engine ─────────────────────────

def _confident(seg) -> bool:
    if getattr(seg, "no_speech_prob", 0.0) > 0.6 and getattr(seg, "avg_logprob", 0.0) < -0.7:
        return False
    if getattr(seg, "avg_logprob", 0.0) < -1.2:
        return False
    if getattr(seg, "compression_ratio", 1.0) > 2.6:
        return False
    return True


class Dictation:
    def __init__(self, state: dict | None = None, model_factory: Callable | None = None) -> None:
        self.state = state if state is not None else {}
        self.model_name = os.getenv("JARVIS_WHISPER_MODEL", "small")
        self._factory = model_factory
        self._own = None
        self._loading: asyncio.Future | None = None
        self._sem = asyncio.Semaphore(1)
        self.waiting = 0
        self.last_error = ""

    @property
    def multilingual(self) -> bool:
        return not self.model_name.endswith(".en")

    def _shared(self):
        v = self.state.get("voice")
        return getattr(v, "stt", None) if v is not None else None

    def available(self) -> tuple[bool, str]:
        if self._factory or self._own is not None or self._shared() is not None:
            return True, ""
        try:
            import faster_whisper  # noqa: F401
            import numpy  # noqa: F401
            return True, ""
        except Exception as e:
            return False, f"local speech engine not installed ({getattr(e, 'name', e)}): " \
                          "run .\\.venv\\Scripts\\pip install faster-whisper numpy"

    def status(self) -> dict:
        ok, why = self.available()
        return {"ok": True, "available": ok, "reason": why, "engine": "whisper-local",
                "model": self.model_name, "multilingual": self.multilingual,
                "loaded": self._own is not None or self._shared() is not None or bool(self._factory),
                "shared_with_voice_loop": self._shared() is not None,
                "languages": [{"code": c, "name": n} for c, n in LANGUAGES] if self.multilingual
                else [{"code": "en", "name": "English"}],
                "translate": self.multilingual, "max_seconds": MAX_SECONDS, "busy": self.waiting,
                "hold": holding()}

    async def _model(self):
        m = self._shared()
        if m is not None:
            return m
        if self._own is not None:
            return self._own
        if self._loading is None:
            loop = asyncio.get_running_loop()

            def load():
                if self._factory:
                    return self._factory(self.model_name)
                from faster_whisper import WhisperModel
                return WhisperModel(self.model_name, device="cpu", compute_type="int8")
            self._loading = loop.run_in_executor(None, load)
        try:
            self._own = await self._loading
        finally:
            self._loading = None
        return self._own

    def _run(self, model, audio, lang: str | None, task: str, prompt: str):
        kw = dict(language=lang, task=task, beam_size=5, vad_filter=True,
                  vad_parameters={"min_silence_duration_ms": 500},
                  condition_on_previous_text=True, initial_prompt=prompt or None)
        segments, info = model.transcribe(audio, **kw)
        text = " ".join(s.text.strip() for s in segments if _confident(s)).strip()
        return text, getattr(info, "language", None) or lang, getattr(info, "language_probability", None)

    async def transcribe(self, data: bytes, lang: str = "auto", task: str = "transcribe",
                         context: str = "") -> dict:
        audio = decode_wav(data)                              # raises AudioError
        lang = (lang or "auto").lower()
        if lang not in LANG_NAMES:
            raise AudioError("unknown language")
        if not self.multilingual:
            lang, task = "en", "transcribe"
        task = task if task in ("transcribe", "translate") else "transcribe"
        import numpy as np
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        secs = round(audio.size / 16000, 2)
        if peak < 0.004:
            return {"ok": True, "text": "", "duration": secs, "note": "no speech detected (the microphone was silent)"}
        if peak < 0.9:
            audio = audio * min(20.0, 0.9 / peak)
        names = "Jarvis, Stark, Cap, Widow, Hawkeye, Hulk, Thor, Vision, Doom."
        prompt = (names + " " + (context or "")[-300:]).strip()
        self.waiting += 1
        try:
            async with self._sem:
                t0 = time.perf_counter()
                model = await self._model()
                loop = asyncio.get_running_loop()
                text, det, prob = await loop.run_in_executor(
                    None, self._run, model, audio, None if lang == "auto" else lang, task, prompt)
        finally:
            self.waiting -= 1
        agent, rest = addressed(text, (self.state.get("team") or {}).keys())
        return {"ok": True, "text": text, "duration": secs,
                "language": det, "language_name": LANG_NAMES.get(det or "", det),
                "language_probability": round(prob, 2) if isinstance(prob, (int, float)) else None,
                "translated": task == "translate", "agent": agent, "agent_text": rest if agent else None,
                "took_ms": round((time.perf_counter() - t0) * 1000), "engine": "whisper-local",
                "model": self.model_name}


# ───────────────────────── wiring ─────────────────────────

def _engine(ctx) -> Dictation:
    st = ctx["state"]
    if "dictation" not in st:
        st["dictation"] = Dictation(st)
    return st["dictation"]


def build_router(ctx) -> APIRouter:
    r = APIRouter()

    @r.get("/api/dictate/status")
    async def dictate_status():
        return _engine(ctx).status()

    @r.post("/api/dictate/hold")
    async def dictate_hold(request: Request):
        try:
            b = await request.json()
        except Exception:
            b = {}
        on = bool(b.get("on")) if isinstance(b, dict) else False
        set_hold(on)
        return {"ok": True, "hold": holding()}

    @r.post("/api/dictate")
    async def dictate(request: Request, lang: str = "auto", task: str = "transcribe"):
        eng = _engine(ctx)
        ok, why = eng.available()
        if not ok:
            return JSONResponse({"ok": False, "error": why, "fallback": "browser"}, status_code=503)
        if eng.waiting >= MAX_QUEUE:
            return JSONResponse({"ok": False, "error": "dictation engine busy, try again in a moment"}, status_code=429)
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BYTES * 3:
            return JSONResponse({"ok": False, "error": f"recording too long (max {MAX_SECONDS // 60} minutes)"}, status_code=413)
        data = await request.body()
        ctx_text = unquote(request.headers.get("x-dictate-context", ""))[:300]
        try:
            return await eng.transcribe(data, lang=lang, task=task, context=ctx_text)
        except AudioError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:                                   # model load / decode failure
            eng.last_error = f"{type(e).__name__}: {e}"
            return JSONResponse({"ok": False, "error": f"transcription failed: {eng.last_error}",
                                 "fallback": "browser"}, status_code=500)
        finally:
            set_hold(False)

    return r
