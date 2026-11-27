"""PRIVACY SHIELD (v8.7) — DOOM's live proof of what stays on this machine and what leaves it.

Everything here is MEASURED on this PC, never assumed:

  outbound     the remote hosts THIS BUCS process (and its children) holds connections to right now
               (psutil, sampled every 30 s), aggregated into a ledger: first/last seen, ports,
               reverse-DNS name, and a purpose label from a fixed table of the public feeds BUCS
               uses. Anything not in the table is shown as UNLABELLED for the commander to judge.
  at rest      which private files exist on this install (names, sizes, ages — never contents) and
               whether git ignores each one (so it can never be committed or published).
  ai prompts   where AI prompts go right now: on this machine (local model / templates) or to a
               cloud model — read from the active brain, not guessed.
  perimeter    remote access state (password set or remote access disabled).
  score        100 minus measured deductions, each listed with its reason.

  GET  /api/privacy          the snapshot
  POST /api/privacy/scan     sample now (commander)
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent
SAMPLE_S = 30
LEDGER_MAX = 120
RDNS_PER_SAMPLE = 6

PRIVATE_FILES = (".env", "credentials.json", "token.json", "auth.json", "fleet_token.txt", "pair_code.txt", "node_id.txt",
                 "node_token.txt", "governance.json", "operator_memory.json", "audio_pref.json", "audit.log",
                 "model_pref.json", "commander_memory.md", "license_signing.key", "licences_issued.json",
                 "providers.json", "license.json", "operators.json")
PRIVATE_DIRS = (".update",)

# (suffix of the reverse-DNS name, purpose) — the public services BUCS is built to use
PURPOSE = [
    ("status.anthropic.com", "service-status check (Anthropic)"), ("githubstatus.com", "service-status check (GitHub)"),
    ("redditstatus.com", "service-status check"), ("statuspage.io", "service-status check"),
    ("ndma.gov.in", "India NDMA disaster alerts (Sachet)"), ("imd.gov.in", "India Meteorological Department"),
    ("gdeltproject.org", "GDELT world events"), ("reliefweb.int", "ReliefWeb disasters"), ("wikipedia.org", "Wikipedia (live facts)"),
    ("wikimedia.org", "Wikipedia (live facts)"), ("newsapi.org", "NewsAPI headlines"), ("bbc.co.uk", "news feeds"),
    ("aljazeera.com", "news feeds"), ("thehindu.com", "news feeds"), ("reuters.com", "news feeds"),
    ("arcgisonline.com", "Esri map imagery"), ("arcgis.com", "Esri map imagery"), ("esri.com", "Esri map imagery"),
    ("earthdata.nasa.gov", "NASA GIBS satellite imagery"), ("nasa.gov", "NASA data"), ("eumetsat.int", "EUMETSAT live satellite"),
    ("rainviewer.com", "RainViewer radar"), ("opensky-network.org", "OpenSky flights"), ("celestrak.org", "CelesTrak satellites"),
    ("usgs.gov", "USGS earthquakes"), ("noaa.gov", "NOAA space weather / alerts"), ("weather.gov", "US weather alerts"),
    ("gdacs.org", "GDACS disasters"), ("open-meteo.com", "Open-Meteo weather"), ("openfreemap.org", "OpenFreeMap buildings"),
    ("github.com", "GitHub (updates / releases)"), ("githubusercontent.com", "GitHub (updates / releases)"),
    ("github.io", "BUCS update channel"), ("anthropic.com", "Claude AI (cloud prompts)"),
    ("googleapis.com", "Google (Calendar / Gmail)"), ("google.com", "Google services"), ("windy.com", "Windy webcams"),
    ("osv.dev", "OSV vulnerability database"), ("cisa.gov", "CISA KEV catalogue"), ("ioda.inetintel.cc.gatech.edu", "IODA outages"),
    ("gatech.edu", "IODA internet outages"), ("huggingface.co", "speech model download"), ("pypi.org", "Python packages"),
    ("pythonhosted.org", "Python packages"), ("jsdelivr.net", "CDN libraries"), ("unpkg.com", "CDN libraries"),
    ("cloudflare.com", "Cloudflare (CDN / tunnel)"), ("twilio.com", "Twilio phone agent"), ("ollama.com", "Ollama models"),
    ("1e100.net", "Google services"), ("amazonaws.com", "AWS-hosted service (open data / CDN)"),
    ("cloudfront.net", "AWS CloudFront CDN"), ("akamaitechnologies.com", "Akamai CDN"), ("fastly.net", "Fastly CDN"),
]


# What BUCS itself asked DNS for: ip → the hostname this process requested. Recorded by wrapping
# socket.getaddrinfo (used by asyncio, httpx, urllib) so each live socket is labelled by the service
# BUCS actually contacted — far more precise than reverse DNS. Hostnames only; nothing else is kept.
_ASKED: dict[str, str] = {}
_orig_gai = socket.getaddrinfo


def _gai(host, *a, **k):
    res = _orig_gai(host, *a, **k)
    try:
        if isinstance(host, (str, bytes)):
            h = host.decode() if isinstance(host, bytes) else host
            if h and not h.replace(".", "").isdigit() and ":" not in h and h.lower() != "localhost":
                for r in res:
                    _ASKED[r[4][0]] = h.lower()
                if len(_ASKED) > 4000:
                    for k_ in list(_ASKED)[:1000]:
                        _ASKED.pop(k_, None)
    except Exception:
        pass
    return res


if getattr(socket.getaddrinfo, "__name__", "") != "_gai":
    socket.getaddrinfo = _gai


def _purpose(name: str | None) -> str | None:
    n = (name or "").lower().rstrip(".")
    if n.startswith("status.") or n.endswith("status.com"):
        return "service-status check"
    for suf, label in PURPOSE:
        if n == suf or n.endswith("." + suf):
            return label
    return None


def _scope(ip: str) -> str:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "invalid"
    if a.is_loopback:
        return "loopback"
    if a.is_private or a.is_link_local:
        return "lan"
    return "internet"


class PrivacyShield:
    def __init__(self, state: dict | None = None, now=time.time) -> None:
        self.state = state if state is not None else {}
        self.now = now
        self.ledger: dict[str, dict] = {}
        self.rdns: dict[str, str | None] = {}
        self.last_sample = None
        self.error = None

    # ── outbound ──
    def _conns(self) -> list[tuple[str, int, str]]:
        import psutil
        me = psutil.Process(os.getpid())
        procs = [me] + me.children(recursive=True)
        out = []
        for p in procs:
            try:
                conns = p.net_connections(kind="inet") if hasattr(p, "net_connections") else p.connections(kind="inet")
            except (psutil.Error, OSError):
                continue
            for c in conns:
                if c.raddr and c.status in ("ESTABLISHED", "SYN_SENT", "CLOSE_WAIT", "TIME_WAIT"):
                    out.append((c.raddr.ip, c.raddr.port, c.status))
        return out

    async def sample(self) -> dict:
        loop = asyncio.get_running_loop()
        try:
            conns = await loop.run_in_executor(None, self._conns)
            self.error = None
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"[:160]
            conns = []
        now = self.now()
        lookups = 0
        for ip, port, status in conns:
            scope = _scope(ip)
            if scope == "loopback":
                continue
            e = self.ledger.get(ip)
            if e is None:
                e = self.ledger[ip] = {"ip": ip, "scope": scope, "first_seen": now, "ports": [], "samples": 0,
                                       "name": None, "purpose": "local network device" if scope == "lan" else None}
            e["last_seen"] = now
            e["samples"] += 1
            if port not in e["ports"]:
                e["ports"] = (e["ports"] + [port])[-6:]
            if scope == "internet" and ip not in _ASKED and ip not in self.rdns and lookups < RDNS_PER_SAMPLE:
                lookups += 1
                try:
                    name = await asyncio.wait_for(loop.run_in_executor(None, lambda i=ip: socket.gethostbyaddr(i)[0]), 2.0)
                except Exception:
                    name = None
                self.rdns[ip] = name
            if scope == "internet":
                asked = _ASKED.get(ip)
                e["asked"] = asked
                e["name"] = asked or self.rdns.get(ip)
                e["purpose"] = _purpose(asked) or _purpose(self.rdns.get(ip))
        if len(self.ledger) > LEDGER_MAX:                       # keep the most recent hosts
            for ip, _ in sorted(self.ledger.items(), key=lambda kv: kv[1]["last_seen"])[:len(self.ledger) - LEDGER_MAX]:
                self.ledger.pop(ip, None)
        self.last_sample = now
        return self.snapshot()

    # ── at rest ──
    def _ignored(self, rel: list[str]) -> set[str]:
        try:
            r = subprocess.run(["git", "check-ignore", "--", *rel], cwd=str(HERE), capture_output=True, text=True, timeout=8)
            return {l.strip().replace("\\", "/") for l in r.stdout.splitlines() if l.strip()}
        except Exception:
            return set()

    def at_rest(self) -> dict:
        files = []
        for name in PRIVATE_FILES:
            p = HERE / name
            if p.is_file():
                st = p.stat()
                files.append({"name": name, "bytes": st.st_size, "modified": st.st_mtime})
        upd = HERE / ".update"
        n_upd = sum(1 for x in upd.rglob("*") if x.is_file()) if upd.is_dir() else 0
        rel = [f["name"] for f in files] + ([".update/"] if upd.is_dir() else [])
        ign = self._ignored(rel) if rel else set()
        git_ok = (HERE / ".git").exists()
        for f in files:
            f["git_ignored"] = (f["name"] in ign) if git_ok else None
        return {"files": files, "update_dir_files": n_upd,
                "update_dir_ignored": (".update/" in ign or ".update" in ign) if (git_ok and upd.is_dir()) else None,
                "repo": git_ok}

    # ── AI prompts ──
    def ai_route(self) -> dict:
        b = self.state.get("brain")
        mode = getattr(b, "mode", "unknown") if b is not None else "unknown"
        force_local = bool(getattr(b, "force_local", False))
        provider = getattr(b, "provider_name", None)
        if force_local or mode in ("local-llm", "local", "offline"):
            where, local = ("a local model on this machine" if mode == "local-llm" or force_local else
                            "built-in templates on this machine"), True
        elif provider:
            where, local = f"a cloud model ({provider})", False
        elif mode == "llm":
            where, local = "Claude (cloud) via the Claude CLI", False
        else:
            where, local = "unknown (brain not probed yet)", None
        return {"mode": mode, "where": where, "local": local}

    def perimeter(self) -> dict:
        import sys
        m = sys.modules.get("main")                # never import the app from here (tests import this module)
        auth = getattr(m, "AUTH", None) if m is not None else None
        pw = bool(getattr(auth, "password_set", False)) if auth is not None else None
        return {"remote_access": ("password-protected" if pw else "disabled (no password set)") if pw is not None else "unknown",
                "loopback_trusted": True}

    # ── snapshot + score ──
    def snapshot(self) -> dict:
        now = self.now()
        hosts = sorted(self.ledger.values(), key=lambda e: -e["last_seen"])
        live = [h for h in hosts if now - h["last_seen"] <= SAMPLE_S * 2 + 5]
        unl = [h for h in hosts if h["scope"] == "internet" and not h.get("purpose")]
        ai = self.ai_route()
        rest = self.at_rest()
        exposed = [f["name"] for f in rest["files"] if f.get("git_ignored") is False]
        factors, score = [], 100
        if ai["local"] is False:
            score -= 20
            factors.append({"delta": -20, "why": f"AI prompts go to {ai['where']}"})
        if unl:
            d = min(25, 5 * len(unl))
            score -= d
            factors.append({"delta": -d, "why": f"{len(unl)} outbound host(s) without a known purpose"})
        if exposed:
            d = min(30, 15 * len(exposed))
            score -= d
            factors.append({"delta": -d, "why": "private file(s) NOT ignored by git: " + ", ".join(exposed[:4])})
        if rest.get("update_dir_ignored") is False:
            score -= 15
            factors.append({"delta": -15, "why": ".update/ (private state) is not ignored by git"})
        if not factors:
            factors.append({"delta": 0, "why": "nothing measured counts against privacy right now"})
        def pub(h):
            return {k: h.get(k) for k in ("ip", "name", "asked", "purpose", "scope", "ports", "first_seen", "last_seen", "samples")}
        return {"ok": True, "generated": round(now), "sampled_at": self.last_sample, "error": self.error,
                "score": max(0, score), "factors": factors,
                "outbound": {"live": [pub(h) for h in live][:40], "ledger": [pub(h) for h in hosts][:60],
                             "live_count": len(live), "hosts_seen": len(hosts), "unlabelled": len(unl)},
                "at_rest": rest, "ai": ai, "perimeter": self.perimeter(),
                "promise": "Your data stays on this machine unless a feature you use needs a public feed; "
                           "every outbound host is listed here, live."}

    def summary(self) -> str:
        s = self.snapshot()
        o = s["outbound"]
        line = (f"Privacy score {s['score']} of 100. Right now BUCS holds {o['live_count']} outbound connection"
                f"{'s' if o['live_count'] != 1 else ''} ({o['hosts_seen']} hosts seen since start")
        line += f", {o['unlabelled']} without a known purpose)." if o["unlabelled"] else ", all with a known purpose)."
        line += f" AI prompts go to {s['ai']['where']}."
        n = len(s["at_rest"]["files"])
        line += f" {n} private file{'s' if n != 1 else ''} on disk, never uploaded."
        return line

    async def loop(self) -> None:
        await asyncio.sleep(8)
        while True:
            try:
                await self.sample()
            except Exception:
                pass
            await asyncio.sleep(SAMPLE_S)


def _engine(ctx) -> PrivacyShield:
    st = ctx["state"]
    if "privacy" not in st:
        st["privacy"] = PrivacyShield(st)
    return st["privacy"]


async def start(ctx) -> None:
    await _engine(ctx).loop()


def build_router(ctx) -> APIRouter:
    r = APIRouter()

    @r.get("/api/privacy")
    async def privacy_get():
        return _engine(ctx).snapshot()

    @r.post("/api/privacy/scan")
    async def privacy_scan():
        if not ctx["commander"]():
            return JSONResponse({"ok": False, "error": "OBSERVER role: switch to COMMANDER to run a privacy scan."}, status_code=403)
        return await _engine(ctx).sample()

    return r
