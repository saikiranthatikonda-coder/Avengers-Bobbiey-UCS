"""OpenAI-compatible local LLM client.

Works with Ollama (http://127.0.0.1:11434/v1), LM Studio (http://127.0.0.1:1234/v1),
llama.cpp server, vLLM, or any endpoint that speaks /v1/chat/completions.

Configure via .env:
    LOCAL_LLM_URL=http://127.0.0.1:11434/v1
    LOCAL_LLM_MODEL=llama3.2        # optional — auto-picks first model if empty
    LOCAL_LLM_KEY=                  # optional bearer token
"""

import os
import time

import httpx


class LocalLLM:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, hub=None) -> None:
        self.base_url = (base_url or os.getenv("LOCAL_LLM_URL",
                         "http://127.0.0.1:11434/v1")).rstrip("/")
        self.model = model or os.getenv("LOCAL_LLM_MODEL") or None
        self.api_key = api_key or os.getenv("LOCAL_LLM_KEY") or None
        self.hub = hub
        self.available = False
        self.last_latency_ms: int | None = None

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
