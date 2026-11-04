"""AUTONOMOUS EVOLUTION — a controlled, recursive self-improvement loop for BUCS.

  OBSERVE → ANALYZE → IDENTIFY → PROPOSE → TEST (sandbox) → APPROVAL → DEPLOY → EVALUATE

A persistent agent with a heartbeat (every N minutes) and scheduled research missions,
under strict human control:

  observe    real signals only: self-heal checks + repairs, cyber-analyst findings, server
             exceptions (logging handler: type @ repo file:line, scrubbed message, counts),
             endpoint latency (in-process sampling of cheap GET endpoints + an optional timing
             middleware hook), voice diagnostics, command-engine latency, unit-test results
             (owner machine only, scheduled subprocess), release/dependency status, 👍/👎 feedback
  analyze    deterministic analyzers (evolution_core) + an LLM analyst that sees ONLY the
             measured evidence → improvement backlog (evidence, impact, risk, effort,
             confidence, trend), deduplicated by key, auto-resolved when the signal is gone
  propose    code-level items become a precise task for the engineering agent
             (devagent.diagnose — read-only); nothing is ever applied silently
  test       devagent.sandbox(): the change is made and tested in an ISOLATED COPY
             (unit-test subset + boot check) before anyone is asked to approve it
  approval   the commander approves (diff + tests + risk shown) or rejects with a reason
  deploy     devagent.deploy_patch(): only an approved, passing, non-stale patch; then the
             existing KEEP · UNDO · SHIP path. Owner machine only
  evaluate   the targeted metric before vs after over a window; regressions are flagged with
             a one-click revert (approval needed) — automatic only when self-heal reports a
             catastrophic core failure, then self-heal's guarded restart rules apply

Customer installs: observation + analysis only. Every action is audited. Persistence:
.update/evolution.json (+ .update/evolution/patches, .update/evolution/deploys).
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

import evolution_core as core
from evolution_core import scrub

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".update" / "evolution.json"
PATCH_DIR = HERE / ".update" / "evolution" / "patches"
PROCESS_START = time.time()

SAMPLE_ENDPOINTS = ("/api/status", "/api/health", "/api/roadmap", "/api/agenda", "/api/engine", "/api/secagent",
                    "/api/dev")
DEFAULT_SETTINGS = {"autonomy": "propose",          # observe | propose  (deploy ALWAYS needs approval)
                    "heartbeat_min": 10, "eval_window_min": 30, "tests_every_h": 12, "research_every_h": 168,
                    "auto_propose_every_h": 6, "auto_propose_min_priority": 10.0, "analyst_every_min": 60}
HEARTBEAT_COOLDOWN = 20
ANALYST_COOLDOWN = 120
RESEARCH_COOLDOWN = 600
FEEDBACK_PER_HOUR = 30
FEEDBACK_AREAS = ("general", "voice", "agents", "dashboard", "vision", "security", "performance", "engineering",
                  "evolution", "google", "news", "other")
LOOP_LABEL = {"observe": "OBSERVE", "analyze": "ANALYZE", "identify": "IDENTIFY", "propose": "PROPOSE",
              "test": "SANDBOX TEST", "approval": "APPROVAL", "deploy": "DEPLOY", "evaluate": "EVALUATE"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

RECORDER = core.ErrorRecorder(HERE)       # one handler per process

# ═══ optional passive timing hook (main.py: app.middleware("http")(evolution.timing_middleware)) ═══
_PASSIVE: dict[str, list] = {}


def record_request(path: str, ms: float, status: int = 200) -> None:
    if not path.startswith("/api/") or path.startswith("/api/evolution"):
        return
    s = _PASSIVE.setdefault(path[:80], [])
    s.append(float(ms))
    del s[:-200]
    if len(_PASSIVE) > 150:
        _PASSIVE.pop(next(iter(_PASSIVE)))


async def timing_middleware(request, call_next):
    t = time.perf_counter()
    resp = await call_next(request)
    try:
        route = request.scope.get("route")
        record_request(getattr(route, "path", None) or request.url.path, (time.perf_counter() - t) * 1000,
                       resp.status_code)
    except Exception:
        pass
    return resp


class Evolution:
    def __init__(self, state: dict, ctx: dict | None = None, store_path: Path = STATE_FILE, root: Path = HERE,
                 recorder: core.ErrorRecorder | None = None) -> None:
        self.state, self.ctx, self.root = state, ctx or {}, Path(root)
        self.store = core.Store(store_path)
        self.patch_dir = Path(store_path).parent / "evolution" / "patches"
        self.errors = recorder or RECORDER
        self.signals = core.Signals()
        self.backlog = core.Backlog()
        self.settings = dict(DEFAULT_SETTINGS)
        self.feedback: list[dict] = []
        self.research: dict = {}
        self.analyst: dict | None = None
        self.heartbeat = {"beats": 0, "last": None, "next": None, "last_ms": None, "last_error": None,
                          "running": False, "reason": None}
        self.tests = {"last": None, "prev": None, "running": False, "next": None}
        self.history: list[dict] = []
        self.last_observe: dict = {}
        self.last_analysis: dict = {}
        self.auto_last = 0.0
        self.sec_findings: list[dict] = []
        self.voice_diag: dict | None = None
        self.update_info: dict | None = None
        self._err_counts: dict[str, int] = {}
        self._sh_seen = 0.0
        self._beat_lock = asyncio.Lock()
        self._tasks: set = set()
        self._last_manual = {"beat": 0.0, "analyst": 0.0, "research": 0.0}
        self._fb_times: list[float] = []
        self._load()

    # ── persistence ──────────────────────────────────────────────
    def _load(self) -> None:
        d = self.store.load()
        if not d:
            return
        self.settings.update({k: v for k, v in (d.get("settings") or {}).items() if k in DEFAULT_SETTINGS})
        self.signals.load(d.get("signals"))
        self.errors.load(d.get("errors"))
        self.backlog.items = {i["id"]: i for i in d.get("items") or [] if isinstance(i, dict) and i.get("id")}
        self.feedback = (d.get("feedback") or [])[-300:]
        self.research = d.get("research") or {}
        self.analyst = d.get("analyst")
        self.heartbeat.update({k: v for k, v in (d.get("heartbeat") or {}).items() if k in ("beats", "last", "last_ms")})
        t = d.get("tests") or {}
        self.tests.update(last=t.get("last"), prev=t.get("prev"))
        self.history = (d.get("history") or [])[-200:]
        self.last_observe = d.get("last_observe") or {}
        self.last_analysis = d.get("last_analysis") or {}
        self.auto_last = d.get("auto_last") or 0.0
        self._err_counts = d.get("err_counts") or {}
        self._sh_seen = d.get("sh_seen") or 0.0
        for it in self.backlog.items.values():          # work that was in flight when BUCS stopped
            if it["stage"] in ("proposing", "sandboxing", "deploying", "reverting"):
                core.transition(it, "failed", "interrupted by a restart")

    def save(self) -> None:
        self.store.save({"v": 1, "settings": self.settings, "signals": self.signals.to_json(),
                         "errors": self.errors.snapshot(), "items": list(self.backlog.items.values()),
                         "feedback": self.feedback[-300:], "research": self.research, "analyst": self.analyst,
                         "heartbeat": {k: self.heartbeat.get(k) for k in ("beats", "last", "last_ms")},
                         "tests": {"last": self.tests.get("last"), "prev": self.tests.get("prev")},
                         "history": self.history[-200:], "last_observe": self.last_observe,
                         "last_analysis": self.last_analysis, "auto_last": self.auto_last,
                         "err_counts": self._err_counts, "sh_seen": self._sh_seen})

    def _patch_path(self, iid: str) -> Path:
        return self.patch_dir / f"{re.sub(r'[^a-z0-9]', '', iid)}.json"

    # ── roles / install type ─────────────────────────────────────
    @property
    def owner(self) -> bool:
        lic = self.state.get("licensing")
        if lic is not None:
            return bool(getattr(lic, "owner", False))
        return os.getenv("BUCS_OWNER", "0") == "1"

    @property
    def active_install(self) -> bool:
        lic = self.state.get("licensing")
        return lic is None or bool(getattr(lic, "paid", True))

    def _operator(self) -> str:
        return (self.state.get("rbac") or {}).get("operator", "commander")

    async def _audit(self, action: str, detail: str = "") -> None:
        detail = scrub(detail, 200)
        fn = self.ctx.get("audit")
        try:
            if fn:
                await fn(action, detail)
            elif self.state.get("audit") is not None:
                await self.state["audit"].log(action, detail, operator=self._operator())
        except Exception:
            pass

    async def _broadcast(self, event: str, **kw) -> None:
        hub = self.state.get("hub")
        if hub is not None:
            try:
                await hub.broadcast({"type": "evolution", "event": event, **kw})
            except Exception:
                pass

    async def _log(self, msg: str, level: str = "info") -> None:
        hub = self.state.get("hub")
        if hub is not None:
            try:
                await hub.broadcast({"type": "log", "level": level, "msg": "[evolution] " + scrub(msg, 240)})
            except Exception:
                pass

    def _note(self, iid: str | None, action: str, by: str = "system", note: str = "") -> None:
        self.history.append({"ts": time.time(), "item": iid, "action": action, "by": by, "note": scrub(note, 240)})
        del self.history[:-200]

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    # ═══ OBSERVE ═════════════════════════════════════════════════
    async def observe(self) -> dict:
        now, sig, src = time.time(), self.signals, {}
        n0 = sum(len(v) for v in sig.series.values())

        def guard(name, fn):
            try:
                r = fn()
                src[name] = r or "ok"
            except Exception as e:
                src[name] = f"error: {type(e).__name__}"

        def selfheal():
            sh = self.state.get("selfheal")
            if sh is None:
                return "not running"
            snap = sh.snapshot()
            if not snap.get("checks"):
                return "no checks yet"
            for k, v in snap["checks"].items():
                sig.add("check:" + k, core.STATUS_VAL.get(v.get("status"), 0), now, v.get("detail", ""))
                if k == "event_loop" and isinstance(v.get("value"), (int, float)):
                    sig.add("selfheal:event_loop_ms", v["value"], now)
            sig.add("selfheal:overall", core.STATUS_VAL.get(snap.get("overall"), 0), now)
            newest = self._sh_seen
            for e in snap.get("log") or []:
                if e.get("ts", 0) > self._sh_seen:
                    sig.event("selfheal." + str(e.get("kind")), e.get("msg", ""), e.get("ts"))
                    newest = max(newest, e["ts"])
            self._sh_seen = newest
            return f"{len(snap['checks'])} checks"

        def secagent():
            sa = self.state.get("secagent")
            if sa is None:
                return "not loaded"
            act = sa.active()
            self.sec_findings = [{k: f.get(k) for k in ("id", "severity", "title", "evidence", "fix", "task")}
                                 for f in act if f.get("severity") in ("critical", "high", "medium")][:40]
            sig.add("secagent:active_high", sum(1 for f in act if f.get("severity") in ("critical", "high")), now)
            sig.add("secagent:active", len(act), now)
            if getattr(sa, "last_full", 0) or getattr(sa, "last_light", 0):
                from secagent import score
                sig.add("secagent:score", score(act), now)
            return f"{len(act)} active findings"

        def errors():
            snap = self.errors.snapshot()
            total = 0
            for e in snap:
                prev = self._err_counts.get(e["sig"], 0)
                delta = max(0, e["count"] - prev) if e["count"] >= prev else e["count"]
                self._err_counts[e["sig"]] = e["count"]
                sig.add("errors:" + e["sig"], delta, now)
                total += delta
            if len(self._err_counts) > 400:
                keep = {e["sig"] for e in snap}
                self._err_counts = {k: v for k, v in self._err_counts.items() if k in keep}
            sig.add("errors:total", total, now)
            return f"{total} new errors · {len(snap)} signatures"

        def passive():
            n = 0
            for path, xs in list(_PASSIVE.items()):
                if xs:
                    sig.add("endpoint:" + path, core.pct(xs, 0.95), now)
                    n += len(xs)
                    xs.clear()
            return f"{n} requests timed" if n else "no timing hook (sampling only)"

        def voice():
            v = self.state.get("voice")
            if v is None:
                return "voice off"
            d = v.diagnostics()
            self.voice_diag = {"utterances": [{k: u.get(k) for k in ("ts", "stt_ms", "peak", "gain", "outcome",
                                                                        "seconds")}
                                              for u in (d.get("utterances") or [])[:30] if isinstance(u, dict)]}
            utt = self.voice_diag["utterances"]
            if utt:
                ign = sum(1 for u in utt if str(u.get("outcome", "")).startswith("ignored"))
                sig.add("voice:ignored_ratio", ign / len(utt), now)
                stt = [u["stt_ms"] for u in utt if isinstance(u.get("stt_ms"), (int, float))]
                if stt:
                    sig.add("voice:stt_ms_p50", core.pct(stt, 0.5), now)
            return f"{len(utt)} utterances"

        def engine():
            eng = self.state.get("engine")
            if eng is None:
                return "no engine"
            snap = eng.snapshot()
            for path, v in (snap.get("latency") or {}).items():
                if v.get("p95_ms") is not None:
                    sig.add(f"engine:{path}:p95", v["p95_ms"], now)
            tot = snap.get("totals") or {}
            for k in ("failed", "done", "cancelled"):
                if isinstance(tot.get(k), (int, float)):
                    sig.add("engine:" + k, tot[k], now)
            if snap.get("first_token_p50_ms") is not None:
                sig.add("engine:first_token_p50", snap["first_token_p50_ms"], now)
            return f"{len(snap.get('latency') or {})} routes"

        def brain():
            b = self.state.get("brain")
            rc = getattr(b, "route_counts", None) or {}
            tot = sum(rc.values())
            if tot:
                sig.add("brain:fallback_pct", (rc.get("template", 0) + rc.get("offline", 0)) * 100 / tot, now)
            for k, ms in (getattr(b, "route_ms", None) or {}).items():
                sig.add(f"brain:{k}:ms", ms, now)
            return f"{tot} replies" if tot else "no replies yet"

        def updates():
            up = self.state.get("updater")
            if up is None:
                return "no updater"
            snap = up.snapshot()
            self.update_info = {k: snap.get(k) for k in ("version", "latest", "available", "date", "managed_by_git",
                                                         "checked_at")}
            return "update available" if snap.get("available") else f"v{snap.get('version')}"

        for name, fn in (("selfheal", selfheal), ("secagent", secagent), ("errors", errors), ("timing", passive),
                         ("voice", voice), ("engine", engine), ("brain", brain), ("updates", updates)):
            guard(name, fn)
        try:
            src["endpoints"] = await self._sample_endpoints(now)
        except Exception as e:
            src["endpoints"] = f"error: {type(e).__name__}"
        t = self.tests.get("last")
        src["tests"] = ("running" if self.tests["running"] else
                        (f"last run {t.get('ran')} tests, {len(t.get('failing') or [])} failing" if t and not t.get("skipped")
                         else (t or {}).get("skipped") or "not run yet"))
        fb = [f for f in self.feedback if f["ts"] > now - 7 * 86400]
        src["feedback"] = f"{len(fb)} in 7 days"
        n1 = sum(len(v) for v in sig.series.values())
        self.last_observe = {"ts": now, "sources": {k: scrub(v, 80) for k, v in src.items()},
                             "samples": max(0, n1 - n0)}
        return self.last_observe

    async def _sample_endpoints(self, now: float) -> str:
        m = sys.modules.get("main")
        app = getattr(m, "app", None)
        if app is None:
            return "app not available"
        import httpx
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50999))
        n = 0
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", timeout=15) as c:
            for path in SAMPLE_ENDPOINTS:
                t = time.perf_counter()
                try:
                    r = await c.get(path)
                except Exception:
                    continue
                ms = (time.perf_counter() - t) * 1000
                if r.status_code == 200:
                    self.signals.add("endpoint:" + path, ms, now)
                    n += 1
        return f"{n}/{len(SAMPLE_ENDPOINTS)} endpoints sampled"

    # ═══ ANALYZE + IDENTIFY ══════════════════════════════════════
    def analyze(self, now: float | None = None) -> dict:
        now = now or time.time()
        by, cands = {}, []
        steps = (("errors", lambda: core.analyze_errors(self.errors.snapshot(), now)),
                 ("performance", lambda: core.analyze_perf(self.signals, now)),
                 ("checks", lambda: core.analyze_checks(self.signals, now)),
                 ("tests", lambda: core.analyze_tests(self.tests.get("last"), self.tests.get("prev"))),
                 ("security", lambda: core.analyze_security(self.sec_findings)),
                 ("voice", lambda: core.analyze_voice(self.voice_diag)),
                 ("feedback", lambda: core.analyze_feedback(self.feedback, now)),
                 ("updates", lambda: core.analyze_updates(self.update_info, (self.research or {}).get("outdated") or [])))
        for name, fn in steps:
            try:
                r = fn()
            except Exception as e:
                r = []
                by[name] = f"error: {type(e).__name__}"
                continue
            by[name] = len(r)
            cands += r
        res = self.backlog.upsert_all(cands, now)
        for iid in res["new"]:
            self._note(iid, "identified", note=self.backlog.items[iid]["title"])
        for iid in res["resolved"]:
            self._note(iid, "resolved", note="signal absent")
        self.last_analysis = {"ts": now, "candidates": len(cands), "by_analyzer": by, **{k: len(v) for k, v in res.items()}}
        return res

    # ═══ HEARTBEAT ═══════════════════════════════════════════════
    async def beat(self, reason: str = "heartbeat") -> dict:
        if self._beat_lock.locked():
            return {"ok": False, "error": "a heartbeat is already running"}
        async with self._beat_lock:
            t0 = time.time()
            self.heartbeat.update(running=True, reason=reason)
            await self._broadcast("beat", stage="observe")
            try:
                await self.observe()
                await self._broadcast("beat", stage="analyze")
                res = self.analyze()
                await self.evaluate()
                await self._maybe_scheduled()
                await self._maybe_auto_propose()
                if res["new"] and time.time() - (self.analyst or {}).get("ts", 0) > self.settings["analyst_every_min"] * 60:
                    if any(self.backlog.items[i]["impact"] >= 4 for i in res["new"]):
                        self._spawn(self.run_analyst(auto=True))
                self.heartbeat["last_error"] = None
            except Exception as e:
                res = {"new": [], "updated": [], "resolved": []}
                self.heartbeat["last_error"] = f"{type(e).__name__}: {scrub(e, 160)}"
            ms = round((time.time() - t0) * 1000)
            self.heartbeat.update(running=False, last=time.time(), last_ms=ms, beats=self.heartbeat["beats"] + 1,
                                  next=time.time() + self.settings["heartbeat_min"] * 60)
            self.save()
            await self._broadcast("beat", stage="done", new=len(res["new"]))
            if res["new"]:
                await self._log(f"heartbeat: {len(res['new'])} new improvement(s) identified")
            return {"ok": True, "ms": ms, **{k: len(v) for k, v in res.items()}}

    async def _maybe_scheduled(self) -> None:
        now = time.time()
        if self.owner and not self.tests["running"]:
            last = (self.tests.get("last") or {}).get("ts", 0)
            due = max(last + self.settings["tests_every_h"] * 3600, PROCESS_START + 600)
            self.tests["next"] = due
            if now >= due:
                self._spawn(self.run_tests())
        r_last = (self.research or {}).get("ts", 0)
        if (self.owner or self.active_install) and now - r_last > self.settings["research_every_h"] * 3600 \
                and now - PROCESS_START > 300 and not (self.research or {}).get("running"):
            self._spawn(self.run_research())

    async def loop(self) -> None:
        await asyncio.sleep(45)
        while True:
            if self.active_install:
                try:
                    await self.beat()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            await asyncio.sleep(max(2, int(self.settings["heartbeat_min"])) * 60)

    # ═══ test suite (owner machine only) ═════════════════════════
    def test_status(self, modules: list[str]) -> str:
        last = self.tests.get("last")
        if not last or last.get("skipped") or not last.get("ran"):
            return "unknown"
        known = set(last.get("modules") or [])
        failing = last.get("failing") or []
        mods = [m for m in modules if m in known]
        if not mods:
            return "unknown"
        if any(any(f.startswith(f"{m}.") or f".{m}." in f or f.startswith(f"tests.{m}.") for f in failing) for m in mods):
            return "fail"
        return "pass"

    async def run_tests(self) -> dict:
        if not self.owner:
            return {"ok": False, "error": "tests run only on the owner's machine"}
        if not (self.root / "tests").is_dir():
            self.tests["last"] = {"ts": time.time(), "skipped": "tests/ is not shipped with this install"}
            return {"ok": True, "skipped": True}
        if self.tests["running"]:
            return {"ok": False, "error": "a test run is already in progress"}
        self.tests["running"] = True
        await self._broadcast("tests", running=True)
        try:
            import evolution_sandbox as sbx
            py = sbx.python_exe(self.root)
            mods = sorted(p.stem for p in (self.root / "tests").glob("test_*.py"))

            def run():
                t0 = time.time()
                proc = subprocess.Popen([py, "-m", "unittest", "discover", "-s", "tests"], cwd=self.root,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
                                        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                try:
                    out, _ = proc.communicate(timeout=1500)
                except subprocess.TimeoutExpired:
                    sbx.kill_tree(proc)
                    return {"ts": time.time(), "ok": False, "ran": 0, "failing": [], "modules": mods,
                            "tail": "timed out after 1500 s", "seconds": round(time.time() - t0)}
                text = out.decode("utf-8", "replace")
                p = sbx.parse_unittest(text)
                tail = " | ".join(l for l in text.strip().splitlines()[-4:] if l.strip())
                return {"ts": time.time(), "ok": proc.returncode == 0, "ran": p["ran"], "failing": p["failing"],
                        "modules": mods, "tail": scrub(tail, 400), "seconds": round(time.time() - t0)}
            res = await asyncio.to_thread(run)
            prev = self.tests.get("last")
            if prev and not prev.get("skipped"):
                self.tests["prev"] = {k: prev.get(k) for k in ("ts", "ok", "ran", "failing")}
                was, now_f = set(prev.get("failing") or []), set(res["failing"])
                res["flaky"] = sorted((was ^ now_f))[:5] if (was ^ now_f) and not res.get("changed_code") else []
            self.tests["last"] = res
            self.signals.add("tests:failed", len(res["failing"]))
            self.signals.add("tests:ran", res["ran"])
            self._note(None, "tests", note=f"{res['ran']} tests, {len(res['failing'])} failing")
            self.analyze()
            self.save()
            return {"ok": True, **res}
        finally:
            self.tests["running"] = False
            await self._broadcast("tests", running=False)

    # ═══ research missions ═══════════════════════════════════════
    async def run_research(self) -> dict:
        self.research = {**(self.research or {}), "running": True}
        errors, deps, watch = [], [], []
        import httpx
        try:
            names = _requirement_names(self.root / "requirements.txt")
            from importlib import metadata
            installed = {}
            for n in names:
                try:
                    installed[n] = metadata.version(n)
                except Exception:
                    pass
            async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                         headers={"User-Agent": "BUCS-Evolution/1 (+local research)"}) as c:
                async def one(n):
                    try:
                        r = await c.get(f"https://pypi.org/pypi/{n}/json")
                        r.raise_for_status()
                        info = r.json().get("info") or {}
                        urls = info.get("project_urls") or {}
                        notes = next((v for k, v in urls.items() if re.search(r"change|release|history|news", k, re.I)
                                      and str(v).startswith("https://")), None)
                        return {"name": n, "installed": installed.get(n), "latest": info.get("version"),
                                "notes_url": notes, "ts": time.time()}
                    except Exception as e:
                        errors.append(f"PyPI {n}: {type(e).__name__}")
                        return None
                deps = [d for d in await asyncio.gather(*(one(n) for n in installed)) if d]
                try:
                    import livefacts
                    for q in ("new AI model release LLM", "Ollama new model", "Anthropic Claude release"):
                        try:
                            watch += [scrub(h.lstrip("- "), 200) for h in await livefacts._news(c, q)][:4]
                        except Exception as e:
                            errors.append(f"news '{q}': {type(e).__name__}")
                except Exception as e:
                    errors.append(f"news: {type(e).__name__}")
        except Exception as e:
            errors.append(f"research: {type(e).__name__}")
        outdated = [d for d in deps if d.get("installed") and d.get("latest") and _vless(d["installed"], d["latest"])]
        suggestions = [{"kind": "dependency", "title": f"{d['name']} {d['installed']} → {d['latest']}",
                        "source": "PyPI", "url": d.get("notes_url")} for d in outdated]
        suggestions += [{"kind": "capability-watch", "title": h, "source": "Google News (7 days)", "url": None}
                        for h in dict.fromkeys(watch)][:10]
        self.research = {"ts": time.time(), "running": False, "deps": deps, "outdated": outdated,
                         "suggestions": suggestions, "errors": errors[:10]}
        self._note(None, "research", note=f"{len(deps)} deps checked, {len(outdated)} outdated, {len(watch)} headlines")
        self.analyze()
        self.save()
        await self._audit("evolution.research", f"{len(outdated)} outdated deps, {len(suggestions)} suggestions")
        return {"ok": True, **self.research}

    # ═══ LLM analyst (grounded: only measured evidence) ══════════
    async def run_analyst(self, auto: bool = False) -> dict:
        brain = self.state.get("brain")
        if brain is None:
            return {"ok": False, "error": "no AI connected"}
        open_items = [i for i in self.backlog.ranked() if i["stage"] not in core.CLOSED][:10]
        lines = "\n".join(f"- id {i['id']} [{i['kind']}] {i['title']} | impact {i['impact']}/5 risk {i['risk']}/5 "
                          f"effort {i['effort']}/5 conf {i['confidence']} | evidence: {' ; '.join(i['evidence'][:3])}"
                          for i in open_items) or "- no open items"
        lo = self.last_observe.get("sources") or {}
        sources = "; ".join(f"{k}: {v}" for k, v in lo.items())
        prompt = ("You are the evolution analyst of BUCS, a local FastAPI command dashboard. These are REAL, "
                  "measured observations and the improvement backlog derived from them:\n"
                  f"Signal sources: {sources}\nBacklog:\n{lines}\n\n"
                  "Reply in plain text. First, one line per backlog item in the form 'ITEM <id>: <do-now|later|ignore> "
                  "— <why, max 20 words>'. Then one line 'OVERALL: <the single most valuable next step>'. "
                  "Use ONLY the data above; never invent metrics, files or versions.")
        try:
            text = await asyncio.wait_for(brain.think(prompt, agent="jarvis", timeout=60), 75)
        except Exception as e:
            text = f"[analyst unavailable: {type(e).__name__}]"
        text = scrub(text or "", 1500)
        notes = {}
        for m in re.finditer(r"ITEM\s+([0-9a-f]{8})\s*:\s*(.+)", text):
            if m.group(1) in self.backlog.items:
                notes[m.group(1)] = m.group(2).strip()[:200]
                self.backlog.items[m.group(1)]["analyst"] = notes[m.group(1)]
        self.analyst = {"ts": time.time(), "text": text, "auto": auto, "items": len(open_items),
                        "engine": getattr(brain, "provider_name", None) or getattr(brain, "mode", "")}
        self.save()
        return {"ok": not text.startswith("["), **self.analyst}

    # ═══ PROPOSE → SANDBOX ═══════════════════════════════════════
    def _dev_busy(self) -> str | None:
        dev = self.state.get("dev")
        if dev is None or not hasattr(dev, "diagnose"):
            return "engineering agent not available"
        if dev.lock.locked():
            return "an engineering job is already running"
        if (dev.job or {}).get("stage") in ("applied", "applying", "testing", "diagnosing", "sandboxing"):
            return "the ENGINEERING panel has an unfinished change — keep, undo or ship it first"
        return None

    async def _maybe_auto_propose(self) -> None:
        if not self.owner or self.settings["autonomy"] != "propose":
            return
        if time.time() - self.auto_last < self.settings["auto_propose_every_h"] * 3600:
            return
        if any(i["stage"] in core.ACTIVE for i in self.backlog.items.values()):
            return                                       # one item through the loop at a time
        if (self.state.get("rbac") or {}).get("role") != "commander" or self._dev_busy():
            return
        top = next((i for i in self.backlog.ranked() if i["stage"] == "identified" and i.get("code_level")
                    and i.get("priority", 0) >= self.settings["auto_propose_min_priority"]
                    and i.get("confidence", 0) >= 0.6), None)
        if top:
            self.auto_last = time.time()
            await self.propose(top["id"], by="evolution (auto)", auto=True)

    async def propose(self, iid: str, by: str = "commander", auto: bool = False) -> dict:
        it = self.backlog.items.get(iid)
        if it is None:
            return {"ok": False, "error": "unknown item"}
        if not self.owner:
            return {"ok": False, "error": "customer install: observation and analysis only — no code changes"}
        if not it.get("code_level") or not it.get("task"):
            return {"ok": False, "error": "this item has no code-level task — follow its recommendation"}
        busy = self._dev_busy()
        if busy:
            return {"ok": False, "error": busy}
        try:
            core.transition(it, "proposing", "handed to the engineering agent (read-only diagnosis)", by)
        except core.TransitionError as e:
            return {"ok": False, "error": str(e)}
        it.pop("sandbox", None)
        it.pop("approval", None)
        self._note(iid, "propose", by, "auto" if auto else "")
        await self._audit("evolution.propose", f"{iid} {it['title'][:120]}" + (" (auto)" if auto else ""))
        self.save()
        self._spawn(self._run_proposal(it))
        await self._broadcast("item", id=iid, stage=it["stage"])
        return {"ok": True, "stage": it["stage"]}

    async def _run_proposal(self, it: dict) -> None:
        dev = self.state.get("dev")
        try:
            r = await dev.diagnose(f"[AUTONOMOUS EVOLUTION · item {it['id']}] {it['task']}\nEvidence: "
                                   + " ; ".join(it.get("evidence", [])[:4]))
            job = dev.job or {}
            if not r.get("ok") or job.get("id") != r.get("id"):
                core.transition(it, "failed", r.get("error") or r.get("summary") or "diagnosis failed")
                it["error"] = scrub(r.get("error") or r.get("summary") or "diagnosis failed", 300)
            else:
                it["plan"] = scrub(job.get("plan", ""), 4000)
                it["summary"] = scrub(r.get("summary", ""), 300)
                it["dev_job"] = r["id"]
                if r.get("fixable"):
                    core.transition(it, "proposed", "engineering proposed a fix")
                else:
                    core.transition(it, "diagnosed", "no code change proposed")
            self.save()
            await self._broadcast("item", id=it["id"], stage=it["stage"])
            if it["stage"] == "proposed":
                await self._run_sandbox(it)
        except Exception as e:
            if it["stage"] == "proposing":
                core.transition(it, "failed", f"{type(e).__name__}")
            self.save()

    async def sandbox(self, iid: str, by: str = "commander") -> dict:
        it = self.backlog.items.get(iid)
        if it is None:
            return {"ok": False, "error": "unknown item"}
        if not self.owner:
            return {"ok": False, "error": "customer install: observation and analysis only"}
        if it["stage"] not in ("proposed", "sandbox_failed"):
            return {"ok": False, "error": f"item is {it['stage']} — it needs an engineering proposal first"}
        dev = self.state.get("dev")
        j = (dev.job if dev else None) or {}
        if j.get("id") != it.get("dev_job") or j.get("stage") != "proposed":
            return {"ok": False, "error": "the engineering proposal for this item is gone — PROPOSE again"}
        self._spawn(self._run_sandbox(it, by))
        return {"ok": True, "stage": "sandboxing"}

    async def _run_sandbox(self, it: dict, by: str = "system") -> None:
        dev = self.state.get("dev")
        try:
            core.transition(it, "sandboxing", "testing the change in an isolated copy", by)
            self.save()
            await self._broadcast("item", id=it["id"], stage=it["stage"])
            r = await dev.sandbox()
            sb = (r or {}).get("sandbox") or {"ok": False, "reason": (r or {}).get("error", "sandbox unavailable")}
            it["sandbox"] = {k: sb.get(k) for k in ("id", "ts", "ok", "reason", "changed", "ops", "added", "removed",
                                                     "tests", "boot", "seconds")}
            it["sandbox"]["diff"] = scrub_diff(sb.get("diff") or "")
            it["sandbox"]["cli"] = scrub(sb.get("cli") or "", 600)
            if sb.get("ok") and r.get("patch"):
                self.patch_dir.mkdir(parents=True, exist_ok=True)
                from atomicio import atomic_write_text
                atomic_write_text(self._patch_path(it["id"]), json.dumps(r["patch"]))
                core.transition(it, "awaiting_approval", "sandbox passed — waiting for the commander")
                await self._log(f"'{it['title'][:80]}' passed the sandbox — approve or reject it in EVOLUTION", "warn")
                await self._audit("evolution.sandbox", f"{it['id']} passed · {len(sb.get('changed') or [])} files")
            else:
                core.transition(it, "sandbox_failed", sb.get("reason") or "sandbox failed")
                await self._audit("evolution.sandbox", f"{it['id']} FAILED · {sb.get('reason', '')[:100]}")
        except Exception as e:
            if it["stage"] == "sandboxing":
                core.transition(it, "failed", f"sandbox error {type(e).__name__}")
        self.save()
        await self._broadcast("item", id=it["id"], stage=it["stage"])

    # ═══ APPROVAL → DEPLOY ═══════════════════════════════════════
    def _metric_value(self, metric: dict | None, since: float, until: float):
        if not metric:
            return None
        xs = self.signals.values(metric["key"], since, until)
        if metric.get("agg") == "sum":
            return sum(xs) / max((until - since) / 3600, 1 / 60) if xs else None   # per hour
        return core.aggregate(xs, metric.get("agg", "mean"))

    def _errors_rate(self, since: float, until: float):
        xs = self.signals.values("errors:total", since, until)
        return sum(xs) / max((until - since) / 3600, 1 / 60) if xs else None

    async def approve(self, iid: str, by: str) -> dict:
        it = self.backlog.items.get(iid)
        if it is None:
            return {"ok": False, "error": "unknown item"}
        if not self.owner:
            return {"ok": False, "error": "customer install: no code changes"}
        if it["stage"] != "awaiting_approval":
            return {"ok": False, "error": f"item is {it['stage']}, not awaiting approval"}
        try:
            patch = json.loads(self._patch_path(iid).read_text(encoding="utf-8"))
        except Exception:
            return {"ok": False, "error": "the tested patch is missing — run the sandbox again"}
        if patch.get("id") != (it.get("sandbox") or {}).get("id"):
            return {"ok": False, "error": "patch does not match the tested sandbox result"}
        it["approval"] = {"decision": "approved", "by": by, "ts": time.time(), "sandbox_id": it["sandbox"]["id"]}
        try:
            core.transition(it, "deploying", "approved by " + by, by)
        except core.TransitionError as e:
            it.pop("approval", None)
            return {"ok": False, "error": str(e)}
        self._note(iid, "approve", by)
        await self._audit("evolution.approve", f"{iid} approved — deploying {len(patch.get('files') or {})} files")
        now = time.time()
        win = self.settings["eval_window_min"] * 60
        baseline = self._metric_value(it.get("metric"), now - 2 * win, now)
        err_base = self._errors_rate(now - 2 * win, now)
        r = await self.state["dev"].deploy_patch(patch, approved_by=by)
        if not r.get("ok"):
            core.transition(it, "failed", "deploy refused: " + str(r.get("error")))
            it["error"] = scrub(r.get("error"), 300)
            self.save()
            await self._audit("evolution.deploy", f"{iid} refused: {r.get('error', '')[:120]}")
            return {"ok": False, "error": r.get("error")}
        changed = r.get("changed") or []
        static_only = all(f.startswith("static/") for f in changed)
        it["deploy"] = {"ts": time.time(), "id": r.get("id"), "changed": changed, "tests": r.get("tests"),
                        "backup_dir": r.get("backup_dir"), "hashes": r.get("hashes"), "static_only": static_only}
        if isinstance((r.get("tests") or {}).get("ok"), bool):
            self.signals.add("tests:failed", 0 if r["tests"]["ok"] else 1)
        it["eval"] = {"metric": it.get("metric"), "baseline": baseline, "errors_baseline": err_base, "window_s": win,
                      "live_since": it["deploy"]["ts"] if static_only else None,
                      "status": "measuring" if static_only else "awaiting restart (Python changes load on the next start)"}
        core.transition(it, "evaluating", "deployed · evaluation started")
        try:
            self._patch_path(iid).unlink()
        except OSError:
            pass
        if not (r.get("tests") or {}).get("ok", True):
            it["eval"].update(verdict="regressed", status="unit tests fail after deploy")
            core.transition(it, "regressed", "unit tests fail in the real repo after deploy")
            await self._log(f"'{it['title'][:60]}' deployed but tests FAIL — REVERT offered", "error")
        self._note(iid, "deploy", by, ", ".join(changed[:5]))
        await self._audit("evolution.deploy", f"{iid} deployed {len(changed)} files · tests "
                                              f"{'pass' if (r.get('tests') or {}).get('ok') else 'FAIL'}")
        self.save()
        await self._broadcast("item", id=iid, stage=it["stage"])
        return {"ok": True, "stage": it["stage"], "changed": changed, "tests": r.get("tests")}

    async def reject(self, iid: str, by: str, reason: str) -> dict:
        it = self.backlog.items.get(iid)
        if it is None:
            return {"ok": False, "error": "unknown item"}
        reason = scrub(reason or "", 300)
        if not reason:
            return {"ok": False, "error": "give a reason — it is kept with the decision"}
        try:
            core.transition(it, "rejected", reason, by)
        except core.TransitionError as e:
            return {"ok": False, "error": str(e)}
        it["approval"] = {"decision": "rejected", "by": by, "ts": time.time(), "reason": reason,
                          "sandbox_id": (it.get("sandbox") or {}).get("id")}
        try:
            self._patch_path(iid).unlink()
        except OSError:
            pass
        self._note(iid, "reject", by, reason)
        await self._audit("evolution.reject", f"{iid}: {reason[:150]}")
        self.save()
        return {"ok": True, "stage": it["stage"]}

    async def dismiss(self, iid: str, by: str, reason: str = "", days: float = 7) -> dict:
        it = self.backlog.items.get(iid)
        if it is None:
            return {"ok": False, "error": "unknown item"}
        try:
            core.transition(it, "dismissed", reason or "dismissed", by)
        except core.TransitionError as e:
            return {"ok": False, "error": str(e)}
        it["snooze_until"] = time.time() + max(0.0, min(90.0, days)) * 86400
        self._note(iid, "dismiss", by, reason)
        await self._audit("evolution.dismiss", f"{iid} for {days:g} d {scrub(reason, 100)}")
        self.save()
        return {"ok": True, "stage": it["stage"]}

    async def keep(self, iid: str, by: str) -> dict:
        it = self.backlog.items.get(iid)
        if it is None or it["stage"] not in ("evaluating", "regressed"):
            return {"ok": False, "error": "nothing under evaluation to keep"}
        core.transition(it, "kept", "kept by " + by, by)
        self._note(iid, "keep", by)
        await self._audit("evolution.keep", iid)
        self.save()
        return {"ok": True, "stage": it["stage"]}

    async def revert(self, iid: str, by: str, auto: bool = False, reason: str = "") -> dict:
        it = self.backlog.items.get(iid)
        if it is None:
            return {"ok": False, "error": "unknown item"}
        if not self.owner:
            return {"ok": False, "error": "customer install: no code changes"}
        if it["stage"] not in ("evaluating", "regressed", "kept") or not it.get("deploy"):
            return {"ok": False, "error": f"item is {it['stage']} — nothing deployed to revert"}
        core.transition(it, "reverting", reason or ("auto: catastrophic health failure" if auto else "revert by " + by), by)
        r = await self.state["dev"].revert_deploy(it["deploy"], reason=reason, auto=auto)
        if r.get("ok"):
            it["revert"] = {"ts": time.time(), "by": by, "auto": auto, "reverted": r.get("reverted"),
                            "skipped": r.get("skipped"), "reason": scrub(reason, 200)}
            core.transition(it, "reverted", f"{len(r.get('reverted') or [])} files restored"
                            + (f", {len(r['skipped'])} skipped (changed since)" if r.get("skipped") else ""))
        else:
            core.transition(it, "failed", "revert failed: " + str(r.get("error")))
        self._note(iid, "revert", by, ("AUTO " if auto else "") + reason)
        await self._audit("evolution.revert", f"{iid} {'AUTO ' if auto else ''}{r.get('error') or 'ok'} {reason[:80]}")
        self.save()
        await self._broadcast("item", id=iid, stage=it["stage"])
        return {"ok": bool(r.get("ok")), "stage": it["stage"], **{k: r.get(k) for k in ("reverted", "skipped", "error")}}

    async def ship(self, iid: str, by: str) -> dict:
        it = self.backlog.items.get(iid)
        dev = self.state.get("dev")
        if it is None or not it.get("deploy"):
            return {"ok": False, "error": "nothing deployed"}
        if not dev or not dev.job or dev.job.get("id") != it["deploy"].get("id") or dev.job.get("stage") != "applied":
            return {"ok": False, "error": "this change is no longer the open engineering job — ship it from git by hand"}
        r = await dev.ship()
        if r.get("ok"):
            it["deploy"]["shipped"] = {"ts": time.time(), "commit": r.get("commit"), "pushed": r.get("pushed")}
            self._note(iid, "ship", by, str(r.get("commit")))
            self.save()
        return r

    # ═══ EVALUATE ════════════════════════════════════════════════
    async def evaluate(self) -> None:
        now = time.time()
        sh = self.state.get("selfheal")
        for it in list(self.backlog.items.values()):
            if it["stage"] != "evaluating":
                continue
            ev, dep = it.setdefault("eval", {}), it.get("deploy") or {}
            if ev.get("live_since") is None:
                if PROCESS_START > dep.get("ts", now):
                    ev["live_since"] = PROCESS_START
                    ev["status"] = "measuring"
                else:
                    continue
            win = ev.get("window_s") or self.settings["eval_window_min"] * 60
            live = ev["live_since"]
            cat = sh.catastrophic() if sh is not None and hasattr(sh, "catastrophic") else []
            if cat and now - live < 2 * win:
                ev.update(verdict="catastrophic", status="core health checks failing: " + ", ".join(cat))
                await self._log(f"core health failure after deploying '{it['title'][:60]}' — reverting automatically", "error")
                await self.revert(it["id"], by="evolution (auto)", auto=True, reason="self-heal catastrophic: " + ", ".join(cat))
                if hasattr(sh, "restart_guarded"):
                    await sh.restart_guarded("reverted an evolution change after a catastrophic health failure")
                continue
            if now < live + win:
                ev["status"] = f"measuring · {int((live + win - now) / 60) + 1} min left"
                continue
            after = self._metric_value(it.get("metric"), live + 30, now)
            m = it.get("metric") or {}
            v = core.verdict(ev.get("baseline"), after, m.get("dir", "down"), min_abs=1.0 if m.get("agg") == "sum" else 5.0
                             if str(m.get("key", "")).startswith(("endpoint:", "engine:", "selfheal:")) else 0.5)
            err_after = self._errors_rate(live + 30, now)
            guard = core.verdict(ev.get("errors_baseline"), err_after, "down", tolerance=1.0, min_abs=3.0)
            ev.update(after=after, verdict=v, errors_after=err_after, guard=guard, evaluated=now)
            if v == "regressed" or guard == "regressed":
                ev["status"] = "regression detected — revert offered"
                core.transition(it, "regressed", f"metric {ev.get('baseline')} → {after}; errors guard {guard}")
                self._note(it["id"], "regression", note=f"{m.get('key')} {ev.get('baseline')} → {after}")
                await self._audit("evolution.regression", f"{it['id']} {m.get('key')}")
                await self._log(f"evaluation: '{it['title'][:60]}' made things worse — REVERT offered", "error")
            elif v == "insufficient-data" and now < live + 3 * win:
                ev["status"] = "waiting for enough data"
            else:
                ev["status"] = {"improved": "improved ✓", "neutral": "no measurable change",
                                "insufficient-data": "kept (not enough data to judge)"}.get(v, v)
                core.transition(it, "kept", "evaluation: " + v)
                self._note(it["id"], "evaluated", note=v)

    # ═══ feedback ════════════════════════════════════════════════
    async def add_feedback(self, vote: str, note: str, area: str, by: str) -> dict:
        now = time.time()
        self._fb_times = [t for t in self._fb_times if t > now - 3600]
        if len(self._fb_times) >= FEEDBACK_PER_HOUR:
            return {"ok": False, "error": "feedback rate limit reached — try again later"}
        if vote not in ("up", "down"):
            return {"ok": False, "error": "vote must be up or down"}
        area = area if area in FEEDBACK_AREAS else "other"
        self._fb_times.append(now)
        entry = {"ts": now, "vote": vote, "note": scrub(note or "", 300), "area": area, "by": scrub(by, 40)}
        self.feedback.append(entry)
        del self.feedback[:-300]
        self.signals.add("feedback:" + area, 1 if vote == "up" else -1, now)
        await self._audit("evolution.feedback", f"{vote} · {area}")
        self.analyze()
        self.save()
        return {"ok": True, "entry": entry}

    # ═══ views ═══════════════════════════════════════════════════
    def loop_counts(self) -> list[dict]:
        now = time.time()
        items = list(self.backlog.items.values())

        def n(stages):
            return sum(1 for i in items if i["stage"] in stages)
        obs = sum(1 for s in self.signals.series.values() for t, _ in s if t >= now - 3600)
        deployed7 = sum(1 for i in items if (i.get("deploy") or {}).get("ts", 0) > now - 7 * 86400)
        counts = {"observe": obs, "analyze": self.last_analysis.get("candidates", 0),
                  "identify": n({"identified"}), "propose": n({"proposing", "proposed", "diagnosed"}),
                  "test": n({"sandboxing", "sandbox_failed"}), "approval": n({"awaiting_approval"}),
                  "deploy": n({"deploying"}) or deployed7, "evaluate": n({"evaluating", "regressed", "reverting"})}
        busy = {"observe": self.heartbeat["running"], "analyze": self.heartbeat["running"],
                "propose": n({"proposing"}) > 0, "test": n({"sandboxing"}) > 0, "approval": counts["approval"] > 0,
                "deploy": n({"deploying"}) > 0, "evaluate": n({"evaluating", "reverting"}) > 0,
                "identify": False}
        notes = {"observe": "samples · last hour", "analyze": "candidates · last pass", "identify": "open in backlog",
                 "propose": "with engineering", "test": "isolated copy", "approval": "awaiting you",
                 "deploy": "deploying" if n({"deploying"}) else "deployed · 7 days", "evaluate": "measuring"}
        return [{"stage": s, "label": LOOP_LABEL[s], "count": counts[s], "busy": bool(busy[s]), "note": notes[s]}
                for s in core.LOOP]

    def _item_view(self, i: dict, full: bool = False) -> dict:
        keys = ("id", "key", "kind", "title", "stage", "impact", "risk", "effort", "confidence", "priority",
                "code_level", "recommendation", "source", "created", "updated", "last_seen", "seen", "trend",
                "summary", "error", "analyst", "approval", "eval", "revert", "snooze_until")
        v = {k: i.get(k) for k in keys if i.get(k) is not None}
        v["loop"] = core.STAGE_LOOP.get(i["stage"])
        v["evidence"] = i.get("evidence", [])
        if i.get("deploy"):
            v["deploy"] = {k: i["deploy"].get(k) for k in ("ts", "changed", "tests", "static_only", "shipped")}
        if i.get("sandbox"):
            sb = dict(i["sandbox"])
            if not full:
                sb.pop("diff", None)
                sb.pop("cli", None)
            v["sandbox"] = sb
        if full:
            v.update(task=i.get("task"), plan=i.get("plan"), timeline=i.get("timeline", []), metric=i.get("metric"))
        return v

    def snapshot(self) -> dict:
        ranked = self.backlog.ranked()
        open_ = [i for i in ranked if i["stage"] not in core.CLOSED]
        closed = sorted((i for i in ranked if i["stage"] in core.CLOSED), key=lambda i: -i["updated"])[:20]
        current = next((i for i in sorted(self.backlog.items.values(), key=lambda i: -i["updated"])
                        if i["stage"] in core.ACTIVE), None)
        errs = sorted(self.errors.snapshot(), key=lambda e: -e["last"])[:12]
        dev = self.state.get("dev")
        return {
            "owner": self.owner, "active": self.active_install,
            "mode": "full loop (approval-gated)" if self.owner else "observe + analyse only (customer install)",
            "settings": self.settings, "loop": self.loop_counts(),
            "current": self._item_view(current) if current else None,
            "heartbeat": {**self.heartbeat, "interval_min": self.settings["heartbeat_min"]},
            "observe": self.last_observe, "analysis": self.last_analysis,
            "backlog": [self._item_view(i) for i in open_[:60]],
            "closed": [self._item_view(i) for i in closed],
            "approvals": [self._item_view(i, full=True) for i in open_ if i["stage"] == "awaiting_approval"],
            "evaluations": [self._item_view(i) for i in sorted(self.backlog.items.values(), key=lambda i: -i["updated"])
                            if i.get("eval")][:10],
            "sandbox_runs": [{"id": i["id"], "title": i["title"], **{k: i["sandbox"].get(k) for k in
                              ("ok", "reason", "changed", "added", "removed", "tests", "boot", "seconds", "ts")}}
                             for i in sorted(self.backlog.items.values(), key=lambda i: -(i.get("sandbox") or {}).get("ts", 0))
                             if i.get("sandbox")][:8],
            "tests": {k: self.tests.get(k) for k in ("last", "prev", "running", "next")},
            "research": {k: v for k, v in (self.research or {}).items() if k != "deps"},
            "analyst": self.analyst,
            "feedback": {"recent": self.feedback[-8:][::-1], "areas": FEEDBACK_AREAS,
                         "up_7d": sum(1 for f in self.feedback if f["vote"] == "up" and f["ts"] > time.time() - 7 * 86400),
                         "down_7d": sum(1 for f in self.feedback if f["vote"] == "down" and f["ts"] > time.time() - 7 * 86400)},
            "errors": [{k: e.get(k) for k in ("sig", "type", "where", "count", "last", "msg")} for e in errs],
            "events": self.signals.events[:12],
            "history": self.history[-25:][::-1],
            "engineering": {"busy": bool(dev and dev.lock.locked()), "stage": ((dev.job or {}).get("stage") if dev else None)},
        }

    def signal_view(self, prefix: str = "", limit: int = 120) -> dict:
        keys = sorted(self.signals.keys(prefix))[:200]
        return {"keys": keys, "latest": {k: self.signals.latest.get(k) for k in keys},
                "series": {k: self.signals.series[k][-limit:] for k in keys[:40]}}


# ═══ helpers ═════════════════════════════════════════════════════════════════
def scrub_diff(diff: str, limit: int = 20000) -> str:
    """Diffs are shown to the commander: scrub each line (a fix could print a secret pattern)."""
    out = []
    for line in diff.splitlines()[:1500]:
        out.append(scrub(line, 400, keep_space=True))
    return "\n".join(out)[:limit]


def _requirement_names(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for l in lines:
        l = l.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", l)
        if m:
            out.append(m.group(1).lower())
    return out


def _vless(a: str, b: str) -> bool:
    try:
        from secagent import vless
        return vless(a, b)
    except Exception:
        def t(v):
            return tuple(int(x) for x in re.findall(r"\d+", v)[:4])
        return t(a) < t(b)


# ═══ wiring (main.py FEATURE_MODULES) ════════════════════════════════════════
def _engine(ctx) -> Evolution:
    st = ctx["state"]
    e = st.get("evolution")
    if not isinstance(e, Evolution):
        e = Evolution(st, ctx)
        st["evolution"] = e
    return e


class FeedbackReq(BaseModel):
    vote: str
    note: str = ""
    area: str = "general"


class ReasonReq(BaseModel):
    reason: str = ""
    days: float = 7


class SettingsReq(BaseModel):
    autonomy: str | None = None
    heartbeat_min: int | None = None
    eval_window_min: int | None = None
    tests_every_h: int | None = None


_LOCKED = {"ok": False, "error": "OBSERVER role — evolution actions are locked. Switch to COMMANDER."}
_HOST = {"ok": False, "error": "code-changing evolution actions can only be taken on the host machine"}


def build_router(ctx) -> APIRouter:
    r = APIRouter()
    evo = _engine(ctx)
    core.install_error_recorder(evo.errors)

    def by() -> str:
        return (ctx["state"].get("rbac") or {}).get("operator", "commander")

    @r.get("/api/evolution")
    async def evolution_get():
        return evo.snapshot()

    @r.get("/api/evolution/capabilities")
    async def evolution_capabilities():
        rm = ctx["state"].get("roadmap")
        if rm is None or not hasattr(rm, "capabilities"):
            from roadmap import Roadmap
            rm = Roadmap(ctx["state"])
        caps = await asyncio.to_thread(rm.capabilities)
        return {"capabilities": caps, "probed_at": time.time(),
                "tests_last_run": (evo.tests.get("last") or {}).get("ts"),
                "summary": {"total": len(caps), "done": sum(1 for c in caps if c["done"]),
                            "loaded": sum(1 for c in caps if c["loaded"]),
                            "failing_tests": sum(1 for c in caps if c["tests"] == "fail")}}

    @r.get("/api/evolution/signals")
    async def evolution_signals(prefix: str = ""):
        return evo.signal_view(prefix[:40])

    @r.get("/api/evolution/items/{iid}")
    async def evolution_item(iid: str):
        it = evo.backlog.items.get(iid[:16])
        return evo._item_view(it, full=True) if it else {"ok": False, "error": "unknown item"}

    @r.post("/api/evolution/heartbeat")
    async def evolution_heartbeat():
        if not ctx["commander"]():
            return _LOCKED
        wait = HEARTBEAT_COOLDOWN - (time.time() - evo._last_manual["beat"])
        if wait > 0:
            return {"ok": False, "error": f"rate limited — next manual heartbeat in {int(wait) + 1} s"}
        evo._last_manual["beat"] = time.time()
        await ctx["audit"]("evolution.heartbeat", "manual")
        return await evo.beat("manual")

    @r.post("/api/evolution/analyst")
    async def evolution_analyst():
        if not ctx["commander"]():
            return _LOCKED
        if time.time() - evo._last_manual["analyst"] < ANALYST_COOLDOWN:
            return {"ok": False, "error": "the analyst ran moments ago — try again in a couple of minutes"}
        evo._last_manual["analyst"] = time.time()
        return await evo.run_analyst()

    @r.post("/api/evolution/research")
    async def evolution_research():
        if not ctx["commander"]():
            return _LOCKED
        if time.time() - evo._last_manual["research"] < RESEARCH_COOLDOWN:
            return {"ok": False, "error": "research ran recently — try again in 10 minutes"}
        evo._last_manual["research"] = time.time()
        evo._spawn(evo.run_research())
        return {"ok": True, "started": True}

    @r.post("/api/evolution/tests")
    async def evolution_tests(request: Request):
        if not ctx["commander"]():
            return _LOCKED
        if not evo.owner:
            return {"ok": False, "error": "tests run only on the owner's machine"}
        if evo.tests["running"]:
            return {"ok": False, "error": "a test run is already in progress"}
        evo._spawn(evo.run_tests())
        return {"ok": True, "started": True}

    @r.post("/api/evolution/feedback")
    async def evolution_feedback(req: FeedbackReq):
        return await evo.add_feedback(req.vote, req.note[:600], req.area[:40], by())

    @r.post("/api/evolution/settings")
    async def evolution_settings(req: SettingsReq):
        if not ctx["commander"]():
            return _LOCKED
        s = evo.settings
        if req.autonomy is not None:
            if req.autonomy not in ("observe", "propose"):
                return {"ok": False, "error": "autonomy is 'observe' or 'propose' (deploy always needs approval)"}
            if req.autonomy == "propose" and not evo.owner:
                return {"ok": False, "error": "customer install: observation and analysis only"}
            s["autonomy"] = req.autonomy
        for k, lo, hi in (("heartbeat_min", 2, 240), ("eval_window_min", 5, 1440), ("tests_every_h", 1, 168)):
            v = getattr(req, k)
            if v is not None:
                s[k] = max(lo, min(hi, int(v)))
        evo.save()
        await ctx["audit"]("evolution.settings", json.dumps({k: s[k] for k in ("autonomy", "heartbeat_min",
                                                                              "eval_window_min", "tests_every_h")}))
        return {"ok": True, "settings": s}

    async def code_action(request: Request, fn):
        if not ctx["commander"]():
            return _LOCKED
        if not ctx["host_only"](request):
            return _HOST
        return await fn()

    @r.post("/api/evolution/items/{iid}/propose")
    async def item_propose(iid: str, request: Request):
        return await code_action(request, lambda: evo.propose(iid[:16], by()))

    @r.post("/api/evolution/items/{iid}/sandbox")
    async def item_sandbox(iid: str, request: Request):
        return await code_action(request, lambda: evo.sandbox(iid[:16], by()))

    @r.post("/api/evolution/items/{iid}/approve")
    async def item_approve(iid: str, request: Request):
        return await code_action(request, lambda: evo.approve(iid[:16], by()))

    @r.post("/api/evolution/items/{iid}/revert")
    async def item_revert(iid: str, request: Request, req: ReasonReq | None = None):
        return await code_action(request, lambda: evo.revert(iid[:16], by(), reason=(req.reason if req else "")[:300]))

    @r.post("/api/evolution/items/{iid}/ship")
    async def item_ship(iid: str, request: Request):
        return await code_action(request, lambda: evo.ship(iid[:16], by()))

    @r.post("/api/evolution/items/{iid}/reject")
    async def item_reject(iid: str, req: ReasonReq):
        if not ctx["commander"]():
            return _LOCKED
        return await evo.reject(iid[:16], by(), req.reason)

    @r.post("/api/evolution/items/{iid}/dismiss")
    async def item_dismiss(iid: str, req: ReasonReq):
        if not ctx["commander"]():
            return _LOCKED
        return await evo.dismiss(iid[:16], by(), req.reason, req.days)

    @r.post("/api/evolution/items/{iid}/keep")
    async def item_keep(iid: str):
        if not ctx["commander"]():
            return _LOCKED
        return await evo.keep(iid[:16], by())

    return r


async def start(ctx) -> None:
    evo = _engine(ctx)
    core.install_error_recorder(evo.errors)
    await evo.loop()
