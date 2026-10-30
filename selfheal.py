"""Self-healing watchdog — BUCS checks itself, repairs what it safely can, asks
the connected AI to diagnose what it can't, and restarts itself as a last resort.

Every CHECK_EVERY seconds it measures real signals (never estimates):
  event loop  — scheduling lag of the asyncio loop (a blocked loop freezes everything)
  cpu / ram   — this process's own CPU share and memory
  tasks       — the runtime background tasks (telemetry, network, voice) are alive
  scheduler   — APScheduler is running and its jobs exist
  weather / news / google — time since each last succeeded
  brain       — how replies were routed recently (provider failures fall back)

Repairs are SAFE and reversible: restart a crashed background task, re-run a
stale feed, restart the scheduler. Code changes are never made automatically —
the AI diagnosis can be handed to engineering mode, which still requires the
commander's approval. A full process restart (through the launcher, which rolls
back if needed) is the last resort, limited to once per RESTART_COOLDOWN and
only when BUCS runs under the launcher (BUCS_LAUNCHER=1).
"""

import asyncio
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).parent
STATE_FILE = HERE / ".update" / "selfheal.json"
CHECK_EVERY = 30
RESTART_COOLDOWN = 30 * 60
AI_COOLDOWN = 15 * 60


class SelfHeal:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.auto = True
        self.checks: dict[str, dict] = {}
        self.log: list[dict] = []                 # newest first, real actions only
        self.ai: dict | None = None
        self._ai_ts = 0.0
        self._lag_ms = 0.0
        self._strikes: dict[str, int] = {}
        self._proc = None
        self._load()

    # ── persistence (auto flag + restart history) ─────────────────
    def _load(self) -> None:
        try:
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.auto = bool(d.get("auto", True))
            self._restarts = [t for t in d.get("restarts", []) if time.time() - t < 24 * 3600]
            self.log = d.get("log", [])[:40]
        except Exception:
            self._restarts = []

    def _save(self) -> None:
        try:
            from atomicio import atomic_write_text
            STATE_FILE.parent.mkdir(exist_ok=True)
            atomic_write_text(STATE_FILE, json.dumps({"auto": self.auto, "restarts": self._restarts,
                                                      "log": self.log[:40]}, indent=1))
        except Exception:
            pass

    async def _note(self, kind: str, msg: str, level: str = "info") -> None:
        self.log.insert(0, {"ts": time.time(), "kind": kind, "msg": msg, "level": level})
        del self.log[40:]
        self._save()
        hub = self.state.get("hub")
        if hub:
            await hub.broadcast({"type": "log", "level": level, "msg": f"[self-heal] {msg}"})
        audit = self.state.get("audit")
        if audit:
            try:
                await audit.log(f"selfheal.{kind}", msg, operator=self.state.get("rbac", {}).get("operator", "system"))
            except Exception:
                pass

    # ── measurements ──────────────────────────────────────────────
    async def _measure_lag(self) -> float:
        t = time.perf_counter()
        await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        return max(0.0, (time.perf_counter() - t - 0.05) * 1000)

    def _set(self, key: str, status: str, detail: str, value=None) -> None:
        self.checks[key] = {"status": status, "detail": detail, "value": value, "ts": time.time()}

    async def run_checks(self, repair: bool | None = None) -> dict:
        repair = self.auto if repair is None else repair
        st, now = self.state, time.time()

        lag = await self._measure_lag()
        self._lag_ms = lag
        self._set("event_loop", "ok" if lag < 250 else "warn" if lag < 2000 else "fail",
                  f"scheduling lag {lag:.0f} ms", round(lag))

        try:
            import psutil
            if self._proc is None:
                self._proc = psutil.Process()
                self._proc.cpu_percent(None)
            cpu = self._proc.cpu_percent(None)
            rss = self._proc.memory_info().rss / 1048576
            self._set("cpu", "ok" if cpu < 60 else "warn" if cpu < 90 else "fail", f"{cpu:.0f}% of one core", round(cpu))
            self._set("memory", "ok" if rss < 1200 else "warn" if rss < 2500 else "fail", f"{rss:.0f} MB", round(rss))
        except Exception as e:
            self._set("cpu", "unknown", f"unavailable: {e}")

        # background tasks
        tasks: dict = st.get("runtime_tasks", {})
        factories: dict = st.get("runtime_factories", {})
        dead = [n for n, t in tasks.items() if t.done()]
        if not st.get("runtime_started"):
            self._set("tasks", "idle", "runtime not started (locked install)")
        elif dead:
            self._set("tasks", "fail", "stopped: " + ", ".join(dead))
            if repair:
                for n in dead:
                    err = None
                    try:
                        err = tasks[n].exception()
                    except (asyncio.CancelledError, asyncio.InvalidStateError):
                        pass
                    if n in factories:
                        tasks[n] = asyncio.create_task(factories[n]())
                        await self._note("repair", f"restarted the '{n}' background task"
                                                   + (f" (it had crashed: {str(err)[:120]})" if err else ""), "warn")
                self._set("tasks", "ok", "restarted: " + ", ".join(dead))
        else:
            self._set("tasks", "ok", f"{len(tasks)} running")

        sched = st.get("scheduler")
        if sched is not None and st.get("runtime_started"):
            running = getattr(sched, "running", False)
            n = len(sched.get_jobs()) if running else 0
            self._set("scheduler", "ok" if running and n else "fail", f"{n} jobs" if running else "stopped", n)
            if not running and repair:
                try:
                    sched.start()
                    await self._note("repair", "restarted the job scheduler", "warn")
                except Exception as e:
                    await self._note("error", f"scheduler restart failed: {e}", "error")

        await self._feed("weather", st.get("weather"), 20 * 60, repair, lambda w: w.get(), "weather")
        await self._feed("news", st.get("news"), 40 * 60, repair, lambda n: n.fetch_top(), "news feed")
        gcal = st.get("gcal")
        if gcal is not None and getattr(gcal, "token_present", lambda: False)():
            age = now - (getattr(gcal, "last_sync", 0) or 0)
            err = getattr(gcal, "last_error", None)
            self._set("google", "ok" if age < 20 * 60 and not err else "warn",
                      (f"last sync {age / 60:.0f} min ago" if getattr(gcal, "last_sync", 0) else "not synced yet")
                      + (f" · {err}" if err else ""), round(age))
        else:
            self._set("google", "idle", "not connected")

        brain = st.get("brain")
        if brain is not None:
            rc = getattr(brain, "route_counts", {}) or {}
            total = sum(rc.values())
            fallback = rc.get("template", 0) + rc.get("offline", 0)
            pct = round(fallback * 100 / total) if total else 0
            self._set("brain", "ok" if pct < 30 else "warn",
                      f"{total} replies · {pct}% fell back to rule templates" if total else "no replies yet", pct)

        await self._escalate(repair)
        return self.snapshot()

    async def _feed(self, key, svc, max_age, repair, refresh, label) -> None:
        if svc is None:
            return
        last = getattr(svc, "last_ok", 0) or 0
        age = time.time() - last if last else None
        if age is None or age > max_age:
            if repair and self.state.get("runtime_started"):
                try:
                    await asyncio.wait_for(refresh(svc), 20)
                except Exception:
                    pass
                last2 = getattr(svc, "last_ok", 0) or 0
                if last2 > last:
                    await self._note("repair", f"{label} was stale; refreshed it successfully")
                    self._set(key, "ok", "refreshed just now", 0)
                    return
            self._set(key, "warn", "no successful update yet" if age is None else f"stale: {age / 60:.0f} min old",
                      None if age is None else round(age))
        else:
            self._set(key, "ok", f"updated {age / 60:.0f} min ago", round(age))

    # ── escalation: AI diagnosis, then restart as a last resort ──
    async def _escalate(self, repair: bool) -> None:
        bad = {k: v for k, v in self.checks.items() if v["status"] == "fail"}
        for k in list(self._strikes):
            if k not in bad:
                self._strikes.pop(k)
        for k in bad:
            self._strikes[k] = self._strikes.get(k, 0) + 1
        persistent = [k for k, n in self._strikes.items() if n >= 3]        # ~90 s of failure
        if bad and time.time() - self._ai_ts > AI_COOLDOWN:
            await self.diagnose(auto=True)
        if persistent and repair and any(k in ("event_loop", "cpu", "memory") for k in persistent):
            await self._restart("persistent failure: " + ", ".join(persistent))

    async def _restart(self, why: str) -> None:
        if os.getenv("BUCS_LAUNCHER") != "1":
            await self._note("hold", f"would restart ({why}) but BUCS isn't running under its launcher", "warn")
            return
        recent = [t for t in self._restarts if time.time() - t < RESTART_COOLDOWN]
        if recent:
            await self._note("hold", f"restart needed ({why}) but one already happened in the last 30 min, "
                                     "leaving it to you to avoid a restart loop", "error")
            self._strikes.clear()
            return
        self._restarts.append(time.time())
        await self._note("restart", f"restarting BUCS to recover ({why})", "error")
        import signal
        from updater import request_exit
        request_exit(HERE, "restart")
        asyncio.get_running_loop().call_later(1.0, signal.raise_signal, signal.SIGINT)

    async def diagnose(self, auto: bool = False) -> dict:
        """Plain-language diagnosis from the connected AI, grounded in the real check results."""
        brain = self.state.get("brain")
        if brain is None:
            return {"ok": False, "error": "no AI connected"}
        self._ai_ts = time.time()
        report = "\n".join(f"- {k}: {v['status']} ({v['detail']})" for k, v in self.checks.items())
        recent = "\n".join(f"- {time.strftime('%H:%M', time.localtime(e['ts']))} {e['msg']}" for e in self.log[:8])
        prompt = ("You are the self-diagnosis module of BUCS, a local FastAPI command dashboard. "
                  "These are REAL health-check results measured just now:\n" + report +
                  ("\nRecent self-healing actions:\n" + recent if recent else "") +
                  "\n\nIn at most 4 short sentences: say what (if anything) is wrong, the most likely cause, "
                  "and the single best next step for the owner. Do not invent data that is not listed. "
                  "If everything is healthy, say so in one sentence.")
        try:
            text = await asyncio.wait_for(brain.think(prompt, agent="jarvis", timeout=60), 75)
        except Exception as e:
            text = f"[diagnosis unavailable: {e}]"
        self.ai = {"ts": time.time(), "text": (text or "").strip()[:800], "auto": auto,
                   "engine": getattr(brain, "provider_name", None) or getattr(brain, "mode", "")}
        if auto and not text.startswith("["):
            await self._note("diagnosis", "AI diagnosis: " + self.ai["text"][:200], "warn")
        return {"ok": not text.startswith("["), **self.ai}

    def snapshot(self) -> dict:
        order = {"fail": 0, "warn": 1, "unknown": 2, "idle": 3, "ok": 4}
        worst = min((v["status"] for v in self.checks.values()), key=lambda s: order.get(s, 5), default="unknown")
        return {"auto": self.auto, "overall": worst, "checks": self.checks, "log": self.log[:20], "ai": self.ai,
                "restarts_24h": len(self._restarts), "under_launcher": os.getenv("BUCS_LAUNCHER") == "1",
                "checked_at": max((v["ts"] for v in self.checks.values()), default=None)}

    async def loop(self) -> None:
        await asyncio.sleep(40)                          # let boot settle first
        while True:
            try:
                await self.run_checks()
                hub = self.state.get("hub")
                if hub:
                    snap = self.snapshot()
                    await hub.broadcast({"type": "selfheal", "overall": snap["overall"]})
            except Exception as e:
                try:
                    await self._note("error", f"watchdog error: {e}", "error")
                except Exception:
                    pass
            await asyncio.sleep(CHECK_EVERY)
