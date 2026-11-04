"""COMPUTER USE · a supervised desktop agent for the operator's own machine.

Natural-language task → loop { capture the screen (downscaled, coordinate-mapped,
multi-monitor, DPI-correct) → ask a vision model for the next action → SAFETY GATE →
execute → verify on the next frame } until done / max steps / max time / stopped.

Safety (CLAUDE.md §6g spirit, non-negotiable):
  * starting a task, approving, replying, taking over and settings: COMMANDER role AND the
    host machine (host_only). STOP is always allowed — stopping is never dangerous.
  * the gate (computeruse_core.check_action) pauses for the operator before payments,
    credentials, deletes/moves, sends/posts/submits, installs, system settings and
    irreversible choices; it REFUSES to type secrets and hands control to the human.
  * PAUSE · RESUME · STOP (immediate: the loop is cancelled) · TAKE OVER / HAND BACK;
    fail-safe = mouse into any primary-screen corner; hotkey Ctrl+Alt+Backspace (Windows);
    a human moving the mouse pauses the agent (TAKE OVER).
  * rate limit, max steps, max duration; every action is audited; screenshots live only
    under .update/computeruse/frames and are deleted 10 min after the task ends (stale folders are swept every minute).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import computeruse_core as core
import computeruse_models as models

ROOT = Path(__file__).parent
DATA = ROOT / ".update" / "computeruse"
FRAMES = DATA / "frames"
FRAME_TTL = 600                    # seconds a finished task's screenshots are kept for review
APPROVAL_TIMEOUT = 300
QUESTION_TIMEOUT = 600
LAUNCH_SETTLE = 2.5               # after open_app / open_url: avoids double launches
SETTLE = 0.7                       # seconds to let the UI react before the verification frame
DEFAULTS = {"adapter": "auto", "monitor": 1, "max_steps": 25, "max_minutes": 10, "min_interval": 0.5,
            "per_minute": 60, "long_edge": 1280, "approve_all": False, "pause_on_mouse": True,
            "allow_apps": True, "allow_urls": True, "allowed_dirs": []}
SECRET_FILE = re.compile(r"(^\.env|\.pem$|\.key$|\.pfx$|\.p12$|id_rsa|id_ed25519|credential|token|secret|"
                         r"password|\.kdbx$|wallet|keychain|cookies)", re.I)
_LOCKED = {"ok": False, "error": "OBSERVER role — command actions are locked. Switch to COMMANDER to proceed."}
_HOST = {"ok": False, "error": "computer use can only be controlled from the host machine"}


def _now() -> float:
    return time.time()


def _protected() -> set[str]:
    try:
        import devagent
        return {p.lower() for p in devagent.PROTECTED}
    except Exception:
        return {".env", "credentials.json", "token.json", "auth.json", "providers.json"}


def sanitize_settings(raw: dict, base: dict | None = None) -> tuple[dict, list[str]]:
    """Clamp every setting; folders must exist, be absolute, not a drive root, not BUCS itself."""
    s, errs = dict(base or DEFAULTS), []
    raw = raw or {}

    def num(k, lo, hi, cast=int):
        if k in raw:
            try:
                s[k] = max(lo, min(hi, cast(raw[k])))
            except (TypeError, ValueError):
                errs.append(f"{k} must be a number")
    num("monitor", 1, 16)
    num("max_steps", 1, 100)
    num("max_minutes", 1, 60)
    num("min_interval", 0.2, 5.0, float)
    num("per_minute", 10, 120)
    num("long_edge", 960, 1568)
    for k in ("approve_all", "pause_on_mouse", "allow_apps", "allow_urls"):
        if k in raw:
            s[k] = bool(raw[k])
    if "adapter" in raw:
        a = str(raw["adapter"] or "auto")[:120]
        s["adapter"] = a if re.fullmatch(r"auto|anthropic|claude-cli|ollama:[\w.:/-]+|provider:[\w-]+", a) else "auto"
    if "allowed_dirs" in raw:
        out = []
        for d in (raw["allowed_dirs"] or [])[:10]:
            try:
                p = Path(str(d)).expanduser()
                if not p.is_absolute():
                    errs.append(f"{d}: use an absolute path"); continue
                p = p.resolve()
                if not p.is_dir():
                    errs.append(f"{d}: not an existing folder"); continue
                if p.parent == p:
                    errs.append(f"{d}: a whole drive is too broad"); continue
                if p == ROOT.resolve() or ROOT.resolve().is_relative_to(p) or p.is_relative_to(ROOT.resolve()):
                    errs.append(f"{d}: the BUCS folder holds private data and can't be shared"); continue
                if str(p) not in out:
                    out.append(str(p))
            except Exception as e:
                errs.append(f"{d}: {e}")
        s["allowed_dirs"] = out
    return s, errs


def safe_path(path: str, allowed: list[str]) -> Path:
    """Resolve `path` (symlinks too) and require it inside an approved folder; refuse secrets."""
    if not allowed:
        raise core.ActionError("no folders are approved for reading (COMPUTER USE settings)")
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(allowed[0]) / p
    p = p.resolve()
    if not any(p == Path(a) or p.is_relative_to(Path(a)) for a in allowed):
        raise core.ActionError("that path is outside the approved folders")
    if p.name.lower() in _protected() or any(SECRET_FILE.search(part) for part in p.parts[-3:]):
        raise core.ActionError("that file looks private (keys / credentials) and is never read")
    return p


def read_file(path: str, allowed: list[str]) -> str:
    p = safe_path(path, allowed)
    if not p.is_file():
        raise core.ActionError("not a file")
    with open(p, "rb") as f:
        data = f.read(64 * 1024)
    if b"\x00" in data[:4096]:
        raise core.ActionError("binary file — only text files can be read")
    text = data.decode("utf-8", errors="replace")
    return text[:6000] + ("\n…[truncated]" if len(text) > 6000 or p.stat().st_size > 64 * 1024 else "")


def list_dir(path: str, allowed: list[str]) -> str:
    p = safe_path(path, allowed)
    if not p.is_dir():
        raise core.ActionError("not a folder")
    rows = []
    for i, e in enumerate(sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))):
        if i >= 200:
            rows.append("…"); break
        try:
            rows.append(f"{e.name}/" if e.is_dir() else f"{e.name}  ({e.stat().st_size} B)")
        except OSError:
            rows.append(e.name)
    return f"{p}:\n" + ("\n".join(rows) or "(empty)")


class Engine:
    def __init__(self, ctx: dict, data_dir: Path = DATA) -> None:
        self.ctx = ctx
        self.state = ctx["state"]
        self.data = Path(data_dir)
        self.frames = self.data / "frames"
        self.settings, _ = sanitize_settings(self._load("settings.json", {}))
        self.history: list[dict] = self._load("history.json", [])[:30]
        self.task: dict | None = None
        self.ctl: core.TaskControl | None = None
        self.runner: asyncio.Task | None = None
        self.adapter = None
        self.inp = None
        self.watchdog = None
        self.candidates: list[dict] = []
        self.cand_ts = 0.0
        self.events: list[dict] = []
        self.perm: dict | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ended_at: dict[str, float] = {}
        # test seams
        self.capture_fn = None
        self.executor = None
        self.adapter_factory = None
        self.titles_fn = None

    # ── persistence ─────────────────────────────────────────────────────
    def _load(self, name: str, default):
        try:
            return json.loads((self.data / name).read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save(self, name: str, obj) -> None:
        try:
            from atomicio import atomic_write_text
            self.data.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.data / name, json.dumps(obj, indent=1))
        except Exception:
            pass

    # ── events / audit / broadcast ──────────────────────────────────────
    async def _audit(self, action: str, detail: str = "") -> None:
        try:
            r = self.ctx["audit"](action, detail[:400])
            if asyncio.iscoroutine(r):
                await r
        except Exception:
            pass

    async def _event(self, msg: str, level: str = "info", log: bool = False) -> None:
        self.events.insert(0, {"ts": _now(), "level": level, "msg": msg[:300]})
        del self.events[30:]
        hub = self.state.get("hub")
        if hub is None:
            return
        try:
            if log:
                await hub.broadcast({"type": "log", "level": level, "msg": f"[computer use] {msg[:240]}"})
            await hub.broadcast({"type": "computeruse", "status": self.status()})
        except Exception:
            pass

    def status(self) -> str:
        if self.ctl and self.task and self.task.get("status") in core.ACTIVE:
            return self.ctl.status
        return self.task["status"] if self.task else "idle"

    # ── adapters ────────────────────────────────────────────────────────
    async def refresh_candidates(self, force: bool = False) -> list[dict]:
        if force or _now() - self.cand_ts > 30 or not self.candidates:
            try:
                self.candidates = await models.discover(self.state)
            except Exception as e:
                self.candidates = [{"id": "error", "name": "discovery failed", "available": False,
                                    "reason": str(e)[:200], "kind": "", "auto": False}]
            self.cand_ts = _now()
        return self.candidates

    def permissions(self) -> dict:
        if self.perm is None or _now() - self.perm.get("ts", 0) > 60:
            try:
                import computeruse_screen as scr
                self.perm = {**scr.permissions(), "ts": _now()}
            except Exception as e:
                self.perm = {"ok": False, "screen": False, "input": False, "problems": [str(e)[:200]],
                             "howto": [], "ts": _now()}
        return self.perm

    # ── snapshot for the dashboard ──────────────────────────────────────
    def snapshot(self) -> dict:
        t = self.task
        cur = None
        if t:
            cur = {k: t.get(k) for k in ("id", "task", "adapter", "adapter_name", "status", "started", "ended",
                                         "summary", "error", "max_steps", "max_minutes", "frame", "reasoning", "source")}
            cur["step"] = t.get("n", 0)
            cur["steps"] = t["steps"][-40:]
            cur["elapsed"] = round((t.get("ended") or _now()) - t["started"], 1)
            cur["frames_kept"] = (self.frames / t["id"]).is_dir()
        pending = None
        if self.ctl and self.ctl.pending:
            pending = {k: v for k, v in self.ctl.pending.items()}
        perm = self.permissions()
        avail = [c for c in self.candidates if c.get("available")]
        return {"status": self.status(), "task": cur, "pending": pending,
                "paused_reason": self.ctl.paused_reason if self.ctl else "",
                "settings": self.settings, "history": self.history[:20], "events": self.events[:15],
                "candidates": self.candidates, "chosen": models.pick(self.candidates, self.settings["adapter"]),
                "no_model": bool(self.candidates) and not avail, "howto": models.HOWTO_NONE,
                "permissions": {k: perm.get(k) for k in ("platform", "ok", "screen", "input", "problems", "howto", "dpi")},
                "hotkey": core.EMERGENCY_HOTKEY if os.name == "nt" else "", "failsafe": "mouse into any screen corner"}

    # ── task lifecycle ──────────────────────────────────────────────────
    async def start_task(self, text: str, adapter: str | None = None, max_steps: int | None = None,
                         max_minutes: int | None = None) -> dict:
        text = (text or "").strip()
        if not text or len(text) > 1000:
            return {"ok": False, "error": "describe the task in 1–1000 characters"}
        if self.runner and not self.runner.done():
            return {"ok": False, "error": "a task is already running — stop it first"}
        perm = self.permissions() if self.capture_fn is None else {"ok": True}
        if not perm.get("ok"):
            return {"ok": False, "error": "; ".join(perm.get("problems") or ["screen/input unavailable"]),
                    "howto": perm.get("howto")}
        cands = await self.refresh_candidates(force=True)
        choice = models.pick(cands, adapter or self.settings["adapter"])
        if choice is None:
            want = adapter or self.settings["adapter"]
            return {"ok": False, "error": (f"model '{want}' is not available" if want != "auto" else "no vision model available"),
                    "howto": models.HOWTO_NONE}
        try:
            self.adapter = (self.adapter_factory or (lambda ch: models.build(ch, self.state)))(choice)
        except Exception as e:
            return {"ok": False, "error": f"could not start {choice['name']}: {e}"}
        tid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.ctl = core.TaskControl()
        self.task = {"id": tid, "task": text, "adapter": choice["id"], "adapter_name": choice["name"],
                     "status": "starting", "started": _now(), "ended": None, "n": 0, "steps": [], "summary": "",
                     "error": "", "frame": None, "reasoning": "", "notes": [],
                     "max_steps": int(max_steps or self.settings["max_steps"]),
                     "max_minutes": int(max_minutes or self.settings["max_minutes"])}
        self.task["max_steps"] = max(1, min(100, self.task["max_steps"]))
        self.task["max_minutes"] = max(1, min(60, self.task["max_minutes"]))
        self.loop = asyncio.get_running_loop()
        await self._audit("computeruse.task.start", f"{tid} · {choice['id']} · {text[:200]}")
        await self._event(f"task started with {choice['name']}: {text[:120]}", log=True)
        self.runner = asyncio.create_task(self._run())
        return {"ok": True, "id": tid, "adapter": choice}

    async def submit_task(self, instruction: str, source: str = "agentos", **kw) -> dict:
        """Programmatic entry for other BUCS modules (AI Core / agentos_tools.py). Same path as a
        dashboard task: COMMANDER role required, then the identical sensitive-action gate,
        approvals, privacy guard, limits and audit. Returns {"ok", "task_id", "status"}."""
        if not self.ctx["commander"]():
            return {"ok": False, "task_id": None, "status": "refused", "error": _LOCKED["error"]}
        src = re.sub(r"[^\w.-]", "", str(source or "agentos"))[:24] or "agentos"
        await self._audit("computeruse.submit", f"source={src} · {str(instruction)[:200]}")
        r = await self.start_task(instruction, kw.get("adapter"), kw.get("max_steps"), kw.get("max_minutes"))
        if not r.get("ok"):
            return {"ok": False, "task_id": None, "status": "refused", "error": r.get("error", "refused"),
                    "howto": r.get("howto")}
        self.task["source"] = src
        return {"ok": True, "task_id": r["id"], "status": self.status(), "adapter": r["adapter"]["name"]}

    def control(self, action: str, reason: str = "") -> dict:
        c = self.ctl
        if not c or not self.task or self.task["status"] not in core.ACTIVE:
            return {"ok": False, "error": "no task is running"}
        ok = False
        if action == "pause":
            ok = c.pause(reason or "paused by operator")
        elif action == "takeover":
            ok = c.takeover(reason or "operator took control")
        elif action in ("resume", "handback"):
            was = c.status
            ok = c.resume()
            if ok and was == "taken_over":
                self.task["notes"].append("The operator took control and has now handed back. The screen may have "
                                          "changed — look carefully before acting.")
        elif action == "stop":
            ok = self.stop(reason or "stopped by operator")
        else:
            return {"ok": False, "error": "unknown control"}
        if ok and self.watchdog is not None:
            (self.watchdog.suspended.set if c.status in ("paused", "taken_over") else self.watchdog.suspended.clear)()
        if ok and self.loop:
            self.loop.create_task(self._audit(f"computeruse.{action}", self.task["id"]))
            self.loop.create_task(self._event(f"{action.upper()}: {reason or c.status}", "warn" if action == "stop" else "info",
                                              log=action in ("stop", "takeover")))
        return {"ok": ok, "status": c.status}

    def stop(self, reason: str) -> bool:
        c = self.ctl
        if not c or not c.stop(reason):
            return False
        if self.runner and not self.runner.done():
            self.runner.cancel()                    # immediate: abandons a pending model call / wait
        return True

    def emergency(self, reason: str) -> None:
        """From the watchdog thread (via call_soon_threadsafe) or a FailSafeException."""
        if self.stop(f"EMERGENCY STOP: {reason}") and self.loop:
            self.loop.create_task(self._audit("computeruse.emergency_stop", reason))
            self.loop.create_task(self._event(f"EMERGENCY STOP — {reason}", "error", log=True))

    def human_input(self, reason: str) -> None:
        c = self.ctl
        if c and c.status in ("thinking", "acting", "starting") and self.settings.get("pause_on_mouse", True):
            if c.takeover(reason):
                if self.watchdog is not None:
                    self.watchdog.suspended.set()
                if self.loop:
                    self.loop.create_task(self._event(reason, "warn", log=True))
                    self.loop.create_task(self._audit("computeruse.takeover", "human input detected"))

    def decide(self, decision: str) -> dict:
        c = self.ctl
        if not c or not c.pending or c.pending.get("kind") != "approval":
            return {"ok": False, "error": "nothing is waiting for approval"}
        if decision not in ("approve", "deny", "takeover", "stop"):
            return {"ok": False, "error": "decision must be approve, deny, takeover or stop"}
        if decision == "stop":
            return {"ok": self.stop("stopped at the approval prompt")}
        ok = c.decide(decision)
        if ok and decision == "takeover" and self.watchdog is not None:
            self.watchdog.suspended.set()
        return {"ok": ok}

    def reply(self, text: str) -> dict:
        c = self.ctl
        if not c or not c.pending or c.pending.get("kind") != "question":
            return {"ok": False, "error": "the agent is not waiting for an answer"}
        text = (text or "").strip()[:1000]
        if not text:
            return {"ok": False, "error": "empty reply"}
        return {"ok": c.decide("reply", text)}

    # ── the loop ────────────────────────────────────────────────────────
    def _job(self, frame: core.Frame) -> models.Job:
        t = self.task
        try:
            import win_actions
            apps = sorted(win_actions.APPS) if os.name == "nt" else []
        except Exception:
            apps = []
        if os.name != "nt":
            apps = ["any installed application by name"] if os.uname().sysname == "Darwin" else []
        if not self.settings["allow_apps"]:
            apps = []
        mon = (f"Monitor {self.settings['monitor']} of the desktop."
               if self.settings["monitor"] != 1 else "")
        return models.Job(task=t["task"], step=t["n"] + 1, max_steps=t["max_steps"], platform=_platform(),
                          history=[{"n": s["n"], "desc": s["desc"], "result": s["result"], "reasoning": s.get("reasoning", "")}
                                   for s in t["steps"]], notes=list(t["notes"]),
                          allowed_dirs=self.settings["allowed_dirs"], apps=apps, monitor_note=mon)

    async def _capture(self, step: int):
        """Grab a frame. PRIVACY GUARD: if a private file / password manager window is visible
        on that monitor, the frame is discarded (never stored, never sent) and _Private raised."""
        if self.capture_fn is not None:
            fr, full = await self.capture_fn(step)
            titles = await self.titles_fn() if self.titles_fn else []
            hit = core.private_window(titles)
            if hit:
                raise _Private(hit)
            return fr, full
        import computeruse_screen as scr
        fr, full = await asyncio.to_thread(scr.capture, self.settings["monitor"], self.settings["long_edge"], 70, step)
        titles = await asyncio.to_thread(scr.window_titles, (fr.mon_left, fr.mon_top, fr.mon_w, fr.mon_h))
        hit = core.private_window(titles)
        if hit:
            raise _Private(hit)
        d = self.frames / self.task["id"]
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{step:04d}.jpg"
        p.write_bytes(fr.jpeg)
        fr.path = str(p)
        return fr, full

    def _record(self, action: dict | None, desc: str, result: str, ok: bool, reasoning: str, gate: dict | None,
                frame: core.Frame) -> dict:
        t = self.task
        t["n"] += 1
        s = {"n": t["n"], "ts": _now(), "type": (action or {}).get("type", "error"), "desc": desc[:300],
             "result": result[:400], "ok": ok, "reasoning": reasoning[:600], "gate": gate, "frame": frame.step,
             "fw": frame.img_w, "fh": frame.img_h}
        if action:
            for k in ("x", "y", "x2", "y2"):
                if k in action:
                    s[k] = action[k]
        t["steps"].append(s)
        del t["steps"][:-80]
        return s

    async def _run(self) -> None:
        t, c, s = self.task, self.ctl, self.settings
        limiter = core.RateLimiter(s["min_interval"], s["per_minute"])
        deadline = t["started"] + t["max_minutes"] * 60
        errors = bad_replies = denials = 0
        prev_thumb, last_effect = b"", False
        try:
            if self.executor is None:
                import computeruse_screen as scr
                self.inp = await asyncio.to_thread(scr.Input)
                self.watchdog = scr.Watchdog(self.inp, lambda r: self.loop.call_soon_threadsafe(self.emergency, r),
                                             lambda r: self.loop.call_soon_threadsafe(self.human_input, r),
                                             detect_human=s["pause_on_mouse"])
                self.watchdog.start()
            c.phase("thinking")
            shot = 0
            while True:
                await c.checkpoint()
                if t["n"] >= t["max_steps"]:
                    raise _Fail(f"reached the step limit ({t['max_steps']})")
                if _now() > deadline:
                    raise _Fail(f"reached the time limit ({t['max_minutes']} min)")
                shot += 1
                try:
                    frame, full = await self._capture(shot)
                except _Private as e:
                    await self._privacy_hold(str(e))
                    continue
                t["frame"] = frame.meta()
                if last_effect and prev_thumb:
                    try:
                        import computeruse_screen as scr
                        if scr.change_ratio(prev_thumb, frame.thumb) < 0.002:
                            t["notes"].append("The screen did not visibly change after the last action — "
                                              "it may have missed; verify before repeating.")
                    except Exception:
                        pass
                prev_thumb, last_effect = frame.thumb, False
                c.phase("thinking")
                await self._event(f"step {t['n'] + 1}: thinking")
                job = self._job(frame)
                consumed_notes = list(t["notes"])
                t["notes"].clear()
                plan = await asyncio.wait_for(self.adapter.plan(job, frame), max(5.0, deadline - _now()))
                await c.checkpoint()
                t["reasoning"] = plan.reasoning
                if plan.error:
                    errors += 1
                    self._record(None, "model error", plan.error, False, plan.reasoning, None, frame)
                    await self._event(f"model error: {plan.error}", "warn")
                    if errors >= 3:
                        raise _Fail(f"model failed 3 times: {plan.error}")
                    t["notes"].extend(consumed_notes)
                    await asyncio.sleep(2)
                    continue
                errors = 0
                if plan.final:
                    await self._finish("done", summary=plan.final)
                    return
                results, out_notes, failed = [], [], False
                for item in plan.actions:
                    native = item.get("native")
                    res = {"tool_id": item.get("tool_id"), "toolset": item.get("toolset"), "ok": False, "text": ""}
                    results.append(res)
                    if failed:
                        res["text"] = "Not executed: an earlier computer action in this turn failed."
                        continue
                    # ── validate ──
                    try:
                        if native == "bucs_request_approval":
                            reason = str((item.get("input") or {}).get("reason") or "")[:300]
                            d = await self._ask_approval({"type": "declared"}, core.GateResult(core.APPROVE, "declared", reason),
                                                         plan.reasoning, frame, f"approve: {reason}")
                            res["ok"] = d == "approve"
                            res["text"] = ("Approved by the operator — proceed with that step." if res["ok"] else
                                           f"The operator answered '{d}'. Do NOT do that step; choose another way or finish.")
                            self._record({"type": "approval"}, f"approval requested: {reason}", res["text"], res["ok"],
                                         plan.reasoning, {"verdict": "approve", "category": "declared", "reason": reason}, frame)
                            continue
                        if native:
                            cur = None
                            if self.inp is not None:
                                try:
                                    cur = frame.to_frame(*self.inp.cursor())
                                except Exception:
                                    cur = None
                            action = models.map_native(native, item.get("input") or {}, frame, cur)
                        else:
                            if item.get("action") is None:
                                raise core.ActionError(item.get("error") or "invalid action")
                            action = item["action"]
                    except core.ActionError as e:
                        bad_replies += 1
                        res["text"] = f"Invalid action: {e}"
                        failed = True
                        self._record(None, "invalid action", str(e), False, plan.reasoning, None, frame)
                        if not native:
                            t["notes"].append(f"Your last reply was invalid ({e}). Follow the JSON schema exactly.")
                        if bad_replies >= 3:
                            raise _Fail(f"the model produced 3 invalid actions in a row: {e}")
                        continue
                    bad_replies = 0
                    desc = core.describe(action)
                    a_type = action["type"]
                    # ── terminal / conversational actions ──
                    if a_type == "done":
                        self._record(action, desc, "task finished", True, plan.reasoning, None, frame)
                        await self._finish("done", summary=action["summary"])
                        return
                    if a_type == "ask_user":
                        self._record(action, desc, "waiting for the operator", True, plan.reasoning, None, frame)
                        await self._event(f"agent asks: {action['question'][:200]}", "warn", log=True)
                        ans = await c.ask("question", {"question": action["question"]}, QUESTION_TIMEOUT)
                        if ans.get("decision") != "reply":
                            raise _Fail("no answer from the operator")
                        res.update(ok=True, text=f"Operator replied: {ans.get('text', '')}")
                        if not native:
                            t["notes"].append(f"You asked: {action['question'][:200]} — operator replied: {ans.get('text', '')}")
                        await self._audit("computeruse.reply", t["id"])
                        continue
                    # ── gate ──
                    gate = core.check_action(action, plan.reasoning, bool(item.get("sensitive")),
                                             item.get("sensitive_reason") or "", s["approve_all"])
                    if gate.verdict == core.REFUSE:
                        res["text"] = f"REFUSED by BUCS safety: {gate.reason}. The human is taking over for this part."
                        failed = True
                        self._record(action, desc if a_type != "type" else "type [withheld secret]", res["text"], False,
                                     plan.reasoning, gate.as_dict(), frame)
                        await self._audit("computeruse.refused", f"{gate.category}: {gate.reason[:200]}")
                        await self._event(f"refused: {gate.reason}", "error", log=True)
                        c.takeover(f"needs the human: {gate.reason}")
                        if self.watchdog is not None:
                            self.watchdog.suspended.set()
                        t["notes"].append("BUCS refused to type a secret; the operator handled that part by hand. "
                                          "Look at the screen and continue.")
                        continue
                    if gate.verdict == core.APPROVE:
                        d = await self._ask_approval(action, gate, plan.reasoning, frame, desc)
                        if d != "approve":
                            denials += 1
                            res["text"] = ("The operator DENIED this action. Do not retry it; choose another way, "
                                           "ask_user, or finish." if d in ("deny", "timeout") else
                                           "The operator took over instead of this action. Take a fresh screenshot "
                                           "before continuing.")
                            failed = True
                            self._record(action, desc, f"not executed ({d})", False, plan.reasoning, gate.as_dict(), frame)
                            if not native:
                                t["notes"].append(f"Operator {d} for: {desc}. Do not retry it.")
                            if d == "timeout":
                                c.pause("approval timed out — task paused")
                            if denials >= 5:
                                raise _Fail("the operator denied 5 actions")
                            continue
                    # ── execute ──
                    await c.checkpoint()
                    await limiter.wait()
                    c.phase("acting")
                    await self._event(f"step {t['n'] + 1}: {desc}")
                    try:
                        ok, text, image = await self._execute(action, frame, full)
                    except _Emergency as e:
                        self.emergency(str(e))
                        await c.checkpoint()
                        raise
                    except core.ActionError as e:
                        ok, text, image = False, str(e), None
                    except Exception as e:
                        ok, text, image = False, f"{type(e).__name__}: {str(e)[:200]}", None
                    res.update(ok=ok, text=text, image=image, type=a_type)
                    if not ok:
                        failed = True
                    last_effect = last_effect or (ok and a_type in core.EFFECTFUL)
                    self._record(action, desc, text or ("ok" if ok else "failed"), ok, plan.reasoning,
                                 gate.as_dict() if gate.verdict != core.ALLOW else None, frame)
                    if not native and a_type in ("read_file", "list_dir", "cursor_position") and ok:
                        t["notes"].append(f"Result of {desc}:\n{text[:3000]}")
                    await self._audit("computeruse.action", f"{t['id']} #{t['n']} {desc}" + ("" if ok else f" → {text[:120]}"))
                    c.phase("thinking")
                self.adapter.add_results(results, frame, [])
                launched = any(r.get("ok") and (r.get("type") in ("open_app", "open_url")) for r in results)
                await asyncio.sleep(LAUNCH_SETTLE if launched else SETTLE)   # let a new window appear first
        except asyncio.CancelledError:
            await self._finish("stopped", error=(c.stop_reason if c.stopped else "cancelled"))
        except core.Stopped as e:
            await self._finish("stopped", error=str(e))
        except _Fail as e:
            await self._finish("failed", error=str(e))
        except asyncio.TimeoutError:
            await self._finish("failed", error=f"reached the time limit ({t['max_minutes']} min)")
        except Exception as e:
            await self._finish("failed", error=f"{type(e).__name__}: {str(e)[:300]}")

    async def _privacy_hold(self, what: str) -> None:
        """A private file / password manager is visible: hand control to the human."""
        reason = (f"privacy guard: a private window ({what}) is visible — the screen was NOT captured or "
                  "sent. Close or minimise it, then press HAND BACK.")
        if self.ctl.takeover(reason) and self.watchdog is not None:
            self.watchdog.suspended.set()
        await self._audit("computeruse.privacy_guard", f"{self.task['id']} {what}")
        await self._event(reason, "warn", log=True)
        self.task["notes"].append("A private window was on screen and the operator handled it. Re-check the screen.")

    async def _ask_approval(self, action: dict, gate: core.GateResult, reasoning: str, frame: core.Frame, desc: str) -> str:
        t = self.task
        payload = {"action": desc, "type": action.get("type"), "category": gate.category, "reason": gate.reason,
                   "reasoning": reasoning[:600], "frame": frame.step, "fw": frame.img_w, "fh": frame.img_h,
                   **{k: action[k] for k in ("x", "y", "x2", "y2") if k in action}}
        if self.watchdog is not None:
            self.watchdog.suspended.set()        # the operator will use the mouse to answer
        await self._audit("computeruse.approval.request", f"{t['id']} {gate.category}: {desc[:160]}")
        await self._event(f"APPROVAL NEEDED ({gate.category}): {desc}", "warn", log=True)
        try:
            ans = await self.ctl.ask("approval", payload, APPROVAL_TIMEOUT)
        finally:
            if self.watchdog is not None and self.ctl.status not in ("paused", "taken_over"):
                self.watchdog.suspended.clear()
                self.watchdog.rebase()
        d = ans.get("decision", "deny")
        await self._audit("computeruse.approval." + d, f"{t['id']} {desc[:160]}")
        await self._event(f"operator: {d.upper()} — {desc}", "info" if d == "approve" else "warn")
        return d

    async def _execute(self, a: dict, frame: core.Frame, full) -> tuple[bool, str, bytes | None]:
        """Run one validated action. Returns (ok, text-for-model, image-for-model)."""
        t = a["type"]
        if self.executor is not None:                       # tests
            return await self.executor(a, frame)
        s = self.settings
        if t == "wait":
            await asyncio.sleep(a["seconds"])
            return True, "OK", None
        if t == "screenshot":
            try:
                fr, _ = await self._capture(10_000 + self.task["n"])
            except _Private as e:
                await self._privacy_hold(str(e))
                return False, "Privacy guard: a private window was on screen; the operator is handling it. Take a new screenshot.", None
            return True, "OK", fr.jpeg
        if t == "zoom":
            import computeruse_screen as scr
            return True, "OK", await asyncio.to_thread(scr.zoom_image, full, frame, a["region"])
        if t == "read_file":
            return True, await asyncio.to_thread(read_file, a["path"], s["allowed_dirs"]), None
        if t == "list_dir":
            return True, await asyncio.to_thread(list_dir, a["path"], s["allowed_dirs"]), None
        if t == "open_app":
            if not s["allow_apps"]:
                raise core.ActionError("launching apps is disabled in settings")
            return await asyncio.to_thread(_open_app, a["app"])
        if t == "open_url":
            if not s["allow_urls"]:
                raise core.ActionError("opening URLs is disabled in settings")
            import browser
            r = await asyncio.to_thread(browser.open_url, a["url"], False, False)
            return bool(r.get("opened")), "OK" if r.get("opened") else r.get("error", "failed"), None
        inp, wd = self.inp, self.watchdog
        if inp is None:
            raise core.ActionError("input backend unavailable")
        try:
            wd.agent_busy.set()
            import pyautogui
            try:
                return await asyncio.to_thread(_drive, inp, a, frame)
            except pyautogui.FailSafeException as e:
                raise _Emergency(str(e) or "fail-safe corner") from None
        finally:
            await asyncio.sleep(0.05)
            wd.rebase()
            wd.agent_busy.clear()

    async def _finish(self, status: str, summary: str = "", error: str = "") -> None:
        t = self.task
        if not t or t["status"] in core.FINAL:
            return
        t["status"], t["ended"] = status, _now()
        t["summary"], t["error"] = summary[:1000], error[:500]
        if self.ctl:
            self.ctl.status = status
            self.ctl.pending = None
        if self.watchdog is not None:
            self.watchdog.halt.set()
        if self.inp is not None:
            try:
                await asyncio.to_thread(self.inp.release_all)
            except Exception:
                pass
        self.ended_at[t["id"]] = _now()
        self.history.insert(0, {"id": t["id"], "task": t["task"], "adapter": t["adapter_name"], "status": status,
                                "source": t.get("source", "dashboard"),
                                "steps": t["n"], "started": t["started"], "ended": t["ended"],
                                "summary": t["summary"], "error": t["error"]})
        del self.history[30:]
        self._save("history.json", self.history)
        await self._audit(f"computeruse.task.{status}", f"{t['id']} · {t['n']} steps · {(summary or error)[:200]}")
        lvl = {"done": "info", "stopped": "warn", "failed": "error"}[status]
        await self._event(f"task {status.upper()} after {t['n']} steps — {(summary or error)[:200]}", lvl, log=True)

    # ── housekeeping ────────────────────────────────────────────────────
    def sweep(self) -> int:
        """Delete screenshots of tasks that ended > FRAME_TTL ago, and any frame folder untouched
        for FRAME_TTL (crash leftovers / other processes). Age-based, so a second BUCS process
        (tests, a restart) can never wipe the frames of a task that is still running."""
        n = 0
        if not self.frames.is_dir():
            return 0
        cur = self.task["id"] if self.task and self.task["status"] in core.ACTIVE else None
        for d in self.frames.iterdir():
            if d.name == cur:
                continue
            ended = self.ended_at.get(d.name)
            try:
                newest = max([d.stat().st_mtime] + [f.stat().st_mtime for f in d.iterdir()])
            except OSError:
                newest = 0
            if _now() - newest > FRAME_TTL or (ended is not None and _now() - ended > FRAME_TTL):
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        return n

    async def loop_forever(self) -> None:
        self.sweep()
        await self.refresh_candidates(force=True)
        while True:
            await asyncio.sleep(60)
            try:
                self.sweep()
            except Exception:
                pass


class _Fail(Exception):
    pass


class _Emergency(Exception):
    pass


class _Private(Exception):
    pass


def _platform() -> str:
    import platform
    return platform.system()


def _open_app(name: str) -> tuple[bool, str, None]:
    import platform
    sysname = platform.system()
    if sysname == "Windows":
        import win_actions
        r = win_actions.open_app(name)
        return bool(r.get("ok")), r.get("say", ""), None
    if sysname == "Darwin":
        try:
            p = subprocess.run(["open", "-a", name], capture_output=True, text=True, timeout=10)
            return p.returncode == 0, "OK" if p.returncode == 0 else (p.stderr.strip()[:200] or "not found"), None
        except Exception as e:
            return False, str(e)[:200], None
    return False, "launching apps is supported on Windows and macOS only", None


def _drive(inp, a: dict, frame: core.Frame) -> tuple[bool, str, None]:
    """Blocking input for pointer/keyboard actions (runs in a worker thread)."""
    t = a["type"]
    if t in core.POINTER:
        x, y = frame.to_screen(a["x"], a["y"])
        if t == "move":
            inp.move(x, y)
        else:
            btn = {"right_click": "right", "middle_click": "middle"}.get(t, "left")
            cnt = {"double_click": 2, "triple_click": 3}.get(t, 1)
            inp.click(x, y, btn, cnt, a.get("modifiers") or [])
    elif t == "drag":
        x, y = frame.to_screen(a["x"], a["y"])
        x2, y2 = frame.to_screen(a["x2"], a["y2"])
        inp.drag(x, y, x2, y2)
    elif t == "scroll":
        if "x" in a:
            x, y = frame.to_screen(a["x"], a["y"])
            inp.scroll(a["direction"], a["amount"], x, y)
        else:
            inp.scroll(a["direction"], a["amount"])
    elif t == "type":
        inp.type_text(a["text"])
    elif t == "key":
        inp.key(a["keys"], a.get("repeat", 1))
    elif t == "hold_key":
        inp.hold_key(a["keys"], a["seconds"])
    elif t in ("mouse_down", "mouse_up"):
        inp.button("left", t == "mouse_down")
    elif t == "cursor_position":
        x, y = frame.to_frame(*inp.cursor())
        return True, f"X={x}, Y={y}", None
    else:
        raise core.ActionError(f"'{t}' cannot be executed here")
    return True, "OK", None


# ═══ ROUTER ══════════════════════════════════════════════════════════════════

class TaskReq(BaseModel):
    task: str
    adapter: str | None = None
    max_steps: int | None = None
    max_minutes: int | None = None


class ControlReq(BaseModel):
    action: str
    reason: str = ""


class DecideReq(BaseModel):
    decision: str


class ReplyReq(BaseModel):
    text: str


def _engine(ctx) -> Engine:
    st = ctx["state"]
    if "computeruse" not in st:
        st["computeruse"] = Engine(ctx)
    return st["computeruse"]


def build_router(ctx) -> APIRouter:
    r = APIRouter()
    eng = _engine(ctx)

    def gate(request: Request, need_host: bool = True):
        if not ctx["commander"]():
            return _LOCKED
        if need_host and not ctx["host_only"](request):
            return _HOST
        return None

    @r.get("/api/computeruse")
    async def cu_get():
        if not eng.candidates:
            await eng.refresh_candidates()
        return eng.snapshot()

    @r.get("/api/computeruse/models")
    async def cu_models():
        return {"candidates": await eng.refresh_candidates(force=True),
                "chosen": models.pick(eng.candidates, eng.settings["adapter"]), "howto": models.HOWTO_NONE}

    @r.get("/api/computeruse/monitors")
    async def cu_monitors(request: Request):
        if not ctx["host_only"](request):
            return _HOST
        try:
            import computeruse_screen as scr
            return {"ok": True, "monitors": await asyncio.to_thread(scr.monitors)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200], "monitors": []}

    @r.get("/api/computeruse/frame/{tid}/{step}")
    async def cu_frame(tid: str, step: int, request: Request):
        if not ctx["host_only"](request):                 # the screen never leaves the host
            return JSONResponse(_HOST, status_code=403)
        if not re.fullmatch(r"[\w-]{6,40}", tid) or not (0 <= step < 100_000):
            return JSONResponse({"ok": False, "error": "bad frame id"}, status_code=400)
        p = eng.frames / tid / f"{step:04d}.jpg"
        if not p.is_file():
            return JSONResponse({"ok": False, "error": "frame expired"}, status_code=404)
        return FileResponse(p, media_type="image/jpeg", headers={"cache-control": "no-store"})

    @r.post("/api/computeruse/task")
    async def cu_task(req: TaskReq, request: Request):
        if (g := gate(request)):
            return g
        return await eng.start_task(req.task, req.adapter, req.max_steps, req.max_minutes)

    @r.post("/api/computeruse/control")
    async def cu_control(req: ControlReq, request: Request):
        if req.action == "stop":                           # stopping is always allowed
            return eng.control("stop", req.reason[:200])
        if (g := gate(request)):
            return g
        return eng.control(req.action, req.reason[:200])

    @r.post("/api/computeruse/approve")
    async def cu_approve(req: DecideReq, request: Request):
        if req.decision == "stop":
            return eng.decide("stop")
        if (g := gate(request)):
            return g
        return eng.decide(req.decision)

    @r.post("/api/computeruse/reply")
    async def cu_reply(req: ReplyReq, request: Request):
        if (g := gate(request)):
            return g
        return eng.reply(req.text)

    @r.post("/api/computeruse/settings")
    async def cu_settings(body: dict, request: Request):
        if (g := gate(request)):
            return g
        if eng.runner and not eng.runner.done():
            return {"ok": False, "error": "settings can't change while a task runs"}
        new, errs = sanitize_settings(body, eng.settings)
        eng.settings = new
        eng._save("settings.json", new)
        await eng._audit("computeruse.settings", json.dumps({k: v for k, v in new.items() if k != "allowed_dirs"})[:300]
                         + f" · {len(new['allowed_dirs'])} folder(s)")
        return {"ok": not errs, "errors": errs, "settings": new}

    @r.post("/api/computeruse/history/clear")
    async def cu_clear(request: Request):
        if (g := gate(request)):
            return g
        eng.history = []
        eng._save("history.json", [])
        return {"ok": True}

    return r


async def submit_task(state_or_ctx, instruction: str, source: str = "agentos", **kw) -> dict:
    """Module-level alias: computeruse.submit_task(state, ...) → state["computeruse"].submit_task(...)."""
    st = state_or_ctx.get("state", state_or_ctx) if isinstance(state_or_ctx, dict) else {}
    eng = st.get("computeruse")
    if eng is None:
        return {"ok": False, "task_id": None, "status": "unavailable", "error": "computer use module not loaded"}
    return await eng.submit_task(instruction, source, **kw)


async def start(ctx) -> None:
    await _engine(ctx).loop_forever()
