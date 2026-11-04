"""JARVIS · Engineering — diagnose and fix BUCS from inside the dashboard.

The operator says or types something like "Jarvis, the vision digest isn't
showing anything — fix it" and BUCS works on its own codebase:

  1. DIAGNOSE (read-only)  Claude CLI in the repo with tools Read/Grep/Glob
                           only, plus a live-state snapshot (status, voice
                           diagnostics, recent errors). Streams its analysis
                           and ends with a concrete proposal. Nothing changes.
  2. APPLY (operator-approved)  Only after the operator clicks APPLY (or says
                           "apply the fix"): Read/Grep/Glob/Edit/Write with
                           acceptEdits. NO shell tool — verified the CLI cannot
                           execute commands with this tool set.
  3. VERIFY                The test suite runs; the diff stat is shown.
  4. KEEP · UNDO · SHIP    Undo reverts only files this job changed (and that
                           were clean before). Ship commits + pushes those
                           files. Nothing is committed or pushed automatically.

Guardrails: one job at a time, commander role (enforced by the API), every
step audited, secrets (.env, credentials, tokens, auth) backed up before apply
and restored if touched, hard timeouts, CLAUDE.md (the dev manual) applies
because the CLI runs in the repo.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTECTED = [".env", "credentials.json", "token.json", "auth.json", "fleet_token.txt",
             "pair_code.txt", "node_id.txt", "node_token.txt", "operators.json",
             "commander_memory.md", "operator_memory.json", "providers.json", "license.json",
             "license_signing.key", "licences_issued.json",
             "commerce_test.db", "commerce_live.db",
             # private runtime data under .update/ (phone credentials + call transcripts, device
             # tokens, security state, mic preference); the whole of .update/ is PROTECTED_DIRS too
             ".update/phone.json", ".update/phone.db", ".update/devices.json", ".update/secagent.json",
             ".update/mic_pref.json"]
# Directories the engineering CLI may never read or write and a fix may never touch
# (.update/ holds every module's private state: agentos memory DB, phone, devices, backups…).
PROTECTED_DIRS = (".update/",)
# Written by the running app while a job runs: protected by tool denial + path refusal, but
# never byte-restored after an apply (that would roll back live data such as new call logs).
RUNTIME_DATA = {".update/phone.db", ".update/secagent.json"}
# Claude CLI permission rules: deny every read/edit of protected files and directories (Edit rules
# cover Write too; verified 2026-11-03 with the real CLI: a canary in .update/ was DENIED).
DENY_RULES = [f"{t}(./{p})" for p in PROTECTED for t in ("Read", "Edit")] + \
             [f"{t}(./{d}**)" for d in PROTECTED_DIRS for t in ("Read", "Edit")]
DIAG_TOOLS = "Read,Grep,Glob"
APPLY_TOOLS = "Read,Grep,Glob,Edit,Write"
# A fix may never touch what runs automatically afterwards (the test suite, git
# internals/hooks, CI, release tooling): changes there are reverted and the job
# is refused before anything executes.
FORBIDDEN_PREFIXES = ("tests/", ".git/", ".github/", "tools/")
_NO_HOOKS = str(Path(__file__).resolve().parent / ".update" / "no-hooks")   # empty dir → git runs no hooks

DIAG_PROMPT = """You are JARVIS's engineering subroutine working INSIDE the Bobbiey UCS
repository (read CLAUDE.md for the rules). The operator reported, from the live dashboard:

  "{request}"

Live state of the running server right now:
{live}

Investigate the codebase (read-only: you can read and search files, you cannot edit or run
anything). Find the real cause. Then answer in this exact shape, plain text, concise:

SUMMARY: one sentence a person can hear spoken aloud
CAUSE: what is actually wrong, with file:line references
PROPOSED FIX: the specific minimal change (files + what changes), or "none needed" with why
RISK: low/medium/high + one line why
NEEDS: anything only the operator can do (credentials, restarts, hardware), or "nothing"
"""

APPLY_PROMPT = """Implement this approved fix in the Bobbiey UCS repository (follow CLAUDE.md).

Operator request: "{request}"
Approved diagnosis and plan:
{plan}

Rules: make the minimal change that implements the plan; edit only files inside this repo;
never touch .env, credentials.json, token.json, auth.json or any *_token / pair_code file;
keep existing behaviour working; if a static/ asset changes, bump its ?v= in static/index.html.
When done, reply with: CHANGED: <files> and RESULT: <one sentence>."""


def _git(*args, timeout=30) -> str:
    os.makedirs(_NO_HOOKS, exist_ok=True)
    r = subprocess.run(["git", "-c", f"core.hooksPath={_NO_HOOKS}", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout


def _protected_backup() -> dict[str, bytes]:
    """Bytes of every protected FILE (restored if a job modified or deleted it)."""
    out = {}
    for p in PROTECTED:
        f = ROOT / p
        if p not in RUNTIME_DATA and f.is_file():
            try:
                out[p] = f.read_bytes()
            except OSError:
                pass
    return out


def _porcelain() -> dict[str, str]:
    out = {}
    for line in _git("status", "--porcelain", "--untracked-files=all").splitlines():
        if len(line) > 3:
            out[line[3:].strip().strip('"')] = line[:2]
    return out


class DevAgent:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.job: dict | None = None
        self.lock = asyncio.Lock()
        self.history: list[dict] = []

    @property
    def hub(self):
        return self.state.get("hub")

    async def _emit(self, stage: str, **kw) -> None:
        if self.hub and self.job:
            await self.hub.broadcast({"type": "dev", "id": self.job["id"], "stage": stage,
                                      "request": self.job["request"][:200], **kw})

    def snapshot(self) -> dict:
        j = self.job or {}
        return {"busy": self.lock.locked(), "job": {k: v for k, v in j.items() if k not in ("before", "backup", "patch")},
                "history": self.history[-10:]}

    # ── live context for the diagnosis ────────────────────────────
    async def _live_state(self) -> str:
        s = self.state
        lines = []
        try:
            st = {"brain_mode": getattr(s.get("brain"), "mode", "?"),
                  "news_items": len(getattr(s.get("news"), "recent", []) or []),
                  "tts": s["tts"].audio_state() if s.get("tts") else None,
                  "google": s["gcal"].status() if s.get("gcal") else None}
            lines.append("status: " + json.dumps(st, default=str)[:900])
        except Exception as e:
            lines.append(f"status: unavailable ({e})")
        v = s.get("voice")
        if v is not None:
            try:
                d = v.diagnostics()
                d["utterances"] = d.get("utterances", [])[:5]
                lines.append("voice: " + json.dumps(d, default=str)[:900])
            except Exception:
                pass
        ins = s.get("insights")
        if ins is not None:
            lines.append("vision digest: " + json.dumps(getattr(ins, "last_digest", None), default=str)[:400])
        eng = s.get("engine")
        if eng is not None:
            snap = eng.snapshot()
            fails = [c for c in snap.get("recent", []) if c.get("stage") in ("failed",)][:3]
            lines.append("engine: " + json.dumps({"totals": snap.get("totals"), "recent_failures": fails}, default=str)[:700])
        return "\n".join("  " + l for l in lines)

    # ── Claude CLI in the repo (stream-json) ──────────────────────
    async def _run_cli(self, prompt: str, tools: str, apply: bool, timeout: float, cwd: Path | None = None) -> str:
        brain = self.state.get("brain")
        exe = getattr(brain, "claude_bin", "claude")
        args = [exe, "-p", "--tools", tools, "--output-format", "stream-json",
                "--include-partial-messages", "--verbose", "--strict-mcp-config",
                "--disallowedTools", ",".join(DENY_RULES)]
        if apply:
            args += ["--permission-mode", "acceptEdits"]
        args += ["--", prompt]
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(cwd or ROOT), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1 << 22)
        text, final, pending, last = [], None, "", 0.0

        async def read():
            nonlocal final, pending, last
            async for raw in proc.stdout:
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                if ev.get("type") == "stream_event":
                    d = (ev.get("event") or {}).get("delta") or {}
                    if d.get("type") == "text_delta":
                        text.append(d["text"]); pending += d["text"]
                        if time.time() - last > 0.15:
                            await self._emit("delta", text=pending); pending, last = "", time.time()
                elif ev.get("type") == "assistant":
                    for block in (ev.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use":       # show what it is looking at
                            inp = block.get("input") or {}
                            target = inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
                            await self._emit("tool", tool=block.get("name"), target=str(target)[-120:])
                elif ev.get("type") == "result":
                    final = ev.get("result") if not ev.get("is_error") else f"[error] {ev.get('result')}"
                    return
        try:
            await asyncio.wait_for(read(), timeout=timeout)
        finally:
            if pending:
                await self._emit("delta", text=pending)
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        return (final if isinstance(final, str) and final.strip() else "".join(text)).strip()

    # ── 1. diagnose ───────────────────────────────────────────────
    def _commander(self) -> bool:
        """Engineering actions are commander-only, however they arrive (API, chat, voice)."""
        return self.state.get("rbac", {}).get("role") == "commander"

    _LOCKED = {"ok": False, "error": "OBSERVER role: engineering mode is locked. Switch to COMMANDER."}

    async def diagnose(self, request: str) -> dict:
        if not self._commander():
            return dict(self._LOCKED)
        if self.lock.locked():
            return {"ok": False, "error": "an engineering job is already running"}
        async with self.lock:
            self.job = {"id": uuid.uuid4().hex[:8], "request": request, "stage": "diagnosing",
                        "started": time.time(), "plan": "", "summary": ""}
            await self._emit("diagnosing")
            await self._audit("dev.diagnose", request[:120])
            try:
                live = await self._live_state()
                cm = self.state.get("commander")
                if cm is not None and cm.sections:
                    rules = cm.context_for(request + " rules production data never require authorisation", budget=1800)
                    live += "\n  operator's own rules/context:\n" + rules
                plan = await self._run_cli(DIAG_PROMPT.format(request=request, live=live),
                                           DIAG_TOOLS, apply=False, timeout=240)
            except asyncio.TimeoutError:
                plan = "[error] diagnosis timed out"
            except FileNotFoundError:
                plan = "[error] Claude CLI not found — engineering mode needs it"
            summary = next((l.split(":", 1)[1].strip() for l in plan.splitlines()
                            if l.upper().startswith("SUMMARY:")), plan[:200])
            fixable = ("PROPOSED FIX:" in plan.upper() and "none needed" not in plan.lower()
                       and not plan.startswith("[error]"))
            self.job.update(stage="proposed" if fixable else "diagnosed", plan=plan, summary=summary,
                            fixable=fixable)
            await self._emit(self.job["stage"], plan=plan, summary=summary, fixable=fixable)
            return {"ok": not plan.startswith("[error]"), "summary": summary, "fixable": fixable,
                    "id": self.job["id"]}

    # ── 2-3. apply + verify ───────────────────────────────────────
    async def apply(self) -> dict:
        if not self._commander():
            return dict(self._LOCKED)
        if not self.job or self.job.get("stage") != "proposed":
            return {"ok": False, "error": "no approved proposal to apply — ask me to diagnose first"}
        if self.lock.locked():
            return {"ok": False, "error": "busy"}
        async with self.lock:
            job = self.job
            job["stage"] = "applying"
            await self._emit("applying")
            await self._audit("dev.apply", job["request"][:120])
            before = _porcelain()
            backup = _protected_backup()
            try:
                result = await self._run_cli(APPLY_PROMPT.format(request=job["request"], plan=job["plan"]),
                                             APPLY_TOOLS, apply=True, timeout=420)
            except asyncio.TimeoutError:
                result = "[error] apply timed out"
            restored = []
            for p, data in backup.items():
                f = ROOT / p
                if not f.exists() or f.read_bytes() != data:      # modified OR deleted
                    f.write_bytes(data); restored.append(p)
            after = _porcelain()
            changed = sorted(f for f in after if after.get(f) != before.get(f))
            clean_before = [f for f in changed if f not in before]
            forbidden = [f for f in changed if f.replace("\\", "/").startswith(FORBIDDEN_PREFIXES + PROTECTED_DIRS)
                         or f.replace("\\", "/") in PROTECTED]
            job.update(changed=changed, revertable=clean_before, restored=restored, result=result)
            if forbidden:
                # never run tests (code) the change itself wrote: revert everything it did
                job["stage"] = "applied"
                await asyncio.to_thread(self._revert, job)
                job.update(stage="refused", tests={"ok": False, "summary": "refused: change touched " + ", ".join(forbidden[:5])})
                await self._emit("refused", forbidden=forbidden)
                await self._audit("dev.refused", "touched " + ", ".join(forbidden[:5]))
                self._close()
                return {"ok": False, "error": "the change touched protected areas (" + ", ".join(forbidden[:5]) + ") and was reverted"}
            await self._emit("testing", changed=changed)
            tests = await self._tests()
            job.update(stage="applied", tests=tests, diffstat=_git("diff", "--stat", "--", *changed)[-1500:] if changed else "")
            await self._emit("applied", changed=changed, tests=tests, result=result, restored=restored,
                             diffstat=job["diffstat"])
            await self._audit("dev.applied", f"{len(changed)} files · tests {'pass' if tests['ok'] else 'FAIL'}")
            return {"ok": True, "changed": changed, "tests": tests}

    async def _tests(self) -> dict:
        py = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        exe = str(py) if py.exists() else sys.executable
        proc = await asyncio.create_subprocess_exec(
            exe, "-m", "unittest", "discover", "-s", "tests", cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "summary": "tests timed out"}
        tail = out.decode(errors="replace").strip().splitlines()[-6:]
        return {"ok": proc.returncode == 0, "summary": " | ".join(t for t in tail if t.strip())[-400:]}

    # ── 4. keep / undo / ship ─────────────────────────────────────
    def _revert(self, job) -> tuple[list, list]:
        tracked = set(_git("ls-files", "--", *job["revertable"]).split()) if job["revertable"] else set()
        restore = [f for f in job["revertable"] if f in tracked]
        new = [f for f in job["revertable"] if f not in tracked]
        if restore:
            _git("checkout", "--", *restore)
        for f in new:
            try:
                (ROOT / f).unlink()
            except OSError:
                pass
        return restore, new

    async def undo(self) -> dict:
        if not self._commander():
            return dict(self._LOCKED)
        job = self.job
        if not job or job.get("stage") != "applied":
            return {"ok": False, "error": "nothing to undo"}
        restore, new = await asyncio.to_thread(self._revert, job)   # git off the event loop
        skipped = [f for f in job["changed"] if f not in job["revertable"]]
        job["stage"] = "undone"
        await self._emit("undone", reverted=restore + new, skipped=skipped)
        await self._audit("dev.undo", f"reverted {len(restore) + len(new)} files")
        self._close()
        return {"ok": True, "reverted": restore + new, "skipped_preexisting_changes": skipped}

    async def keep(self) -> dict:
        if not self.job or self.job.get("stage") != "applied":
            return {"ok": False, "error": "nothing to keep"}
        self.job["stage"] = "kept"
        await self._emit("kept", changed=self.job["changed"])
        self._close()
        return {"ok": True}

    async def ship(self) -> dict:
        if not self._commander():
            return dict(self._LOCKED)
        job = self.job
        if not job or job.get("stage") != "applied":
            return {"ok": False, "error": "nothing applied to ship"}
        if not job.get("tests", {}).get("ok"):
            return {"ok": False, "error": "tests are failing — fix or undo before shipping"}
        files = [f for f in job["changed"] if f not in PROTECTED and not f.replace("\\", "/").startswith(PROTECTED_DIRS)]
        if not files:
            return {"ok": False, "error": "no files to ship"}
        msg = f"fix: {job['summary'][:68]}\n\nApplied from the BUCS dashboard (JARVIS engineering mode).\nRequest: {job['request'][:200]}"

        def _commit_and_push():
            # hooks disabled (core.hooksPath → empty dir): nothing written by a fix runs here
            _git("add", "--", *files)
            subprocess.run(["git", "-c", f"core.hooksPath={_NO_HOOKS}", "-c", "user.name=Sai",
                            "-c", "user.email=saikiran.thatikonda@t-hub.co",
                            "commit", "-q", "-m", msg, "--", *files], cwd=ROOT, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            sha_ = _git("rev-parse", "--short", "HEAD").strip()
            push_ = subprocess.run(["git", "-c", f"core.hooksPath={_NO_HOOKS}", "push", "origin", "HEAD"],
                                   cwd=ROOT, capture_output=True, text=True, timeout=120,
                                   env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return sha_, push_

        sha, push = await asyncio.to_thread(_commit_and_push)       # never block the event loop
        job["stage"] = "shipped"
        await self._emit("shipped", commit=sha, pushed=push.returncode == 0)
        await self._audit("dev.ship", f"{sha} pushed={push.returncode == 0}")
        self._close()
        return {"ok": True, "commit": sha, "pushed": push.returncode == 0,
                "push_error": "" if push.returncode == 0 else push.stderr[-300:]}

    # ── 5. sandbox → approval → deploy (AUTONOMOUS EVOLUTION, evolution.py) ──
    # The candidate change is produced and validated in an ISOLATED COPY of the repo; the
    # real working tree is only written by deploy_patch(), after the commander approved a
    # PASSING sandbox result. deploy_patch() then hands over to the existing
    # KEEP · UNDO · SHIP path (stage "applied"); revert_deploy() restores the pre-deploy
    # bytes of exactly the files it wrote (and only while they are still what it wrote).
    _NOT_OWNER = {"ok": False, "error": "code changes run only on the owner's machine (BUCS_OWNER=1); "
                                        "customer installs observe and analyse only"}

    def _owner(self) -> bool:
        lic = self.state.get("licensing")
        if lic is not None:
            return bool(getattr(lic, "owner", False))
        return os.getenv("BUCS_OWNER", "0") == "1"

    @staticmethod
    def _unsafe_path(f: str) -> bool:
        f = f.replace("\\", "/")
        p = Path(f)
        return (p.is_absolute() or ".." in p.parts or ":" in f or f.startswith(FORBIDDEN_PREFIXES + PROTECTED_DIRS)
                or p.name in PROTECTED or f in PROTECTED)

    async def sandbox(self) -> dict:
        if not self._commander():
            return dict(self._LOCKED)
        if not self._owner():
            return dict(self._NOT_OWNER)
        if not self.job or self.job.get("stage") != "proposed":
            return {"ok": False, "error": "no proposal to test — diagnose first"}
        if self.lock.locked():
            return {"ok": False, "error": "busy"}
        import base64
        import evolution_sandbox as sbx
        async with self.lock:
            job = self.job
            job["stage"] = "sandboxing"
            await self._emit("sandboxing")
            await self._audit("dev.sandbox", job["request"][:120])
            t0, box = time.time(), None
            sid = uuid.uuid4().hex[:10]
            res: dict = {"id": sid, "ts": t0, "ok": False}
            patch = None
            try:
                box, files = await asyncio.to_thread(sbx.make_copy, ROOT, PROTECTED)
                try:
                    result = await self._run_cli(APPLY_PROMPT.format(request=job["request"], plan=job["plan"]),
                                                 APPLY_TOOLS, apply=True, timeout=420, cwd=box)
                except asyncio.TimeoutError:
                    result = "[error] sandbox apply timed out"
                except FileNotFoundError:
                    result = "[error] Claude CLI not found — engineering mode needs it"
                d = await asyncio.to_thread(sbx.diff, ROOT, box, files)
                changed = sorted(d["changes"])
                forbidden = [f for f in changed if self._unsafe_path(f)]
                res.update(changed=changed, added=d["added"], removed=d["removed"], diff=d["diff"][:20000],
                           ops={f: v["op"] for f, v in d["changes"].items()}, cli=(result or "")[:1500])
                if (result or "").startswith("[error]"):
                    res["reason"] = result
                elif forbidden:
                    res["reason"] = "refused: the change touched protected areas (" + ", ".join(forbidden[:5]) + ")"
                elif not changed:
                    res["reason"] = "the engineering agent produced no change"
                else:
                    py = sbx.python_exe(ROOT)
                    tests = await sbx.run_tests(box, sbx.related_tests(box, changed), py)
                    boot = await sbx.boot_check(box, py)
                    broken = [f for f in changed if f.endswith(".py")
                              and any(f"] {Path(f).stem} not loaded" in l for l in boot.get("modules_not_loaded", []))]
                    if broken:
                        boot["ok"] = False
                        boot["broken_modules"] = broken
                    ok = tests.get("ok") is not False and boot.get("ok") is True
                    res.update(tests=tests, boot=boot, ok=ok,
                               reason="" if ok else ("unit tests failed" if tests.get("ok") is False
                                                     else "the changed copy did not boot cleanly"))
                    if ok:
                        patch = {"id": sid, "job": job["id"], "request": job["request"][:300],
                                 "summary": job.get("summary", "")[:200],
                                 "files": {f: {"op": v["op"], "orig_sha": v["orig_sha"],
                                               "new": None if v["new"] is None else base64.b64encode(v["new"]).decode()}
                                           for f, v in d["changes"].items()}}
            except Exception as e:
                res["reason"] = f"sandbox error: {type(e).__name__}: {str(e)[:200]}"
            finally:
                await asyncio.to_thread(sbx.cleanup, box)
            res["seconds"] = round(time.time() - t0, 1)
            job.update(stage="sandboxed" if res["ok"] else "proposed", sandbox=res, patch=patch)
            await self._emit("sandboxed", ok=res["ok"], reason=res.get("reason", ""), changed=res.get("changed", []))
            await self._audit("dev.sandboxed", f"{sid} ok={res['ok']} {res.get('reason', '')[:80]}")
            return {"ok": True, "sandbox": res, "patch": patch}

    async def deploy_patch(self, patch: dict, approved_by: str = "commander") -> dict:
        """Write an approved, sandbox-tested patch into the repo, then the existing path (tests →
        stage 'applied' → KEEP · UNDO · SHIP). Refuses stale patches (repo changed since the test)."""
        import base64
        import evolution_sandbox as sbx
        if not self._commander():
            return dict(self._LOCKED)
        if not self._owner():
            return dict(self._NOT_OWNER)
        files = (patch or {}).get("files") or {}
        if not files:
            return {"ok": False, "error": "empty patch"}
        bad = [f for f in files if self._unsafe_path(f)]
        if bad:
            return {"ok": False, "error": "refused: patch touches protected areas (" + ", ".join(bad[:5]) + ")"}
        if self.lock.locked():
            return {"ok": False, "error": "an engineering job is running — try again when it finishes"}
        if self.job and self.job.get("stage") in ("applied", "applying", "sandboxing", "diagnosing", "testing"):
            return {"ok": False, "error": "the ENGINEERING panel has an unfinished job (" + self.job["stage"] +
                                          ") — keep, undo or finish it first"}
        async with self.lock:
            def cur(f):
                p = ROOT / f
                return p.read_bytes() if p.is_file() else None
            stale = [f for f, v in files.items() if sbx.sha(cur(f)) != v.get("orig_sha")]
            if stale:
                return {"ok": False, "error": "the code changed since the sandbox test (" + ", ".join(stale[:4]) +
                                              ") — re-run the sandbox", "stale": stale}
            before = await asyncio.to_thread(_porcelain)
            pid = str(patch.get("id") or uuid.uuid4().hex[:10])
            bdir = ROOT / ".update" / "evolution" / "deploys" / pid
            prot = _protected_backup()
            manifest, hashes = {}, {}
            for f in files:
                orig = cur(f)
                manifest[f] = {"existed": orig is not None}
                if orig is not None:
                    dst = bdir / "files" / f
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(orig)
            for f, v in files.items():
                p = ROOT / f
                if v.get("new") is None:
                    if p.exists():
                        p.unlink()
                    hashes[f] = None
                else:
                    data = base64.b64decode(v["new"])
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(data)
                    hashes[f] = sbx.sha(data)
            restored = []
            for p, data in prot.items():
                fp = ROOT / p
                if not fp.exists() or fp.read_bytes() != data:
                    fp.write_bytes(data)
                    restored.append(p)
            bdir.mkdir(parents=True, exist_ok=True)
            (bdir / "manifest.json").write_text(json.dumps({"id": pid, "ts": time.time(), "files": manifest,
                                                            "hashes": hashes, "approved_by": approved_by}),
                                                encoding="utf-8")
            changed = sorted(files)
            self.job = {"id": pid, "request": patch.get("request", "evolution change"), "stage": "testing",
                        "started": time.time(), "plan": "", "summary": patch.get("summary", ""), "changed": changed,
                        "revertable": [f for f in changed if f not in before], "restored": restored,
                        "result": f"deployed sandbox-tested patch {pid}, approved by {approved_by}",
                        "deployed_from": "evolution"}
            await self._emit("testing", changed=changed)
            await self._audit("dev.deploy", f"{pid} · {len(changed)} files · approved by {approved_by}")
            tests = await self._tests()
            self.job.update(stage="applied", tests=tests,
                            diffstat=(await asyncio.to_thread(_git, "diff", "--stat", "--", *changed))[-1500:])
            await self._emit("applied", changed=changed, tests=tests, result=self.job["result"], restored=restored,
                             diffstat=self.job["diffstat"])
            return {"ok": True, "id": pid, "changed": changed, "tests": tests,
                    "backup_dir": str(bdir.relative_to(ROOT)).replace("\\", "/"), "hashes": hashes}

    async def revert_deploy(self, record: dict, reason: str = "", auto: bool = False) -> dict:
        """Restore the pre-deploy bytes of the files a deploy wrote — only files still exactly as
        deployed (the operator's later edits are never overwritten). Commander-only unless auto
        (catastrophic health failure, decided by evolution.py under selfheal's rules)."""
        if not auto and not self._commander():
            return dict(self._LOCKED)
        if not self._owner():
            return dict(self._NOT_OWNER)
        import evolution_sandbox as sbx
        bdir = ROOT / str((record or {}).get("backup_dir") or "")
        try:
            if (ROOT / ".update" / "evolution" / "deploys").resolve() not in bdir.resolve().parents:
                raise ValueError("bad backup dir")
            man = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            return {"ok": False, "error": "deploy backup not found — revert by hand (git)"}
        reverted, skipped = [], []
        for f, info in man.get("files", {}).items():
            if self._unsafe_path(f):
                skipped.append(f)
                continue
            p = ROOT / f
            now = p.read_bytes() if p.is_file() else None
            if sbx.sha(now) != man.get("hashes", {}).get(f):
                skipped.append(f)
                continue
            if info.get("existed"):
                p.write_bytes((bdir / "files" / f).read_bytes())
            elif p.exists():
                p.unlink()
            reverted.append(f)
        if self.job and self.job.get("id") == man.get("id") and self.job.get("stage") == "applied":
            self.job["stage"] = "undone"
            await self._emit("undone", reverted=reverted, skipped=skipped)
            self._close()
        await self._audit("dev.revert_deploy", f"{man.get('id')} reverted {len(reverted)} skipped {len(skipped)}"
                          + (" (AUTO: health failure)" if auto else "") + (f" · {reason[:80]}" if reason else ""))
        return {"ok": True, "reverted": reverted, "skipped": skipped}


    def _close(self) -> None:
        if self.job:
            self.history.append({k: self.job.get(k) for k in ("id", "request", "summary", "stage", "changed")})
            self.job = None

    async def _audit(self, action: str, detail: str) -> None:
        a = self.state.get("audit")
        if a is not None:
            rb = self.state.get("rbac", {})
            try:
                await a.log(action, detail, operator=rb.get("operator", "operator"), role=rb.get("role", "commander"))
            except Exception:
                pass
