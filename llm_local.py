"""OpenAI-compatible local LLM client.

Works with Ollama (http://127.0.0.1:11434/v1), LM Studio (http://127.0.0.1:1234/v1),
llama.cpp server, vLLM, or any endpoint that speaks /v1/chat/completions.

Configure via .env:
    LOCAL_LLM_URL=http://127.0.0.1:11434/v1
    LOCAL_LLM_MODEL=llama3.2        # optional — auto-picks first model if empty
    LOCAL_LLM_KEY=                  # optional bearer token
"""

import json
import os
import time
from pathlib import Path

import httpx

PREF_FILE = Path(__file__).parent / "model_pref.json"


class LocalLLM:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, hub=None) -> None:
        self.base_url = (base_url or os.getenv("LOCAL_LLM_URL",
                         "http://127.0.0.1:11434/v1")).rstrip("/")
        self.model = model or os.getenv("LOCAL_LLM_MODEL") or None
        # persisted selection (survives restarts) takes precedence
        try:
            pref = json.loads(PREF_FILE.read_text(encoding="utf-8"))
            if pref.get("model"):
                self.model = pref["model"]
        except Exception:
            pass
        self.api_key = api_key or os.getenv("LOCAL_LLM_KEY") or None
        self.hub = hub
        self.available = False
        self.last_latency_ms: int | None = None

    def save_pref(self) -> None:
        try:
            PREF_FILE.write_text(json.dumps({"model": self.model}), encoding="utf-8")
        except Exception:
            pass

    async def list_models(self) -> list[dict]:
        """Installed local models. Prefers Ollama's native /api/tags (rich
        metadata: size, params, quantization); falls back to /v1/models."""
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as c:
                r = await c.get(f"{root}/api/tags")
                r.raise_for_status()
                data = r.json()
            out = []
            for m in data.get("models", []):
                det = m.get("details") or {}
                out.append({
                    "name": m.get("name"),
                    "size_gb": round((m.get("size") or 0) / 1e9, 1),
                    "param_size": det.get("parameter_size"),
                    "quant": det.get("quantization_level"),
                    "family": det.get("family"),
                })
            if out:
                return out
        except Exception:
            pass
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as c:
                r = await c.get(f"{self.base_url}/models", headers=self._headers())
                r.raise_for_status()
                return [{"name": m.get("id")} for m in (r.json().get("data") or [])
                        if m.get("id")]
        except Exception:
            return []

    def _headers(self) -> dict:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def probe(self) -> bool:
        """Check the endpoint is alive; auto-pick a model if none configured."""
        try:
            # trust_env=False: never route localhost through a system/corporate
            # proxy (causes 403 Forbidden on networks that block private addrs)
            async with httpx.AsyncClient(timeout=4, trust_env=False) as c:
                r = await c.get(f"{self.base_url}/models", headers=self._headers())
                r.raise_for_status()
                data = r.json()
            models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
            if not self.model and models:
                self.model = models[0]
            self.available = bool(self.model)
        except Exception:
            self.available = False
        return self.available

    async def chat(self, prompt: str, system: str | None = None,
                   timeout: float = 60.0, max_tokens: int = 300) -> str | None:
        """Single-turn chat. Returns None on any failure (caller falls back)."""
        if not self.model:
            return None
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
                r = await c.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.4,
                    },
                )
                r.raise_for_status()
                data = r.json()
            self.last_latency_ms = int((time.time() - t0) * 1000)
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            return (content or "").strip() or None
        except Exception as e:
            self.available = False
            if self.hub:
                await self.hub.broadcast({
                    "type": "log", "level": "warn",
                    "msg": f"local LLM call failed: {e}",
                })
            return None
