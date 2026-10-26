"""LLM provider registry — switch brains and plug in any model from the dashboard.

Built-ins:  "auto"   — Claude CLI → local LLM → rule templates (the original chain)
            "claude" — Claude Code CLI only (then the chain's fallbacks)
            "ollama" — the local OpenAI-compatible endpoint (LocalLLM)
Custom:     any OpenAI-compatible /v1 endpoint (OpenAI, Anthropic, Gemini, Groq,
            OpenRouter, DeepSeek, Mistral, Together, LM Studio, vLLM, …) added
            with base_url + model (+ API key when the service needs one).

Stored in providers.json — gitignored, protected from engineering-mode edits.
API keys never leave this machine except to the provider they belong to, and
are never returned to the browser in full (masked).

Whatever is selected, a failure falls back to the original chain, so BUCS
never goes silent because one provider is down or out of credit.
"""

import json
import re
import time
from pathlib import Path

from llm_local import LocalLLM

FILE = Path(__file__).parent / "providers.json"

PRESETS = {
    "openai":     {"name": "OpenAI",             "base_url": "https://api.openai.com/v1",                               "model": "gpt-4o-mini",               "key": True},
    "anthropic":  {"name": "Anthropic API",      "base_url": "https://api.anthropic.com/v1",                            "model": "claude-haiku-4-5-20251001", "key": True},
    "gemini":     {"name": "Google Gemini",      "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.0-flash",          "key": True},
    "groq":       {"name": "Groq",               "base_url": "https://api.groq.com/openai/v1",                          "model": "llama-3.3-70b-versatile",   "key": True},
    "openrouter": {"name": "OpenRouter",         "base_url": "https://openrouter.ai/api/v1",                            "model": "openrouter/auto",           "key": True},
    "deepseek":   {"name": "DeepSeek",           "base_url": "https://api.deepseek.com/v1",                             "model": "deepseek-chat",             "key": True},
    "mistral":    {"name": "Mistral",            "base_url": "https://api.mistral.ai/v1",                               "model": "mistral-small-latest",      "key": True},
    "together":   {"name": "Together AI",        "base_url": "https://api.together.xyz/v1",                             "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "key": True},
    "lmstudio":   {"name": "LM Studio (local)",  "base_url": "http://127.0.0.1:1234/v1",                                "model": "",                          "key": False},
    "custom":     {"name": "Custom endpoint",    "base_url": "",                                                        "model": "",                          "key": False},
}
CLOUD = "cloud"      # tier label used for entitlements (paid editions)
LOCAL = "local"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:32] or "provider"


def _is_local(url: str) -> bool:
    return bool(re.match(r"https?://(127\.0\.0\.1|localhost|\[::1\])(:|/|$)", url or ""))


def mask(key: str | None) -> str:
    if not key:
        return ""
    return key[:3] + "…" + key[-4:] if len(key) > 10 else "••••"


class ProviderRegistry:
    def __init__(self, brain, local_llm: LocalLLM, path: Path = FILE) -> None:
        self.brain, self.local = brain, local_llm
        self.path = Path(path)
        self.custom: list[dict] = []
        self.active = "auto"
        self.clients: dict[str, LocalLLM] = {}
        self.health: dict[str, dict] = {}
        self.gate = None          # licensing.allows — None = everything allowed (tests, owner)
        self._load()
        self._apply()

    # ── persistence ───────────────────────────────────────────────
    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.custom = [p for p in d.get("providers", []) if p.get("id") and p.get("base_url")]
            self.active = d.get("active", "auto")
        except Exception:
            # first run: keep whatever the old MODEL menu persisted
            self.custom = []
            self.active = "ollama" if getattr(self.local, "pref_force_local", False) else "auto"

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"active": self.active, "providers": self.custom}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ── catalogue ─────────────────────────────────────────────────
    def list(self) -> list[dict]:
        out = [
            {"id": "auto", "name": "Auto (Claude → local → rules)", "kind": "chain", "tier": CLOUD,
             "model": "", "builtin": True},
            {"id": "claude", "name": "Claude (Claude Code CLI)", "kind": "claude-cli", "tier": CLOUD,
             "model": "your Claude login", "builtin": True,
             "available": getattr(self.brain, "mode", "") == "llm"},
            {"id": "ollama", "name": "Ollama (local)", "kind": "openai-compatible", "tier": LOCAL,
             "model": self.local.model or "", "base_url": self.local.base_url, "builtin": True,
             "available": bool(self.local.available)},
        ]
        for p in self.custom:
            out.append({"id": p["id"], "name": p["name"], "kind": "openai-compatible",
                        "tier": LOCAL if _is_local(p["base_url"]) else CLOUD,
                        "model": p.get("model", ""), "base_url": p["base_url"],
                        "api_key": mask(p.get("api_key")), "builtin": False})
        cloud_ok = self.cloud_allowed()
        for p in out:
            p["active"] = p["id"] == self.active
            p["health"] = self.health.get(p["id"])
            p["locked"] = p["tier"] == CLOUD and not cloud_ok
        return out

    def cloud_allowed(self) -> bool:
        return self.gate is None or bool(self.gate("cloud_brain"))

    def get(self, pid: str) -> dict | None:
        return next((p for p in self.list() if p["id"] == pid), None)

    def upsert(self, name: str, base_url: str, model: str, api_key: str | None = None,
               pid: str | None = None) -> dict:
        base_url = (base_url or "").strip().rstrip("/")
        if not re.match(r"https?://", base_url):
            return {"ok": False, "error": "base URL must start with http:// or https://"}
        if not base_url.startswith("https://") and not _is_local(base_url):
            return {"ok": False, "error": "remote providers must use https://"}
        if not (model or "").strip():
            return {"ok": False, "error": "model name is required"}
        pid = pid or _slug(name)
        if pid in ("auto", "claude", "ollama"):
            pid += "-custom"
        existing = next((p for p in self.custom if p["id"] == pid), None)
        entry = {"id": pid, "name": (name or pid).strip()[:40], "base_url": base_url, "model": model.strip()[:120],
                 "api_key": (api_key.strip() if api_key else (existing or {}).get("api_key", "")), "added": time.time()}
        if existing:
            self.custom[self.custom.index(existing)] = entry
        else:
            self.custom.append(entry)
        self.clients.pop(pid, None)
        self._save()
        return {"ok": True, "id": pid}

    def remove(self, pid: str) -> dict:
        before = len(self.custom)
        self.custom = [p for p in self.custom if p["id"] != pid]
        if self.active == pid:
            self.active = "auto"
        self.clients.pop(pid, None)
        self._save(); self._apply()
        return {"ok": len(self.custom) < before}

    # ── switching ─────────────────────────────────────────────────
    def client(self, pid: str) -> LocalLLM | None:
        if pid == "ollama":
            return self.local
        p = next((x for x in self.custom if x["id"] == pid), None)
        if p is None:
            return None
        c = self.clients.get(pid)
        if c is None:
            c = LocalLLM(base_url=p["base_url"], model=p["model"], api_key=p.get("api_key") or None,
                         isolated=True)
            c.available = True                       # trust until a call fails
            self.clients[pid] = c
        return c

    def set_active(self, pid: str) -> dict:
        if pid not in ("auto", "claude") and self.client(pid) is None:
            return {"ok": False, "error": "unknown provider"}
        self.active = pid
        self._save(); self._apply()
        return {"ok": True, "active": pid}

    def _apply(self) -> None:
        """Point the Brain at the selected provider (None = original chain)."""
        b = self.brain
        b.provider = None
        b.provider_name = None
        b.prefer_claude = self.active == "claude"
        b.cloud_locked = not self.cloud_allowed()
        cloud = self.active in ("auto", "claude") or (
            self.active != "ollama" and not _is_local(next((p["base_url"] for p in self.custom
                                                           if p["id"] == self.active), "")))
        if cloud and not self.cloud_allowed():
            # unlicensed customer install: cloud brains are a paid edition → local AI only
            b.prefer_claude = False
            b.force_local = True
            return
        if self.active == "ollama":
            b.force_local = True
        elif self.active not in ("auto", "claude"):
            b.force_local = False
            b.provider = self.client(self.active)
            b.provider_name = self.active
        else:
            b.force_local = False

    # ── health check: a real round-trip ───────────────────────────
    async def test(self, pid: str) -> dict:
        t = time.perf_counter()
        if pid == "claude":
            reply = await self.brain._llm_call("Reply with exactly: OK", None, 60)
            ok = not reply.startswith("[")
        elif pid == "auto":
            reply, path = await self.brain.stream("Reply with exactly: OK")
            ok = path != "offline"
        else:
            c = self.client(pid)
            if c is None:
                return {"ok": False, "error": "unknown provider"}
            reply = await c.chat("Reply with exactly: OK", timeout=45, max_tokens=8)
            ok = bool(reply)
            reply = reply or (c.last_error or "no reply")
        res = {"ok": ok, "latency_ms": round((time.perf_counter() - t) * 1000), "reply": (reply or "")[:120],
               "ts": time.time()}
        self.health[pid] = res
        return res
