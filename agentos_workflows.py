"""AI CORE · agent & workflow builder.

Custom agents: name, role/system prompt, model policy (+ optional task), allowed tools,
memory scope (none | global | own), optional schedule (heartbeat every N minutes with a
heartbeat prompt).

Workflows: ordered steps with branching —
  agent      run a custom agent (or an ad-hoc prompt) → output saved to a variable
  tool       call a registered tool with templated arguments
  condition  compare a variable (contains / not_contains / equals / gt / lt / exists / empty)
             and jump to `then` / `else` (a step id or "end")
  approval   pause until the operator approves in AI CORE (reject = stop)
  notify     post a message to the dashboard event stream
Variables: {{input}}, {{<save_as>}}, {{last}}. Every run has a log and is kept in history.

Runs execute in the background (asyncio) and schedules are driven by our own 30-s loop, so
they work whether or not the dashboard's APScheduler runtime has been started.
Permissions: agents/workflows are created by the commander, so action-tier tools they were
explicitly allowed are pre-authorised; destructive tools still require a per-call approval.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path

STEP_TYPES = ("agent", "tool", "condition", "approval", "notify")
OPS = ("contains", "not_contains", "equals", "gt", "lt", "exists", "empty")
MAX_STEPS_EXEC = 60
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:32] or uuid.uuid4().hex[:8]


def render(tpl, vars: dict):
    if isinstance(tpl, str):
        return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", lambda m: str(vars.get(m.group(1), ""))[:4000], tpl)
    if isinstance(tpl, dict):
        return {k: render(v, vars) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [render(v, vars) for v in tpl]
    return tpl


def check_condition(cond: dict, vars: dict) -> bool:
    v = vars.get(cond.get("var", "last"))
    op = cond.get("op", "contains")
    want = cond.get("value", "")
    s = "" if v is None else str(v)
    if op == "exists":
        return v is not None and s != ""
    if op == "empty":
        return v is None or s.strip() == ""
    if op == "contains":
        return str(want).lower() in s.lower()
    if op == "not_contains":
        return str(want).lower() not in s.lower()
    if op == "equals":
        return s.strip().lower() == str(want).strip().lower()
    try:
        num = float(re.search(r"-?\d+(\.\d+)?", s).group(0))
        return num > float(want) if op == "gt" else num < float(want)
    except Exception:
        return False


def validate_agent(d: dict, known_tools: set, policies: set) -> tuple[dict | None, str | None]:
    name = str(d.get("name") or "").strip()[:60]
    if not name:
        return None, "name is required"
    tools = [t for t in (d.get("tools") or []) if isinstance(t, str)]
    unknown = [t for t in tools if t not in known_tools]
    if unknown:
        return None, f"unknown tools: {', '.join(unknown)}"
    policy = d.get("policy") or "balanced"
    if policy not in policies:
        return None, "unknown policy"
    sched = d.get("schedule") or None
    if sched:
        try:
            every = int(sched.get("every_min"))
        except Exception:
            return None, "schedule.every_min must be a number"
        if every < 5:
            return None, "schedules run at most every 5 minutes"
        sched = {"every_min": every}
    mem = d.get("memory_scope") or "global"
    if mem not in ("none", "global", "own"):
        return None, "memory_scope must be none, global or own"
    return {"id": d.get("id") if d.get("id") and _ID.match(str(d.get("id"))) else _slug(name),
            "name": name, "role": str(d.get("role") or "")[:120], "system": str(d.get("system") or "")[:6000],
            "policy": policy, "task": d.get("task") or None, "tools": tools, "memory_scope": mem,
            "schedule": sched, "heartbeat": str(d.get("heartbeat") or "")[:1000],
            "enabled": bool(d.get("enabled", True))}, None


def validate_workflow(d: dict, known_tools: set, agents: dict) -> tuple[dict | None, str | None]:
    name = str(d.get("name") or "").strip()[:60]
    if not name:
        return None, "name is required"
    steps = d.get("steps") or []
    if not isinstance(steps, list) or not steps or len(steps) > 30:
        return None, "a workflow needs 1–30 steps"
    out, ids = [], set()
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or s.get("type") not in STEP_TYPES:
            return None, f"step {i + 1}: type must be one of {', '.join(STEP_TYPES)}"
        sid = str(s.get("id") or f"s{i + 1}")
        if not _ID.match(sid) or sid in ids:
            return None, f"step {i + 1}: bad or duplicate id"
        ids.add(sid)
        st = {"id": sid, "type": s["type"], "label": str(s.get("label") or "")[:80]}
        if s["type"] == "agent":
            if s.get("agent") and s["agent"] not in agents:
                return None, f"step {sid}: unknown agent"
            if not s.get("agent") and not s.get("prompt"):
                return None, f"step {sid}: agent or prompt required"
            st.update(agent=s.get("agent"), prompt=str(s.get("prompt") or "")[:4000], save_as=s.get("save_as") or sid,
                      policy=s.get("policy"))
        elif s["type"] == "tool":
            if s.get("tool") not in known_tools:
                return None, f"step {sid}: unknown tool"
            st.update(tool=s["tool"], args=s.get("args") if isinstance(s.get("args"), dict) else {},
                      save_as=s.get("save_as") or sid)
        elif s["type"] == "condition":
            c = s.get("if") or {}
            if c.get("op") not in OPS:
                return None, f"step {sid}: condition op must be one of {', '.join(OPS)}"
            st.update(**{"if": {"var": str(c.get("var") or "last"), "op": c["op"], "value": c.get("value", "")},
                         "then": s.get("then"), "else": s.get("else")})
        elif s["type"] == "approval":
            st.update(message=str(s.get("message") or "Approve to continue")[:400])
        elif s["type"] == "notify":
            st.update(message=str(s.get("message") or "")[:400])
        out.append(st)
    for st in out:
        if st["type"] == "condition":
            for k in ("then", "else"):
                if st.get(k) not in (None, "", "end", "next") and st[k] not in ids:
                    return None, f"step {st['id']}: {k} points to unknown step"
    sched = d.get("schedule") or None
    if sched:
        try:
            every = int(sched.get("every_min"))
        except Exception:
            return None, "schedule.every_min must be a number"
        if every < 5:
            return None, "schedules run at most every 5 minutes"
        sched = {"every_min": every}
    return {"id": d.get("id") if d.get("id") and _ID.match(str(d.get("id"))) else _slug(name), "name": name,
            "description": str(d.get("description") or "")[:300], "steps": out, "schedule": sched,
            "enabled": bool(d.get("enabled", True))}, None


class Studio:
    def __init__(self, folder: Path, *, ask, run_tool, request_approval, notify=None, tool_names=lambda: set(),
                 policies=frozenset({"balanced"}), audit=None) -> None:
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ask = ask                        # async (prompt, agent_def|None, policy) -> {"answer", ...}
        self.run_tool = run_tool              # async (name, args, run) -> tool result
        self.request_approval = request_approval   # (run, step) -> approval dict
        self.notify = notify                  # async (msg)
        self.tool_names = tool_names
        self.policies = set(policies)
        self.audit = audit
        self.agents: dict = {}
        self.workflows: dict = {}
        self.runs: list[dict] = []
        self.last_fire: dict = {}
        self._tasks: set = set()
        self._load()

    # ── persistence ────────────────────────────────────────────
    def _p(self, n: str) -> Path:
        return self.dir / n

    def _load(self) -> None:
        for attr, fn in (("agents", "agents.json"), ("workflows", "workflows.json")):
            try:
                setattr(self, attr, {d["id"]: d for d in json.loads(self._p(fn).read_text(encoding="utf-8"))})
            except Exception:
                pass
        try:
            self.runs = json.loads(self._p("runs.json").read_text(encoding="utf-8"))
        except Exception:
            self.runs = []
        try:
            self.last_fire = json.loads(self._p("schedule.json").read_text(encoding="utf-8"))
        except Exception:
            self.last_fire = {}
        for r in self.runs:
            if r["status"] == "running":
                r["status"] = "interrupted"
                r["log"].append({"ts": time.time(), "msg": "BUCS restarted while this run was in progress"})

    def _save(self, what: str = "all") -> None:
        from atomicio import atomic_write_text
        if what in ("all", "agents"):
            atomic_write_text(self._p("agents.json"), json.dumps(list(self.agents.values()), indent=1), encoding="utf-8")
        if what in ("all", "workflows"):
            atomic_write_text(self._p("workflows.json"), json.dumps(list(self.workflows.values()), indent=1), encoding="utf-8")
        if what in ("all", "runs"):
            atomic_write_text(self._p("runs.json"), json.dumps(self.runs[:100], default=str), encoding="utf-8")
        if what in ("all", "schedule"):
            atomic_write_text(self._p("schedule.json"), json.dumps(self.last_fire), encoding="utf-8")

    # ── CRUD ───────────────────────────────────────────────────
    def upsert_agent(self, d: dict) -> dict:
        a, err = validate_agent(d, self.tool_names(), self.policies)
        if err:
            return {"ok": False, "error": err}
        self.agents[a["id"]] = a
        self._save("agents")
        return {"ok": True, "agent": a}

    def delete_agent(self, aid: str) -> dict:
        used = [w["name"] for w in self.workflows.values() if any(s.get("agent") == aid for s in w["steps"])]
        if used:
            return {"ok": False, "error": "used by workflow(s): " + ", ".join(used)}
        ok = self.agents.pop(aid, None) is not None
        self._save("agents")
        return {"ok": ok}

    def upsert_workflow(self, d: dict) -> dict:
        w, err = validate_workflow(d, self.tool_names(), self.agents)
        if err:
            return {"ok": False, "error": err}
        self.workflows[w["id"]] = w
        self._save("workflows")
        return {"ok": True, "workflow": w}

    def delete_workflow(self, wid: str) -> dict:
        ok = self.workflows.pop(wid, None) is not None
        self._save("workflows")
        return {"ok": ok}

    # ── runs ───────────────────────────────────────────────────
    def _new_run(self, kind: str, ref: str, name: str, trigger: str, inp: str) -> dict:
        r = {"id": uuid.uuid4().hex[:10], "kind": kind, "ref": ref, "name": name, "trigger": trigger,
             "status": "running", "started": time.time(), "ended": None, "pc": 0, "vars": {"input": inp},
             "log": [], "output": "", "approval_id": None, "executed": 0}
        self.runs.insert(0, r)
        self.runs = self.runs[:100]
        return r

    def _log(self, run: dict, msg: str, step: str | None = None, **kw) -> None:
        run["log"].append({"ts": time.time(), "step": step, "msg": str(msg)[:1200], **kw})
        run["log"] = run["log"][-200:]

    def get_run(self, rid: str) -> dict | None:
        return next((r for r in self.runs if r["id"] == rid), None)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def run_agent(self, aid: str, prompt: str | None = None, trigger: str = "manual", wait: bool = False) -> dict:
        a = self.agents.get(aid)
        if a is None:
            return {"ok": False, "error": "unknown agent"}
        run = self._new_run("agent", aid, a["name"], trigger, prompt or a.get("heartbeat") or "")
        self._save("runs")

        async def go():
            try:
                q = prompt or a.get("heartbeat") or "Report your status."
                self._log(run, f"prompt: {q[:300]}")
                res = await self.ask(q, a, a.get("policy"))
                for s in res.get("steps") or []:
                    self._log(run, f"tool {s['tool']} → {'ok' if s['ok'] else s.get('error')}", tier=s.get("tier"))
                d = res.get("decision") or {}
                self._log(run, f"model: {d.get('used') or d.get('chosen')} · {d.get('explain', '')[:300]}")
                run["output"] = res.get("answer") or res.get("error") or ""
                run["status"] = "done" if res.get("ok", True) and run["output"] else "failed"
            except Exception as e:
                run["status"] = "failed"
                self._log(run, f"error: {type(e).__name__}: {e}")
            run["ended"] = time.time()
            self._save("runs")
        if wait:
            await go()
        else:
            self._spawn(go())
        return {"ok": True, "run": run}

    async def run_workflow(self, wid: str, inp: str = "", trigger: str = "manual", wait: bool = False) -> dict:
        w = self.workflows.get(wid)
        if w is None:
            return {"ok": False, "error": "unknown workflow"}
        run = self._new_run("workflow", wid, w["name"], trigger, inp)
        self._save("runs")
        if wait:
            await self._exec(run)
        else:
            self._spawn(self._exec(run))
        return {"ok": True, "run": run}

    async def _exec(self, run: dict) -> None:
        w = self.workflows.get(run["ref"])
        if w is None:
            run["status"] = "failed"
            self._log(run, "workflow was deleted")
            self._save("runs")
            return
        steps = w["steps"]
        index = {s["id"]: i for i, s in enumerate(steps)}
        vars = run["vars"]
        try:
            while 0 <= run["pc"] < len(steps):
                if run["executed"] >= MAX_STEPS_EXEC:
                    raise RuntimeError(f"stopped after {MAX_STEPS_EXEC} steps (loop?)")
                s = steps[run["pc"]]
                run["executed"] += 1
                nxt = run["pc"] + 1
                if s["type"] == "agent":
                    prompt = render(s.get("prompt") or "{{input}}", vars)
                    agent = self.agents.get(s.get("agent")) if s.get("agent") else None
                    res = await self.ask(prompt, agent, s.get("policy") or (agent or {}).get("policy"))
                    out = res.get("answer") or ""
                    if not res.get("ok", True) and not out:
                        raise RuntimeError(res.get("error") or "agent step failed")
                    vars[s["save_as"]] = vars["last"] = out
                    d = res.get("decision") or {}
                    self._log(run, f"{(agent or {}).get('name', 'prompt')} → {out[:400]}", s["id"],
                              model=d.get("used") or d.get("chosen"))
                elif s["type"] == "tool":
                    args = render(s.get("args") or {}, vars)
                    res = await self.run_tool(s["tool"], args, run)
                    if res.get("approval_id"):
                        run["status"] = "waiting_approval"
                        run["approval_id"] = res["approval_id"]
                        run["pending_var"] = s["save_as"]
                        run["pc"] = nxt
                        vars[s["save_as"]] = vars["last"] = "(waiting for approval)"
                        self._log(run, f"tool {s['tool']} needs approval {res['approval_id']}", s["id"])
                        self._save("runs")
                        return
                    if not res.get("ok"):
                        raise RuntimeError(f"tool {s['tool']}: {res.get('error')}")
                    val = res.get("result")
                    vars[s["save_as"]] = vars["last"] = val if isinstance(val, str) else json.dumps(val, default=str)[:6000]
                    self._log(run, f"tool {s['tool']} ok ({res.get('ms')} ms)", s["id"])
                elif s["type"] == "condition":
                    ok = check_condition(s["if"], vars)
                    target = s.get("then") if ok else s.get("else")
                    self._log(run, f"if {s['if']['var']} {s['if']['op']} {s['if'].get('value')!r} → {ok}"
                                   f" → {target or 'next'}", s["id"])
                    if target == "end":
                        nxt = len(steps)
                    elif target and target != "next":
                        nxt = index[target]
                elif s["type"] == "approval":
                    ap = self.request_approval(run, s)
                    run["status"] = "waiting_approval"
                    run["approval_id"] = ap["id"]
                    run["pc"] = nxt
                    self._log(run, f"waiting for operator approval ({ap['id']}): {s['message']}", s["id"])
                    self._save("runs")
                    return
                elif s["type"] == "notify":
                    msg = render(s["message"], vars)
                    if self.notify:
                        await self.notify(f"{run['name']}: {msg}")
                    self._log(run, f"notified: {msg[:300]}", s["id"])
                run["pc"] = nxt
                self._save("runs")
            run["status"] = "done"
            run["output"] = str(vars.get("last", ""))[:4000]
        except asyncio.CancelledError:
            run["status"] = "interrupted"
            raise
        except Exception as e:
            run["status"] = "failed"
            self._log(run, f"error: {e}")
        finally:
            if run["status"] != "waiting_approval":
                run["ended"] = time.time()
            self._save("runs")

    async def resume(self, approval_id: str, approved: bool, wait: bool = False, value=None) -> dict:
        """Continue (or stop) a run parked on an approval. `value` = result of an approved tool call."""
        run = next((r for r in self.runs if r.get("approval_id") == approval_id and r["status"] == "waiting_approval"), None)
        if run is None:
            return {"ok": False, "error": "no run is waiting on that approval"}
        run["approval_id"] = None
        pv = run.pop("pending_var", None)
        if pv and value is not None:
            v = value if isinstance(value, str) else json.dumps(value, default=str)[:6000]
            run["vars"][pv] = run["vars"]["last"] = v
        if not approved:
            run["status"] = "rejected"
            run["ended"] = time.time()
            self._log(run, "operator rejected — run stopped")
            self._save("runs")
            return {"ok": True, "run": run}
        self._log(run, "operator approved — continuing")
        run["status"] = "running"
        if wait:
            await self._exec(run)
        else:
            self._spawn(self._exec(run))
        return {"ok": True, "run": run}

    def waiting(self) -> list[dict]:
        return [r for r in self.runs if r["status"] == "waiting_approval"]

    # ── schedules ──────────────────────────────────────────────
    async def tick(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        fired = []
        for kind, items in (("agent", self.agents), ("workflow", self.workflows)):
            for d in list(items.values()):
                sch = d.get("schedule")
                if not sch or not d.get("enabled", True):
                    continue
                key = f"{kind}:{d['id']}"
                last = self.last_fire.get(key, 0)
                if now - last >= sch["every_min"] * 60:
                    if any(r["ref"] == d["id"] and r["status"] in ("running", "waiting_approval") for r in self.runs[:30]):
                        continue
                    self.last_fire[key] = now
                    if kind == "agent":
                        await self.run_agent(d["id"], trigger="schedule")
                    else:
                        await self.run_workflow(d["id"], trigger="schedule")
                    fired.append(key)
        if fired:
            self._save("schedule")
        return fired

    def snapshot(self) -> dict:
        return {"agents": list(self.agents.values()), "workflows": list(self.workflows.values()),
                "runs": [{k: r[k] for k in ("id", "kind", "ref", "name", "trigger", "status", "started", "ended",
                                             "approval_id")} | {"output": (r.get("output") or "")[:300],
                                                                "steps": len(r["log"])} for r in self.runs[:40]]}
