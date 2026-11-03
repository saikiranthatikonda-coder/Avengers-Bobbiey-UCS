"""CYBER ANALYST · AUTONOMOUS DEFENCE — BUCS assesses itself and this host.

Every finding comes from a REAL measurement (never a placeholder) and carries a
severity, the evidence it was based on, and a fix:

  deps      installed packages of the BUCS interpreter (importlib.metadata) checked
            against OSV.dev (querybatch + per-vuln detail) → real CVEs / GHSAs
  secrets   git-tracked files must not include protected secret files or obvious
            key patterns; protected files must be gitignored. Only PATHS, line
            numbers and rule names are ever reported — never a matched value.
  exposure  what BUCS binds to, which sockets listen on all interfaces (psutil,
            netstat fallback), Windows Firewall profiles (netsh), Defender
            (Get-MpComputerStatus) — subprocesses off the loop with timeouts.
  config    origin_guard / auth_gate middleware present, no CORS, no permission
            bypass flags, no reload/debug in the launcher, remote-access settings,
            BUCS release currency, Python end-of-life.
  intel     CISA KEV cross-check: OSV CVE aliases that are known-exploited, KEV
            product names matching installed packages (best effort, labelled), and
            recent Windows KEV entries vs. this host's last installed update.

Self-upgrading: OSV results and the KEV catalogue are refreshed on a schedule and
stored with a ruleset version in .update/secagent.json; finding trends (new /
resolved) are tracked per scan; the commander can mark findings (or a whole rule)
as accepted risk and the agent remembers it.

Developing BUCS: PROPOSE FIX hands a precise task to the engineering agent
(devagent.DevAgent.diagnose — read-only, commander-only). Nothing is applied here:
the commander reviews and approves the proposal in the ENGINEERING panel.
"""

import asyncio
import json
import math
import os
import platform
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".update" / "secagent.json"
RULESET_VERSION = "2026.11.1"
OS = platform.system()

FULL_EVERY = 6 * 3600          # deps + secrets + KEV + everything
LIGHT_EVERY = 30 * 60          # exposure + config
KEV_EVERY = 24 * 3600
OSV_DETAIL_TTL = 7 * 24 * 3600
SCAN_COOLDOWN = 120            # manual scans: one per 2 min
BRIEF_COOLDOWN = 10 * 60
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
UA = "BUCS-CyberAnalyst/" + RULESET_VERSION + " (+local security self-assessment)"

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEV_WEIGHT = {"critical": 25, "high": 12, "medium": 5, "low": 2, "info": 0}
CATEGORIES = ("deps", "secrets", "exposure", "config", "intel")
LIGHT_CATEGORIES = ("exposure", "config")

# Files that must never be committed (devagent.PROTECTED is the canonical list).
try:
    from devagent import PROTECTED as _DEV_PROTECTED
except Exception:                      # pragma: no cover — devagent always ships with BUCS
    _DEV_PROTECTED = []
PROTECTED_FILES = sorted(set(_DEV_PROTECTED) | {".env", "credentials.json", "token.json", "auth.json",
                                                "license_signing.key", "licences_issued.json"})
KEY_FILE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".jks", ".keystore")
KEY_FILE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
SAFE_TRACKED = {".env.example", ".env.sample", ".env.template"}

# Obvious key formats. Rules report path + line + rule name; the match itself is discarded.
SECRET_RULES = [
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("google_oauth_client_secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{20,}")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("stripe_live_key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}")),
]
_BYPASS_FLAG = "--dangerously-" + "skip-permissions"      # built so this file never matches itself

# Ports that are risky when reachable from the network.
RISKY_PORTS = {21: "FTP", 23: "Telnet", 135: "MS-RPC", 139: "NetBIOS", 445: "SMB", 2375: "Docker API (no TLS)",
               3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 5985: "WinRM", 5986: "WinRM",
               6379: "Redis", 9200: "Elasticsearch", 11434: "Ollama LLM API", 27017: "MongoDB"}
HIGH_RISK_PORTS = {23, 2375, 6379, 9200, 11434, 27017, 5900}
WINDOWS_DEFAULT_PORTS = {135, 139, 445}
SYSTEM_PROCS = {"system", "svchost.exe", "lsass.exe", "wininit.exe", "services.exe", "spoolsv.exe",
                "system idle process", ""}
TOOLING_PKGS = {"pip", "setuptools", "wheel"}
# CPython end-of-life (python.org devguide)
PY_EOL = {(3, 8): "2024-10-07", (3, 9): "2025-10-31", (3, 10): "2026-10-31", (3, 11): "2027-10-31",
          (3, 12): "2028-10-31", (3, 13): "2029-10-31", (3, 14): "2030-10-31"}


# ═══ small helpers ═══════════════════════════════════════════════════════════

def norm_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def _vkey(v: str):
    try:
        from packaging.version import Version
        return (0, Version(v))
    except Exception:
        parts = []
        for p in re.split(r"[.\-+]", v or ""):
            parts.append((0, int(p), "") if p.isdigit() else (1, 0, p))
        return (1, tuple(parts))


def vless(a: str, b: str) -> bool:
    ka, kb = _vkey(a), _vkey(b)
    if ka[0] != kb[0]:
        return str(a) < str(b)
    return ka[1] < kb[1]


def _now() -> float:
    return time.time()


def _finding(fid, category, rule, severity, title, evidence, fix, task=None, **extra) -> dict:
    return {"id": fid, "category": category, "rule": rule, "severity": severity, "title": title,
            "evidence": evidence, "fix": fix, "fixable": bool(task), "task": task, **extra}


async def _run(*args, timeout: float = 10) -> str:
    """Run a command off the event loop with a hard timeout (connectivity.py pattern)."""
    kw = {}
    if OS == "Windows":
        kw["creationflags"] = 0x08000000          # CREATE_NO_WINDOW
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, cwd=str(HERE), **kw)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return out.decode("utf-8", errors="ignore")


# ═══ 1. dependency vulnerabilities (OSV.dev) ═════════════════════════════════

def installed_packages() -> list[tuple[str, str]]:
    import importlib.metadata as md
    seen = {}
    for d in md.distributions():
        name = d.metadata.get("Name") if d.metadata else None
        if name and d.version:
            seen.setdefault(norm_pkg(name), (norm_pkg(name), d.version))
    return sorted(seen.values())


def direct_requirements(root: Path) -> set[str]:
    out = set()
    try:
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._\-]*)", line)
            if m:
                out.add(norm_pkg(m.group(1)))
    except Exception:
        pass
    return out


def parse_osv_batch(packages: list[tuple[str, str]], batch: dict) -> dict:
    """querybatch results are positional: results[i] answers packages[i]."""
    out = {}
    for (name, ver), res in zip(packages, (batch or {}).get("results", []) or []):
        ids = [(v.get("id"), v.get("modified")) for v in (res or {}).get("vulns", []) or [] if v.get("id")]
        if ids:
            out[(name, ver)] = ids
    return out


_CVSS3 = {"AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}, "AC": {"L": 0.77, "H": 0.44},
          "UI": {"N": 0.85, "R": 0.62}, "CIA": {"H": 0.56, "L": 0.22, "N": 0.0}}


def cvss3_score(vector: str) -> float | None:
    try:
        m = dict(p.split(":", 1) for p in vector.split("/")[1:])
        scope_changed = m["S"] == "C"
        pr = {"N": 0.85, "L": 0.68 if scope_changed else 0.62, "H": 0.5 if scope_changed else 0.27}[m["PR"]]
        iss = 1 - (1 - _CVSS3["CIA"][m["C"]]) * (1 - _CVSS3["CIA"][m["I"]]) * (1 - _CVSS3["CIA"][m["A"]])
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if scope_changed else 6.42 * iss
        expl = 8.22 * _CVSS3["AV"][m["AV"]] * _CVSS3["AC"][m["AC"]] * pr * _CVSS3["UI"][m["UI"]]
        if impact <= 0:
            return 0.0
        base = min((1.08 if scope_changed else 1.0) * (impact + expl), 10)
        return math.ceil(base * 10 - 1e-9) / 10
    except Exception:
        return None


def _sev_from_score(s: float) -> str:
    return "critical" if s >= 9 else "high" if s >= 7 else "medium" if s >= 4 else "low"


def osv_severity(detail: dict) -> tuple[str, str]:
    """(severity, how-it-was-rated). GHSA rating first, then a computed CVSS v3 base score."""
    lab = ((detail.get("database_specific") or {}).get("severity") or "").upper()
    if lab in ("CRITICAL", "HIGH", "MODERATE", "MEDIUM", "LOW"):
        return ({"MODERATE": "medium"}.get(lab, lab.lower()), "GHSA rating " + lab)
    for s in detail.get("severity") or []:
        if str(s.get("type", "")).startswith("CVSS_V3"):
            sc = cvss3_score(s.get("score", ""))
            if sc is not None:
                return (_sev_from_score(sc), f"CVSS v3 {sc}")
    return ("medium", "unrated by source — treated as medium")


def fixed_version(detail: dict, pkg: str, installed: str) -> str | None:
    cands, any_fixed = [], []
    for aff in detail.get("affected") or []:
        p = aff.get("package") or {}
        if norm_pkg(p.get("name", "")) != pkg:
            continue
        for rg in aff.get("ranges") or []:
            if rg.get("type") not in ("ECOSYSTEM", "SEMVER"):
                continue
            intro = None
            for ev in rg.get("events") or []:
                if "introduced" in ev:
                    intro = ev["introduced"]
                elif "fixed" in ev:
                    fx = ev["fixed"]
                    any_fixed.append(fx)
                    lo_ok = intro in (None, "0") or not vless(installed, intro)
                    if lo_ok and vless(installed, fx):
                        cands.append(fx)
                    intro = None
    pool = cands or [f for f in any_fixed if vless(installed, f)]
    if not pool:
        return None
    best = pool[0]
    for f in pool[1:]:
        if vless(f, best):
            best = f
    return best


def minimal_detail(detail: dict) -> dict:
    """What we cache from an OSV record (no huge reference lists)."""
    return {"id": detail.get("id"), "summary": (detail.get("summary") or detail.get("details") or "")[:200],
            "aliases": detail.get("aliases") or [], "modified": detail.get("modified"),
            "severity": detail.get("severity") or [], "database_specific": {
                "severity": (detail.get("database_specific") or {}).get("severity")},
            "affected": [{"package": a.get("package"), "ranges": a.get("ranges")} for a in detail.get("affected") or []]}


def dep_findings(hits: dict, details: dict, direct: set, kev_cves: set) -> list[dict]:
    out = []
    order = lambda i: (0 if i.startswith("GHSA") else 1, i)
    for (pkg, ver), ids in sorted(hits.items()):
        seen = set()
        fixes = [fx for fx in (fixed_version(details[i], pkg, ver) for i, _ in ids if details.get(i)) if fx]
        target = None
        for fx in fixes:                       # one upgrade that clears every advisory with a fix
            if target is None or vless(target, fx):
                target = fx
        groups: list[set] = []                 # GHSA + PYSEC + CVE ids of one advisory count once
        for i, _ in ids:
            d0 = details.get(i)
            if not d0:
                continue
            ks = {i, *(d0.get("aliases") or [])}
            hit = next((g for g in groups if g & ks), None)
            hit.update(ks) if hit is not None else groups.append(ks)
        n_adv = len(groups)
        for vid in sorted((i for i, _ in ids), key=order):
            if vid in seen:
                continue
            d = details.get(vid)
            if not d:
                continue
            seen.add(vid)
            seen.update(d.get("aliases") or [])
            sev, how = osv_severity(d)
            cves = [a for a in d.get("aliases") or [] if a.startswith("CVE-")]
            if vid.startswith("CVE-"):
                cves.append(vid)
            exploited = sorted(c for c in cves if c in kev_cves)
            if exploited:
                sev, how = "critical", how + "; listed in CISA KEV (actively exploited)"
            fx = fixed_version(d, pkg, ver)
            if fx and target:
                fx = target
            summary = d.get("summary") or "vulnerability"
            ref = vid + (f" / {', '.join(cves[:3])}" if cves else "")
            where = "pinned in requirements.txt" if pkg in direct else (
                "venv tooling" if pkg in TOOLING_PKGS else "transitive dependency")
            if fx and pkg in TOOLING_PKGS:
                fix, task = f"Run: .venv\\Scripts\\python.exe -m pip install -U \"{pkg}>={fx}\"", None
            elif fx:
                fix = (f"Upgrade {pkg} to >= {fx}, then pip install -r requirements.txt"
                       + (f" (one upgrade covers this package's {n_adv} advisories)" if n_adv > 1 else ""))
                task = (f"Security fix from the Cyber Analyst (OSV {ref}): the BUCS venv has {pkg} {ver}, which is "
                        f"affected by: {summary}. Edit requirements.txt so {pkg} requires >= {fx} "
                        f"({'update its existing pin' if pkg in direct else 'add a line, it is a transitive dependency'}); "
                        f"keep every other pin unchanged unless a pinned package caps {pkg} below {fx} (then raise that pin to the "
                        f"oldest release that allows it). Check the BUCS code that uses {pkg} is compatible with {fx}. "
                        f"NEEDS: the operator runs pip install -r requirements.txt after approval.")
            else:
                fix, task = f"No fixed {pkg} release is published yet — review the advisory for mitigations", None
            out.append(_finding(f"dep:{pkg}:{vid}", "deps", "dependency_vulnerability", sev,
                                f"{pkg} {ver}: {summary}",
                                f"{pkg}=={ver} installed ({where}); OSV {ref}; severity: {how}",
                                fix, task, ref=vid, package=pkg, kev=exploited or None,
                                url=f"https://osv.dev/vulnerability/{vid}"))
    return out


# ═══ 2. secret hygiene ═══════════════════════════════════════════════════════

def _is_key_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    if base in SAFE_TRACKED:
        return False
    if base in PROTECTED_FILES or base in KEY_FILE_NAMES or base.lower().endswith(KEY_FILE_SUFFIXES):
        return True
    return base.startswith(".env") and base not in SAFE_TRACKED


def scan_file_for_secrets(path: Path, max_bytes: int = 1_000_000) -> list[tuple[str, int]]:
    """[(rule, line_no)] — the matched text is never returned or stored."""
    try:
        if path.stat().st_size > max_bytes:
            return []
        raw = path.read_bytes()
    except Exception:
        return []
    if b"\0" in raw[:4096]:
        return []
    text = raw.decode("utf-8", errors="ignore")
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for rule, rx in SECRET_RULES:
            if rx.search(line):
                hits.append((rule, n))
    return hits


def secret_findings(root: Path, tracked: list[str], ignored: set[str], self_name: str = "secagent.py") -> list[dict]:
    out = []
    for p in tracked:
        if _is_key_file(p):
            out.append(_finding(f"secret:tracked:{p}", "secrets", "secret_file_tracked", "critical",
                                f"Secret file is tracked by git: {p}",
                                f"`git ls-files` lists {p} (a protected/private-key file name). Contents not read.",
                                f"git rm --cached \"{p}\", add it to .gitignore, and rotate whatever it contained",
                                f"Security fix from the Cyber Analyst: the private file {p} is tracked by git. Add an "
                                f"entry for it to .gitignore (do not open or modify {p} itself). NEEDS: the operator "
                                f"runs git rm --cached \"{p}\" and rotates the credential it holds."))
    by_file: dict[tuple[str, str], list[int]] = {}
    for p in tracked:
        if p == self_name or p.startswith(".venv/") or _is_key_file(p):
            continue
        for rule, line in scan_file_for_secrets(root / p):
            by_file.setdefault((p, rule), []).append(line)
    for (p, rule), lines in sorted(by_file.items()):
        test_file = p.startswith("tests/")
        out.append(_finding(f"secret:pattern:{p}:{rule}", "secrets", "secret_pattern",
                            "medium" if test_file else "high",
                            f"Possible {rule.replace('_', ' ')} in tracked file {p}",
                            f"{p} line(s) {', '.join(map(str, lines[:8]))} match rule {rule} "
                            f"(value withheld){' — test file, may be a fixture' if test_file else ''}",
                            "Move the value to .env / a gitignored file, rotate it, and purge it from git history"
                            if not test_file else "Confirm it is a dummy value; otherwise rotate and remove it",
                            None if test_file else
                            (f"Security fix from the Cyber Analyst: {p} (lines {', '.join(map(str, lines[:8]))}) contains "
                             f"text matching the {rule} secret pattern. Replace the literal with a value read from the "
                             f"environment (os.getenv) or a gitignored file; never print the value. NEEDS: the operator "
                             f"rotates the credential and purges it from git history.")))
    for name in PROTECTED_FILES:
        if name in ignored:
            continue
        exists = (root / name).exists()
        out.append(_finding(f"secret:unignored:{name}", "secrets", "protected_not_ignored",
                            "high" if exists else "medium",
                            f"Protected file {name} is not covered by .gitignore",
                            f"`git check-ignore --no-index {name}` → not ignored; file {'exists' if exists else 'absent'} on disk",
                            f"Add {name} to .gitignore",
                            f"Security fix from the Cyber Analyst: the protected file {name} is not covered by .gitignore. "
                            f"Add a line for it to .gitignore (do not create, open or modify {name})."))
    return out


# ═══ 3. exposure ═════════════════════════════════════════════════════════════

def parse_netstat(text: str) -> list[dict]:
    """TCP LISTENING rows of `netstat -ano` (Windows)."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() in ("LISTENING", "LISTEN"):
            addr = parts[1]
            ip, _, port = addr.rpartition(":")
            ip = ip.strip("[]")
            if port.isdigit():
                out.append({"ip": ip, "port": int(port), "pid": int(parts[4]) if parts[4].isdigit() else None})
    return out


def _psutil_listeners() -> list[dict] | None:
    try:
        import psutil
        out = []
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr:
                out.append({"ip": c.laddr.ip, "port": c.laddr.port, "pid": c.pid})
        return out
    except Exception:
        return None


def _proc_names(pids) -> dict:
    names = {}
    try:
        import psutil
        for pid in set(p for p in pids if p):
            try:
                names[pid] = psutil.Process(pid).name()
            except Exception:
                names[pid] = ""
    except Exception:
        pass
    return names


def is_loopback(ip: str) -> bool:
    return ip.startswith("127.") or ip in ("::1", "localhost") or ip.startswith("::ffff:127.")


def parse_firewall(text: str) -> dict:
    """{"Domain": "ON", ...} from `netsh advfirewall show allprofiles state`."""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^\s*(\w+) Profile Settings", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"^\s*State\s+(\S+)", line)
        if m and cur:
            out[cur] = m.group(1).upper()
            cur = None
    return out


def exposure_findings(listeners: list[dict], names: dict, bind_host: str, bucs_port: int,
                      password_set: bool, pin_set: bool, firewall: dict) -> list[dict]:
    out = []
    fw_off = [p for p, s in firewall.items() if s == "OFF"]
    for p in fw_off:
        out.append(_finding(f"exposure:firewall:{p.lower()}", "exposure", "firewall_off", "high",
                            f"Windows Firewall {p} profile is OFF",
                            f"netsh advfirewall show allprofiles state → {p} Profile State OFF",
                            f"netsh advfirewall set {p.lower()}profile state on (elevated prompt)"))
    if bind_host and not is_loopback(bind_host):
        if password_set:
            out.append(_finding("exposure:bucs:remote", "exposure", "bucs_remote_access",
                                "low" if pin_set else "medium",
                                f"BUCS is reachable from the network ({bind_host}:{bucs_port}) with remote access armed",
                                f"JARVIS_HOST={bind_host}; remote-access password set; JARVIS_PIN {'set' if pin_set else 'not set'}",
                                "Keep it only if you use remote access; set JARVIS_PIN so the COMMANDER role needs a PIN"
                                if not pin_set else "Bind to 127.0.0.1 when remote access is not needed"))
        else:
            out.append(_finding("exposure:bucs:bound", "exposure", "bucs_bound_all", "low",
                                f"BUCS listens on {bind_host}:{bucs_port} but remote access is locked (no password)",
                                f"JARVIS_HOST={bind_host}; no remote-access password → remote API refused (secure default)",
                                "Set JARVIS_HOST=127.0.0.1 unless you plan to enable remote access"))
    wild = {}
    for l in listeners:
        if is_loopback(l["ip"]):
            continue
        wild.setdefault(l["port"], l)
    others = []
    for port, l in sorted(wild.items()):
        proc = names.get(l.get("pid"), "") or "?"
        if port in RISKY_PORTS:
            sev = "high" if (port in HIGH_RISK_PORTS or fw_off) else (
                "low" if port in WINDOWS_DEFAULT_PORTS else "medium")
            out.append(_finding(f"exposure:port:{port}", "exposure", "risky_port_exposed", sev,
                                f"{RISKY_PORTS[port]} (port {port}) is reachable from the network",
                                f"TCP {l['ip']}:{port} LISTENING, pid {l.get('pid')} ({proc}); firewall "
                                f"{'OFF on ' + ', '.join(fw_off) if fw_off else 'on for all profiles'}",
                                f"Bind {RISKY_PORTS[port]} to 127.0.0.1, or disable it if unused; keep the firewall on"
                                + (" (Windows default service — block it on Public networks)" if port in WINDOWS_DEFAULT_PORTS else "")))
        elif port == bucs_port:
            continue
        elif port >= 49152 and proc.lower() in SYSTEM_PROCS:
            continue                                    # Windows RPC dynamic endpoints
        else:
            others.append(f"{port} ({proc})")
    if others:
        out.append(_finding("exposure:listeners", "exposure", "lan_listeners", "low",
                            f"{len(others)} other service(s) accept connections from the network",
                            "listening on a non-loopback address: " + ", ".join(others[:15]) + (" …" if len(others) > 15 else ""),
                            "Stop services you don't need, or bind dev servers to 127.0.0.1"))
    return out


def defender_findings(d: dict | None) -> list[dict]:
    if not d:
        return []
    out = []
    if d.get("AntivirusEnabled") is False or d.get("AMServiceEnabled") is False:
        out.append(_finding("exposure:defender:off", "exposure", "defender_off", "high",
                            "Microsoft Defender antivirus is disabled",
                            f"Get-MpComputerStatus: AntivirusEnabled={d.get('AntivirusEnabled')}, "
                            f"AMServiceEnabled={d.get('AMServiceEnabled')}",
                            "Re-enable Defender (or confirm another antivirus is active)"))
    elif d.get("RealTimeProtectionEnabled") is False:
        out.append(_finding("exposure:defender:rtp", "exposure", "defender_rtp_off", "high",
                            "Defender real-time protection is OFF",
                            "Get-MpComputerStatus: RealTimeProtectionEnabled=False",
                            "Windows Security → Virus & threat protection → turn Real-time protection on"))
    age = d.get("AntivirusSignatureAge")
    if isinstance(age, int) and age > 7:
        out.append(_finding("exposure:defender:sigs", "exposure", "defender_signatures_stale", "medium",
                            f"Defender signatures are {age} days old",
                            f"Get-MpComputerStatus: AntivirusSignatureAge={age}",
                            "Run Windows Update or: Update-MpSignature"))
    return out


# ═══ 4. app config ═══════════════════════════════════════════════════════════

def _has_middleware(text: str, name: str) -> bool:
    return re.search(r'@app\.middleware\(\s*["\']http["\']\s*\)\s*\n\s*async def ' + name + r"\b", text) is not None


def config_findings(main_text: str, launcher_text: str, bypass_hits: list[str], updater: dict | None,
                    py: tuple, today=None) -> list[dict]:
    out = []
    today = today or datetime.now(timezone.utc).date()
    if main_text:
        for mw, why in (("origin_guard", "cross-site / DNS-rebinding requests to /api are no longer refused"),
                        ("auth_gate", "remote requests are no longer authenticated")):
            if not _has_middleware(main_text, mw):
                out.append(_finding(f"config:missing:{mw}", "config", "middleware_missing", "critical",
                                    f"{mw} middleware not found in main.py",
                                    f"main.py has no `@app.middleware(\"http\")` + `async def {mw}` — {why}",
                                    f"Restore the {mw} middleware (CLAUDE.md §6g security invariant)",
                                    f"Security fix from the Cyber Analyst: main.py no longer defines the {mw} HTTP "
                                    f"middleware ({why}). Restore it exactly as described in CLAUDE.md §6g."))
        if "CORSMiddleware" in main_text:
            out.append(_finding("config:cors", "config", "cors_enabled", "high",
                                "CORS middleware is enabled in main.py",
                                "main.py references CORSMiddleware — CLAUDE.md §6g forbids CORS headers",
                                "Remove CORSMiddleware",
                                "Security fix from the Cyber Analyst: main.py adds CORSMiddleware, which violates the "
                                "origin_guard invariant (CLAUDE.md §6g). Remove the CORS middleware and its import."))
        if re.search(r"FastAPI\([^)]*debug\s*=\s*True", main_text):
            out.append(_finding("config:debug", "config", "debug_enabled", "medium",
                                "FastAPI debug mode is on", "main.py: FastAPI(..., debug=True) — tracebacks leak to clients",
                                "Remove debug=True",
                                "Security fix from the Cyber Analyst: main.py creates FastAPI with debug=True, which "
                                "returns tracebacks to clients. Remove the debug=True argument."))
    if re.search(r"--reload\b", launcher_text or ""):
        out.append(_finding("config:reload", "config", "reload_enabled", "medium",
                            "The launcher runs uvicorn with --reload",
                            "run.ps1 passes --reload (file-watching dev mode in production)",
                            "Remove --reload from run.ps1",
                            "Security fix from the Cyber Analyst: run.ps1 starts uvicorn with --reload. Remove that flag."))
    for p in bypass_hits:
        out.append(_finding(f"config:bypass:{p}", "config", "permission_bypass", "critical",
                            f"{p} passes the Claude CLI permission-bypass flag",
                            f"{p} contains the {_BYPASS_FLAG} flag (CLAUDE.md §6g: never)",
                            "Remove the flag; use scoped --tools / --allowedTools instead",
                            f"Security fix from the Cyber Analyst: {p} passes {_BYPASS_FLAG} to the Claude CLI, which "
                            f"CLAUDE.md §6g forbids. Remove it and use a scoped --tools/--allowedTools list."))
    if updater and not updater.get("managed_by_git") and updater.get("available"):
        out.append(_finding("config:outdated", "config", "bucs_outdated", "medium",
                            f"BUCS {updater.get('latest')} is available (running {updater.get('version')})",
                            f"updater: latest signed release {updater.get('latest')} > installed {updater.get('version')}",
                            "Install the update from the version menu (signed, auto-rollback)"))
    eol = PY_EOL.get(tuple(py[:2]))
    if eol:
        eol_d = datetime.strptime(eol, "%Y-%m-%d").date()
        ver = f"{py[0]}.{py[1]}"
        if eol_d <= today:
            out.append(_finding("config:python_eol", "config", "python_eol", "high",
                                f"Python {ver} is end-of-life (since {eol})",
                                f"BUCS runs on Python {'.'.join(map(str, py[:3]))}; no more security fixes upstream",
                                "Rebuild .venv on a supported Python (3.12+)"))
        elif (eol_d - today).days <= 180:
            out.append(_finding("config:python_eol", "config", "python_eol_soon", "low",
                                f"Python {ver} reaches end-of-life on {eol}",
                                f"BUCS runs on Python {'.'.join(map(str, py[:3]))}",
                                "Plan a move to Python 3.12+"))
    return out


# ═══ 5. CISA KEV cross-check ═════════════════════════════════════════════════

def compact_kev(raw: dict, keep_days: int = 120) -> dict:
    vulns = raw.get("vulnerabilities") or []
    cut = (datetime.now(timezone.utc) - timedelta(days=keep_days)).date().isoformat()
    recent = [{k: v.get(k) for k in ("cveID", "vendorProject", "product", "vulnerabilityName", "dateAdded")}
              for v in vulns if (v.get("dateAdded") or "") >= cut]
    return {"version": raw.get("catalogVersion"), "released": raw.get("dateReleased"), "count": len(vulns),
            "cves": sorted({v.get("cveID") for v in vulns if v.get("cveID")}), "recent": recent}


def kev_findings(kev: dict | None, packages: list[tuple[str, str]], last_hotfix: str | None, today=None) -> list[dict]:
    if not kev:
        return []
    out = []
    today = today or datetime.now(timezone.utc).date()
    pk = {p for p, _ in packages}
    cut30 = (today - timedelta(days=30)).isoformat()
    for e in kev.get("recent") or []:
        prod = norm_pkg(e.get("product") or "")
        if prod and prod in pk and (e.get("dateAdded") or "") >= cut30:
            out.append(_finding(f"intel:kev:{e.get('cveID')}", "intel", "kev_name_match", "medium",
                                f"KEV {e.get('cveID')} names \"{e.get('product')}\" — same name as an installed package",
                                f"CISA KEV added {e.get('dateAdded')}: {e.get('vendorProject')} {e.get('product')} — "
                                f"{e.get('vulnerabilityName')}. BEST-EFFORT name match, not a version check.",
                                "Check whether the KEV entry applies to the installed package; upgrade if it does"))
    win = [e for e in kev.get("recent") or [] if "windows" in (e.get("product") or "").lower()
           and (e.get("vendorProject") or "").lower() == "microsoft" and (e.get("dateAdded") or "") >= cut30]
    if OS == "Windows" and win and last_hotfix:
        try:
            hf = datetime.strptime(last_hotfix[:10], "%Y-%m-%d").date()
            age = (today - hf).days
            newest = max(e["dateAdded"] for e in win)
            if age > 35 and newest > hf.isoformat():
                out.append(_finding("intel:windows_patch", "intel", "windows_unpatched_vs_kev", "high",
                                    f"Windows last updated {age} days ago; {len(win)} actively-exploited Windows CVEs added to KEV in 30 days",
                                    f"Get-HotFix newest install {hf.isoformat()}; CISA KEV Microsoft Windows entries "
                                    f"added since {cut30}: {', '.join(sorted(e['cveID'] for e in win)[:6])}",
                                    "Run Windows Update now"))
        except Exception:
            pass
    return out


# ═══ scoring ═════════════════════════════════════════════════════════════════

def score(findings: list[dict]) -> int:
    """0-100 posture. Each package / finding group counts once at its worst severity (ten advisories
    fixed by one upgrade are one problem), and penalties decay so the gauge stays informative:
    one critical → 73, one high → 86, one medium → 94."""
    worst: dict[str, int] = {}
    for f in findings:
        key = f"{f['category']}:{f.get('package')}" if f.get("package") else f["id"]
        worst[key] = max(worst.get(key, 0), SEV_WEIGHT.get(f["severity"], 0))
    return int(round(100 * math.exp(-sum(worst.values()) / 80)))


def counts(findings: list[dict]) -> dict:
    c = {s: 0 for s in SEVERITIES}
    for f in findings:
        c[f["severity"]] = c.get(f["severity"], 0) + 1
    return c


# ═══ the agent ═══════════════════════════════════════════════════════════════

class SecAgent:
    def __init__(self, state: dict, ctx: dict | None = None, root: Path = HERE, state_file: Path = STATE_FILE):
        self.state, self.ctx, self.root, self.file = state, ctx or {}, Path(root), Path(state_file)
        self.lock = asyncio.Lock()
        self.findings: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
        self.checks: dict[str, dict] = {}
        self.accepted: dict[str, dict] = {}         # finding id OR "rule:<rule>" → {ts, note, by}
        self.history: list[dict] = []
        self.log: list[dict] = []
        self.intel: dict = {"ruleset": RULESET_VERSION, "osv_updated": None, "kev_updated": None, "osv": {},
                            "kev": None}
        self.last_full = self.last_light = 0.0
        self.last_manual = 0.0
        self.last_new: list[str] = []
        self.last_resolved: list[str] = []
        self.brief: dict | None = None
        self.proposals: list[dict] = []
        self.running: str | None = None
        self._load()

    # ── persistence ──────────────────────────────────────────────
    def _load(self) -> None:
        try:
            d = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            return
        self.accepted = d.get("accepted") or {}
        self.history = (d.get("history") or [])[-120:]
        self.log = (d.get("log") or [])[:60]
        self.intel.update(d.get("intel") or {})
        self.intel["ruleset"] = RULESET_VERSION
        for c in CATEGORIES:
            self.findings[c] = (d.get("findings") or {}).get(c) or []
        self.checks = d.get("checks") or {}
        self.last_full, self.last_light = d.get("last_full") or 0.0, d.get("last_light") or 0.0
        self.brief = d.get("brief")
        self.proposals = (d.get("proposals") or [])[-20:]

    def _save(self) -> None:
        try:
            from atomicio import atomic_write_text
            self.file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.file, json.dumps({
                "ruleset": RULESET_VERSION, "intel": self.intel, "accepted": self.accepted,
                "history": self.history[-120:], "log": self.log[:60], "findings": self.findings,
                "checks": self.checks, "last_full": self.last_full, "last_light": self.last_light,
                "brief": self.brief, "proposals": self.proposals[-20:]}, indent=1))
        except Exception:
            pass

    async def _note(self, msg: str, level: str = "info", audit: str | None = None) -> None:
        self.log.insert(0, {"ts": _now(), "msg": msg, "level": level})
        del self.log[60:]
        hub = self.state.get("hub")
        if hub:
            try:
                await hub.broadcast({"type": "log", "level": level, "msg": f"[cyber] {msg}"})
            except Exception:
                pass
        if audit and self.ctx.get("audit"):
            try:
                await self.ctx["audit"](audit, msg[:200])
            except Exception:
                pass

    # ── views ────────────────────────────────────────────────────
    def is_accepted(self, f: dict) -> bool:
        return f["id"] in self.accepted or ("rule:" + f["rule"]) in self.accepted

    def all_findings(self) -> list[dict]:
        out = [f for c in CATEGORIES for f in self.findings.get(c, [])]
        rank = {s: i for i, s in enumerate(SEVERITIES)}
        return sorted(out, key=lambda f: (rank.get(f["severity"], 9), f["category"], f["id"]))

    def active(self) -> list[dict]:
        return [f for f in self.all_findings() if not self.is_accepted(f)]

    def snapshot(self) -> dict:
        act = self.active()
        acc = [{**f, "accepted": self.accepted.get(f["id"]) or self.accepted.get("rule:" + f["rule"])}
               for f in self.all_findings() if self.is_accepted(f)]
        kev = self.intel.get("kev") or {}
        return {
            "score": score(act), "counts": counts(act), "findings": act, "accepted": acc,
            "accepted_rules": sorted(k[5:] for k in self.accepted if k.startswith("rule:")),
            "checks": self.checks, "running": self.running,
            "last_scan": {"full": self.last_full or None, "light": self.last_light or None},
            "trend": [{"ts": h["ts"], "score": h["score"], "kind": h["kind"], "new": len(h.get("new", [])),
                       "resolved": len(h.get("resolved", []))} for h in self.history[-40:]],
            "new": self.last_new, "resolved": self.last_resolved,
            "intel": {"ruleset": RULESET_VERSION, "osv_updated": self.intel.get("osv_updated"),
                      "kev_updated": self.intel.get("kev_updated"), "kev_version": kev.get("version"),
                      "kev_count": kev.get("count"), "osv_cached": len(self.intel.get("osv") or {})},
            "log": self.log[:25], "brief": self.brief, "proposals": self.proposals[-5:],
            "next_full": (self.last_full + FULL_EVERY) if self.last_full else None,
        }

    def _check(self, name: str, status: str, detail: str) -> None:
        self.checks[name] = {"status": status, "detail": detail, "ts": _now()}

    # ── probes (all off the event loop) ──────────────────────────
    async def _http(self):
        import httpx
        return httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True)

    async def osv_query(self, packages):
        async with await self._http() as c:
            r = await c.post(OSV_BATCH, json={"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v}
                                                          for n, v in packages]})
            r.raise_for_status()
            return r.json()

    async def osv_detail(self, vid: str) -> dict:
        async with await self._http() as c:
            r = await c.get(OSV_VULN + vid)
            r.raise_for_status()
            return r.json()

    async def kev_fetch(self) -> dict:
        async with await self._http() as c:
            r = await c.get(KEV_URL, timeout=40)
            r.raise_for_status()
            return r.json()

    async def git_tracked(self) -> list[str] | None:
        if not (self.root / ".git").exists():
            return None
        out = await _run("git", "-c", "core.quotepath=off", "ls-files", "-z", timeout=20)
        return [p for p in out.split("\0") if p]

    async def git_ignored(self, names: list[str]) -> set[str]:
        proc = await asyncio.create_subprocess_exec(
            "git", "check-ignore", "--no-index", "--stdin", cwd=str(self.root), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **({"creationflags": 0x08000000} if OS == "Windows" else {}))
        try:
            out, _ = await asyncio.wait_for(proc.communicate("\n".join(names).encode() + b"\n"), 15)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return {l.strip() for l in out.decode("utf-8", "ignore").splitlines() if l.strip()}

    async def listeners(self) -> list[dict]:
        ls = await asyncio.to_thread(_psutil_listeners)
        if ls is None and OS == "Windows":
            ls = parse_netstat(await _run("netstat", "-ano", "-p", "TCP", timeout=10))
        return ls or []

    async def firewall(self) -> dict:
        if OS != "Windows":
            return {}
        return parse_firewall(await _run("netsh", "advfirewall", "show", "allprofiles", "state", timeout=8))

    async def defender(self) -> dict | None:
        if OS != "Windows":
            return None
        ps = ("$ErrorActionPreference='Stop';$s=Get-MpComputerStatus;"
              "$h=Get-HotFix | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 1;"
              "[pscustomobject]@{AMServiceEnabled=$s.AMServiceEnabled;AntivirusEnabled=$s.AntivirusEnabled;"
              "RealTimeProtectionEnabled=$s.RealTimeProtectionEnabled;AntivirusSignatureAge=$s.AntivirusSignatureAge;"
              "LastHotfix=$(if($h){$h.InstalledOn.ToString('yyyy-MM-dd')}else{$null})} | ConvertTo-Json -Compress")
        out = await _run("powershell", "-NoProfile", "-NonInteractive", "-Command", ps, timeout=25)
        try:
            return json.loads(out.strip())
        except Exception:
            return None

    def _password_set(self) -> bool:
        m = sys.modules.get("main")
        auth = getattr(m, "AUTH", None) if m else None
        if auth is not None:
            return bool(getattr(auth, "password_set", False))
        try:                                       # boolean only — the file's values never leave this function
            return bool(json.loads((self.root / "auth.json").read_text(encoding="utf-8")).get("password"))
        except Exception:
            return False

    # ── scan stages ──────────────────────────────────────────────
    async def scan_deps(self) -> None:
        pkgs = await asyncio.to_thread(installed_packages)
        batch = await self.osv_query(pkgs)
        hits = parse_osv_batch(pkgs, batch)
        cache = self.intel.setdefault("osv", {})
        need = []
        for ids in hits.values():
            for vid, mod in ids:
                c = cache.get(vid)
                if not c or c.get("modified") != mod or _now() - c.get("_ts", 0) > OSV_DETAIL_TTL:
                    need.append(vid)
        need = sorted(set(need))[:150]
        sem = asyncio.Semaphore(8)

        async def one(vid):
            async with sem:
                try:
                    cache[vid] = {**minimal_detail(await self.osv_detail(vid)), "_ts": _now()}
                except Exception:
                    pass
        await asyncio.gather(*(one(v) for v in need))
        live = {vid for ids in hits.values() for vid, _ in ids}
        for k in list(cache):
            if k not in live:
                cache.pop(k, None)
        self.intel["osv_updated"] = _now()
        kev = set((self.intel.get("kev") or {}).get("cves") or [])
        self.findings["deps"] = dep_findings(hits, cache, direct_requirements(self.root), kev)
        n = len(self.findings["deps"])
        self._check("deps", "warn" if n else "ok",
                    f"{len(pkgs)} packages checked against OSV.dev — {n} known vulnerabilit{'y' if n == 1 else 'ies'}")

    async def scan_secrets(self) -> None:
        tracked = await self.git_tracked()
        if tracked is None:
            self._check("secrets", "unknown", "not a git checkout — secret hygiene needs git")
            self.findings["secrets"] = []
            return
        ignored = await self.git_ignored(PROTECTED_FILES)
        self.findings["secrets"] = await asyncio.to_thread(secret_findings, self.root, tracked, ignored)
        n = len(self.findings["secrets"])
        self._check("secrets", "warn" if n else "ok",
                    f"{len(tracked)} tracked files + {len(PROTECTED_FILES)} protected names checked — {n} issue(s)")

    async def scan_exposure(self) -> None:
        out, notes = [], []
        try:
            fw = await self.firewall()
            notes.append("firewall " + (", ".join(f"{k} {v}" for k, v in fw.items()) or "n/a"))
        except Exception as e:
            fw = {}
            notes.append(f"firewall unknown ({type(e).__name__})")
        ls = await self.listeners()
        names = await asyncio.to_thread(_proc_names, [l.get("pid") for l in ls if not is_loopback(l["ip"])])
        bind = os.getenv("JARVIS_HOST", "127.0.0.1")
        try:
            port = int(os.getenv("JARVIS_PORT", "8765"))
        except ValueError:
            port = 8765
        out += exposure_findings(ls, names, bind, port, self._password_set(), bool(os.getenv("JARVIS_PIN")), fw)
        notes.append(f"{sum(1 for l in ls if not is_loopback(l['ip']))} network listeners")
        hotfix = None
        try:
            d = await self.defender()
            if d:
                hotfix = d.get("LastHotfix")
                out += defender_findings(d)
                notes.append("Defender RTP " + ("on" if d.get("RealTimeProtectionEnabled") else "OFF"))
            elif OS == "Windows":
                notes.append("Defender status unavailable")
        except Exception as e:
            notes.append(f"Defender unknown ({type(e).__name__})")
        self.intel["last_hotfix"] = hotfix or self.intel.get("last_hotfix")
        self.findings["exposure"] = out
        self._check("exposure", "warn" if out else "ok", "; ".join(notes))

    async def scan_config(self) -> None:
        def read():
            def txt(p):
                try:
                    return (self.root / p).read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    return ""
            hits = []
            for p in sorted(self.root.glob("*.py")) + sorted(self.root.glob("*/*.py")):
                rel = p.relative_to(self.root).as_posix()
                if rel == "secagent.py" or rel.startswith(("tests/", ".venv/")):
                    continue
                if _BYPASS_FLAG in txt(rel):
                    hits.append(rel)
            return txt("main.py"), txt("run.ps1") + "\n" + txt("start-jarvis.cmd"), hits
        main_text, launcher, hits = await asyncio.to_thread(read)
        up = self.state.get("updater")
        snap = None
        try:
            snap = up.snapshot() if up is not None else None
        except Exception:
            pass
        self.findings["config"] = config_findings(main_text, launcher, hits, snap, tuple(sys.version_info[:3]))
        n = len(self.findings["config"])
        self._check("config", "warn" if n else "ok",
                    f"middleware, CORS, CLI flags, launcher, release + Python EOL checked — {n} issue(s)")

    async def refresh_kev(self, force: bool = False) -> None:
        if not force and self.intel.get("kev") and _now() - (self.intel.get("kev_updated") or 0) < KEV_EVERY:
            return
        self.intel["kev"] = await asyncio.to_thread(compact_kev, await self.kev_fetch())
        self.intel["kev_updated"] = _now()

    async def scan_intel(self) -> None:
        pkgs = await asyncio.to_thread(installed_packages)
        self.findings["intel"] = kev_findings(self.intel.get("kev"), pkgs, self.intel.get("last_hotfix"))
        kev = self.intel.get("kev") or {}
        self._check("intel", "warn" if self.findings["intel"] else "ok",
                    f"CISA KEV {kev.get('version') or '?'} ({kev.get('count') or 0} entries) cross-checked")

    # ── orchestration ────────────────────────────────────────────
    async def scan(self, full: bool = True, reason: str = "manual") -> dict:
        if self.lock.locked():
            return {"ok": False, "error": "a scan is already running"}
        async with self.lock:
            t0 = _now()
            kind = "full" if full else "light"
            self.running = kind
            before = {f["id"] for f in self.active()}
            stages = ([("intel-refresh", self.refresh_kev), ("deps", self.scan_deps), ("secrets", self.scan_secrets)]
                      if full else [])
            stages += [("exposure", self.scan_exposure), ("config", self.scan_config)]
            if full:
                stages.append(("intel", self.scan_intel))
            errors = []
            try:
                for name, fn in stages:
                    try:
                        await fn()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        errors.append(f"{name}: {type(e).__name__}")
                        if name in self.findings:                       # keep last-known findings, flag stale
                            for f in self.findings[name]:
                                f["stale"] = True
                        self._check(name if name in CATEGORIES else "kev_feed", "error",
                                    f"{name} check failed ({type(e).__name__}: {str(e)[:80]}) — last known results kept")
            finally:
                self.running = None
            act = self.active()
            after = {f["id"] for f in act}
            self.last_new = sorted(after - before)
            self.last_resolved = sorted(before - after)
            now = _now()
            if full:
                self.last_full = now
            self.last_light = now
            sc = score(act)
            self.history.append({"ts": now, "kind": kind, "score": sc, "counts": counts(act),
                                 "new": self.last_new[:30], "resolved": self.last_resolved[:30]})
            del self.history[:-120]
            c = counts(act)
            msg = (f"{kind} scan ({reason}) · score {sc} · {c['critical']} critical, {c['high']} high, "
                   f"{c['medium']} medium · {len(self.last_new)} new, {len(self.last_resolved)} resolved"
                   + (f" · errors: {', '.join(errors)}" if errors else ""))
            await self._note(msg, "warn" if (c["critical"] or c["high"] or errors) else "info",
                             audit="secagent.scan" if (reason != "auto" or full) else None)
            self._save()
            hub = self.state.get("hub")
            if hub:
                try:
                    await hub.broadcast({"type": "secagent", "score": sc, "counts": c})
                except Exception:
                    pass
            return {"ok": True, "kind": kind, "score": sc, "counts": c, "new": self.last_new,
                    "resolved": self.last_resolved, "errors": errors, "duration": round(_now() - t0, 1)}

    def find(self, fid: str) -> dict | None:
        return next((f for f in self.all_findings() if f["id"] == fid), None)

    async def accept(self, fid: str, note: str = "", scope: str = "finding", undo: bool = False) -> dict:
        f = self.find(fid)
        key = fid if scope != "rule" else ("rule:" + (f["rule"] if f else fid.removeprefix("rule:")))
        if undo:
            if self.accepted.pop(key, None) is None:
                return {"ok": False, "error": "not an accepted risk"}
            await self._note(f"accepted risk withdrawn: {key}", "info", audit="secagent.unaccept")
        else:
            if f is None and scope != "rule":
                return {"ok": False, "error": "unknown finding"}
            by = (self.state.get("rbac") or {}).get("operator", "commander")
            self.accepted[key] = {"ts": _now(), "note": (note or "")[:200], "by": by,
                                  "title": (f or {}).get("title", key)[:160]}
            await self._note(f"accepted risk: {key}" + (f" — {note[:80]}" if note else ""), "info",
                             audit="secagent.accept")
        self._save()
        return {"ok": True, **self.snapshot()}

    async def propose(self, fid: str) -> dict:
        """Hand a finding's fix to the engineering agent as a READ-ONLY diagnosis request.
        Never edits, applies, commits or ships anything itself."""
        f = self.find(fid)
        if f is None:
            return {"ok": False, "error": "unknown finding"}
        if not f.get("task"):
            return {"ok": False, "error": "this finding has no code fix — follow the FIX text instead"}
        task = f["task"][:880]
        dev = self.state.get("dev")
        entry = {"ts": _now(), "id": fid, "task": task, "via": None}
        if dev is None or not hasattr(dev, "diagnose"):
            entry["via"] = "manual"
            self.proposals.append(entry)
            await self._note(f"engineering agent unavailable — fix task for {fid} ready to copy", "warn",
                             audit="secagent.propose")
            self._save()
            return {"ok": True, "queued": False, "task": task,
                    "note": "engineering agent not running — paste this task into the ENGINEERING panel"}
        lock = getattr(dev, "lock", None)
        if lock is not None and lock.locked():
            return {"ok": False, "error": "an engineering job is already running — try again when it finishes",
                    "task": task}
        entry["via"] = "devagent"
        self.proposals.append(entry)
        del self.proposals[:-20]

        async def run():
            try:
                r = await dev.diagnose(task)
                entry["result"] = {k: r.get(k) for k in ("ok", "summary", "fixable", "id", "error")}
                await self._note(f"engineering proposal for {fid}: "
                                 f"{(r.get('summary') or r.get('error') or '')[:140]} — review it in ENGINEERING",
                                 "info" if r.get("ok") else "warn")
                self._save()
            except Exception as e:
                await self._note(f"engineering hand-off failed for {fid}: {type(e).__name__}", "warn")
        asyncio.create_task(run())
        await self._note(f"fix for {fid} handed to engineering (diagnosis only — you approve any change)", "info",
                         audit="secagent.propose")
        self._save()
        return {"ok": True, "queued": True, "task": task,
                "note": "engineering is preparing a proposal — nothing changes until you APPLY it in the ENGINEERING panel"}

    async def make_brief(self) -> dict:
        brain = self.state.get("brain")
        if brain is None:
            return {"ok": False, "error": "no AI connected"}
        if self.brief and _now() - self.brief.get("ts", 0) < BRIEF_COOLDOWN and not self.brief.get("text", "").startswith("["):
            return {"ok": True, **self.brief, "cached": True}
        act = self.active()
        lines = "\n".join(f"- [{f['severity'].upper()}] {f['title']} | evidence: {f['evidence'][:160]} | fix: {f['fix'][:100]}"
                          for f in act[:25]) or "- no open findings"
        prompt = ("You are the cyber-analyst module of BUCS, a local FastAPI command dashboard on the owner's Windows PC. "
                  f"These are the REAL findings measured by the latest scans (posture score {score(act)}/100, "
                  f"{len(self.accepted)} accepted risks excluded):\n{lines}\n\n"
                  "Write an analyst briefing in at most 5 short sentences: overall posture, the 1-3 most important "
                  "risks and why, and the single best next action. Use ONLY the findings listed; do not invent "
                  "vulnerabilities, versions or numbers. If there are no findings, say the posture is clean in one sentence.")
        try:
            text = await asyncio.wait_for(brain.think(prompt, agent="jarvis", timeout=60), 75)
        except Exception as e:
            text = f"[briefing unavailable: {type(e).__name__}]"
        self.brief = {"ts": _now(), "text": (text or "").strip()[:900], "findings": len(act),
                      "engine": getattr(brain, "provider_name", None) or getattr(brain, "mode", "")}
        await self._note("analyst briefing generated", "info", audit="secagent.brief")
        self._save()
        return {"ok": not self.brief["text"].startswith("["), **self.brief}

    async def loop(self) -> None:
        await asyncio.sleep(90)                       # let boot settle
        while True:
            lic = self.state.get("licensing")
            if lic is None or getattr(lic, "paid", True):
                try:
                    full = _now() - (self.last_full or 0) >= FULL_EVERY
                    await self.scan(full=full, reason="auto")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    try:
                        await self._note(f"scan loop error: {type(e).__name__}", "error")
                    except Exception:
                        pass
            await asyncio.sleep(LIGHT_EVERY)


# ═══ wiring (main.py FEATURE_MODULES) ════════════════════════════════════════

def _agent(ctx) -> SecAgent:
    st = ctx["state"]
    a = st.get("secagent")
    if not isinstance(a, SecAgent):
        a = SecAgent(st, ctx)
        st["secagent"] = a
    return a


class ScanReq(BaseModel):
    full: bool = True


class AcceptReq(BaseModel):
    id: str
    note: str = ""
    scope: str = "finding"        # "finding" | "rule"
    undo: bool = False


class ProposeReq(BaseModel):
    id: str


_LOCKED = {"ok": False, "error": "OBSERVER role — command actions are locked. Switch to COMMANDER to proceed."}


def build_router(ctx) -> APIRouter:
    r = APIRouter()
    agent = _agent(ctx)

    @r.get("/api/secagent")
    async def secagent_get():
        return agent.snapshot()

    @r.post("/api/secagent/scan")
    async def secagent_scan(req: ScanReq | None = None):
        if not ctx["commander"]():
            return _LOCKED
        if agent.lock.locked():
            return {"ok": False, "error": "a scan is already running"}
        wait = SCAN_COOLDOWN - (_now() - agent.last_manual)
        if wait > 0:
            return {"ok": False, "error": f"rate limited — next manual scan in {int(wait) + 1} s",
                    "retry_after": int(wait) + 1}
        agent.last_manual = _now()
        full = True if req is None else bool(req.full)
        await ctx["audit"]("secagent.scan.request", "full" if full else "light")
        asyncio.create_task(agent.scan(full=full, reason="manual"))
        return {"ok": True, "started": "full" if full else "light"}

    @r.post("/api/secagent/accept")
    async def secagent_accept(req: AcceptReq):
        if not ctx["commander"]():
            return _LOCKED
        return await agent.accept(req.id[:300], req.note, "rule" if req.scope == "rule" else "finding", req.undo)

    @r.post("/api/secagent/propose")
    async def secagent_propose(req: ProposeReq, request: Request):
        if not ctx["commander"]():
            return _LOCKED
        if not ctx["host_only"](request):
            return {"ok": False, "error": "fix proposals can only be sent from the host machine"}
        return await agent.propose(req.id[:300])

    @r.post("/api/secagent/brief")
    async def secagent_brief():
        if not ctx["commander"]():
            return _LOCKED
        return await agent.make_brief()

    return r


async def start(ctx) -> None:
    await _agent(ctx).loop()
