"""AI CORE · tool registry, permission tiers and the tool-calling loop.

Tools are JSON-schema functions over REAL BUCS capabilities (live web/news lookup, weather,
system metrics, calendar, devices, disaster feed, security findings, computer use, approved
folders, memory). Plugins add more through the same registry.

Permission tiers (enforced here, server-side, for every caller — chat, agents, workflows,
plugins, other modules):
  read         free for any authenticated caller
  action       changes something inside BUCS / the LAN → commander role required
  destructive  outward or hard-to-undo (unlock a door, run a computer-use task, overwrite a
               file) → commander AND an explicit approval of that exact call. Without it the
               call is parked in the approval queue and the model is told so.

The loop speaks native tool calling to providers that support it and a JSON protocol to
those that don't (Claude CLI, older local models), falling back from native to JSON on the
same model when native calls fail.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

TIERS = ("read", "action", "destructive")
_RANK = {t: i for i, t in enumerate(TIERS)}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    tier: str
    handler: object                       # async/sync callable(args: dict, tctx) -> json-able
    source: str = "builtin"
    tier_fn: object = None                # optional args -> tier (can only ESCALATE)
    available: object = None              # optional () -> (bool, why)

    def tier_for(self, args: dict) -> str:
        t = self.tier
        if self.tier_fn:
            try:
                t2 = self.tier_fn(args or {})
                if t2 in _RANK and _RANK[t2] > _RANK[t]:
                    t = t2
            except Exception:
                t = "destructive"
        return t

    def is_available(self) -> tuple[bool, str]:
        if not self.available:
            return True, ""
        try:
            return self.available()
        except Exception as e:
            return False, type(e).__name__

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    def public(self) -> dict:
        ok, why = self.is_available()
        return {"name": self.name, "description": self.description, "parameters": self.parameters,
                "tier": self.tier, "source": self.source, "available": ok, "unavailable_reason": why}


@dataclass
class ToolContext:
    commander: bool = False
    approved: bool = False                # this exact call was approved by the operator
    requester: str = "operator"
    allowed: set | None = None            # agent allow-list (None = every tool)
    audit: object = None                  # async (action, detail)
    meta: dict = field(default_factory=dict)


def validate(schema: dict, args: dict) -> str | None:
    """Tiny JSON-schema subset: object, required, property types, enum, min/max."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    props = (schema or {}).get("properties") or {}
    for r in (schema or {}).get("required") or []:
        if r not in args or args[r] in (None, ""):
            return f"missing required argument '{r}'"
    types = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "object": dict, "array": list}
    for k, v in args.items():
        p = props.get(k)
        if p is None:
            continue
        t = p.get("type")
        if t in types and v is not None:
            if t == "number" and isinstance(v, str):
                try:
                    args[k] = v = float(v)
                except ValueError:
                    return f"'{k}' must be a number"
            if t == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
                args[k] = v = int(v)
            if t == "integer" and isinstance(v, float) and v.is_integer():
                args[k] = v = int(v)
            if t == "boolean" and isinstance(v, str):
                args[k] = v = v.lower() in ("1", "true", "yes", "on")
            if not isinstance(v, types[t]) or (t in ("number", "integer") and isinstance(v, bool)):
                return f"'{k}' must be {t}"
        if "enum" in p and v not in p["enum"]:
            return f"'{k}' must be one of {p['enum']}"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in p and v < p["minimum"]:
                return f"'{k}' must be ≥ {p['minimum']}"
            if "maximum" in p and v > p["maximum"]:
                return f"'{k}' must be ≤ {p['maximum']}"
        if isinstance(v, str) and len(v) > int(p.get("maxLength", 20000)):
            return f"'{k}' is too long"
    return None


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}
        self.approvals: list[dict] = []
        self.log: list[dict] = []
        self.on_change = None

    def register(self, tool: Tool) -> None:
        if not re.match(r"^[a-z][a-z0-9_]{1,48}$", tool.name):
            raise ValueError(f"bad tool name {tool.name!r}")
        if tool.tier not in TIERS:
            raise ValueError(f"bad tier {tool.tier!r}")
        cur = self.tools.get(tool.name)
        if cur is not None and cur.source != tool.source:
            raise ValueError(f"tool {tool.name} already provided by {cur.source}")
        self.tools[tool.name] = tool

    def unregister_source(self, source: str) -> None:
        for n in [n for n, t in self.tools.items() if t.source == source]:
            self.tools.pop(n, None)

    def list(self, allowed: set | None = None) -> list[Tool]:
        return [t for t in self.tools.values() if (allowed is None or t.name in allowed) and t.is_available()[0]]

    def public(self) -> list[dict]:
        return [t.public() for t in sorted(self.tools.values(), key=lambda t: (_RANK[t.tier], t.name))]

    def check(self, tool: Tool, args: dict, tctx: ToolContext) -> tuple[bool, str, bool]:
        """(allowed, reason, needs_approval)"""
        if tctx.allowed is not None and tool.name not in tctx.allowed:
            return False, f"tool '{tool.name}' is not in this agent's allowed tools", False
        tier = tool.tier_for(args)
        if tier == "read":
            return True, "read-only", False
        if not tctx.commander:
            return False, f"'{tool.name}' is a {tier} tool — commander role required", False
        if tier == "destructive" and not tctx.approved:
            return False, f"'{tool.name}' needs explicit operator approval", True
        return True, tier, False

    async def run(self, name: str, args: dict | None, tctx: ToolContext) -> dict:
        t0 = time.perf_counter()
        tool = self.tools.get(name)
        args = dict(args or {})
        if tool is None:
            return {"ok": False, "error": f"unknown tool '{name}'", "tool": name}
        ok_av, why = tool.is_available()
        if not ok_av:
            return {"ok": False, "error": f"'{name}' unavailable: {why}", "tool": name}
        err = validate(tool.parameters, args)
        if err:
            return {"ok": False, "error": err, "tool": name}
        allowed, reason, needs = self.check(tool, args, tctx)
        tier = tool.tier_for(args)
        if not allowed:
            out = {"ok": False, "error": reason, "tool": name, "tier": tier}
            if needs:
                ap = self.request_approval(name, args, tctx)
                out["approval_id"] = ap["id"]
                out["error"] = (f"'{name}' needs explicit operator approval — queued as approval {ap['id']} "
                                "in AI CORE. Tell the operator it is waiting for their approval.")
            return out
        try:
            res = tool.handler(args, tctx)
            if inspect.isawaitable(res):
                res = await asyncio.wait_for(res, timeout=60)
            out = {"ok": True, "result": res, "tool": name, "tier": tier}
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            out = {"ok": False, "error": "tool timed out", "tool": name, "tier": tier}
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "tool": name, "tier": tier}
        out["ms"] = round((time.perf_counter() - t0) * 1000)
        self.log = ([{"ts": time.time(), "tool": name, "tier": tier, "ok": out["ok"], "by": tctx.requester,
                      "ms": out["ms"]}] + self.log)[:100]
        if tier != "read" and tctx.audit:
            try:
                await tctx.audit("agentos.tool", f"{name} ({tier}) by {tctx.requester} → {'ok' if out['ok'] else 'failed'}")
            except Exception:
                pass
        return out

    # ── approvals ─────────────────────────────────────────────
    def request_approval(self, tool: str, args: dict, tctx: ToolContext, kind: str = "tool",
                         run_id: str | None = None, message: str = "") -> dict:
        ap = {"id": uuid.uuid4().hex[:10], "kind": kind, "tool": tool, "args": args, "run_id": run_id,
              "requester": tctx.requester if tctx else "workflow", "message": message[:400],
              "created": time.time(), "status": "pending"}
        self.approvals = ([ap] + [a for a in self.approvals if a["status"] == "pending"]
                          + [a for a in self.approvals if a["status"] != "pending"][:30])[:100]
        if self.on_change:
            try:
                self.on_change(ap)
            except Exception:
                pass
        return ap

    def pending(self) -> list[dict]:
        return [a for a in self.approvals if a["status"] == "pending"]

    async def decide_approval(self, aid: str, approve: bool, tctx: ToolContext) -> dict:
        ap = next((a for a in self.approvals if a["id"] == aid), None)
        if ap is None or ap["status"] != "pending":
            return {"ok": False, "error": "no such pending approval"}
        if not tctx.commander:
            return {"ok": False, "error": "commander role required"}
        ap["status"] = "approved" if approve else "rejected"
        ap["decided"] = time.time()
        if ap["kind"] != "tool" or not approve:
            return {"ok": True, "approval": ap}
        res = await self.run(ap["tool"], ap["args"], ToolContext(commander=True, approved=True,
                                                                  requester=f"approval {aid}", audit=tctx.audit))
        ap["result"] = _clip(res, 1500)
        return {"ok": True, "approval": ap, "result": res}


def _clip(obj, n: int = 3500):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…(truncated)"


# ── JSON protocol ────────────────────────────────────────────────
def json_protocol_prompt(tools: list[Tool]) -> str:
    lines = []
    for t in tools:
        props = (t.parameters or {}).get("properties") or {}
        req = set((t.parameters or {}).get("required") or [])
        sig = ", ".join(f"{k}{'' if k in req else '?'}: {v.get('type', 'any')}" for k, v in props.items())
        lines.append(f"- {t.name}({sig}): {t.description}")
    return ("You can call tools to get REAL data. Tools:\n" + "\n".join(lines) + "\n\n"
            "To call tools reply with ONLY a JSON object, no prose:\n"
            '{"tools": [{"tool": "<name>", "arguments": {...}}]}\n'
            "You will receive the results and can call more tools. When you have what you need reply with ONLY:\n"
            '{"final": "<your answer to the operator, citing which tool each fact came from>"}\n'
            "Never invent tool results.")


def extract_json(text: str):
    """First balanced JSON object in text (models wrap JSON in prose / code fences)."""
    s = text or ""
    start = s.find("{")
    while start != -1:
        depth, instr, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
            elif ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except Exception:
                        break
        start = s.find("{", start + 1)
    return None


def parse_json_calls(text: str, known: set) -> tuple[list[dict], str | None]:
    """→ (calls, final). Accepts {"tools":[...]}, {"tool":..}, {"name":..,"parameters"/"arguments":..}, {"final":..}."""
    obj = extract_json(text)
    if not isinstance(obj, dict):
        return [], (text or "").strip() or None
    if "final" in obj:
        return [], str(obj["final"])
    items = obj.get("tools") if isinstance(obj.get("tools"), list) else [obj]
    calls = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("tool") or it.get("name")
        args = it.get("arguments", it.get("parameters", it.get("args", {})))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name in known:
            calls.append({"id": f"j{len(calls)}", "name": name, "arguments": args if isinstance(args, dict) else {}})
    if calls:
        return calls, None
    return [], (text or "").strip() or None


async def run_tool_loop(router, registry: ToolRegistry, prompt: str, *, tctx: ToolContext,
                        system: str = "", allowed: set | None = None, policy: str | None = None,
                        task: str | None = None, max_steps: int = 5, context: str = "",
                        decision: dict | None = None, timeout: float = 90) -> dict:
    tools = registry.list(allowed if allowed is not None else tctx.allowed)
    known = {t.name for t in tools}
    if decision is None:
        decision = router.decide(prompt, task=task or "tools", policy=policy, tools=True)
    steps: list[dict] = []
    t_start = time.perf_counter()

    async def loop_with(mid: str, native: bool) -> dict | None:
        sys_txt = (system + "\n\n" if system else "") + (context + "\n\n" if context else "")
        if not native and tools:
            sys_txt += json_protocol_prompt(tools)
        msgs = [{"role": "system", "content": sys_txt.strip()}] if sys_txt.strip() else []
        msgs.append({"role": "user", "content": prompt})
        schemas = [t.schema() for t in tools] if native else None
        first = True
        for step in range(max_steps + 1):
            last_round = step == max_steps
            res = await router.call(mid, msgs, tools=None if last_round else schemas, timeout=timeout, max_tokens=900)
            decision["attempts"].append({"model": mid, "ok": res["ok"], "ms": res["ms"], "error": res.get("error"),
                                         "native_tools": native, "step": step})
            if not res["ok"]:
                if first:
                    return None
                return {"ok": False, "answer": "", "error": res.get("error")}
            first = False
            calls = res["tool_calls"]
            final = None
            if not calls:
                calls, final = parse_json_calls(res["text"], known) if not last_round else ([], res["text"])
                if native and calls:            # model put a call in content instead of tool_calls
                    pass
            if not calls:
                return {"ok": True, "answer": (final if final is not None else res["text"]).strip(), "model": mid,
                        "native": native}
            # execute the requested tools (in parallel; tier checks per call)
            outs = await asyncio.gather(*(registry.run(c["name"], c.get("arguments") or {}, tctx) for c in calls[:6]))
            if native and res["tool_calls"]:
                msgs.append({"role": "assistant", "content": res["text"] or "",
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments") or {})}}
                                            for c in calls[:6]]})
                for c, o in zip(calls, outs):
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                                 "content": _clip(o.get("result") if o["ok"] else {"error": o["error"]})})
            else:
                msgs.append({"role": "assistant", "content": res["text"] or json.dumps(
                    {"tools": [{"tool": c["name"], "arguments": c.get("arguments")} for c in calls]})})
                block = "\n".join(f"TOOL RESULT {c['name']}: " + _clip(o.get("result") if o["ok"] else {"error": o["error"]})
                                  for c, o in zip(calls, outs))
                msgs.append({"role": "user", "content": block + "\n\nContinue: call more tools or reply with the final JSON."})
            for c, o in zip(calls, outs):
                steps.append({"tool": c["name"], "arguments": c.get("arguments"), "ok": o["ok"],
                              "tier": o.get("tier"), "ms": o.get("ms"), "error": o.get("error"),
                              "approval_id": o.get("approval_id"),
                              "result_preview": _clip(o.get("result"), 400) if o["ok"] else None})
        return {"ok": True, "answer": "", "model": mid, "native": native}

    out = None
    for cand in decision["ranked"][:3]:
        mid = cand["id"]
        spec = router.hub.models.get(mid)
        if spec is None:
            continue
        native = "tools" in spec.caps and getattr(router.hub.adapters.get(spec.provider), "native_tools", True)
        out = await loop_with(mid, native)
        if out is None and native:
            out = await loop_with(mid, False)        # same model, JSON protocol
        if out is not None:
            break
    if out is None:
        out = {"ok": False, "answer": "", "error": "no model could run the tool loop — " + decision["explain"]}
    if out.get("ok") and not out.get("answer"):
        done = [s for s in steps if s["ok"]]
        out["answer"] = ("Tool results: " + "; ".join(f"{s['tool']} → {s['result_preview']}" for s in done)) if done \
            else "I couldn't complete that with the available tools."
    decision["used"] = out.get("model")
    router._remember(decision)
    out.update({"steps": steps, "decision": decision, "ms": round((time.perf_counter() - t_start) * 1000),
                "approvals": [s["approval_id"] for s in steps if s.get("approval_id")]})
    return out


# ── built-in BUCS tools ──────────────────────────────────────────
PROTECTED_NAMES = {".env", "credentials.json", "token.json", "auth.json", "fleet_token.txt", "pair_code.txt",
                   "node_id.txt", "node_token.txt", "operators.json", "commander_memory.md", "operator_memory.json",
                   "providers.json", "license.json", "license_signing.key", "licences_issued.json"}


def safe_path(folders: list[str], rel: str) -> Path:
    """Resolve rel inside one of the approved folders, or raise."""
    if not folders:
        raise PermissionError("no approved folders configured")
    p = Path(rel)
    cands = [p] if p.is_absolute() else [Path(f) / rel for f in folders]
    for c in cands:
        r = c.resolve()
        for f in folders:
            base = Path(f).resolve()
            if r == base or base in r.parents:
                if r.name in PROTECTED_NAMES or r.name.startswith(".env") or r.suffix in (".key", ".pem"):
                    raise PermissionError("protected file")
                return r
    raise PermissionError("path is outside the approved folders")


def _computeruse_hook(state: dict):
    names = ("submit_task", "start_task", "launch_task", "request_task", "run_task", "create_task")
    cands = [state.get("computeruse"), (state.get("feature_modules") or {}).get("computeruse"),
             sys.modules.get("computeruse")]
    for obj in cands:
        if obj is None:
            continue
        for n in names:
            fn = getattr(obj, n, None)
            if callable(fn):
                return fn, n
    return None, None


async def _call_flex(fn, values: dict, state: dict):
    """Call a function whose exact signature we can't know at build time (concurrently built module)."""
    try:
        sig = inspect.signature(fn)
        kw, pos = {}, []
        for name, p in sig.parameters.items():
            if name in values:
                kw[name] = values[name]
            elif name in ("state",):
                kw[name] = state
            elif name in ("ctx",):
                kw[name] = {"state": state}
            elif p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                pos.append(values.get("goal"))
        res = fn(*pos, **kw) if pos and not kw else fn(**kw) if kw else fn(values.get("goal"))
    except (TypeError, ValueError):
        res = fn(values.get("goal"))
    if inspect.isawaitable(res):
        res = await res
    return res


def builtin_tools(state: dict, memory=None, folders_fn=lambda: []) -> list[Tool]:
    import httpx

    async def web_lookup(a, t):
        import livefacts
        q = str(a["query"])[:200]
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), headers=livefacts.UA,
                                     follow_redirects=True) as c:
            news, wiki = await asyncio.gather(livefacts._news(c, q), livefacts._wiki(c, f"what is {q}"),
                                              return_exceptions=True)
        return {"query": q, "fetched": time.strftime("%Y-%m-%d %H:%M"),
                "news": news if isinstance(news, list) else [], "wikipedia": wiki if isinstance(wiki, list) else [],
                "sources": "Google News RSS (last 7 days), Wikipedia REST"}

    async def news_search(a, t):
        import livefacts
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), headers=livefacts.UA,
                                     follow_redirects=True) as c:
            items = await livefacts._news(c, str(a["query"])[:200])
        return {"query": a["query"], "headlines": items, "source": "Google News RSS (last 7 days)",
                "fetched": time.strftime("%Y-%m-%d %H:%M")}

    async def weather(a, t):
        city = str(a.get("city") or "").strip()[:80]
        w = state.get("weather")
        lat = lon = None
        name = None
        if city:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                                params={"name": city, "count": 1, "language": "en", "format": "json"})
                hits = (r.json() or {}).get("results") or []
            if not hits:
                return {"error": f"place '{city}' not found"}
            h = hits[0]
            lat, lon = h["latitude"], h["longitude"]
            name = ", ".join(x for x in (h.get("name"), h.get("admin1"), h.get("country")) if x)
        if w is not None:
            res = await w.get(lat, lon)
        else:
            from weather import WeatherService
            res = await WeatherService().get(lat, lon)
        res = dict(res or {})
        if name:
            res["city"] = name
        res["source"] = "Open-Meteo (measured now)"
        return res

    def system_metrics(a, t):
        m = dict(getattr(state.get("sysmon"), "latest", None) or {})
        if "cpu" not in m:
            import psutil
            m = {"cpu": psutil.cpu_percent(interval=0.2), "mem": psutil.virtual_memory().percent,
                 "disk": psutil.disk_usage("/").percent}
        m["measured"] = "now (BUCS system monitor)"
        return m

    def calendar(a, t):
        ag = state.get("agenda")
        g = state.get("gcal")
        if ag is None:
            return {"connected": False, "events": []}
        snap = ag.snapshot()
        connected = bool(g and getattr(g, "token_present", lambda: False)())
        ev = [{k: e.get(k) for k in ("title", "start", "location", "attendees", "minutes_until") if k in e}
              for e in (snap.get("events") or [])][: int(a.get("limit") or 10)]
        return {"connected": connected, "events": ev,
                "note": "" if connected else "Google Calendar not connected — events may be empty"}

    def devices_list(a, t):
        import devices
        return {"devices": [{k: v for k, v in devices.public(d).items() if k in
                             ("id", "name", "kind", "label", "control", "status", "host")}
                            for d in list(devices._devices.values())]}

    async def device_control(a, t):
        import devices
        d = devices._devices.get(str(a["device_id"]))
        if not d:
            return {"error": "unknown device id — call devices_list first"}
        res = await devices.control(d, str(a["action"])[:40], a.get("params") if isinstance(a.get("params"), dict) else {})
        try:
            await devices.refresh(d["id"])
        except Exception:
            pass
        return {"device": d["name"], **res}

    def device_tier(a):
        act = (str(a.get("action", "")) + " " + str((a.get("params") or {}).get("service", ""))).lower()
        if re.search(r"unlock|open_cover|open\b|disarm|alarm|publish", act):
            return "destructive"
        return "action"

    def disaster_events(a, t):
        d = state.get("disaster")
        if d is None:
            return {"error": "disaster monitor not running"}
        snap = d.snapshot()
        items = snap.get("items") or []
        if a.get("near_only"):
            items = [i for i in items if i.get("near")]
        return {"counts": snap.get("counts"), "location": snap.get("location"),
                "events": [{k: i.get(k) for k in ("title", "type", "sev_label", "source", "distance_km", "ts", "url")}
                           for i in items[: int(a.get("limit") or 10)]]}

    def security_findings(a, t):
        s = state.get("secagent")
        if s is None:
            return {"error": "security agent not running"}
        snap = s.snapshot()
        return {"score": snap.get("score"), "counts": snap.get("counts"),
                "findings": [{k: f.get(k) for k in ("severity", "title", "category", "fix")}
                             for f in (snap.get("findings") or [])[: int(a.get("limit") or 10)]],
                "last_scan": snap.get("last_scan")}

    async def computer_use(a, t):
        fn, name = _computeruse_hook(state)
        if fn is None:
            return {"error": "computer-use module exposes no task API yet"}
        res = await _call_flex(fn, {"goal": a["goal"], "task": a["goal"], "prompt": a["goal"], "text": a["goal"],
                                    "instruction": a["goal"], "source": "agentos"}, state)
        return {"launched_via": name, "result": res if isinstance(res, (dict, list, str, int, float, bool)) else str(res)}

    def files_list(a, t):
        folders = folders_fn()
        base = safe_path(folders, a.get("path") or ".") if a.get("path") else Path(folders[0]).resolve() if folders else None
        if base is None:
            return {"error": "no approved folders configured", "folders": []}
        items = []
        for p in sorted(base.iterdir())[:200]:
            if p.name in PROTECTED_NAMES:
                continue
            items.append({"name": p.name, "dir": p.is_dir(), "bytes": p.stat().st_size if p.is_file() else None})
        return {"folder": str(base), "items": items, "approved_folders": folders}

    def files_read(a, t):
        p = safe_path(folders_fn(), str(a["path"]))
        if not p.is_file():
            return {"error": "not a file"}
        data = p.read_bytes()[:200_000]
        return {"path": str(p), "bytes": p.stat().st_size, "text": data.decode("utf-8", errors="replace")[:20000]}

    def files_write(a, t):
        p = safe_path(folders_fn(), str(a["path"]))
        from atomicio import atomic_write_text
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, str(a["content"])[:500_000], encoding="utf-8")
        return {"written": str(p), "bytes": p.stat().st_size}

    def files_tier(a):
        try:
            return "destructive" if safe_path(folders_fn(), str(a.get("path", ""))).exists() else "action"
        except Exception:
            return "action"

    def mem_search(a, t):
        if memory is None:
            return {"error": "memory store not ready"}
        return {"results": [{k: h[k] for k in ("id", "kind", "title", "text", "score", "source")}
                            for h in memory.search(str(a["query"]), limit=int(a.get("limit") or 6))]}

    def mem_remember(a, t):
        if memory is None:
            return {"error": "memory store not ready"}
        return memory.add(str(a["text"]), kind="fact", source=f"tool:{t.requester}")

    async def notify(a, t):
        hub = state.get("hub")
        msg = str(a["message"])[:300]
        if hub:
            await hub.broadcast({"type": "log", "level": "info", "msg": f"[ai core] {msg}"})
        return {"notified": True}

    S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
    st = {"type": "string"}
    return [
        Tool("web_lookup", "Live web lookup: recent news headlines plus a Wikipedia summary for a topic.",
             S({"query": {**st, "maxLength": 200}}, ["query"]), "read", web_lookup),
        Tool("news_search", "Latest news headlines (last 7 days) for a search query, with source and time.",
             S({"query": {**st, "maxLength": 200}}, ["query"]), "read", news_search),
        Tool("weather", "Current measured weather for a city (or the operator's location if city is omitted).",
             S({"city": {**st, "maxLength": 80}}), "read", weather),
        Tool("system_metrics", "Live CPU, memory, disk and network figures of this machine.", S({}), "read", system_metrics),
        Tool("calendar_upcoming", "Upcoming Google Calendar events (if the calendar is connected).",
             S({"limit": {"type": "integer", "minimum": 1, "maximum": 30}}), "read", calendar),
        Tool("devices_list", "Smart-home / LAN devices registered in BUCS with their live status.", S({}), "read", devices_list),
        Tool("device_control", "Control a registered device (on/off/toggle/brightness…). Unlock/open/disarm need approval.",
             S({"device_id": st, "action": st, "params": {"type": "object"}}, ["device_id", "action"]), "action",
             device_control, tier_fn=device_tier),
        Tool("disaster_events", "Live disaster / outage events (USGS, GDACS, NASA EONET, NDMA, status pages), nearest first.",
             S({"near_only": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 30}}), "read",
             disaster_events, available=lambda: (state.get("disaster") is not None, "disaster monitor not running")),
        Tool("security_findings", "Open findings from the BUCS cyber analyst (dependency CVEs, secrets, exposure).",
             S({"limit": {"type": "integer", "minimum": 1, "maximum": 30}}), "read", security_findings,
             available=lambda: (state.get("secagent") is not None, "security agent not running")),
        Tool("computer_use_task", "Launch a computer-use task on this PC (drives mouse/keyboard). Always needs approval.",
             S({"goal": {**st, "maxLength": 600}}, ["goal"]), "destructive", computer_use,
             available=lambda: (_computeruse_hook(state)[0] is not None, "computer-use module exposes no task API")),
        Tool("files_list", "List files in the operator's approved folders.", S({"path": st}), "read", files_list),
        Tool("files_read", "Read a text file inside an approved folder.", S({"path": st}, ["path"]), "read", files_read),
        Tool("files_write", "Write a text file inside an approved folder (overwriting needs approval).",
             S({"path": st, "content": st}, ["path", "content"]), "action", files_write, tier_fn=files_tier),
        Tool("memory_search", "Search the operator's long-term AI memory (facts, notes, documents, past conversations).",
             S({"query": st, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]), "read", mem_search),
        Tool("memory_remember", "Store a durable fact in long-term memory.", S({"text": {**st, "maxLength": 1000}}, ["text"]),
             "action", mem_remember),
        Tool("notify", "Show a notification on the BUCS dashboard event stream.",
             S({"message": {**st, "maxLength": 300}}, ["message"]), "action", notify),
    ]
