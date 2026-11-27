"""DOOM SENTINEL (v8.9) — DOOM's live defence layer on top of the cyber analyst (secagent.py), the
Privacy Shield and the DOOM GATE. Everything is measured on this machine; nothing is simulated.

  INTRUSION WATCH  every request the security perimeter refuses: unauthenticated remote API calls,
                   cross-site / DNS-rebinding attempts stopped by the origin guard, failed logins
                   and locked-out sources. Kept in memory only (IP, path without query string,
                   reason, time), never headers, cookies, tokens or bodies. A burst from one source
                   raises a dashboard alert and an audit entry.
  CODE INTEGRITY   the security-perimeter files are fingerprinted (SHA-256) when BUCS starts; a
                   change on disk while BUCS runs is TAMPER. Every 5 min `git status` also lists
                   tracked code changed outside a commit and new untracked executables/scripts
                   inside the app folder.
  INJECTION SHIELD every block of dashboard facts handed to an agent passes through shield():
                   prompt-injection phrases ("ignore previous instructions", fake system tags,
                   "send the passwords to …", piped shell one-liners …) are replaced with
                   "[instruction removed by DOOM]" and counted per source. Only the pattern name
                   and source are kept, never the text.
  DEFCON           one threat level (5 = normal … 1 = maximum) from all of the above plus the cyber
                   analyst score and the privacy score, with every reason listed.

  GET  /api/sentinel          snapshot
  POST /api/sentinel/scan     commander · integrity check now
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent
PERIMETER_FILES = ("main.py", "doomgate.py", "updater.py", "secagent.py", "doomsentinel.py", "licensing.py",
                   "privacyshield.py", "devagent.py")
EXEC_SUFFIX = (".py", ".js", ".mjs", ".ps1", ".psm1", ".bat", ".cmd", ".vbs", ".exe", ".dll", ".pyd", ".sh", ".so", ".dylib", ".scr", ".jar")
BURST_N, BURST_WINDOW, BURST_COOLDOWN = 12, 300, 900
INTEGRITY_EVERY = 300
KINDS = {"origin": "cross-site / DNS-rebinding request blocked", "unauth": "unauthenticated remote request refused",
         "remote_off": "remote request while remote access is disabled", "login_fail": "failed remote login",
         "lockout": "request from a locked-out source"}

REMOVED = "[instruction removed by DOOM]"
INJECTION = [
    ("override", re.compile(r"\b(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+|every\s+)*(previous|prior|above|earlier|preceding|system|original)\s+"
                            r"(instructions?|prompts?|rules?|messages?|directions?|guidelines?)", re.I)),
    ("role hijack", re.compile(r"\b(you are now|from now on,? you are|pretend (to be|you are)|act as)\s+(an?\s+)?(unrestricted|jailbroken|dan\b|developer mode|evil|root|admin)", re.I)),
    ("jailbreak", re.compile(r"\b(jailbreak|do anything now|developer mode enabled|DAN mode)\b", re.I)),
    ("fake system tag", re.compile(r"(<\|im_start\|>|<\|im_end\|>|\[/?INST\]|<<SYS>>|</?\s*system\s*>|\bBEGIN SYSTEM PROMPT\b|\[SYSTEM\]\s*:)", re.I)),
    ("prompt extraction", re.compile(r"\b(reveal|print|show|repeat|leak)\s+(me\s+)?(your|the)\s+(system|hidden|secret|initial)\s+(prompt|instructions)", re.I)),
    ("data exfiltration", re.compile(r"\b(send|email|upload|post|forward|transfer)\s+(all\s+|the\s+|your\s+|my\s+)*(files?|passwords?|keys?|tokens?|credentials?|secrets?|memory|contacts)\s+(to|at)\b", re.I)),
    ("shell one-liner", re.compile(r"(curl|wget|iwr|invoke-webrequest)\b[^\n|]{0,200}\|\s*(sh|bash|iex|powershell|python)\b|powershell(\.exe)?\s+-(e|enc|encodedcommand)\s+[A-Za-z0-9+/=]{16,}", re.I)),
    ("tool hijack", re.compile(r"\b(run|execute|call)\s+(this|the following|these)\s+(command|code|script|tool)s?\b", re.I)),
]


def shield_text(text: str) -> tuple[str, list[str]]:
    """(neutralised text, names of the patterns that fired)."""
    if not text or not isinstance(text, str):
        return text, []
    hits = []
    for name, rx in INJECTION:
        if rx.search(text):
            hits.append(name)
            text = rx.sub(REMOVED, text)
    return text, hits


def sha256(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def parse_porcelain(out: str) -> dict:
    """`git status --porcelain=v1 --untracked-files=all` → modified / deleted / untracked lists."""
    mod, dele, unt = [], [], []
    for line in (out or "").splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if code == "??":
            unt.append(path)
        elif "D" in code:
            dele.append(path)
        else:
            mod.append(path)
    return {"modified": mod, "deleted": dele, "untracked": unt}


class Sentinel:
    def __init__(self, state: dict | None = None, root: Path = HERE, now=time.time, git=None, audit=None):
        self.state = state if state is not None else {}
        self.root, self.now, self._git, self._audit = root, now, git, audit
        self.events: deque = deque(maxlen=3000)
        self.alerted: dict[str, float] = {}
        self.bursts: deque = deque(maxlen=50)
        self.inj_counts: dict[str, int] = {}
        self.inj_recent: deque = deque(maxlen=40)
        self.inj_total = 0
        self.baseline = {f: sha256(root / f) for f in PERIMETER_FILES if (root / f).exists()}
        self.started = now()
        self.integrity_state: dict = {"checked_at": None, "verdict": "pending"}
        self._tamper_alerted: set[str] = set()

    # ── intrusion watch ──
    def record(self, kind: str, ip: str, path: str = "", why: str = "") -> None:
        t = self.now()
        ip = str(ip or "?")[:64]
        self.events.append({"t": t, "kind": kind if kind in KINDS else "unauth", "ip": ip,
                            "path": str(path or "").split("?", 1)[0][:80], "why": str(why or "")[:80]})
        n = sum(1 for e in self.events if e["ip"] == ip and t - e["t"] <= BURST_WINDOW)
        if n >= BURST_N and t - self.alerted.get(ip, -1e12) > BURST_COOLDOWN:
            self.alerted[ip] = t
            b = {"t": t, "ip": ip, "n": n, "kinds": sorted({e["kind"] for e in self.events if e["ip"] == ip and t - e["t"] <= BURST_WINDOW})}
            self.bursts.append(b)
            try:
                asyncio.get_running_loop().create_task(self._burst_alert(b))
            except RuntimeError:
                pass

    async def _burst_alert(self, b: dict) -> None:
        hub = self.state.get("hub")
        msg = f"{b['n']} refused requests from {b['ip']} in {BURST_WINDOW // 60} min ({', '.join(b['kinds'])})"
        try:
            if hub:
                await hub.broadcast({"type": "alert", "severity": "warning", "title": f"DOOM: intrusion burst from {b['ip']}",
                                     "detail": msg + ". The perimeter refused every one.", "source": "DOOM SENTINEL · perimeter",
                                     "action": "If this is not your own device, keep remote access off or change the access password."})
            if self._audit:
                await self._audit("doom.intrusion_burst", msg)
        except Exception:
            pass

    def intrusion(self) -> dict:
        t = self.now()
        day = [e for e in self.events if t - e["t"] <= 86400]
        by_kind: dict[str, int] = {}
        src: dict[str, dict] = {}
        for e in day:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
            s = src.setdefault(e["ip"], {"ip": e["ip"], "n": 0, "last": 0, "kinds": set()})
            s["n"] += 1; s["last"] = max(s["last"], e["t"]); s["kinds"].add(e["kind"])
        top = sorted(src.values(), key=lambda s: -s["n"])[:8]
        hist = [0] * 24                                        # refusals per hour, oldest → newest
        for e in day:
            h = int((t - e["t"]) // 3600)
            if 0 <= h < 24:
                hist[23 - h] += 1
        return {"total_24h": len(day), "last_hour": sum(1 for e in day if t - e["t"] <= 3600),
                "by_kind": {k: {"n": v, "label": KINDS.get(k, k)} for k, v in by_kind.items()},
                "top_sources": [{**s, "kinds": sorted(s["kinds"])} for s in top],
                "recent": list(self.events)[-15:][::-1], "hourly": hist,
                "bursts": [b for b in self.bursts if t - b["t"] <= 86400][::-1],
                "note": "memory only: IP, path (no query), reason, time; nothing else is kept"}

    # ── code integrity ──
    async def _git_status(self) -> str | None:
        if self._git:
            return await self._git()
        def run():
            try:
                r = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=self.root,
                                   capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return r.stdout if r.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                return None
        return await asyncio.to_thread(run)

    async def integrity(self) -> dict:
        changed = [f for f, h in self.baseline.items() if sha256(self.root / f) != h]
        out = await self._git_status()
        g = parse_porcelain(out) if out is not None else None
        code = lambda p: p.endswith(EXEC_SUFFIX) or p.endswith((".html", ".css"))
        res = {"checked_at": self.now(), "perimeter_files": sorted(self.baseline), "perimeter_changed": changed,
               "git": g is not None}
        if g is not None:
            res["modified"] = [p for p in g["modified"] if code(p)][:30]
            res["deleted"] = [p for p in g["deleted"] if code(p)][:30]
            res["untracked_exec"] = [p for p in g["untracked"] if p.endswith(EXEC_SUFFIX)][:30]
        res["verdict"] = ("tampered" if changed else
                          "review" if g is not None and (res["modified"] or res["deleted"] or res["untracked_exec"]) else
                          "intact" if g is not None else "partial")
        self.integrity_state = res
        fresh = [f for f in changed if f not in self._tamper_alerted]
        if fresh:
            self._tamper_alerted.update(fresh)
            hub = self.state.get("hub")
            try:
                if hub:
                    await hub.broadcast({"type": "alert", "severity": "critical", "title": "DOOM: security-perimeter code changed on disk",
                                         "detail": f"{', '.join(fresh)} changed while BUCS is running. Approved self-development deploys "
                                                   "also show here; anything you did not approve is tampering.",
                                         "source": "DOOM SENTINEL · code integrity", "action": "Review the change (git diff) and restart BUCS."})
                if self._audit:
                    await self._audit("doom.integrity_tamper", ", ".join(fresh))
            except Exception:
                pass
        return res

    def rebaseline(self, files: list[str] | None = None) -> None:
        """After an approved deploy the new bytes become the reference (called by the commander)."""
        for f in files or list(self.baseline):
            self.baseline[f] = sha256(self.root / f)
            self._tamper_alerted.discard(f)

    # ── injection shield ──
    def shield(self, text: str, source: str = "?") -> str:
        clean, hits = shield_text(text)
        if hits:
            self.inj_total += len(hits)
            self.inj_counts[source] = self.inj_counts.get(source, 0) + len(hits)
            for h in hits:
                self.inj_recent.append({"t": self.now(), "source": str(source)[:40], "pattern": h})
        return clean

    def injection(self) -> dict:
        t = self.now()
        return {"total": self.inj_total, "last_24h": sum(1 for r in self.inj_recent if t - r["t"] <= 86400),
                "by_source": dict(sorted(self.inj_counts.items(), key=lambda kv: -kv[1])[:10]),
                "recent": list(self.inj_recent)[-10:][::-1], "patterns": [n for n, _ in INJECTION]}

    # ── DEFCON ──
    def defcon(self) -> dict:
        t = self.now()
        reasons: list[tuple[int, str]] = []
        integ = self.integrity_state
        if integ.get("verdict") == "tampered":
            reasons.append((1, f"perimeter code changed on disk: {', '.join(integ['perimeter_changed'])}"))
        if any(t - b["t"] <= BURST_COOLDOWN for b in self.bursts):
            reasons.append((2, "an intrusion burst is in progress"))
        sa = self.state.get("secagent")
        try:
            ss = sa.snapshot() if sa else None
        except Exception:
            ss = None
        if ss:
            crit = (ss.get("counts") or {}).get("critical", 0)
            sc = ss.get("score")
            if crit:
                reasons.append((2, f"{crit} critical finding(s) from the cyber analyst"))
            if sc is not None and sc < 60:
                reasons.append((3, f"cyber analyst score {sc}/100"))
            elif sc is not None and sc < 80:
                reasons.append((4, f"cyber analyst score {sc}/100"))
        if integ.get("verdict") == "review":
            n = len(integ.get("modified", [])) + len(integ.get("deleted", [])) + len(integ.get("untracked_exec", []))
            reasons.append((3 if integ.get("untracked_exec") else 4, f"{n} code file(s) changed outside a commit"
                            + (f", incl. {len(integ['untracked_exec'])} new untracked executable/script(s)" if integ.get("untracked_exec") else "")))
        lh = sum(1 for e in self.events if t - e["t"] <= 3600)
        if lh >= 20:
            reasons.append((3, f"{lh} perimeter refusals in the last hour"))
        elif lh:
            reasons.append((4, f"{lh} perimeter refusal(s) in the last hour"))
        inj = self.injection()["last_24h"]
        if inj:
            reasons.append((4, f"{inj} prompt-injection attempt(s) neutralised in 24 h"))
        ps = self.state.get("privacy")
        try:
            psc = ps.snapshot().get("score") if ps else None
        except Exception:
            psc = None
        if psc is not None and psc < 70:
            reasons.append((4, f"privacy score {psc}/100"))
        level = min([r[0] for r in reasons], default=5)
        names = {5: "NORMAL", 4: "WATCH", 3: "ELEVATED", 2: "HIGH", 1: "MAXIMUM"}
        return {"level": level, "name": names[level], "reasons": [r[1] for r in sorted(reasons)],
                "inputs": {"cyber_score": ss.get("score") if ss else None, "privacy_score": psc,
                           "integrity": integ.get("verdict"), "refusals_last_hour": lh, "injections_24h": inj}}

    def snapshot(self) -> dict:
        return {"generated": self.now(), "uptime_s": round(self.now() - self.started), "defcon": self.defcon(),
                "intrusion": self.intrusion(), "integrity": self.integrity_state, "injection": self.injection()}

    def summary(self) -> str:
        d = self.defcon()
        i = self.intrusion()
        return (f"DOOM DEFCON {d['level']} ({d['name']}). {i['total_24h']} perimeter refusals in 24 h, "
                f"code integrity {self.integrity_state.get('verdict')}, {self.injection()['last_24h']} injection attempts neutralised. "
                + ("Reasons: " + "; ".join(d["reasons"][:3]) + "." if d["reasons"] else "No threats measured."))

    async def loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await self.integrity()
            except Exception as e:
                print(f"[sentinel] integrity check failed: {type(e).__name__}: {e}")
            await asyncio.sleep(INTEGRITY_EVERY)


_SINGLETON: Sentinel | None = None


def current() -> Sentinel | None:
    return _SINGLETON


def _engine(ctx) -> Sentinel:
    global _SINGLETON
    st = ctx["state"]
    if "doomsentinel" not in st:
        st["doomsentinel"] = Sentinel(st, audit=ctx.get("audit"))
        _SINGLETON = st["doomsentinel"]
    return st["doomsentinel"]


async def start(ctx) -> None:
    await _engine(ctx).loop()


def build_router(ctx) -> APIRouter:
    r = APIRouter()
    _engine(ctx)                                   # exists before the first request so the perimeter can record

    @r.get("/api/sentinel")
    async def sentinel_get():
        return _engine(ctx).snapshot()

    @r.post("/api/sentinel/scan")
    async def sentinel_scan():
        if not ctx["commander"]():
            return JSONResponse({"ok": False, "error": "OBSERVER role: switch to COMMANDER to run DOOM's integrity check."}, status_code=403)
        res = await _engine(ctx).integrity()
        return {"ok": True, "integrity": res, "defcon": _engine(ctx).defcon()}

    @r.post("/api/sentinel/rebaseline")
    async def sentinel_rebase():
        if not ctx["commander"]():
            return JSONResponse({"ok": False, "error": "OBSERVER role: switch to COMMANDER to accept the current perimeter code."}, status_code=403)
        eng = _engine(ctx)
        eng.rebaseline()
        try:
            await ctx["audit"]("doom.integrity_rebaseline", "commander accepted the current perimeter code as the reference")
        except Exception:
            pass
        return {"ok": True, "integrity": await eng.integrity()}

    return r
