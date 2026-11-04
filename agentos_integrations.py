"""AI CORE · integration discovery.

Detects what this install can actually use — measured, never assumed — and how to set up
what's missing. Secret VALUES are never read out: only "present / absent".
Subprocess/network probes run off the event loop and are cached (60 s).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEY_ENVS = [("ANTHROPIC_API_KEY", "Anthropic API", "console.anthropic.com → API keys"),
            ("OPENAI_API_KEY", "OpenAI", "platform.openai.com → API keys"),
            ("GROQ_API_KEY", "Groq", "console.groq.com → API keys"),
            ("OPENROUTER_API_KEY", "OpenRouter", "openrouter.ai → Keys"),
            ("TOGETHER_API_KEY", "Together AI", "api.together.ai → Settings → API keys"),
            ("DEEPSEEK_API_KEY", "DeepSeek", "platform.deepseek.com → API keys"),
            ("MISTRAL_API_KEY", "Mistral", "console.mistral.ai → API keys"),
            ("GEMINI_API_KEY", "Google Gemini", "aistudio.google.com → Get API key")]
_cache: dict = {"at": 0.0, "data": None}
_lock = asyncio.Lock()


def _gpu() -> dict:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"status": "missing", "detail": "no NVIDIA GPU driver (nvidia-smi) found — local models run on CPU"}
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=6,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = [r.split(", ") for r in out.stdout.strip().splitlines() if r.strip()]
        if not rows:
            return {"status": "partial", "detail": "nvidia-smi found but reported no GPU"}
        return {"status": "ok", "detail": "; ".join(f"{r[0]} · {int(r[1]) / 1024:.1f} GB VRAM ({int(r[2]) / 1024:.1f} GB used)"
                                                    for r in rows if len(r) >= 3)}
    except Exception as e:
        return {"status": "error", "detail": f"nvidia-smi failed ({type(e).__name__})"}


def _spec(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _item(iid, name, group, status, detail, setup=None, **kw) -> dict:
    return {"id": iid, "name": name, "group": group, "status": status, "detail": detail, "setup": setup or [], **kw}


async def detect(state: dict, hub=None, force: bool = False) -> dict:
    async with _lock:
        if not force and _cache["data"] and time.time() - _cache["at"] < 60:
            return _cache["data"]
        items = []
        # ── AI providers ──
        oll = hub.adapters.get("ollama") if hub else None
        if oll is not None:
            h = await oll.health()
            models = [s for s in hub.models.values() if s.provider == "ollama"]
            emb = [s.model for s in models if "embeddings" in s.caps]
            tools = [s.model for s in models if "tools" in s.caps]
            if h.get("ok"):
                st = "ok" if models and emb and tools else "partial"
                det = (f"v{getattr(oll, 'version', '?')} · {len(models)} model(s): "
                       + ", ".join(s.model for s in models[:6]) + (f" · embeddings: {', '.join(emb)}" if emb else
                                                                   " · no embedding model (memory uses BM25)"))
                setup = [] if emb else ["ollama pull nomic-embed-text  (semantic memory search)"]
                if not tools:
                    setup.append("ollama pull qwen2.5:7b  (reliable local tool calling)")
            else:
                st, det = "missing", h.get("detail", "not running")
                setup = ["Install from https://ollama.com/download", "Start Ollama (tray app or `ollama serve`)",
                         "ollama pull qwen2.5:7b", "ollama pull nomic-embed-text"]
            items.append(_item("ollama", "Ollama (local models)", "AI", st, det, setup))
        brain = state.get("brain")
        cli_ok = getattr(brain, "mode", "") == "llm"
        items.append(_item("claude-cli", "Claude Code CLI", "AI", "ok" if cli_ok else "missing",
                           ("reachable" + (" · LOCKED (licence)" if getattr(brain, "cloud_locked", False) else ""))
                           if cli_ok else "claude CLI not found",
                           [] if cli_ok else ["Install Claude Code from https://claude.com/claude-code",
                                              "Run `claude` once and log in", "Restart BUCS"]))
        lm = hub.adapters.get("lmstudio") if hub else None
        if lm is not None:
            h = await lm.health()
            items.append(_item("lmstudio", "LM Studio", "AI", "ok" if h.get("ok") else "missing",
                               h.get("detail", ""), [] if h.get("ok") else
                               ["Optional: install LM Studio, load a model, start the local server on port 1234"]))
        custom = getattr(state.get("providers"), "custom", None) or []
        for env, label, where in KEY_ENVS:
            present = bool(os.getenv(env) or (env == "GEMINI_API_KEY" and os.getenv("GOOGLE_API_KEY")))
            via_registry = any(label.split()[0].lower() in (p.get("name", "") + p.get("base_url", "")).lower()
                               and p.get("api_key") for p in custom)
            st = "ok" if present or via_registry else "missing"
            det = ("key present in .env" if present else "key stored in the AI BRAIN ROUTER card" if via_registry
                   else "no key")
            items.append(_item(f"key-{env.lower()}", label, "AI keys", st, det,
                               [] if st == "ok" else [f"Get a key: {where}", f"Add {env}=… to .env (never commit it)",
                                                      "Restart BUCS — the provider appears in AI CORE automatically"]))
        # ── platform ──
        g = state.get("gcal")
        if g is not None:
            creds = bool(getattr(g, "credentials_present", lambda: False)())
            tok = bool(getattr(g, "token_present", lambda: False)())
            items.append(_item("google", "Google Calendar & Gmail", "Platform",
                               "ok" if tok else "partial" if creds else "missing",
                               "connected" if tok else "OAuth client present, not authorised" if creds else "not set up",
                               [] if tok else (["Open CALENDAR → CONNECT and approve in the browser"] if creds else
                                               ["Create an OAuth Desktop client in Google Cloud Console",
                                                "Upload credentials.json in the CALENDAR card", "Press CONNECT"])))
        try:
            import devices
            kinds: dict = {}
            for d in devices._devices.values():
                kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
            ha = kinds.get("homeassistant", 0)
            det = ", ".join(f"{k}×{n}" for k, n in kinds.items()) or "no devices registered"
            items.append(_item("devices", "Smart home / Home Assistant", "Platform",
                               "ok" if ha else "partial" if kinds else "missing", det,
                               [] if ha else ["DEVICES card → ADD → Home Assistant (URL + long-lived token)",
                                              "Or add Shelly / Tasmota / Hue / ESPHome / WLED / MQTT devices"]))
        except Exception:
            pass
        tw = bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"))
        phone_mod = (state.get("feature_modules") or {}).get("phone")
        items.append(_item("twilio", "Phone (Twilio)", "Platform", "ok" if tw else "missing",
                           ("credentials present" if tw else "no TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN")
                           + (" · phone module loaded" if phone_mod else ""),
                           [] if tw else ["Create a Twilio account and number", "Add TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN "
                                                                               "(and the number) to .env", "Restart BUCS"]))
        from agentos_tools import _computeruse_hook
        fn, name = _computeruse_hook(state)
        items.append(_item("computeruse", "Computer use", "Platform", "ok" if fn else "partial" if
                           (state.get("feature_modules") or {}).get("computeruse") else "missing",
                           f"task API: {name}()" if fn else "module loaded, no task API exposed yet" if
                           (state.get("feature_modules") or {}).get("computeruse") else "module not loaded"))
        # ── voice ──
        stt = _spec("faster_whisper")
        mic = _spec("sounddevice")
        wake = _spec("openwakeword")
        tts = _spec("pyttsx3")
        on = os.getenv("JARVIS_VOICE") == "1"
        st = "ok" if stt and mic and on else "partial" if (tts or stt) else "missing"
        items.append(_item("voice", "Voice stack", "Voice", st,
                           f"STT faster-whisper {'✓' if stt else '✗'} · mic sounddevice {'✓' if mic else '✗'} · "
                           f"wake-word {'✓' if wake else '✗'} · TTS pyttsx3 {'✓' if tts else '✗'} · JARVIS_VOICE={'1' if on else 'off'}",
                           [] if st == "ok" else ["Uncomment the wake-word stack in requirements.txt and pip install it in .venv",
                                                  "Set JARVIS_VOICE=1 in .env", "Restart BUCS"]))
        gpu = await asyncio.get_running_loop().run_in_executor(None, _gpu)
        items.append(_item("gpu", "GPU acceleration", "Hardware", gpu["status"], gpu["detail"],
                           [] if gpu["status"] == "ok" else ["Local models still work on CPU; a CUDA GPU makes them much faster"]))
        data = {"items": items, "at": time.time(),
                "summary": {s: sum(1 for i in items if i["status"] == s) for s in ("ok", "partial", "missing", "error")}}
        _cache.update(at=time.time(), data=data)
        return data
