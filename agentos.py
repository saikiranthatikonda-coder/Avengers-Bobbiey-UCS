"""AI CORE · AGENTS, MEMORY, MODELS — the modular provider + agent architecture of BUCS.

  agentos_providers.py     capability registry + adapters (Ollama, OpenAI-compatible family,
                           Anthropic API, Claude CLI)
  agentos_router.py        task classification, policies, live health/latency, fallback,
                           explainable decisions, operator overrides
  agentos_tools.py         JSON-schema tool registry over real BUCS capabilities, permission
                           tiers + approvals, native / JSON-protocol tool loop
  agentos_memory.py        SQLite long-term memory, hybrid BM25 + embedding retrieval
  agentos_workflows.py     custom agents, workflows, background runs, schedules
  agentos_plugins.py       plugin marketplace (manifest, integrity, permissions)
  agentos_integrations.py  integration discovery matrix

Feature-module contract (main.py): build_router(ctx) + async start(ctx); ctx = state,
audit, commander(), host_only(request). Renders into #agentos-mount (static/agentos.*).

For other modules (computer use, phone, …):  core = agentos.get(state)
    await core.complete(prompt, system=..., task="vision"|"quick"|…, policy=..., images=[path])
    await core.run_tools(prompt, commander=False, allowed={"weather", …})
    core.decide(prompt, task=...)            # routing decision only, no call
    core.register_tool(agentos_tools.Tool(...))
The Brain also gets a "smart" provider (AI BRAIN ROUTER card → Smart router) so every
existing brain.think/stream caller can be routed without code changes.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import llm_providers
from agentos_integrations import detect as detect_integrations
from agentos_memory import KINDS, MemoryStore
from agentos_plugins import PluginManager
from agentos_providers import PULL_SUGGESTIONS, build_adapters
from agentos_router import POLICIES, TASKS, ProviderHub, Router
from agentos_tools import Tool, ToolContext, ToolRegistry, builtin_tools, run_tool_loop
from agentos_workflows import Studio

ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".update" / "agentos"
SYSTEM = ("You are JARVIS, the BUCS operator's AI. Be concise and precise. Use only facts you were given or "
          "fetched with tools for anything current; say so when you can't verify something.")


class AICore:
    def __init__(self, state: dict, ctx: dict | None = None, data_dir: Path = DATA, adapters=None,
                 plugins_dir: Path = ROOT / "plugins") -> None:
        self.state, self.ctx = state, ctx or {}
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self._custom_sig = self._registry_sig()
        self.hub = ProviderHub(adapters if adapters is not None else
                               build_adapters(state.get("brain"), state.get("providers")))
        self._fixed_adapters = adapters is not None
        self.router = Router(self.hub, self.data / "routing.json", cloud_allowed=self._cloud_allowed)
        self.memory = MemoryStore(self.data / "memory.db", embedder=self._embed, commander=state.get("commander"))
        self.tools = ToolRegistry()
        for t in builtin_tools(state, self.memory, self.folders):
            self.tools.register(t)
        self.tools.on_change = self._approval_needed
        self.plugins = PluginManager(plugins_dir, self.data, self.tools, state, self.memory)
        self.studio = Studio(self.data / "studio", ask=self._agent_ask, run_tool=self._workflow_tool,
                             request_approval=self._workflow_approval, notify=self._notify,
                             tool_names=lambda: set(self.tools.tools), policies=set(POLICIES),
                             audit=self._audit)
        self.booted = False
        self.last_error: str | None = None
        self.chats: list[dict] = []

    # ── plumbing ───────────────────────────────────────────────
    def _registry_sig(self) -> str:
        reg = self.state.get("providers")
        return json.dumps([(p.get("id"), p.get("base_url"), p.get("model"), bool(p.get("api_key")))
                           for p in (getattr(reg, "custom", None) or [])])

    def _cloud_allowed(self) -> bool:
        reg = self.state.get("providers")
        if reg is not None and hasattr(reg, "cloud_allowed"):
            return bool(reg.cloud_allowed())
        return not getattr(self.state.get("brain"), "cloud_locked", False)

    async def _audit(self, action: str, detail: str = "") -> None:
        fn = self.ctx.get("audit")
        if fn:
            try:
                await fn(action, detail)
            except Exception:
                pass

    async def _notify(self, msg: str, level: str = "info") -> None:
        hub = self.state.get("hub")
        if hub:
            await hub.broadcast({"type": "log", "level": level, "msg": f"[ai core] {msg[:300]}"})

    async def broadcast_change(self, brain: bool = False) -> None:
        """Tell every open dashboard that AI CORE state changed. brain=True also sends the
        core "models-updated" message (app.js refreshes the orb's LLM CORE badge, the
        AI BRAIN ROUTER card and the model chip) — used for anything that can change which
        model answers: routing policy/pins/disabled models, rediscovery, a pulled model."""
        hub = self.state.get("hub")
        if not hub:
            return
        try:
            if brain:
                await hub.broadcast({"type": "models-updated", "source": "agentos"})
            await hub.broadcast({"type": "sync", "topic": "agentos"})
        except Exception:
            pass

    async def refresh_if_registry_changed(self) -> bool:
        """A provider added/edited/removed in the core LLM switch reaches AI CORE's model list
        at once (instead of on the 10-minute rediscovery)."""
        if self._fixed_adapters or self._registry_sig() == self._custom_sig:
            return False
        await self.refresh()
        await self.broadcast_change(brain=False)
        return True

    def _approval_needed(self, ap: dict) -> None:
        what = ap["tool"] if ap["kind"] == "tool" else f"workflow step — {ap['message'][:80]}"
        try:
            asyncio.get_running_loop().create_task(self._notify(f"approval needed ({ap['id']}): {what} — open AI CORE", "warn"))
        except RuntimeError:
            pass

    async def _embed(self, texts: list[str]):
        e = self.hub.embedder(bool(self.router.config.get("allow_cloud_embeddings")))
        if e is None:
            return None
        adapter, spec = e
        vecs = await adapter.embed(spec.model, texts)
        return (spec.id, vecs) if vecs else None

    def folders(self) -> list[str]:
        try:
            d = json.loads((self.data / "files.json").read_text(encoding="utf-8"))
            fs = [f for f in d.get("folders", []) if Path(f).is_dir()]
        except Exception:
            fs = []
        default = self.data / "files"
        default.mkdir(parents=True, exist_ok=True)
        return [str(default)] + [f for f in fs if Path(f).resolve() != default.resolve()]

    def set_folders(self, folders: list[str]) -> dict:
        ok, bad = [], []
        for f in folders[:20]:
            p = Path(str(f)).expanduser()
            try:
                r = p.resolve()
            except Exception:
                bad.append(f"{f}: invalid"); continue
            if not r.is_dir():
                bad.append(f"{f}: not a folder"); continue
            if r == Path(r.anchor):
                bad.append(f"{f}: a whole drive is too broad"); continue
            if r == ROOT or r in ROOT.parents or ROOT in r.parents:
                bad.append(f"{f}: may not contain or be inside the BUCS install (protected files)"); continue
            ok.append(str(r))
        if bad:
            return {"ok": False, "error": "; ".join(bad)}
        from atomicio import atomic_write_text
        atomic_write_text(self.data / "files.json", json.dumps({"folders": ok}, indent=1), encoding="utf-8")
        return {"ok": True, "folders": self.folders()}

    # ── lifecycle ──────────────────────────────────────────────
    async def refresh(self) -> dict:
        if not self._fixed_adapters and self._registry_sig() != self._custom_sig:
            self._custom_sig = self._registry_sig()
            self.hub.set_adapters(build_adapters(self.state.get("brain"), self.state.get("providers")))
        await self.hub.discover()
        await self.hub.check_health()
        return {"models": len(self.hub.models)}

    async def boot(self) -> None:
        try:
            await self.refresh()
        except Exception as e:
            self.last_error = f"discovery: {type(e).__name__}"
        try:
            self.plugins.bootstrap()
            self.plugins.load_enabled()
        except Exception as e:
            self.last_error = f"plugins: {type(e).__name__}: {e}"
        flag = self.data / "imported.json"
        if not flag.exists():
            try:
                res = self.import_legacy()
                flag.write_text(json.dumps({"at": time.time(), **res}), encoding="utf-8")
            except Exception as e:
                self.last_error = f"memory import: {type(e).__name__}"
        self.booted = True

    def import_legacy(self) -> dict:
        return self.memory.import_legacy(self.state.get("memory"), self.state.get("team_memory"), ROOT / "notes.json")

    async def loop(self) -> None:
        if not self.booted:
            await self.boot()
        n = 0
        while True:
            await asyncio.sleep(30)
            n += 1
            try:
                await self.studio.tick()
            except Exception as e:
                self.last_error = f"scheduler: {type(e).__name__}: {e}"
            try:
                if not await self.refresh_if_registry_changed() and n % 20 == 0:
                    await self.refresh()
                if n % 4 == 0:
                    await self.hub.check_health()
                await self.memory.embed_pending()
            except Exception:
                pass

    # ── public API (for other modules) ─────────────────────────
    def decide(self, prompt: str = "", **kw) -> dict:
        return self.router.decide(prompt, **kw)

    def register_tool(self, tool: Tool) -> None:
        self.tools.register(tool)

    async def complete(self, prompt: str, *, system: str | None = None, task: str | None = None,
                       policy: str | None = None, images: list | None = None, use_memory: bool = False,
                       memory_scope: str | None = None, on_delta=None, max_tokens: int = 800,
                       timeout: float = 90, json_mode: bool = False) -> dict:
        cites: list = []
        sys_txt = system or SYSTEM
        if use_memory:
            block, cites = await self.memory.context_block(prompt, scope=memory_scope)
            if block:
                sys_txt = sys_txt + "\n\n" + block
        msgs = [{"role": "system", "content": sys_txt}, {"role": "user", "content": prompt}]
        res, decision = await self.router.chat(msgs, prompt=prompt, task=task, policy=policy, images=images,
                                               on_delta=on_delta, max_tokens=max_tokens, timeout=timeout,
                                               json_mode=json_mode)
        return {"ok": res["ok"], "text": res.get("text", ""), "error": res.get("error"), "model": res.get("model"),
                "ms": res.get("ms"), "decision": decision, "citations": cites}

    async def run_tools(self, prompt: str, *, commander: bool = False, approved: bool = False,
                        allowed: set | None = None, system: str | None = None, policy: str | None = None,
                        use_memory: bool = True, memory_scope: str | None = None, requester: str = "module",
                        max_steps: int = 5) -> dict:
        context, cites = ("", [])
        if use_memory:
            context, cites = await self.memory.context_block(prompt, scope=memory_scope, budget=1200)
        tctx = ToolContext(commander=commander, approved=approved, requester=requester, allowed=allowed,
                           audit=self._audit)
        from livefacts import clock_block
        out = await run_tool_loop(self.router, self.tools, prompt, tctx=tctx,
                                  system=(system or SYSTEM) + "\n" + clock_block(self.state), allowed=allowed,
                                  policy=policy, context=context, max_steps=max_steps)
        out["citations"] = cites
        return out

    # ── studio callbacks ───────────────────────────────────────
    async def _agent_ask(self, prompt: str, agent: dict | None, policy: str | None) -> dict:
        scope = None
        use_mem = True
        system = SYSTEM
        if agent:
            system = f"You are {agent['name']}" + (f", {agent['role']}" if agent.get("role") else "") + ".\n" + \
                     (agent.get("system") or "")
            use_mem = agent.get("memory_scope") != "none"
            scope = agent["id"] if agent.get("memory_scope") == "own" else None
        if agent and agent.get("tools"):
            out = await self.run_tools(prompt, commander=True, allowed=set(agent["tools"]), system=system,
                                       policy=policy, use_memory=use_mem, memory_scope=scope,
                                       requester=f"agent {agent['name']}")
        else:
            r = await self.complete(prompt, system=system, policy=policy, task=(agent or {}).get("task"),
                                    use_memory=use_mem, memory_scope=scope)
            out = {"ok": r["ok"], "answer": r["text"], "error": r.get("error"), "decision": r["decision"], "steps": []}
        if agent and use_mem and out.get("answer"):
            self.memory.add(f"{agent['name']} was asked: {prompt[:300]}\nAnswer: {out['answer'][:900]}",
                            kind="episode", title=f"agent {agent['name']}", source="agent run",
                            scope=scope or "global")
        return out

    async def _workflow_tool(self, name: str, args: dict, run: dict) -> dict:
        tctx = ToolContext(commander=True, requester=f"workflow {run['name']}", audit=self._audit)
        res = await self.tools.run(name, args, tctx)
        if res.get("approval_id"):
            ap = next((a for a in self.tools.approvals if a["id"] == res["approval_id"]), None)
            if ap:
                ap["run_id"] = run["id"]
        return res

    def _workflow_approval(self, run: dict, step: dict) -> dict:
        return self.tools.request_approval("workflow", {"workflow": run["name"], "step": step["id"]}, None,
                                           kind="workflow", run_id=run["id"], message=step.get("message", ""))

    async def decide_approval(self, aid: str, approve: bool, commander: bool) -> dict:
        tctx = ToolContext(commander=commander, requester="operator", audit=self._audit)
        ap = next((a for a in self.tools.approvals if a["id"] == aid), None)
        res = await self.tools.decide_approval(aid, approve, tctx)
        if res.get("ok") and ap and ap.get("run_id"):
            val = (res.get("result") or {}).get("result") if ap["kind"] == "tool" else None
            await self.studio.resume(aid, approve, value=val)
        await self._audit("agentos.approval", f"{aid} {'approved' if approve else 'rejected'} ({ap['tool'] if ap else '?'})")
        return res

    # ── snapshot ───────────────────────────────────────────────
    def snapshot(self) -> dict:
        return {"booted": self.booted, "error": self.last_error,
                "providers": self.hub.public(), "routing": self.router.config,
                "policies": {k: v["label"] for k, v in POLICIES.items()}, "tasks": TASKS,
                "last_decision": self.router.last, "history": self.router.history[:20],
                "cloud_allowed": self._cloud_allowed(),
                "smart_active": getattr(self.state.get("providers"), "active", None) == "smart",
                "tools": self.tools.public(), "tool_log": self.tools.log[:20],
                "approvals": self.tools.pending(), "studio": self.studio.snapshot(),
                "plugins": self.plugins.snapshot(), "memory": self.memory.stats(),
                "folders": self.folders(), "pull_suggestions": PULL_SUGGESTIONS,
                "pulls": getattr(self.hub.adapters.get("ollama"), "pulls", {})}


# ── access ──────────────────────────────────────────────────────
def get(state: dict, ctx: dict | None = None) -> AICore:
    core = state.get("agentos")
    if not isinstance(core, AICore):
        core = AICore(state, ctx)
        state["agentos"] = core
    elif ctx and not core.ctx:
        core.ctx = ctx
    return core


class SmartClient:
    """LocalLLM-shaped client the Brain can use as its provider ("smart" in the AI BRAIN ROUTER
    card). Returns None on any failure so the Brain falls back to its original chain."""
    available = True
    model = "smart-router"
    api_key = None
    last_error = None
    last_latency_ms = None

    def __init__(self, state: dict, ctx: dict | None = None) -> None:
        self.state, self.ctx = state, ctx

    def _core(self) -> AICore | None:
        try:
            core = get(self.state, self.ctx)
            return core if core.booted else None
        except Exception:
            return None

    async def chat(self, prompt, system=None, timeout=60.0, max_tokens=300):
        return await self.chat_stream(prompt, system=system, on_delta=None, timeout=timeout, max_tokens=max_tokens)

    async def chat_stream(self, prompt, system=None, on_delta=None, timeout=60.0, max_tokens=300):
        core = self._core()
        if core is None:
            return None
        try:
            r = await core.complete(prompt, system=system, on_delta=on_delta, timeout=timeout,
                                    max_tokens=max(max_tokens, 400))
        except Exception as e:
            self.last_error = type(e).__name__
            return None
        self.last_latency_ms = r.get("ms")
        return r["text"] if r["ok"] and r["text"] else None


# ── HTTP API ────────────────────────────────────────────────────
def _locked() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "OBSERVER role — switch to COMMANDER to change AI CORE."}, status_code=403)


def _hostonly() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "only allowed from the host machine"}, status_code=403)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def build_router(ctx) -> APIRouter:
    state = ctx["state"]
    llm_providers.SMART["client"] = SmartClient(state, ctx)
    r = APIRouter()
    commander, audit = ctx["commander"], ctx["audit"]
    core = lambda: get(state, ctx)  # noqa: E731

    pending: dict = {"task": None}

    @r.get("/api/agentos")
    async def overview():
        c = core()
        if (c.booted and not c._fixed_adapters and c._registry_sig() != c._custom_sig
                and (pending["task"] is None or pending["task"].done())):
            pending["task"] = asyncio.create_task(c.refresh_if_registry_changed())
        return c.snapshot()

    # providers & routing
    @r.post("/api/agentos/providers/refresh")
    async def providers_refresh():
        if not commander():
            return _locked()
        out = {"ok": True, **(await core().refresh()), "providers": core().hub.public()}
        await core().broadcast_change(brain=True)
        return out

    @r.post("/api/agentos/providers/test")
    async def providers_test(request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        mid = str(b.get("model", ""))[:160]
        if mid not in core().hub.models:
            return {"ok": False, "error": "unknown model"}
        res = await core().router.call(mid, [{"role": "user", "content": "Reply with exactly: OK"}], max_tokens=10, timeout=60)
        return {"ok": res["ok"], "ms": res["ms"], "reply": res.get("text", "")[:80], "error": res.get("error"),
                "health": core().hub.health.get(mid)}

    @r.post("/api/agentos/routing")
    async def routing_set(request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = core().router.update(b)
        if res.get("ok"):
            await audit("agentos.routing", f"policy {res['config']['policy']}, pins {len(res['config']['pins'])}, "
                                           f"disabled {len(res['config']['disabled'])}")
            # routing decides which model answers when the brain is on the smart router →
            # the orb's LLM CORE badge, the brain card and other open tabs must re-read it
            await core().broadcast_change(brain=True)
        return res

    @r.post("/api/agentos/route")
    async def route_preview(request: Request):
        b = await _body(request)
        return core().decide(str(b.get("prompt", ""))[:20000], task=b.get("task") or None, policy=b.get("policy") or None,
                             tools=bool(b.get("tools")), images=bool(b.get("images")))

    @r.post("/api/agentos/chat")
    async def chat(request: Request):
        b = await _body(request)
        prompt = str(b.get("prompt", "")).strip()[:20000]
        if not prompt:
            return {"ok": False, "error": "empty prompt"}
        c = core()
        is_cmd = commander()
        if b.get("tools"):
            out = await c.run_tools(prompt, commander=is_cmd, policy=b.get("policy") or None,
                                    use_memory=bool(b.get("memory", True)), requester="operator (AI CORE)")
            answer = out.get("answer", "")
        else:
            res = await c.complete(prompt, task=b.get("task") or None, policy=b.get("policy") or None,
                                   use_memory=bool(b.get("memory", True)))
            out = {"ok": res["ok"], "answer": res["text"] or "", "error": res.get("error"), "decision": res["decision"],
                   "citations": res["citations"], "steps": [], "ms": res.get("ms")}
            answer = out["answer"]
        if b.get("remember") and is_cmd and answer:
            c.memory.add(f"Q: {prompt[:400]}\nA: {answer[:900]}", kind="episode", title="AI CORE chat", source="ai core chat")
        await audit("agentos.chat", f"{(out.get('decision') or {}).get('used') or 'no model'} · {prompt[:60]}")
        return out

    # tools & approvals
    @r.post("/api/agentos/tools/{name}/run")
    async def tool_run(name: str, request: Request):
        b = await _body(request)
        tctx = ToolContext(commander=commander(), requester="operator (manual)", audit=audit)
        return await core().tools.run(name, b.get("args") if isinstance(b.get("args"), dict) else {}, tctx)

    @r.post("/api/agentos/approvals/{aid}")
    async def approval(aid: str, request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = await core().decide_approval(aid, bool(b.get("approve")), True)
        await core().broadcast_change()
        return res

    @r.post("/api/agentos/files")
    async def files_set(request: Request):
        if not commander():
            return _locked()
        if not ctx["host_only"](request):
            return _hostonly()
        b = await _body(request)
        res = core().set_folders([str(x) for x in (b.get("folders") or []) if str(x).strip()])
        if res.get("ok"):
            await audit("agentos.folders", f"{len(res['folders'])} approved folder(s)")
            await core().broadcast_change()
        return res

    # memory
    @r.get("/api/agentos/memory")
    async def memory_list(q: str = "", kind: str = "", limit: int = 40, offset: int = 0):
        m = core().memory
        limit = max(1, min(200, limit))
        if q.strip():
            items = await m.asearch(q[:500], limit=limit, kinds=(kind,) if kind in KINDS else None)
        else:
            items = m.list(kind if kind in KINDS else None, limit=limit, offset=max(0, offset))
        for i in items:
            i["text"] = i["text"][:2000]
        return {"items": items, "stats": m.stats()}

    @r.post("/api/agentos/memory")
    async def memory_add(request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        kind = b.get("kind") if b.get("kind") in KINDS else "fact"
        if kind == "document":
            res = core().memory.add_document(str(b.get("title") or "Document")[:150], str(b.get("text", ""))[:300_000])
        else:
            res = core().memory.add(str(b.get("text", ""))[:20000], kind=kind, title=str(b.get("title") or "")[:200],
                                    pinned=bool(b.get("pinned")))
        if res.get("ok"):
            await audit("agentos.memory", f"added {kind}")
            await core().broadcast_change()
        return res

    @r.post("/api/agentos/memory/import")
    async def memory_import():
        if not commander():
            return _locked()
        res = core().import_legacy()
        await audit("agentos.memory", f"import {res.get('imported')}")
        await core().broadcast_change()
        return res

    @r.post("/api/agentos/memory/{iid}")
    async def memory_edit(iid: int, request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = core().memory.update(iid, text=b.get("text"), title=b.get("title"),
                                   pinned=b.get("pinned") if isinstance(b.get("pinned"), bool) else None)
        if res.get("ok"):
            await audit("agentos.memory", f"edited #{iid}")
            await core().broadcast_change()
        return res

    @r.post("/api/agentos/memory/{iid}/forget")
    async def memory_forget(iid: int):
        if not commander():
            return _locked()
        res = core().memory.forget(iid)
        if res.get("ok"):
            await audit("agentos.memory", f"forgot #{iid} ({res['removed']} item(s))")
            await core().broadcast_change()
        return res

    # agents & workflows
    @r.post("/api/agentos/agents")
    async def agent_save(request: Request):
        if not commander():
            return _locked()
        res = core().studio.upsert_agent(await _body(request))
        if res.get("ok"):
            await audit("agentos.agent", f"saved {res['agent']['id']} (tools: {', '.join(res['agent']['tools']) or 'none'})")
            await core().broadcast_change()
        return res

    @r.post("/api/agentos/agents/{aid}/delete")
    async def agent_delete(aid: str):
        if not commander():
            return _locked()
        res = core().studio.delete_agent(aid)
        await audit("agentos.agent", f"deleted {aid}")
        await core().broadcast_change()
        return res

    @r.post("/api/agentos/agents/{aid}/run")
    async def agent_run(aid: str, request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = await core().studio.run_agent(aid, str(b.get("prompt") or "")[:8000] or None)
        if res.get("ok"):
            await audit("agentos.run", f"agent {aid}")
        return res

    @r.post("/api/agentos/workflows")
    async def wf_save(request: Request):
        if not commander():
            return _locked()
        res = core().studio.upsert_workflow(await _body(request))
        if res.get("ok"):
            await audit("agentos.workflow", f"saved {res['workflow']['id']} ({len(res['workflow']['steps'])} steps)")
            await core().broadcast_change()
        return res

    @r.post("/api/agentos/workflows/{wid}/delete")
    async def wf_delete(wid: str):
        if not commander():
            return _locked()
        res = core().studio.delete_workflow(wid)
        if res.get("ok"):
            await audit("agentos.workflow", f"deleted {wid}")
            await core().broadcast_change()
        return res

    @r.post("/api/agentos/workflows/{wid}/run")
    async def wf_run(wid: str, request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = await core().studio.run_workflow(wid, str(b.get("input") or "")[:8000])
        if res.get("ok"):
            await audit("agentos.run", f"workflow {wid}")
        return res

    @r.get("/api/agentos/runs/{rid}")
    async def run_get(rid: str):
        run = core().studio.get_run(rid)
        return run or JSONResponse({"ok": False, "error": "unknown run"}, status_code=404)

    # plugins
    @r.post("/api/agentos/plugins/preview")
    async def plugin_preview(request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        src = str(b.get("source", ""))
        if src == "folder" and not ctx["host_only"](request):
            return _hostonly()
        return await core().plugins.preview(src, str(b.get("value", ""))[:500])

    @r.post("/api/agentos/plugins/install")
    async def plugin_install(request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = core().plugins.install(str(b.get("token", "")), b.get("confirm") is True)
        if res.get("ok"):
            await audit("agentos.plugin", f"installed {res['id']} (disabled until enabled)")
            await core().broadcast_change()
        return res

    @r.post("/api/agentos/plugins/{pid}/enable")
    async def plugin_enable(pid: str, request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = core().plugins.set_enabled(pid, bool(b.get("on")))
        await audit("agentos.plugin", f"{pid} {'enabled' if b.get('on') else 'disabled'} → {'ok' if res.get('ok') else res.get('error')}")
        await core().broadcast_change()
        return res

    @r.post("/api/agentos/plugins/{pid}/uninstall")
    async def plugin_uninstall(pid: str):
        if not commander():
            return _locked()
        res = core().plugins.uninstall(pid)
        await audit("agentos.plugin", f"uninstalled {pid}")
        await core().broadcast_change()
        return res

    # integrations & local model pulls
    @r.get("/api/agentos/integrations")
    async def integrations(refresh: int = 0):
        return await detect_integrations(state, core().hub, force=bool(refresh))

    @r.post("/api/agentos/ollama/pull")
    async def ollama_pull(request: Request):
        if not commander():
            return _locked()
        if not ctx["host_only"](request):
            return _hostonly()
        b = await _body(request)
        model = str(b.get("model", ""))
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,80}$", model):
            return {"ok": False, "error": "invalid model name"}
        oll = core().hub.adapters.get("ollama")
        if oll is None:
            return {"ok": False, "error": "Ollama adapter missing"}
        if (oll.pulls.get(model) or {}).get("done") is False:
            return {"ok": False, "error": "already pulling"}
        await audit("agentos.ollama_pull", model)

        async def go():
            await oll.pull(model)
            await core().refresh()
            await core().broadcast_change(brain=True)     # a new local model is routable now
        asyncio.create_task(go())
        return {"ok": True, "started": model}

    return r


async def start(ctx) -> None:
    await get(ctx["state"], ctx).loop()
