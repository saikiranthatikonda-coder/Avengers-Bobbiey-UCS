"""DOOM GATE (v8.7) — DOOM's security verdict on every self-generated code change, and BOB's
sign-off rule. Pure static analysis of the patch (no execution, no network):

  BLOCK   writes a private file (secrets, memory, licence, .update/…), adds something that looks
          like a credential, executes dynamic code (eval/exec/compile/__import__ of input),
          spawns a shell (shell=True, os.system, os.popen), deserialises untrusted data
          (pickle/marshal/yaml.load without SafeLoader), turns off TLS verification, or deletes
          whole trees outside the sandbox.
  REVIEW  touches the security perimeter (auth gate, origin guard, licence gate, RBAC, this
          gate, the updater/signature code), adds a new outbound host, adds a subprocess call,
          or widens CORS / allowed hosts. Deploy needs the commander's explicit acknowledgement.
  CLEAR   none of the above.

BOB — the supreme orchestrator — never signs a BLOCK, and signs a REVIEW only with the
commander's acknowledgement. Every deploy still needs the commander's approval.
"""

from __future__ import annotations

import base64
import re

PRIVATE = re.compile(r"(^|/)(\.env|credentials\.json|token\.json|auth\.json|fleet_token\.txt|pair_code\.txt|node_id\.txt|"
                     r"node_token\.txt|governance\.json|operator_memory\.json|audio_pref\.json|audit\.log|model_pref\.json|"
                     r"commander_memory\.md|license_signing\.key|licences_issued\.json|providers\.json|license\.json|"
                     r"operators\.json|commerce_[^/]*\.db)$|(^|/)\.update/|(^|/)\.git/", re.I)
PERIMETER = re.compile(r"^(main\.py|auth\w*\.py|rbac\w*\.py|licensing\w*\.py|updater\.py|doomgate\.py|"
                       r"tools/release\.py|computeruse_core\.py)$", re.I)
PERIMETER_SYMBOLS = re.compile(r"\b(auth_gate|origin_guard|licence_gate|_origin_problem|_is_loopback|_commander|"
                               r"verify_manifest|PUBLIC_PATHS|LOCAL_HOSTNAMES|JARVIS_ALLOWED_HOSTS)\b")
BLOCK_RULES = [
    ("dynamic code execution", re.compile(r"(?<![\w.])(eval|exec)\s*\(|\bcompile\s*\([^)]*['\"]exec['\"]")),
    ("shell command execution", re.compile(r"shell\s*=\s*True|\bos\.(system|popen)\s*\(")),
    ("unsafe deserialisation", re.compile(r"\b(pickle|cPickle|marshal|dill)\.loads?\s*\(|\byaml\.load\s*\((?![^)]*SafeLoader)")),
    ("TLS verification disabled", re.compile(r"verify\s*=\s*False|CERT_NONE|_create_unverified_context|check_hostname\s*=\s*False")),
    ("recursive delete", re.compile(r"\bshutil\.rmtree\s*\(\s*(['\"]?[/\\]|Path\.home|os\.path\.expanduser|HERE\b|ROOT\b)")),
    ("embedded credential", re.compile(r"(sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|"
                                       r"xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|"
                                       r"(?i:(password|passwd|secret|api_?key|token)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]))")),
]
REVIEW_RULES = [
    ("new subprocess call", re.compile(r"\bsubprocess\.(run|Popen|call|check_output|check_call)\s*\(")),
    ("CORS / host allow-list widened", re.compile(r"allow_origins\s*=\s*\[\s*['\"]\*|Access-Control-Allow-Origin['\"]?\s*[:,]\s*['\"]\*")),
    ("dynamic import", re.compile(r"\b__import__\s*\(|importlib\.import_module\s*\((?!\s*['\"])")),
]
URL = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _text(info) -> tuple[str | None, str]:
    """(new file text or None for a delete, op)"""
    if not isinstance(info, dict):
        return (info if isinstance(info, str) else None), "write"
    op = info.get("op", "write")
    new = info.get("new")
    if new is None:
        return None, op
    try:
        return base64.b64decode(new).decode("utf-8", "replace"), op
    except Exception:
        return str(new), op


def scan_patch(files: dict, old: dict | None = None) -> dict:
    """files: {path: {"op", "new" (base64) | None}} (devagent patch format) or {path: text}.
    old: optional {path: previous text} so only ADDED lines are judged. Returns the verdict."""
    block, review, hosts = [], [], set()
    old = old or {}
    for path, info in (files or {}).items():
        p = str(path).replace("\\", "/")
        while p.startswith("./"):                   # strip "./" prefixes only — never the dot of ".env" / ".update"
            p = p[2:]
        p = p.lstrip("/")
        text, op = _text(info)
        if PRIVATE.search(p):
            block.append(f"{p}: writes a private file")
            continue
        if PERIMETER.search(p):
            review.append(f"{p}: security-perimeter file")
        if text is None:
            if op in ("delete", "remove"):
                review.append(f"{p}: file deleted")
            continue
        before = set((old.get(path) or "").splitlines())
        added = "\n".join(l for l in text.splitlines() if l not in before)
        for name, rx in BLOCK_RULES:
            if rx.search(added):
                block.append(f"{p}: {name}")
        for name, rx in REVIEW_RULES:
            if rx.search(added):
                review.append(f"{p}: {name}")
        if PERIMETER_SYMBOLS.search(added):
            review.append(f"{p}: changes security-perimeter code")
        for h in URL.findall(added):
            h = h.lower()
            if not h.endswith(("localhost", "example.com", "example.org")) and h not in ("127.0.0.1",):
                hosts.add(h)
    if hosts:
        review.append("new outbound host(s): " + ", ".join(sorted(hosts)[:8]))
    verdict = "block" if block else "review" if review else "clear"
    return {"verdict": verdict, "block": block[:20], "review": sorted(set(review))[:20], "hosts": sorted(hosts)[:20],
            "by": "DOOM", "summary": _summary(verdict, block, review)}


def _summary(verdict, block, review) -> str:
    if verdict == "block":
        return f"DOOM blocks this change: {block[0]}" + (f" (+{len(block) - 1} more)" if len(block) > 1 else "") + "."
    if verdict == "review":
        return f"DOOM flags {len(set(review))} item(s) for your review: {sorted(set(review))[0]}."
    return "DOOM finds nothing dangerous in this change."


def bob_signoff(verdict: dict, acknowledged: bool) -> tuple[bool, str]:
    """BOB's rule: never a BLOCK; a REVIEW only with the commander's acknowledgement."""
    v = (verdict or {}).get("verdict", "block")
    if v == "block":
        return False, "BOB refuses to sign: " + verdict.get("summary", "DOOM blocked this change.")
    if v == "review" and not acknowledged:
        return False, ("BOB needs your acknowledgement first: " + verdict.get("summary", "") +
                       " Approve again with “I have reviewed DOOM's findings” ticked.")
    return True, "BOB signs off" + (" (DOOM findings acknowledged by the commander)." if v == "review" else ".")
