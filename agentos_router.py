"""AI CORE · intelligent routing.

classify()  what kind of job is this (quick chat, deep reasoning, code, vision, tool use,
            long context, private/offline) — cheap regex heuristics, explained.
Router      ranks every reachable model for that job under a policy (balanced, local-first
            / privacy, fastest, best quality, cheapest) using declared capabilities PLUS live
            health and measured latency, applies operator overrides (pins, disabled models),
            and falls back down the ranking automatically when a call fails.

Every decision is a plain dict with the ranking, the reasons and every exclusion, so the
dashboard can show exactly why a model was picked.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from agentos_providers import Adapter, Health, ModelSpec, result

TASKS = {
    "quick": "Quick chat", "reasoning": "Deep reasoning", "code": "Code", "vision": "Vision",
    "tools": "Tool use", "long_context": "Long context", "private": "Private / offline",
}
POLICIES = {
    "balanced":     {"label": "Balanced", "q": 1.0, "s": 0.8, "c": 0.4, "l": 0.3},
    "local_first":  {"label": "Prefer local / privacy", "q": 0.6, "s": 0.5, "c": 0.3, "l": 3.0},
    "fastest":      {"label": "Fastest", "q": 0.3, "s": 2.2, "c": 0.2, "l": 0.1},
    "best_quality": {"label": "Best quality", "q": 2.2, "s": 0.2, "c": 0.1, "l": 0.0},
    "cheapest":     {"label": "Cheapest", "q": 0.4, "s": 0.3, "c": 2.2, "l": 0.4},
}
SPEED_PRIOR = {1: 0.8, 2: 0.5, 3: 0.25}

_PRIVATE = re.compile(r"\b(private(ly)?|offline|confidential|secret|don'?t send|do not send|local only|"
                      r"password|salary|medical|diagnos)\b", re.I)
_CODE = re.compile(r"```|\bdef \w+\(|\bfunction\s*\w*\(|traceback|stack ?trace|\bexception\b|\bregex\b|\bsql\b|"
                   r"\bpython\b|\bjavascript\b|\btypescript\b|\bcompile|\brefactor|\bbug\b|\bunit tests?\b|\bcode\b", re.I)
_REASON = re.compile(r"\b(why|prove|plan|analy[sz]e|compare|strategy|step[- ]by[- ]step|trade-?offs?|design|"
                     r"evaluate|pros and cons|in depth|in detail|reason|derive|architecture|root cause)\b", re.I)


def est_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def classify(prompt: str, *, images: bool = False, tools: bool = False, private: bool = False) -> tuple[str, str]:
    p = prompt or ""
    if private or _PRIVATE.search(p):
        return "private", "private/offline request — must stay on this machine"
    if images:
        return "vision", "an image is attached"
    n = est_tokens(p)
    if n > 6000:
        return "long_context", f"~{n:,} tokens of input"
    if tools:
        return "tools", "needs live tools"
    if _CODE.search(p):
        return "code", "code / debugging request"
    if _REASON.search(p) or len(p) > 600:
        return "reasoning", "analysis / planning request" if _REASON.search(p) else "long, detailed prompt"
    return "quick", "short conversational request"


class ProviderHub:
    """All adapters + the models they expose + live health."""

    def __init__(self, adapters: list[Adapter]) -> None:
        self.adapters: dict[str, Adapter] = {}
        self.models: dict[str, ModelSpec] = {}
        self.health = Health()
        self.adapter_health: dict[str, dict] = {}
        self.discovered_at = 0.0
        self.set_adapters(adapters)

    def set_adapters(self, adapters: list[Adapter]) -> None:
        self.adapters = {a.id: a for a in adapters}
        self.models = {k: v for k, v in self.models.items() if v.provider in self.adapters}

    def add_models(self, specs: list[ModelSpec]) -> None:
        for s in specs:
            self.models[s.id] = s

    async def discover(self) -> dict:
        async def one(a: Adapter):
            try:
                return a.id, await asyncio.wait_for(a.discover(), timeout=12)
            except Exception:
                return a.id, []
        res = await asyncio.gather(*(one(a) for a in self.adapters.values()))
        models: dict[str, ModelSpec] = {}
        for aid, specs in res:
            for s in specs:
                models[s.id] = s
            self.adapter_health.setdefault(aid, {})["models"] = len(specs)
        self.models = models
        self.discovered_at = time.time()
        return {"models": len(models)}

    async def check_health(self) -> dict:
        async def one(a: Adapter):
            t = time.time()
            try:
                h = await asyncio.wait_for(a.health(), timeout=10)
            except Exception as e:
                h = {"ok": False, "detail": type(e).__name__}
            h["ts"] = t
            return a.id, h
        for aid, h in await asyncio.gather(*(one(a) for a in self.adapters.values())):
            prev = self.adapter_health.get(aid, {})
            self.adapter_health[aid] = {**prev, **h}
        return self.adapter_health

    def adapter_ok(self, aid: str) -> bool:
        h = self.adapter_health.get(aid)
        return True if h is None or "ok" not in h else bool(h["ok"])

    def embedder(self, allow_cloud: bool = False) -> tuple[Adapter, ModelSpec] | None:
        specs = [s for s in self.models.values() if "embeddings" in s.caps and self.adapter_ok(s.provider)]
        specs.sort(key=lambda s: (not s.local, "nomic" not in s.model))
        for s in specs:
            if s.local or allow_cloud:
                return self.adapters[s.provider], s
        return None

    def public(self) -> dict:
        prov = []
        for a in self.adapters.values():
            p = a.public()
            p["health"] = self.adapter_health.get(a.id)
            p["models"] = [dict(s.public(), health=self.health.get(s.id)) for s in self.models.values()
                           if s.provider == a.id]
            if getattr(a, "version", None):
                p["version"] = a.version
            prov.append(p)
        return {"providers": prov, "discovered_at": self.discovered_at, "count": len(self.models)}


class Router:
    def __init__(self, hub: ProviderHub, config_path: Path | None = None, cloud_allowed=lambda: True) -> None:
        self.hub = hub
        self.path = Path(config_path) if config_path else None
        self.cloud_allowed = cloud_allowed
        self.config = {"policy": "balanced", "pins": {}, "disabled": [], "allow_cloud_embeddings": False}
        self.last: dict | None = None
        self.history: list[dict] = []
        self._load()

    # ── config ─────────────────────────────────────────────────
    def _load(self) -> None:
        if not self.path:
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            for k in self.config:
                if k in d:
                    self.config[k] = d[k]
        except Exception:
            pass

    def save(self) -> None:
        if not self.path:
            return
        from atomicio import atomic_write_text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(self.config, indent=1), encoding="utf-8")

    def update(self, patch: dict) -> dict:
        if "policy" in patch:
            if patch["policy"] not in POLICIES:
                return {"ok": False, "error": "unknown policy"}
            self.config["policy"] = patch["policy"]
        if "pins" in patch and isinstance(patch["pins"], dict):
            pins = {}
            for t, mid in patch["pins"].items():
                if t in TASKS and mid:
                    pins[t] = str(mid)[:160]
            self.config["pins"] = pins
        if "disabled" in patch and isinstance(patch["disabled"], list):
            self.config["disabled"] = [str(x)[:160] for x in patch["disabled"]][:200]
        if "allow_cloud_embeddings" in patch:
            self.config["allow_cloud_embeddings"] = bool(patch["allow_cloud_embeddings"])
        self.save()
        return {"ok": True, "config": self.config}

    # ── ranking ────────────────────────────────────────────────
    def _speed(self, spec: ModelSpec) -> tuple[float, str]:
        ew = self.hub.health.get(spec.id)["ewma_ms"]
        if ew is not None:
            return 1.0 / (1.0 + ew / 1500.0), f"measured {ew} ms"
        return SPEED_PRIOR.get(spec.speed, 0.5), f"latency class {spec.speed} (not measured yet)"

    def decide(self, prompt: str = "", *, task: str | None = None, policy: str | None = None,
               images: bool = False, tools: bool = False, private: bool = False,
               tokens: int | None = None, exclude: tuple = ()) -> dict:
        if task not in TASKS:
            task, why = classify(prompt, images=images, tools=tools, private=private)
        else:
            why = "set by caller"
        policy = policy if policy in POLICIES else self.config.get("policy", "balanced")
        w = POLICIES[policy]
        need_tokens = int((tokens or est_tokens(prompt)) * 1.3) + 600
        cloud_ok = bool(self.cloud_allowed())
        ranked, excluded = [], []
        for spec in self.hub.models.values():
            mid = spec.id
            a = self.hub.adapters.get(spec.provider)
            why_not = None
            if "chat" not in spec.caps:
                continue                                    # embedding-only models never chat
            if a is None or not a.configured():
                why_not = "provider not configured"
            elif mid in self.config["disabled"] or mid in exclude:
                why_not = "disabled by operator" if mid in self.config["disabled"] else "excluded by caller"
            elif not spec.local and not cloud_ok:
                why_not = "cloud AI locked (licence)"
            elif not self.hub.adapter_ok(spec.provider):
                why_not = "provider unreachable: " + str(self.hub.adapter_health.get(spec.provider, {}).get("detail", ""))
            elif self.hub.health.cooling(mid):
                why_not = "cooling down after failures: " + str(self.hub.health.get(mid)["last_error"])
            elif task == "private" and not spec.local:
                why_not = "private task: cloud not allowed"
            elif (task == "vision" or images) and "vision" not in spec.caps:
                why_not = "no vision"
            elif spec.context < need_tokens:
                why_not = f"context {spec.context:,} < needed ~{need_tokens:,}"
            if why_not:
                excluded.append({"id": mid, "why": why_not})
                continue
            reasons = []
            qn = (spec.quality - 1) / 4
            sn, sn_why = self._speed(spec)
            cn = 1 - spec.cost / 3
            ln = 1.0 if spec.local else 0.0
            score = w["q"] * qn + w["s"] * sn + w["c"] * cn + w["l"] * ln
            reasons.append(f"quality {spec.quality}/5")
            reasons.append(sn_why)
            reasons.append("local" if spec.local else f"cloud · cost class {spec.cost}")
            if task == "quick":
                score += 0.6 * sn
            elif task == "reasoning":
                score += 0.8 * qn + (0.5 if "reasoning" in spec.caps else 0)
                if "reasoning" in spec.caps:
                    reasons.append("reasoning model")
            elif task == "code":
                score += 0.5 * qn + (0.5 if "code" in spec.caps else 0)
                if "code" in spec.caps:
                    reasons.append("code-capable")
            elif task == "tools":
                native = "tools" in spec.caps and getattr(a, "native_tools", True)
                score += 0.7 if native else 0.0
                score += 0.6 * qn
                reasons.append("native tool calling" if native else "tools via JSON protocol")
            elif task == "long_context":
                score += min(1.0, spec.context / 200_000)
                reasons.append(f"{spec.context:,}-token context")
            elif task == "vision":
                reasons.append("vision-capable")
            h = self.hub.health.get(mid)
            tries = h["ok"] + h["fail"]
            if tries >= 3:
                rate = h["ok"] / tries
                score -= (1 - rate) * 1.0
                reasons.append(f"success {round(rate * 100)}%")
            pinned = self.config["pins"].get(task) == mid
            if pinned:
                score += 100
                reasons.insert(0, "operator pin for this task")
            ranked.append({"id": mid, "score": round(score, 3), "reasons": reasons, "local": spec.local,
                           "provider": spec.provider, "model": spec.model, "pinned": pinned})
        ranked.sort(key=lambda r: -r["score"])
        chosen = ranked[0]["id"] if ranked else None
        if chosen:
            top = ranked[0]
            explain = (f"{TASKS[task]} ({why}) under '{POLICIES[policy]['label']}': picked {chosen} — "
                       + ", ".join(top["reasons"][:4])
                       + (f". Fallbacks: {', '.join(r['id'] for r in ranked[1:4])}" if len(ranked) > 1 else ". No fallback model available")
                       + ".")
        else:
            explain = f"{TASKS[task]}: no model can take this — " + ("; ".join(
                f"{e['id']}: {e['why']}" for e in excluded[:4]) or "no providers discovered")
        return {"task": task, "task_label": TASKS[task], "task_reason": why, "policy": policy,
                "chosen": chosen, "ranked": ranked[:12], "excluded": excluded[:20], "explain": explain,
                "attempts": [], "ts": time.time()}

    # ── calling ────────────────────────────────────────────────
    async def call(self, mid: str, messages: list[dict], **kw) -> dict:
        spec = self.hub.models.get(mid)
        if spec is None:
            return result(False, error="unknown model")
        a = self.hub.adapters[spec.provider]
        t0 = time.perf_counter()
        try:
            res = await a.chat(spec, messages, **kw)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            res = result(False, error=f"{type(e).__name__}: {e}")
        ms = res.get("ms") or (time.perf_counter() - t0) * 1000
        ok = res["ok"] and bool(res["text"] or res["tool_calls"])
        if res["ok"] and not ok:
            res = dict(res, ok=False, error="empty reply")
        self.hub.health.record(mid, ok, ms if ok else None, res.get("error"))
        res["model"] = mid
        res["ms"] = round(ms)
        return res

    async def chat(self, messages: list[dict], *, decision: dict | None = None, prompt: str | None = None,
                   task: str | None = None, policy: str | None = None, tools: list | None = None,
                   images: list | None = None, json_mode: bool = False, max_tokens: int = 800,
                   timeout: float = 90, on_delta=None, max_attempts: int = 4) -> tuple[dict, dict]:
        if decision is None:
            text = prompt if prompt is not None else next(
                (m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
            decision = self.decide(text, task=task, policy=policy, images=bool(images), tools=bool(tools))
        order = [r["id"] for r in decision["ranked"]][:max_attempts]
        last = result(False, error=decision["explain"] if not order else "all candidates failed")
        for mid in order:
            spec = self.hub.models.get(mid)
            if spec is None:
                continue
            native = bool(tools) and "tools" in spec.caps and getattr(self.hub.adapters[spec.provider], "native_tools", True)
            res = await self.call(mid, messages, tools=tools if native else None, json_mode=json_mode,
                                  max_tokens=max_tokens, timeout=timeout, on_delta=on_delta, images=images)
            decision["attempts"].append({"model": mid, "ok": res["ok"], "ms": res["ms"],
                                         "error": res.get("error"), "native_tools": native})
            if res["ok"]:
                res["native_tools"] = native
                decision["used"] = mid
                if mid != decision["chosen"]:
                    decision["explain"] += f" Fell back to {mid} after {len(decision['attempts']) - 1} failure(s)."
                self._remember(decision)
                return res, decision
            last = res
        decision["used"] = None
        self._remember(decision)
        return last, decision

    def _remember(self, d: dict) -> None:
        self.last = d
        self.history = ([{"ts": d["ts"], "task": d["task"], "policy": d["policy"], "chosen": d["chosen"],
                          "used": d.get("used"), "attempts": len(d["attempts"])}] + self.history)[:50]
