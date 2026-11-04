"""AI CORE · provider layer: a capability registry + one adapter per provider family.

Every model the platform can reach is described by a ModelSpec (what it can do, how big
its context is, how fast/cheap/good it is, whether it runs on this machine). Adapters do
the talking; the router (agentos_router.py) only ever looks at ModelSpecs + live health.

Adding a provider = adding an Adapter subclass (discover/chat/embed/health). Nothing else
in the platform changes.

Adapters shipped:
  OllamaAdapter        local Ollama: /api/tags discovery (real capabilities + context),
                       chat + native tool calls over /v1, /api/embed embeddings, model pulls
  OpenAICompatAdapter  OpenAI, Groq, OpenRouter, Together, DeepSeek, Mistral, Gemini
                       (OpenAI-compatible endpoint), LM Studio, and every custom endpoint
                       the operator added in the AI BRAIN ROUTER card (providers.json)
  AnthropicAdapter     Anthropic Messages API (native tools) when ANTHROPIC_API_KEY is set
  ClaudeCLIAdapter     the existing Claude Code CLI path (brain._claude_stream); no native
                       tools, so the tool loop uses the JSON protocol with it

Secrets: keys are read from the environment / the in-process provider registry and only
ever sent to the provider they belong to. Nothing here returns a key; public() views carry
`has_key` booleans only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx

CAPS = ("chat", "streaming", "tools", "vision", "computer_use", "embeddings", "stt", "tts",
        "json_mode", "reasoning", "code")

LOCAL_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:|/|$)")

# Ollama models worth pulling, by job. Shown as suggestions; pulling needs the commander.
PULL_SUGGESTIONS = [
    {"model": "nomic-embed-text", "why": "embeddings: semantic memory search (274 MB)", "cap": "embeddings"},
    {"model": "qwen2.5:7b", "why": "reliable native tool calling, good general quality (4.7 GB)", "cap": "tools"},
    {"model": "qwen2.5-coder:7b", "why": "code tasks offline (4.7 GB)", "cap": "code"},
    {"model": "llama3.2-vision", "why": "image understanding offline (7.9 GB)", "cap": "vision"},
    {"model": "deepseek-r1:7b", "why": "step-by-step reasoning offline (4.7 GB)", "cap": "reasoning"},
]

# OpenAI-compatible cloud families detected from env keys (names only — values never leave
# this process except to their own endpoint).
CLOUD_FAMILIES = {
    "openai":     {"name": "OpenAI", "env": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1",
                   "models": ["gpt-4o-mini", "gpt-4o"], "embed": "text-embedding-3-small"},
    "groq":       {"name": "Groq", "env": "GROQ_API_KEY", "base_url": "https://api.groq.com/openai/v1",
                   "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]},
    "openrouter": {"name": "OpenRouter", "env": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1",
                   "models": ["openrouter/auto"]},
    "together":   {"name": "Together AI", "env": "TOGETHER_API_KEY", "base_url": "https://api.together.xyz/v1",
                   "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"]},
    "deepseek":   {"name": "DeepSeek", "env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com/v1",
                   "models": ["deepseek-chat", "deepseek-reasoner"]},
    "mistral":    {"name": "Mistral", "env": "MISTRAL_API_KEY", "base_url": "https://api.mistral.ai/v1",
                   "models": ["mistral-small-latest", "mistral-large-latest"], "embed": "mistral-embed"},
    "gemini":     {"name": "Google Gemini", "env": "GEMINI_API_KEY", "alt_env": "GOOGLE_API_KEY",
                   "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                   "models": ["gemini-2.0-flash", "gemini-2.5-pro"]},
}


# ── capability model ─────────────────────────────────────────────
@dataclass
class ModelSpec:
    provider: str                 # adapter id
    model: str                    # provider's model name
    caps: set = field(default_factory=set)
    context: int = 8192           # usable context window (tokens)
    quality: int = 3              # 1..5
    speed: int = 2                # latency class 1 fast .. 3 slow
    cost: int = 1                 # 0 free/local .. 3 premium
    local: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"

    def public(self) -> dict:
        return {"id": self.id, "provider": self.provider, "model": self.model, "caps": sorted(self.caps),
                "context": self.context, "quality": self.quality, "speed": self.speed, "cost": self.cost,
                "local": self.local, "meta": {k: v for k, v in self.meta.items() if k in
                                              ("size_gb", "params", "quant", "family", "note")}}


def _params_b(s) -> float | None:
    m = re.search(r"([\d.]+)\s*([bm])", str(s or "").lower())
    if not m:
        return None
    v = float(m.group(1))
    return v / 1000 if m.group(2) == "m" else v


def infer_spec(provider: str, model: str, *, local: bool, kind: str, meta: dict | None = None) -> ModelSpec:
    """Best-effort capabilities from what the provider told us (Ollama reports real
    capabilities + context) and the model name for the rest."""
    meta = dict(meta or {})
    n = model.lower()
    reported = set(meta.get("capabilities") or [])
    caps: set = set()
    if "embed" in n or reported == {"embedding"} or ("embedding" in reported and "completion" not in reported):
        caps = {"embeddings"}
    else:
        caps = {"chat", "streaming"}
        if reported:
            if "tools" in reported:
                caps.add("tools")
            if "vision" in reported:
                caps.add("vision")
            if "thinking" in reported:
                caps.add("reasoning")
        else:
            if kind != "ollama" and not re.search(r"reasoner|instruct-0|-r1\b", n):
                caps.add("tools")
            if re.search(r"gpt-4o|gpt-4\.1|gpt-5|gemini|claude|pixtral|vision|llava|vl\b|gemma3|qwen2\.5vl", n):
                caps.add("vision")
        if kind in ("openai", "ollama", "anthropic"):
            caps.add("json_mode")
        if re.search(r"\br1\b|-r1|reasoner|\bo[134]\b|o[134]-|thinking|opus|sonnet|gpt-5|2\.5-pro", n):
            caps.add("reasoning")
        if re.search(r"coder|codestral|code|claude|gpt-4|gpt-5|deepseek|qwen", n):
            caps.add("code")
    p = _params_b(meta.get("params"))
    if local:
        q = 2 if (p or 3) < 4 else 3 if p < 10 else 4
        speed = 1 if (p or 3) < 10 else 2
        cost = 0
        ctx = int(meta.get("context") or 8192)
        ctx = min(ctx, int(meta.get("served_context") or 8192))   # Ollama serves a smaller window by default
    else:
        q = 4
        if re.search(r"opus|gpt-5|2\.5-pro|large|70b|405b|sonnet|gpt-4o(?!-mini)|gpt-4\.1(?!-mini)", n):
            q = 5
        if re.search(r"mini|flash|haiku|small|8b|instant", n):
            q = 3 if "8b" in n or "instant" in n else 4
        speed = 1 if (provider.startswith("groq") or re.search(r"mini|flash|haiku|instant|8b", n)) else 2
        cost = 1 if re.search(r"mini|flash|haiku|small|8b|instant|deepseek", n) else 3 if q == 5 else 2
        ctx = 1_000_000 if "gemini" in n else 200_000 if "claude" in n else 64_000 if "deepseek" in n \
            else 32_000 if "mistral" in n and "large" not in n else 128_000
    return ModelSpec(provider=provider, model=model, caps=caps, context=ctx, quality=q, speed=speed,
                     cost=cost, local=local, meta=meta)


# ── health ───────────────────────────────────────────────────────
class Health:
    """Live per-model measurements. EWMA latency from REAL calls; consecutive failures
    put a model in a short cooldown so routing skips it, then it gets retried."""

    def __init__(self) -> None:
        self.m: dict[str, dict] = {}

    def get(self, mid: str) -> dict:
        return self.m.setdefault(mid, {"ok": 0, "fail": 0, "streak": 0, "ewma_ms": None, "last_ms": None,
                                       "last_ok": None, "last_error": None, "cooldown_until": 0.0})

    def record(self, mid: str, ok: bool, ms: float | None, err: str | None = None) -> None:
        h = self.get(mid)
        if ok:
            h["ok"] += 1
            h["streak"] = 0
            h["last_ok"] = time.time()
            h["cooldown_until"] = 0.0
            if ms is not None:
                h["last_ms"] = round(ms)
                h["ewma_ms"] = round(ms if h["ewma_ms"] is None else 0.6 * h["ewma_ms"] + 0.4 * ms)
        else:
            h["fail"] += 1
            h["streak"] += 1
            h["last_error"] = (err or "failed")[:200]
            h["cooldown_until"] = time.time() + min(600, 20 * (2 ** (h["streak"] - 1)))

    def cooling(self, mid: str) -> bool:
        return self.get(mid)["cooldown_until"] > time.time()


_KEYLIKE = re.compile(r"(sk-ant-|sk-|gsk_|AIza|xai-|pk-|Bearer\s+)[A-Za-z0-9_\-\.]{6,}")


def scrub(s: str | None) -> str | None:
    """Provider error bodies can echo (part of) a key — never let that reach the browser/logs."""
    return None if s is None else _KEYLIKE.sub("[redacted]", str(s))


def result(ok: bool, text: str = "", tool_calls: list | None = None, error: str | None = None, **kw) -> dict:
    return {"ok": ok, "text": text or "", "tool_calls": tool_calls or [], "error": scrub(error), **kw}


def _strip_think(t: str) -> str:
    return re.sub(r"<think>.*?</think>", "", t or "", flags=re.DOTALL).strip()


def _args(a) -> dict:
    if isinstance(a, dict):
        return a
    try:
        v = json.loads(a or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except Exception:
        return {"_raw": str(a)[:500]}


# ── adapters ─────────────────────────────────────────────────────
class Adapter:
    id = "adapter"
    name = "Adapter"
    kind = "generic"
    local = False
    native_tools = True
    transport = None                      # tests inject httpx.MockTransport

    def configured(self) -> bool:
        return True

    def setup_hint(self) -> str:
        return ""

    async def discover(self) -> list[ModelSpec]:
        return []

    async def health(self) -> dict:
        return {"ok": self.configured(), "detail": ""}

    async def chat(self, spec: ModelSpec, messages: list[dict], *, tools: list | None = None,
                   json_mode: bool = False, max_tokens: int = 800, timeout: float = 90,
                   on_delta=None, images: list | None = None) -> dict:
        raise NotImplementedError

    async def embed(self, model: str, texts: list[str], timeout: float = 30) -> list[list[float]] | None:
        return None

    def _client(self, timeout: float) -> httpx.AsyncClient:
        kw = {"timeout": httpx.Timeout(timeout, connect=min(6.0, timeout)), "trust_env": False}
        if self.transport is not None:
            kw["transport"] = self.transport
        return httpx.AsyncClient(**kw)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "local": self.local,
                "configured": self.configured(), "native_tools": self.native_tools, "setup": self.setup_hint()}


class OpenAICompatAdapter(Adapter):
    kind = "openai"

    def __init__(self, aid: str, name: str, base_url: str, api_key: str | None = None,
                 models: list[str] | None = None, embed_model: str | None = None, discover_models: bool = False,
                 key_env: str | None = None) -> None:
        self.id, self.name = aid, name
        self.base_url = base_url.rstrip("/")
        self._key = api_key or None
        self.models = [m for m in (models or []) if m]
        self.embed_model = embed_model
        self.discover_models = discover_models
        self.key_env = key_env
        self.local = bool(LOCAL_RE.match(self.base_url))

    def configured(self) -> bool:
        return self.local or bool(self._key)

    def setup_hint(self) -> str:
        if self.configured():
            return ""
        return f"Add {self.key_env} to .env (or add the provider in the AI BRAIN ROUTER card) and restart."

    def _headers(self) -> dict:
        h = {"content-type": "application/json"}
        if self._key:
            h["authorization"] = f"Bearer {self._key}"
        return h

    async def _list(self) -> list[str]:
        async with self._client(6) as c:
            r = await c.get(f"{self.base_url}/models", headers=self._headers())
            r.raise_for_status()
            return [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]

    async def discover(self) -> list[ModelSpec]:
        if not self.configured():
            return []
        names = list(self.models)
        if self.discover_models or not names:
            try:
                listed = await self._list()
                names = names or listed[:8]
                if self.local:
                    names = listed[:12]
            except Exception:
                if self.local:
                    return []                    # LM Studio etc. not running
        out = [infer_spec(self.id, n, local=self.local, kind="openai") for n in names]
        if self.embed_model:
            out.append(ModelSpec(self.id, self.embed_model, {"embeddings"}, 8192, 3, 1, 1, self.local))
        return out

    async def health(self) -> dict:
        if not self.configured():
            return {"ok": False, "detail": "not configured"}
        t = time.perf_counter()
        try:
            n = len(await self._list())
            return {"ok": True, "detail": f"{n} models listed", "ms": round((time.perf_counter() - t) * 1000)}
        except httpx.HTTPStatusError as e:
            return {"ok": False, "detail": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"ok": False, "detail": type(e).__name__}

    async def chat(self, spec, messages, *, tools=None, json_mode=False, max_tokens=800, timeout=90,
                   on_delta=None, images=None) -> dict:
        msgs = [dict(m) for m in messages]
        if images:
            for m in reversed(msgs):
                if m.get("role") == "user":
                    m["content"] = [{"type": "text", "text": m.get("content") or ""}] + [
                        {"type": "image_url", "image_url": {"url": u}} for u in images]
                    break
        body: dict = {"model": spec.model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0.3}
        if tools:
            body["tools"] = tools
        if json_mode and not tools:
            body["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        try:
            async with self._client(timeout) as c:
                if on_delta and not tools:
                    body["stream"] = True
                    parts: list[str] = []
                    async with c.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(),
                                        json=body) as r:
                        if r.status_code >= 400:
                            await r.aread()
                            return result(False, error=f"HTTP {r.status_code}: {r.text[:160]}")
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            d = line[5:].strip()
                            if d == "[DONE]":
                                break
                            try:
                                ch = (json.loads(d).get("choices") or [{}])[0].get("delta", {}).get("content")
                            except Exception:
                                continue
                            if ch:
                                parts.append(ch)
                                await on_delta(ch)
                    return result(True, _strip_think("".join(parts)), ms=(time.perf_counter() - t0) * 1000)
                r = await c.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
                if r.status_code >= 400:
                    return result(False, error=f"HTTP {r.status_code}: {r.text[:160]}", status=r.status_code)
                data = r.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            calls = [{"id": tc.get("id") or f"call_{i}", "name": (tc.get("function") or {}).get("name", ""),
                      "arguments": _args((tc.get("function") or {}).get("arguments"))}
                     for i, tc in enumerate(msg.get("tool_calls") or [])]
            text = _strip_think(msg.get("content") or "")
            if on_delta and text:
                await on_delta(text)
            return result(True, text, calls, ms=(time.perf_counter() - t0) * 1000, usage=data.get("usage"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return result(False, error=f"{type(e).__name__}: {str(e)[:160]}")

    async def embed(self, model, texts, timeout=30):
        try:
            async with self._client(timeout) as c:
                r = await c.post(f"{self.base_url}/embeddings", headers=self._headers(),
                                 json={"model": model, "input": texts})
                r.raise_for_status()
                return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d.get("index", 0))]
        except Exception:
            return None


class OllamaAdapter(OpenAICompatAdapter):
    kind = "ollama"

    def __init__(self, base_url: str | None = None) -> None:
        url = (base_url or os.getenv("LOCAL_LLM_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
        super().__init__("ollama", "Ollama (local)", url if url.endswith("/v1") else url + "/v1")
        self.root = self.base_url[:-3]
        self.local = True
        self.version = None
        self.pulls: dict[str, dict] = {}

    def configured(self) -> bool:
        return True

    def setup_hint(self) -> str:
        return "Install Ollama from ollama.com, start it, then `ollama pull qwen2.5:7b` (tools) and `ollama pull nomic-embed-text` (memory search)."

    async def discover(self) -> list[ModelSpec]:
        async with self._client(5) as c:
            try:
                v = await c.get(f"{self.root}/api/version")
                self.version = (v.json() or {}).get("version")
            except Exception:
                self.version = None
            try:
                r = await c.get(f"{self.root}/api/tags")
                r.raise_for_status()
                tags = r.json().get("models") or []
            except Exception:
                return []
        out = []
        for m in tags:
            name = m.get("name") or m.get("model")
            if not name:
                continue
            det = m.get("details") or {}
            meta = {"size_gb": round((m.get("size") or 0) / 1e9, 1), "params": det.get("parameter_size"),
                    "quant": det.get("quantization_level"), "family": det.get("family"),
                    "context": det.get("context_length") or 8192,
                    "served_context": int(os.getenv("OLLAMA_CONTEXT_LENGTH") or 8192)}
            if m.get("capabilities") is not None:
                meta["capabilities"] = m["capabilities"]
            out.append(infer_spec("ollama", name, local=True, kind="ollama", meta=meta))
        return out

    async def health(self) -> dict:
        t = time.perf_counter()
        try:
            async with self._client(4) as c:
                r = await c.get(f"{self.root}/api/tags")
                r.raise_for_status()
                n = len(r.json().get("models") or [])
            return {"ok": True, "detail": f"running · {n} model(s)", "ms": round((time.perf_counter() - t) * 1000)}
        except Exception as e:
            return {"ok": False, "detail": f"not reachable ({type(e).__name__})"}

    async def embed(self, model, texts, timeout=30):
        try:
            async with self._client(timeout) as c:
                r = await c.post(f"{self.root}/api/embed", json={"model": model, "input": texts})
                r.raise_for_status()
                return r.json().get("embeddings")
        except Exception:
            return None

    async def pull(self, model: str) -> None:
        """Stream an `ollama pull` with progress into self.pulls[model]."""
        st = self.pulls[model] = {"model": model, "status": "starting", "pct": 0, "done": False, "error": None,
                                  "started": time.time()}
        try:
            async with self._client(3600) as c:
                async with c.stream("POST", f"{self.root}/api/pull", json={"model": model, "stream": True}) as r:
                    async for line in r.aiter_lines():
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        if ev.get("error"):
                            st["error"] = str(ev["error"])[:200]
                            break
                        st["status"] = str(ev.get("status") or "")[:80]
                        if ev.get("total"):
                            st["pct"] = round(100 * (ev.get("completed") or 0) / ev["total"])
        except Exception as e:
            st["error"] = f"{type(e).__name__}"
        st["done"] = True
        if not st["error"]:
            st["pct"] = 100


class AnthropicAdapter(Adapter):
    id, name, kind = "anthropic", "Anthropic API", "anthropic"
    URL = "https://api.anthropic.com/v1"

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY")

    def configured(self) -> bool:
        return bool(self._key)

    def setup_hint(self) -> str:
        return "" if self._key else "Add ANTHROPIC_API_KEY to .env (console.anthropic.com) and restart."

    def _headers(self) -> dict:
        return {"x-api-key": self._key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"}

    async def discover(self) -> list[ModelSpec]:
        if not self._key:
            return []
        names: list[str] = []
        try:
            async with self._client(8) as c:
                r = await c.get(f"{self.URL}/models", headers=self._headers(), params={"limit": 20})
                r.raise_for_status()
                names = [m["id"] for m in r.json().get("data", []) if m.get("id")]
        except Exception:
            pass
        want = os.getenv("ANTHROPIC_MODEL")
        picks: list[str] = [want] if want else []
        for fam in ("haiku", "sonnet", "opus"):           # newest of each family (listing is newest-first)
            hit = next((n for n in names if fam in n), None)
            if hit and hit not in picks:
                picks.append(hit)
        if not picks:
            picks = ["claude-haiku-4-5-20251001"]
        return [infer_spec("anthropic", n, local=False, kind="anthropic") for n in picks]

    async def health(self) -> dict:
        if not self._key:
            return {"ok": False, "detail": "no ANTHROPIC_API_KEY"}
        t = time.perf_counter()
        try:
            async with self._client(8) as c:
                r = await c.get(f"{self.URL}/models", headers=self._headers(), params={"limit": 1})
                return {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}",
                        "ms": round((time.perf_counter() - t) * 1000)}
        except Exception as e:
            return {"ok": False, "detail": type(e).__name__}

    @staticmethod
    def convert(messages: list[dict]) -> tuple[str, list[dict]]:
        system = "\n\n".join(m.get("content") or "" for m in messages if m.get("role") == "system")
        out: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "",
                         "content": str(m.get("content") or "")}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif role == "assistant" and m.get("tool_calls"):
                blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                for tc in m["tool_calls"]:
                    fn = tc.get("function") or {}
                    blocks.append({"type": "tool_use", "id": tc.get("id"), "name": fn.get("name") or tc.get("name"),
                                   "input": _args(fn.get("arguments") if fn else tc.get("arguments"))})
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "assistant" if role == "assistant" else "user", "content": m.get("content") or ""})
        return system, out

    async def chat(self, spec, messages, *, tools=None, json_mode=False, max_tokens=800, timeout=90,
                   on_delta=None, images=None) -> dict:
        system, msgs = self.convert(messages)
        body: dict = {"model": spec.model, "max_tokens": max_tokens, "messages": msgs}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                              "input_schema": t["function"].get("parameters") or {"type": "object"}} for t in tools]
        t0 = time.perf_counter()
        try:
            async with self._client(timeout) as c:
                r = await c.post(f"{self.URL}/messages", headers=self._headers(), json=body)
                if r.status_code >= 400:
                    return result(False, error=f"HTTP {r.status_code}: {r.text[:160]}")
                data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            calls = [{"id": b.get("id"), "name": b.get("name"), "arguments": b.get("input") or {}}
                     for b in data.get("content", []) if b.get("type") == "tool_use"]
            if on_delta and text:
                await on_delta(text)
            return result(True, text.strip(), calls, ms=(time.perf_counter() - t0) * 1000, usage=data.get("usage"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return result(False, error=f"{type(e).__name__}: {str(e)[:160]}")


class ClaudeCLIAdapter(Adapter):
    """The existing Claude Code CLI path. Text in, text out; runs with tools disabled
    (brain.py invariant), so the tool loop drives it with the JSON protocol."""
    id, name, kind = "claude-cli", "Claude (Claude Code CLI)", "claude-cli"
    native_tools = False

    def __init__(self, brain) -> None:
        self.brain = brain

    def configured(self) -> bool:
        return getattr(self.brain, "mode", "") == "llm"

    def setup_hint(self) -> str:
        return "" if self.configured() else "Install Claude Code (claude.com/claude-code), run `claude` once to log in, then restart BUCS."

    async def discover(self) -> list[ModelSpec]:
        if not self.configured():
            return []
        return [ModelSpec("claude-cli", "claude", {"chat", "streaming", "vision", "reasoning", "code", "json_mode"},
                          200_000, 5, 3, 1, False, {"note": "your Claude login"})]

    async def health(self) -> dict:
        if getattr(self.brain, "cloud_locked", False):
            return {"ok": False, "detail": "locked: cloud AI needs a Pro licence"}
        return {"ok": self.configured(), "detail": "CLI reachable" if self.configured() else "CLI not found"}

    @staticmethod
    def flatten(messages: list[dict]) -> tuple[str | None, str]:
        system = "\n\n".join(m.get("content") or "" for m in messages if m.get("role") == "system") or None
        convo = [m for m in messages if m.get("role") != "system"]
        if len(convo) == 1:
            return system, convo[0].get("content") or ""
        lines = []
        for m in convo:
            role = m.get("role")
            if role == "tool":
                lines.append(f"TOOL RESULT ({m.get('name', 'tool')}): {m.get('content')}")
            elif role == "assistant":
                lines.append(f"ASSISTANT: {m.get('content') or ''}")
            else:
                lines.append(f"USER: {m.get('content') or ''}")
        return system, "\n\n".join(lines) + "\n\nASSISTANT:"

    async def chat(self, spec, messages, *, tools=None, json_mode=False, max_tokens=800, timeout=90,
                   on_delta=None, images=None) -> dict:
        system, prompt = self.flatten(messages)
        t0 = time.perf_counter()
        if images:
            path = next((i for i in images if not str(i).startswith(("http:", "https:", "data:"))), None)
            if path:
                text = await self.brain.see(path, prompt, timeout=timeout)
                ok = bool(text) and not text.startswith("[")
                return result(ok, text if ok else "", error=None if ok else text, ms=(time.perf_counter() - t0) * 1000)
        text = await self.brain._claude_stream(prompt, system, timeout, on_delta=on_delta)
        if not text or text.startswith("[brain"):
            return result(False, error=text or "empty")
        return result(True, text, ms=(time.perf_counter() - t0) * 1000)


def build_adapters(brain=None, registry=None) -> list[Adapter]:
    """Everything this install can reach. Cloud families appear only when their key exists;
    custom endpoints come from the AI BRAIN ROUTER card's registry (keys stay in-process)."""
    out: list[Adapter] = [OllamaAdapter()]
    if brain is not None:
        out.append(ClaudeCLIAdapter(brain))
    out.append(AnthropicAdapter())
    for fid, f in CLOUD_FAMILIES.items():
        key = os.getenv(f["env"]) or (os.getenv(f["alt_env"]) if f.get("alt_env") else None)
        model_env = os.getenv(f"{fid.upper()}_MODEL")
        a = OpenAICompatAdapter(fid, f["name"], f["base_url"], key,
                                [model_env] if model_env else f["models"], f.get("embed"), key_env=f["env"])
        out.append(a)
    out.append(OpenAICompatAdapter("lmstudio", "LM Studio (local)", "http://127.0.0.1:1234/v1",
                                   discover_models=True))
    for p in (getattr(registry, "custom", None) or []):
        try:
            out.append(OpenAICompatAdapter("custom-" + p["id"], p.get("name") or p["id"], p["base_url"],
                                           p.get("api_key") or None, [p.get("model")]))
        except Exception:
            continue
    return out
