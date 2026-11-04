"""AUTONOMOUS EVOLUTION · core (pure logic, no FastAPI, no I/O except the store).

  scrub()          remove credentials / personal data from any text we keep or show
  ErrorRecorder    logging.Handler: counts exceptions by signature (type @ repo file:line),
                   keeps only scrubbed messages and repo-relative frames — never values
  Signals          bounded time series of REAL measurements (key → [(ts, value)])
  analyzers        deterministic: error spikes, regressions, flaky checks, perf budgets,
                   security, voice, feedback, updates → candidate improvements with evidence
  Backlog          dedupe by key, trend, auto-resolve, priority score
  state machine    TRANSITIONS + guards: no deploy without approval AND a passing sandbox
  Store            .update/evolution.json, atomic writes, bounded sizes
"""

import hashlib
import json
import logging
import math
import re
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ═══ scrubbing ═══════════════════════════════════════════════════════════════
_SCRUB = [
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),                     # user:pass@host
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"), "<jwt>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "<key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "<key>"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{8,}"), "<key>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<key>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "<key>"),
    (re.compile(r"\bBUCS-[A-Za-z0-9_.=-]{8,}"), "<licence-key>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b([\w-]*(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|apikey|auth(?:orization)?|"
                r"client[_-]?secret|private[_-]?key|cookie|session)[\w-]*)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",;&}]+"),
     r"\1\2<redacted>"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s'\"]+"), "~"),
    (re.compile(r"/(?:home|Users)/[^/\s'\"]+"), "~"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "<hex>"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), "<blob>"),
]


def scrub(text, limit: int = 300, keep_space: bool = False) -> str:
    """Never let a credential, key, e-mail or user path reach the store, the UI or a prompt."""
    s = str(text if text is not None else "")
    for rx, rep in _SCRUB:
        s = rx.sub(rep, s)
    if not keep_space:
        s = " ".join(s.split())
    return s[:limit] + ("…" if len(s) > limit else "")


def short_hash(s: str, n: int = 10) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:n]


# ═══ error recorder ══════════════════════════════════════════════════════════
class ErrorRecorder(logging.Handler):
    """Counts errors by signature. Thread-safe (logging can fire from worker threads)."""

    MAX_SIGS = 200

    def __init__(self, root: Path = ROOT) -> None:
        super().__init__(level=logging.WARNING)
        self.root = str(Path(root).resolve()).lower().replace("\\", "/")
        self.sigs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.total = 0

    def _rel(self, filename: str) -> str | None:
        f = (filename or "").replace("\\", "/")
        fl = f.lower()
        if fl.startswith(self.root + "/") and "/.venv/" not in fl and "/site-packages/" not in fl:
            return f[len(self.root) + 1:]
        return None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith("evolution"):
                return
            exc = record.exc_info if record.exc_info and record.exc_info[0] else None
            if record.levelno < logging.ERROR and not exc:
                return
            if exc:
                etype, evalue, tb = exc
                frames = traceback.extract_tb(tb) if tb else []
                repo = [(self._rel(fr.filename), fr.lineno, fr.name) for fr in frames]
                repo = [r for r in repo if r[0]]
                if repo:
                    where = f"{repo[-1][0]}:{repo[-1][1]}"
                elif frames:
                    where = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}"
                else:
                    where = record.name
                kind = etype.__name__
                msg = scrub(evalue, 200)
                fr = [f"{p}:{n} in {fn}" for p, n, fn in repo[-6:]]
                sig = f"{kind}@{where}"
            else:
                template = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
                msg = scrub(template, 200)                    # the template, never the args
                kind, where, fr = "LogError", record.name, []
                sig = f"log:{record.name}:{short_hash(msg, 8)}"
            self.record(sig, kind, where, msg, fr, record.name)
        except Exception:
            pass

    def record(self, sig, kind, where, msg, frames, logger="", ts=None) -> None:
        ts = ts or time.time()
        with self._lock:
            self.total += 1
            e = self.sigs.get(sig)
            if e is None:
                if len(self.sigs) >= self.MAX_SIGS:          # evict the stalest signature
                    old = min(self.sigs.values(), key=lambda x: x["last"])
                    self.sigs.pop(old["sig"], None)
                e = self.sigs[sig] = {"sig": sig, "type": kind, "where": where, "logger": logger,
                                      "count": 0, "first": ts, "last": ts, "msg": msg, "frames": frames,
                                      "times": []}
            e["count"] += 1
            e["last"] = ts
            e["msg"] = msg or e["msg"]
            if frames:
                e["frames"] = frames
            e["times"].append(round(ts, 1))
            del e["times"][:-120]

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(v, times=list(v["times"])) for v in self.sigs.values()]

    def load(self, items: list[dict]) -> None:
        with self._lock:
            for v in items or []:
                if isinstance(v, dict) and v.get("sig"):
                    self.sigs[v["sig"]] = v


def install_error_recorder(rec: ErrorRecorder) -> None:
    """Attach to the root logger and uvicorn's (which does not propagate). Idempotent."""
    for name in ("", "uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        if not any(isinstance(h, ErrorRecorder) for h in lg.handlers):
            lg.addHandler(rec)
    # uvicorn.error propagates to uvicorn → avoid double counting
    ue = logging.getLogger("uvicorn.error")
    if ue.propagate:
        for h in list(ue.handlers):
            if isinstance(h, ErrorRecorder):
                ue.removeHandler(h)


# ═══ signals ═════════════════════════════════════════════════════════════════
class Signals:
    MAX_POINTS = 600
    MAX_KEYS = 400

    def __init__(self) -> None:
        self.series: dict[str, list] = {}
        self.latest: dict[str, dict] = {}
        self.events: list[dict] = []

    def add(self, key: str, value, ts: float | None = None, detail: str = "") -> None:
        if value is None:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if math.isnan(v) or math.isinf(v):
            return
        ts = ts or time.time()
        if key not in self.series and len(self.series) >= self.MAX_KEYS:
            stale = min(self.latest.items(), key=lambda kv: kv[1]["ts"])[0] if self.latest else next(iter(self.series))
            self.series.pop(stale, None)
            self.latest.pop(stale, None)
        s = self.series.setdefault(key, [])
        s.append([round(ts, 1), round(v, 3)])
        del s[:-self.MAX_POINTS]
        self.latest[key] = {"ts": ts, "value": round(v, 3), "detail": scrub(detail, 160) if detail else ""}

    def event(self, kind: str, msg: str, ts: float | None = None) -> None:
        self.events.insert(0, {"ts": ts or time.time(), "kind": kind, "msg": scrub(msg, 200)})
        del self.events[200:]

    def values(self, key: str, since: float = 0, until: float | None = None) -> list[float]:
        until = until or float("inf")
        return [v for t, v in self.series.get(key, []) if since <= t <= until]

    def keys(self, prefix: str = "") -> list[str]:
        return [k for k in self.series if k.startswith(prefix)]

    def to_json(self) -> dict:
        return {"series": self.series, "latest": self.latest, "events": self.events[:100]}

    def load(self, d: dict) -> None:
        d = d or {}
        self.series = {k: v[-self.MAX_POINTS:] for k, v in (d.get("series") or {}).items() if isinstance(v, list)}
        self.latest = d.get("latest") or {}
        self.events = (d.get("events") or [])[:200]


def pct(xs, p: float):
    xs = sorted(xs)
    if not xs:
        return None
    k = p * (len(xs) - 1)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


# ═══ analyzers (deterministic; every number in an evidence line was measured) ═
def cand(key, kind, title, evidence, impact, risk, effort, confidence, metric=None, code_level=False,
         task=None, recommendation=None, source="analyzer", value=None) -> dict:
    return {"key": key, "kind": kind, "title": scrub(title, 140), "evidence": [scrub(e, 240) for e in evidence][:8],
            "impact": int(max(1, min(5, impact))), "risk": int(max(1, min(5, risk))),
            "effort": int(max(1, min(5, effort))), "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "metric": metric, "code_level": bool(code_level and task), "task": scrub(task, 880) if task else None,
            "recommendation": scrub(recommendation, 300) if recommendation else None, "source": source,
            "value": value}


def analyze_errors(errors: list[dict], now: float, hour: float = 3600) -> list[dict]:
    out = []
    for e in errors:
        times = e.get("times") or []
        recent = sum(1 for t in times if t >= now - hour)
        day = sum(1 for t in times if now - 24 * hour <= t < now - hour)
        prior_rate = day / 23.0
        in_repo = bool(e.get("frames")) or (e.get("where", "").split(":")[0].endswith(".py")
                                           and not e.get("where", "").startswith("log:"))
        spike = recent >= 3 and recent >= 3 * max(prior_rate, 0.34)
        persistent = recent + day >= 10
        fresh = e.get("first", 0) >= now - 24 * hour and recent >= 1
        if not (spike or persistent or fresh):
            continue
        sig = e["sig"]
        ev = [f"{e.get('type')} at {e.get('where')}: {recent} in the last hour, {day} in the 23 h before "
              f"({e.get('count')} since tracking began)",
              f"last message: {e.get('msg') or '—'}"]
        if e.get("frames"):
            ev.append("frames: " + " ← ".join(reversed(e["frames"][-4:])))
        if spike:
            ev.append(f"spike: {recent}/h vs baseline {prior_rate:.2f}/h")
        impact = 4 if spike else 3 if persistent else 2
        task = None
        if in_repo and e.get("frames"):
            task = (f"BUCS recorded a recurring exception {e.get('type')} at {e.get('where')} "
                    f"({recent + day} times in 24 h; last message: '{e.get('msg')}'). "
                    f"Traceback (repo frames, innermost last): {' | '.join(e['frames'][-5:])}. "
                    "Find the root cause and propose the minimal fix. Do not silence the exception blindly; "
                    "keep existing behaviour and tests green.")
        out.append(cand("error:" + sig, "error", f"{'Error spike' if spike else 'Recurring error'}: "
                        f"{e.get('type')} at {e.get('where')}", ev, impact, 2, 2,
                        0.9 if spike else 0.75, metric={"key": "errors:" + sig, "dir": "down", "agg": "sum"},
                        code_level=bool(task), task=task,
                        recommendation=None if task else "Inspect the server log around these timestamps.",
                        value=recent))
    return out


DEFAULT_BUDGETS = {"endpoint": 400.0, "loop_lag": 250.0, "engine": {"control": 600.0, "action": 1500.0,
                                                                    "default": 15000.0}}


def analyze_perf(sig: Signals, now: float, budgets: dict | None = None, window: float = 3600) -> list[dict]:
    b = {**DEFAULT_BUDGETS, **(budgets or {})}
    out = []
    for key in sig.keys("endpoint:"):
        xs = sig.values(key, now - window)
        if len(xs) < 5:
            continue
        p95, p50 = pct(xs, 0.95), pct(xs, 0.5)
        budget = b["endpoint"]
        if p95 > budget:
            path = key.split(":", 1)[1]
            out.append(cand("perf:" + path, "performance", f"Slow endpoint {path}: p95 {p95:.0f} ms (budget {budget:.0f})",
                            [f"{len(xs)} samples in the last {window / 60:.0f} min: p50 {p50:.0f} ms, p95 {p95:.0f} ms, "
                             f"max {max(xs):.0f} ms", f"budget {budget:.0f} ms"],
                            3 if p95 < 3 * budget else 4, 2, 3, min(0.95, 0.5 + len(xs) / 40),
                            metric={"key": key, "dir": "down", "agg": "p95"}, code_level=True,
                            task=(f"The BUCS endpoint {path} is slow: measured p95 {p95:.0f} ms, p50 {p50:.0f} ms over "
                                  f"{len(xs)} samples (budget {budget:.0f} ms). Find what blocks or is slow in its "
                                  "handler (CLAUDE.md: anything >50 ms must run off the event loop and be cached "
                                  "if polled) and propose the minimal fix."), value=round(p95)))
    xs = sig.values("selfheal:event_loop_ms", now - window)
    if len(xs) >= 5 and pct(xs, 0.5) > b["loop_lag"]:
        m = pct(xs, 0.5)
        out.append(cand("perf:event_loop", "performance", f"Event loop lag median {m:.0f} ms",
                        [f"{len(xs)} self-heal measurements: median {m:.0f} ms, max {max(xs):.0f} ms",
                         f"budget {b['loop_lag']:.0f} ms"], 4, 3, 3, 0.7,
                        metric={"key": "selfheal:event_loop_ms", "dir": "down", "agg": "mean"}, code_level=True,
                        task=(f"The asyncio event loop of BUCS shows median scheduling lag {m:.0f} ms "
                              f"(max {max(xs):.0f} ms). Find blocking work on the loop and move it off "
                              "(asyncio.to_thread) without changing behaviour."), value=round(m)))
    eb = b["engine"]
    for key in sig.keys("engine:"):
        if not key.endswith(":p95"):
            continue
        path = key.split(":")[1]
        xs = sig.values(key, now - window)
        if len(xs) < 3:
            continue
        budget = eb.get(path, eb["default"])
        last = xs[-1]
        if last > budget:
            out.append(cand("perf:engine:" + path, "performance", f"Command engine '{path}' route p95 {last:.0f} ms",
                            [f"realtime engine p95 for route '{path}' = {last:.0f} ms (budget {budget:.0f} ms)"],
                            2, 3, 3, 0.55, metric={"key": key, "dir": "down", "agg": "mean"},
                            recommendation="Check the provider/model behind this route (LLM switch) before code changes.",
                            value=round(last)))
    return out


STATUS_VAL = {"ok": 0, "idle": 0, "unknown": 0, "warn": 1, "fail": 2, "error": 2}


def analyze_checks(sig: Signals, now: float) -> list[dict]:
    """Regressions (ok → fail) and flaky checks (status flips) from self-heal results."""
    out = []
    for key in sig.keys("check:"):
        name = key.split(":", 1)[1]
        pts = sig.series.get(key, [])[-20:]
        vals = [v for _, v in pts]
        if len(vals) < 3:
            continue
        flips = sum(1 for a, b2 in zip(vals, vals[1:]) if (a > 0) != (b2 > 0))
        last = vals[-1]
        prior = vals[:-1]
        detail = (sig.latest.get(key) or {}).get("detail", "")
        if last >= 2 and sum(1 for v in prior if v == 0) >= len(prior) * 0.6:
            out.append(cand("regression:check:" + name, "regression", f"Health check '{name}' regressed to FAIL",
                            [f"last {len(prior)} results mostly OK, now FAIL", f"detail: {detail}"],
                            4, 2, 2, 0.8, metric={"key": key, "dir": "down", "agg": "mean"},
                            recommendation="Self-heal repairs what it safely can; open SELF-HEALING for the AI diagnosis.",
                            value=last))
        elif flips >= 4:
            out.append(cand("flaky:check:" + name, "flaky", f"Health check '{name}' is flapping ({flips} flips)",
                            [f"{flips} status changes in the last {len(vals)} measurements", f"latest: {detail}"],
                            2, 1, 2, 0.6, metric={"key": key, "dir": "down", "agg": "mean"},
                            recommendation="Look for an intermittent dependency (network, device) behind this check.",
                            value=flips))
        elif last >= 2 and all(v >= 2 for v in vals[-3:]):
            out.append(cand("failing:check:" + name, "reliability", f"Health check '{name}' keeps failing",
                            [f"FAIL for the last {sum(1 for v in reversed(vals) if v >= 2)} measurements",
                             f"detail: {detail}"], 3, 2, 2, 0.75,
                            metric={"key": key, "dir": "down", "agg": "mean"},
                            recommendation="See the SELF-HEALING panel; hand its diagnosis to engineering if code is at fault.",
                            value=last))
    return out


def analyze_tests(last: dict | None, prev: dict | None) -> list[dict]:
    if not last or last.get("skipped"):
        return []
    out = []
    failing = last.get("failing") or []
    before = set((prev or {}).get("failing") or [])
    new = [t for t in failing if t not in before] if prev else []
    if failing:
        ev = [f"test run {time.strftime('%Y-%m-%d %H:%M', time.localtime(last.get('ts', 0)))}: ran {last.get('ran')}, "
              f"{len(failing)} failing", "failing: " + ", ".join(failing[:8])]
        if new:
            ev.append("newly failing since the previous run: " + ", ".join(new[:6]))
        out.append(cand("tests:failing", "regression" if new else "reliability",
                        f"{len(failing)} unit test{'s' if len(failing) != 1 else ''} failing" + (" (regression)" if new else ""),
                        ev, 5 if new else 4, 2, 3, 0.9, metric={"key": "tests:failed", "dir": "down", "agg": "last"},
                        code_level=True,
                        task=("The BUCS unit test suite has failing tests: " + ", ".join(failing[:8]) +
                              ". Output tail: " + scrub(last.get("tail", ""), 300) +
                              ". Find the cause in the application code (never edit tests/) and propose the minimal fix."),
                        value=len(failing)))
    flaky = (last.get("flaky") or [])
    for t in flaky[:3]:
        out.append(cand("flaky:test:" + t, "flaky", f"Flaky test {t}",
                        [f"{t} changed outcome between consecutive runs without a code change"], 2, 1, 2, 0.5,
                        recommendation="Tests are never changed by engineering mode; fix the flakiness by hand."))
    return out


def analyze_security(findings: list[dict]) -> list[dict]:
    out = []
    for f in findings or []:
        if f.get("severity") not in ("critical", "high"):
            continue
        imp = 5 if f["severity"] == "critical" else 4
        out.append(cand("sec:" + str(f.get("id")), "security", f"[{f['severity'].upper()}] {f.get('title')}",
                        [f"evidence: {f.get('evidence')}", f"fix: {f.get('fix')}"], imp, 2, 2, 0.85,
                        metric={"key": "secagent:active_high", "dir": "down", "agg": "last"},
                        code_level=bool(f.get("task")), task=f.get("task"),
                        recommendation=None if f.get("task") else f.get("fix"), source="secagent"))
    return out


def analyze_voice(diag: dict | None) -> list[dict]:
    if not diag or not isinstance(diag, dict):
        return []
    utt = [u for u in diag.get("utterances") or [] if isinstance(u, dict)]
    if len(utt) < 8:
        return []
    out = []
    ignored = sum(1 for u in utt if str(u.get("outcome", "")).startswith("ignored"))
    ratio = ignored / len(utt)
    peaks = [u.get("peak") for u in utt if isinstance(u.get("peak"), (int, float))]
    if ratio > 0.5:
        ev = [f"{ignored}/{len(utt)} recent utterances ignored (empty / phantom)"]
        if peaks:
            ev.append(f"median peak {pct(peaks, 0.5):.0f} / 32767")
        out.append(cand("voice:ignored", "voice", f"Voice: {ratio:.0%} of utterances ignored", ev, 3, 1, 1, 0.7,
                        metric={"key": "voice:ignored_ratio", "dir": "down", "agg": "mean"},
                        recommendation="Raise the Windows microphone input level first (CLAUDE.md field data), "
                                       "then re-check /api/voice/diagnostics.", value=round(ratio, 2)))
    stt = [u.get("stt_ms") for u in utt if isinstance(u.get("stt_ms"), (int, float))]
    if len(stt) >= 8 and pct(stt, 0.5) > 2500:
        m = pct(stt, 0.5)
        out.append(cand("voice:slow-stt", "performance", f"Voice: speech-to-text median {m:.0f} ms",
                        [f"{len(stt)} utterances, STT median {m:.0f} ms, max {max(stt):.0f} ms"], 3, 2, 2, 0.7,
                        metric={"key": "voice:stt_ms_p50", "dir": "down", "agg": "mean"},
                        recommendation="Use a smaller Whisper model or enable GPU decoding.", value=round(m)))
    return out


def analyze_feedback(feedback: list[dict], now: float) -> list[dict]:
    by: dict[str, dict] = {}
    for f in feedback or []:
        if f.get("ts", 0) < now - 7 * 86400:
            continue
        a = by.setdefault(f.get("area") or "general", {"up": 0, "down": 0, "notes": []})
        a[f.get("vote", "up")] = a.get(f.get("vote", "up"), 0) + 1
        if f.get("vote") == "down" and f.get("note"):
            a["notes"].append(f["note"])
    out = []
    for area, a in by.items():
        if a["down"] >= 2 and a["down"] > a["up"]:
            notes = a["notes"][-4:]
            out.append(cand("feedback:" + area, "feedback", f"Operator feedback: '{area}' rated down {a['down']}×",
                            [f"last 7 days: {a['down']} 👎 vs {a['up']} 👍 for '{area}'"] +
                            [f"note: {n}" for n in notes], 3, 2, 3, 0.6,
                            metric={"key": "feedback:" + area, "dir": "up", "agg": "sum"},
                            code_level=bool(notes),
                            task=(f"The BUCS operator rated the '{area}' area down {a['down']} times this week. "
                                  "Their notes: " + " / ".join(notes) + ". Diagnose what in the code causes this "
                                  "and propose the minimal improvement.") if notes else None,
                            recommendation=None if notes else "Add a note to your 👎 so the analyst knows what to fix.",
                            source="feedback"))
    return out


def analyze_updates(update: dict | None, outdated: list[dict]) -> list[dict]:
    out = []
    if update and update.get("available") and update.get("latest"):
        out.append(cand("update:" + str(update["latest"]), "update", f"BUCS {update['latest']} is available",
                        [f"installed {update.get('version')}, signed release {update['latest']} published "
                         f"{update.get('date') or ''}"], 3, 2, 1, 0.9,
                        recommendation="Install it from SYSTEM → UPDATE (signed, auto-rollback)."))
    for d in outdated or []:
        major = str(d.get("latest", "")).split(".")[0] != str(d.get("installed", "")).split(".")[0]
        out.append(cand("dep:" + d["name"], "dependency",
                        f"{d['name']} {d.get('installed')} → {d.get('latest')} available",
                        [f"installed {d.get('installed')}, PyPI latest {d.get('latest')} (checked "
                         f"{time.strftime('%Y-%m-%d', time.localtime(d.get('ts', time.time())))})"] +
                        ([f"release notes: {d['notes_url']}"] if d.get("notes_url") else []),
                        2, 4 if major else 2, 2, 0.8, source="research",
                        recommendation=f"Review the {d['name']} release notes, then upgrade in a test copy "
                                       "(pip install -U) and run the suite before pinning it in requirements.txt."))
    return out


# ═══ backlog + state machine ═════════════════════════════════════════════════
LOOP = ["observe", "analyze", "identify", "propose", "test", "approval", "deploy", "evaluate"]
STAGE_LOOP = {"identified": "identify", "proposing": "propose", "proposed": "propose", "diagnosed": "propose",
              "sandboxing": "test", "sandbox_failed": "test", "awaiting_approval": "approval",
              "deploying": "deploy", "evaluating": "evaluate", "regressed": "evaluate", "reverting": "evaluate"}
ACTIVE = {"proposing", "proposed", "sandboxing", "awaiting_approval", "deploying", "evaluating", "regressed",
          "reverting"}
CLOSED = {"kept", "reverted", "rejected", "dismissed", "resolved"}
TRANSITIONS = {
    "identified": {"proposing", "dismissed", "resolved"},
    "proposing": {"proposed", "diagnosed", "failed"},
    "proposed": {"sandboxing", "rejected", "dismissed"},
    "diagnosed": {"proposing", "dismissed", "resolved"},
    "sandboxing": {"awaiting_approval", "sandbox_failed", "failed"},
    "sandbox_failed": {"sandboxing", "proposing", "dismissed"},
    "awaiting_approval": {"deploying", "rejected"},
    "deploying": {"evaluating", "failed"},
    "evaluating": {"kept", "regressed", "reverting"},
    "regressed": {"reverting", "kept"},
    "kept": {"reverting"},
    "reverting": {"reverted", "failed"},
    "failed": {"proposing", "dismissed"},
    "rejected": {"proposing", "dismissed"},
    "reverted": {"proposing", "dismissed"},
    "dismissed": {"identified"},
    "resolved": {"identified"},
}


class TransitionError(Exception):
    pass


def transition(item: dict, to: str, note: str = "", by: str = "system") -> dict:
    frm = item.get("stage")
    if to not in TRANSITIONS.get(frm, set()):
        raise TransitionError(f"cannot go from {frm} to {to}")
    if to == "deploying":
        ap = item.get("approval") or {}
        sb = item.get("sandbox") or {}
        if not ap.get("by") or ap.get("decision") != "approved":
            raise TransitionError("deploy needs the commander's explicit approval")
        if sb.get("ok") is not True:
            raise TransitionError("deploy needs a passing sandbox test")
        if ap.get("sandbox_id") != sb.get("id"):
            raise TransitionError("approval was given for a different sandbox result")
    if to == "awaiting_approval" and (item.get("sandbox") or {}).get("ok") is not True:
        raise TransitionError("only a passing sandbox result can be put up for approval")
    item["stage"] = to
    item["updated"] = time.time()
    item.setdefault("timeline", []).append({"ts": item["updated"], "stage": to, "note": scrub(note, 200), "by": by})
    del item["timeline"][:-30]
    return item


def priority(it: dict) -> float:
    return round((it["impact"] * 2 + (6 - it["risk"]) + (6 - it["effort"])) * it["confidence"], 2)


class Backlog:
    MAX = 300
    RESOLVE_AFTER = 3              # analyses in a row without the signal → auto-resolved

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def by_key(self, key: str) -> dict | None:
        live = [i for i in self.items.values() if i["key"] == key and i["stage"] not in ("resolved", "kept", "reverted")]
        return max(live, key=lambda i: i["created"]) if live else None

    def upsert_all(self, cands: list[dict], now: float | None = None) -> dict:
        now = now or time.time()
        seen, new, updated = set(), [], []
        for c in cands:
            if c["key"] in seen:
                continue
            seen.add(c["key"])
            it = self.by_key(c["key"])
            if it and it["stage"] == "dismissed":
                if it.get("snooze_until", 0) > now:
                    continue
                transition(it, "identified", "condition still present after the snooze")
            if it:
                for f in ("title", "evidence", "impact", "risk", "effort", "confidence", "metric", "code_level",
                          "task", "recommendation"):
                    if it["stage"] in ("identified", "diagnosed", "failed") or f in ("evidence", "confidence"):
                        it[f] = c[f]
                it["seen"] += 1
                it["last_seen"] = now
                it["misses"] = 0
                if c.get("value") is not None:
                    it["trend"].append([round(now), c["value"]])
                    del it["trend"][:-40]
                it["priority"] = priority(it)
                updated.append(it["id"])
            else:
                iid = short_hash(c["key"] + str(now), 8)
                it = {**c, "id": iid, "stage": "identified", "created": now, "updated": now, "last_seen": now,
                      "seen": 1, "misses": 0, "trend": [[round(now), c["value"]]] if c.get("value") is not None else [],
                      "timeline": [{"ts": now, "stage": "identified", "note": "detected by " + c["source"], "by": "system"}]}
                it["priority"] = priority(it)
                self.items[iid] = it
                new.append(iid)
        resolved = []
        for it in self.items.values():
            if it["key"] in seen or it["stage"] != "identified":
                continue
            it["misses"] = it.get("misses", 0) + 1
            if it["misses"] >= self.RESOLVE_AFTER:
                transition(it, "resolved", f"signal absent for {it['misses']} analyses")
                resolved.append(it["id"])
        self._trim()
        return {"new": new, "updated": updated, "resolved": resolved}

    def _trim(self) -> None:
        if len(self.items) <= self.MAX:
            return
        closed = sorted((i for i in self.items.values() if i["stage"] in CLOSED), key=lambda i: i["updated"])
        for i in closed[:len(self.items) - self.MAX]:
            self.items.pop(i["id"], None)

    def ranked(self) -> list[dict]:
        order = {"awaiting_approval": 0, "regressed": 1, "evaluating": 2, "sandboxing": 3, "deploying": 3,
                 "reverting": 3, "proposing": 4, "proposed": 4, "sandbox_failed": 5, "identified": 6,
                 "diagnosed": 7, "failed": 8}
        return sorted(self.items.values(), key=lambda i: (order.get(i["stage"], 20), -i.get("priority", 0),
                                                          -i.get("updated", 0)))


# ═══ evaluation ══════════════════════════════════════════════════════════════
def aggregate(xs: list[float], agg: str):
    if not xs:
        return None
    if agg == "p95":
        return pct(xs, 0.95)
    if agg == "sum":
        return float(sum(xs))
    if agg == "last":
        return xs[-1]
    return mean(xs)


def verdict(baseline, after, direction: str = "down", tolerance: float = 0.2, min_abs: float = 1.0) -> str:
    """improved | neutral | regressed | insufficient-data — lower is better for 'down'."""
    if baseline is None or after is None:
        return "insufficient-data"
    delta = (after - baseline) if direction == "down" else (baseline - after)
    scale = max(abs(baseline), min_abs)
    if delta > max(tolerance * scale, min_abs * 0.5) and delta >= min_abs:
        return "regressed"
    if -delta > max(tolerance * scale, min_abs * 0.5):
        return "improved"
    return "neutral"


# ═══ persistence ═════════════════════════════════════════════════════════════
class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self, data: dict) -> bool:
        try:
            from atomicio import atomic_write_text
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps(data, separators=(",", ":"), default=str))
            return True
        except Exception:
            return False
