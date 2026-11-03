"""Cyber Analyst (secagent.py): real-scan parsing, secret hygiene that never leaks values,
commander lock, rate limit, accepted-risk suppression, proposals that never apply anything.
No network: OSV / KEV / git / netstat / netsh / Defender are all mocked."""

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import secagent  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# fake secret values are assembled at runtime so this file never matches a secret rule itself
FAKE_AWS = "AKIA" + "Q7XZ3TESTFAKE0K9"
FAKE_ANTHROPIC = "sk-ant-" + "api03-" + "x" * 30

PKGS = [("jinja2", "2.10"), ("fastapi", "0.115.6"), ("pip", "22.0")]
BATCH = {"results": [
    {"vulns": [{"id": "GHSA-462w-v97r-4m45", "modified": "m1"}, {"id": "PYSEC-2019-217", "modified": "m2"}]},
    {},
    {"vulns": [{"id": "GHSA-pip0-0000-0000", "modified": "m3"}]},
]}
DETAILS = {
    "GHSA-462w-v97r-4m45": {
        "id": "GHSA-462w-v97r-4m45", "summary": "Jinja2 sandbox escape via string formatting",
        "aliases": ["CVE-2019-10906", "PYSEC-2019-217"], "modified": "m1",
        "database_specific": {"severity": "HIGH"},
        "affected": [{"package": {"name": "Jinja2", "ecosystem": "PyPI"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.10.1"}]}]}]},
    "PYSEC-2019-217": {
        "id": "PYSEC-2019-217", "summary": "dup", "aliases": ["CVE-2019-10906", "GHSA-462w-v97r-4m45"], "modified": "m2",
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
        "affected": [{"package": {"name": "jinja2", "ecosystem": "PyPI"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.10.1"}]}]}]},
    "GHSA-pip0-0000-0000": {
        "id": "GHSA-pip0-0000-0000", "summary": "pip issue", "aliases": [], "modified": "m3",
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:L/A:N"}],
        "affected": [{"package": {"name": "pip", "ecosystem": "PyPI"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "23.3"}]}]}]},
}
NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:445            0.0.0.0:0              LISTENING       4
  TCP    0.0.0.0:6379           0.0.0.0:0              LISTENING       900
  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       1234
  TCP    [::]:3000              [::]:0                 LISTENING       555
  TCP    10.0.0.5:51000         20.1.1.1:443           ESTABLISHED     77
  UDP    0.0.0.0:5353           *:*                                    88
"""
FIREWALL = """
Domain Profile Settings:
----------------------------------------------------------------------
State                                 ON

Private Profile Settings:
----------------------------------------------------------------------
State                                 ON

Public Profile Settings:
----------------------------------------------------------------------
State                                 OFF
Ok.
"""
MAIN_OK = ('@app.middleware("http")\nasync def origin_guard(request, call_next):\n    pass\n'
           '@app.middleware("http")\nasync def auth_gate(request, call_next):\n    pass\n')


def run(coro):
    return asyncio.run(coro)


class Parsing(unittest.TestCase):
    def test_cvss3(self):
        self.assertEqual(secagent.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"), 9.8)
        self.assertEqual(secagent.cvss3_score("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:N/A:N"), 8.6)
        self.assertIsNone(secagent.cvss3_score("garbage"))

    def test_osv_results_become_findings(self):
        hits = secagent.parse_osv_batch(PKGS, BATCH)
        self.assertEqual(set(hits), {("jinja2", "2.10"), ("pip", "22.0")})
        fs = secagent.dep_findings(hits, DETAILS, direct={"jinja2"}, kev_cves=set())
        jinja = [f for f in fs if f["package"] == "jinja2"]
        self.assertEqual(len(jinja), 1, "GHSA + PYSEC aliases of one advisory are one finding")
        j = jinja[0]
        self.assertEqual(j["severity"], "high")
        self.assertIn("CVE-2019-10906", j["evidence"])
        self.assertIn("2.10.1", j["fix"])
        self.assertTrue(j["fixable"])
        self.assertIn("requirements.txt", j["task"])
        pip = [f for f in fs if f["package"] == "pip"][0]
        self.assertEqual(pip["severity"], "medium")            # computed CVSS 4.x → medium
        self.assertFalse(pip["fixable"], "venv tooling is an operator action, not a code change")

    def test_kev_listed_cve_escalates_to_critical(self):
        hits = secagent.parse_osv_batch(PKGS, BATCH)
        fs = secagent.dep_findings(hits, DETAILS, direct=set(), kev_cves={"CVE-2019-10906"})
        j = [f for f in fs if f["package"] == "jinja2"][0]
        self.assertEqual(j["severity"], "critical")
        self.assertEqual(j["kev"], ["CVE-2019-10906"])

    def test_netstat_and_firewall(self):
        ls = secagent.parse_netstat(NETSTAT)
        self.assertEqual([(l["ip"], l["port"]) for l in ls],
                         [("0.0.0.0", 445), ("0.0.0.0", 6379), ("127.0.0.1", 8765), ("::", 3000)])
        fw = secagent.parse_firewall(FIREWALL)
        self.assertEqual(fw, {"Domain": "ON", "Private": "ON", "Public": "OFF"})
        fs = secagent.exposure_findings(ls, {4: "System", 900: "redis-server.exe", 555: "node.exe"},
                                        "127.0.0.1", 8765, False, False, fw)
        ids = {f["id"]: f for f in fs}
        self.assertEqual(ids["exposure:firewall:public"]["severity"], "high")
        self.assertEqual(ids["exposure:port:6379"]["severity"], "high")
        self.assertEqual(ids["exposure:port:445"]["severity"], "high", "firewall off escalates SMB")
        self.assertIn("3000 (node.exe)", ids["exposure:listeners"]["evidence"])
        self.assertFalse(any("8765" in f["id"] for f in fs), "loopback BUCS is not exposure")

    def test_bucs_bound_to_lan(self):
        fs = secagent.exposure_findings([], {}, "0.0.0.0", 8765, True, False, {})
        self.assertEqual(fs[0]["id"], "exposure:bucs:remote")
        self.assertEqual(fs[0]["severity"], "medium")
        fs = secagent.exposure_findings([], {}, "0.0.0.0", 8765, False, False, {})
        self.assertEqual(fs[0]["id"], "exposure:bucs:bound")

    def test_defender(self):
        self.assertEqual(secagent.defender_findings(None), [])
        fs = secagent.defender_findings({"AMServiceEnabled": True, "AntivirusEnabled": True,
                                         "RealTimeProtectionEnabled": False, "AntivirusSignatureAge": 12})
        self.assertEqual({f["rule"] for f in fs}, {"defender_rtp_off", "defender_signatures_stale"})

    def test_config(self):
        self.assertEqual(secagent.config_findings(MAIN_OK, "", [], None, (3, 12, 1), today=date(2026, 11, 3)), [])
        fs = secagent.config_findings("app.add_middleware(CORSMiddleware)", "uvicorn --reload", ["evil.py"],
                                      {"managed_by_git": False, "available": True, "latest": "9.0.0", "version": "7.8.0"},
                                      (3, 10, 4), today=date(2026, 11, 3))
        rules = {f["rule"]: f for f in fs}
        self.assertEqual(rules["middleware_missing"]["severity"], "critical")
        for r in ("cors_enabled", "reload_enabled", "permission_bypass", "bucs_outdated", "python_eol"):
            self.assertIn(r, rules)
        self.assertTrue(rules["cors_enabled"]["fixable"])

    def test_score(self):
        self.assertEqual(secagent.score([]), 100)
        one = [{"id": "a", "category": "config", "severity": "critical"}]
        self.assertEqual(secagent.score(one), 73)
        same_pkg = [{"id": f"d{i}", "category": "deps", "package": "x", "severity": "high"} for i in range(10)]
        self.assertEqual(secagent.score(same_pkg), secagent.score(same_pkg[:1]), "one upgrade = one problem")


class Secrets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "app.py").write_text(f'KEY = "{FAKE_AWS}"\nx = 1\nANT = "{FAKE_ANTHROPIC}"\n', encoding="utf-8")
        (self.root / "credentials.json").write_text('{"client_secret": "zzz"}', encoding="utf-8")
        (self.root / "clean.py").write_text("print('hello')\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_never_returns_secret_values(self):
        tracked = ["app.py", "credentials.json", "clean.py", ".env.example"]
        ignored = set(secagent.PROTECTED_FILES) - {".env"}
        fs = secagent.secret_findings(self.root, tracked, ignored)
        ids = {f["id"] for f in fs}
        self.assertIn("secret:tracked:credentials.json", ids)
        self.assertIn("secret:pattern:app.py:aws_access_key_id", ids)
        self.assertIn("secret:pattern:app.py:anthropic_api_key", ids)
        self.assertIn("secret:unignored:.env", ids)
        self.assertNotIn("secret:tracked:.env.example", ids)
        blob = json.dumps(fs)
        for v in (FAKE_AWS, FAKE_ANTHROPIC, "zzz", FAKE_AWS[4:12]):
            self.assertNotIn(v, blob, "a secret value leaked into a finding")
        aws = next(f for f in fs if f["id"] == "secret:pattern:app.py:aws_access_key_id")
        self.assertIn("line(s) 1", aws["evidence"])

    def test_secagent_source_does_not_match_its_own_rules(self):
        src = Path(secagent.__file__)
        self.assertEqual(secagent.scan_file_for_secrets(src), [])
        self.assertEqual(secagent.scan_file_for_secrets(Path(__file__)), [])


class FakeDev:
    def __init__(self, busy=False):
        self.lock = asyncio.Lock()
        self.busy = busy
        self.diagnosed, self.applied, self.shipped, self.kept = [], 0, 0, 0

    async def diagnose(self, request):
        self.diagnosed.append(request)
        return {"ok": True, "summary": "proposal ready", "fixable": True, "id": "abc"}

    async def apply(self):
        self.applied += 1

    async def ship(self):
        self.shipped += 1

    async def keep(self):
        self.kept += 1


class Brain:
    provider_name = "test"

    def __init__(self):
        self.prompts = []

    async def think(self, prompt, agent="jarvis", timeout=60):
        self.prompts.append(prompt)
        return "Posture is weak: jinja2 has a sandbox escape. Upgrade it."


def make_agent(tmp: Path, state=None, ctx=None):
    root = tmp / "repo"
    root.mkdir(exist_ok=True)
    (root / "main.py").write_text(MAIN_OK, encoding="utf-8")
    (root / "requirements.txt").write_text("jinja2==2.10\nfastapi==0.115.6\n", encoding="utf-8")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    a = secagent.SecAgent(state if state is not None else {}, ctx or {}, root=root, state_file=tmp / "secagent.json")

    async def osv_query(pkgs):
        return BATCH
    async def osv_detail(vid):
        return DETAILS[vid]
    async def kev_fetch():
        return {"catalogVersion": "2026.11.01", "dateReleased": "2026-11-01", "count": 1,
                "vulnerabilities": [{"cveID": "CVE-2000-0001", "vendorProject": "X", "product": "Y",
                                     "dateAdded": "2026-10-30", "vulnerabilityName": "z"}]}
    async def git_tracked():
        return ["main.py", "requirements.txt", "app.py"]
    async def git_ignored(names):
        return set(names)
    async def listeners():
        return secagent.parse_netstat(NETSTAT)
    async def firewall():
        return secagent.parse_firewall(FIREWALL.replace("OFF", "ON"))
    async def defender():
        return {"AMServiceEnabled": True, "AntivirusEnabled": True, "RealTimeProtectionEnabled": True,
                "AntivirusSignatureAge": 0, "LastHotfix": None}
    for name, fn in dict(osv_query=osv_query, osv_detail=osv_detail, kev_fetch=kev_fetch, git_tracked=git_tracked,
                         git_ignored=git_ignored, listeners=listeners, firewall=firewall, defender=defender).items():
        setattr(a, name, fn)
    a._password_set = lambda: False
    return a


PATCH_PKGS = mock.patch.object(secagent, "installed_packages", lambda: list(PKGS))


class AgentFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        PATCH_PKGS.start()
        self.addCleanup(PATCH_PKGS.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_scan_trend_and_persistence(self):
        a = make_agent(self.dir)
        r = run(a.scan(full=True))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["errors"], [])
        ids = {f["id"] for f in a.active()}
        self.assertIn("dep:jinja2:GHSA-462w-v97r-4m45", ids)
        self.assertIn("exposure:port:6379", ids)
        self.assertEqual(set(r["new"]), ids)
        saved = json.loads((self.dir / "secagent.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["ruleset"], secagent.RULESET_VERSION)
        self.assertEqual(saved["intel"]["kev"]["version"], "2026.11.01")
        self.assertTrue(saved["intel"]["osv_updated"])
        # the vuln gets fixed → next scan reports it resolved
        global BATCH
        old = BATCH
        try:
            BATCH = {"results": [{}, {}, old["results"][2]]}
            r2 = run(a.scan(full=True))
        finally:
            BATCH = old
        self.assertIn("dep:jinja2:GHSA-462w-v97r-4m45", r2["resolved"])
        self.assertEqual(len(a.snapshot()["trend"]), 2)
        # a fresh agent reloads state from disk
        b = secagent.SecAgent({}, {}, root=a.root, state_file=self.dir / "secagent.json")
        self.assertEqual(len(b.history), 2)

    def test_probe_failure_keeps_last_known_results(self):
        a = make_agent(self.dir)
        run(a.scan(full=True))

        async def boom(pkgs):
            raise OSError("offline")
        a.osv_query = boom
        r = run(a.scan(full=True))
        self.assertTrue(any(e.startswith("deps") for e in r["errors"]))
        self.assertEqual(a.checks["deps"]["status"], "error")
        self.assertTrue(all(f.get("stale") for f in a.findings["deps"]))

    def test_accepted_risk_suppression(self):
        a = make_agent(self.dir)
        run(a.scan(full=True))
        s0 = a.snapshot()["score"]
        fid = "exposure:port:6379"
        r = run(a.accept(fid, note="lab redis, firewalled"))
        self.assertTrue(r["ok"])
        self.assertNotIn(fid, {f["id"] for f in a.active()})
        self.assertIn(fid, {f["id"] for f in a.snapshot()["accepted"]})
        self.assertGreater(a.snapshot()["score"], s0)
        run(a.scan(full=False))                                   # learned: stays suppressed after rescans
        self.assertNotIn(fid, {f["id"] for f in a.active()})
        run(a.accept("exposure:listeners", scope="rule"))       # whole rule
        self.assertFalse([f for f in a.active() if f["rule"] == "lan_listeners"])
        run(a.accept(fid, undo=True))
        self.assertIn(fid, {f["id"] for f in a.active()})
        self.assertFalse(run(a.accept("nope:unknown"))["ok"])

    def test_proposal_only_requests_a_diagnosis(self):
        dev = FakeDev()
        a = make_agent(self.dir, state={"dev": dev})
        run(a.scan(full=True))
        before = {p.name: p.read_bytes() for p in a.root.iterdir()}

        async def go():
            r = await a.propose("dep:jinja2:GHSA-462w-v97r-4m45")
            await asyncio.sleep(0.05)
            return r
        r = run(go())
        self.assertTrue(r["ok"] and r["queued"], r)
        self.assertEqual(len(dev.diagnosed), 1)
        self.assertIn("jinja2", dev.diagnosed[0])
        self.assertIn("2.10.1", dev.diagnosed[0])
        self.assertEqual((dev.applied, dev.shipped, dev.kept), (0, 0, 0), "proposal must never apply/ship/keep")
        self.assertEqual(before, {p.name: p.read_bytes() for p in a.root.iterdir()}, "no file changed")
        # non-code findings cannot be proposed
        self.assertFalse(run(a.propose("exposure:port:6379"))["ok"])

    def test_proposal_when_engineering_busy_or_missing(self):
        dev = FakeDev()
        a = make_agent(self.dir, state={"dev": dev})
        run(a.scan(full=True))

        async def busy():
            async with dev.lock:
                return await a.propose("dep:jinja2:GHSA-462w-v97r-4m45")
        r = run(busy())
        self.assertFalse(r["ok"])
        self.assertIn("task", r)
        self.assertEqual(dev.diagnosed, [])
        a.state.pop("dev")
        r = run(a.propose("dep:jinja2:GHSA-462w-v97r-4m45"))
        self.assertTrue(r["ok"])
        self.assertFalse(r["queued"])
        self.assertIn("requirements.txt", r["task"])

    def test_brief_is_grounded_in_findings(self):
        brain = Brain()
        a = make_agent(self.dir, state={"brain": brain})
        run(a.scan(full=True))
        r = run(a.make_brief())
        self.assertTrue(r["ok"])
        self.assertIn("Jinja2 sandbox escape", brain.prompts[0])
        self.assertIn("do not invent", brain.prompts[0])
        self.assertFalse(run(make_agent(self.dir).make_brief())["ok"], "no AI connected → honest error")


class Router(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.role = {"commander": True, "host": True}
        self.audits = []

        async def audit(action, detail=""):
            self.audits.append(action)
        self.state = {"dev": FakeDev()}
        self.ctx = {"state": self.state, "audit": audit, "commander": lambda: self.role["commander"],
                    "host_only": lambda request: self.role["host"]}
        self.agent = make_agent(self.dir, state=self.state, ctx=self.ctx)
        self.state["secagent"] = self.agent
        app = FastAPI()
        app.include_router(secagent.build_router(self.ctx))
        self.client = TestClient(app)
        PATCH_PKGS.start()
        self.addCleanup(PATCH_PKGS.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_snapshot(self):
        d = self.client.get("/api/secagent").json()
        for k in ("score", "counts", "findings", "trend", "intel", "log", "checks", "last_scan"):
            self.assertIn(k, d)

    def test_commander_lock(self):
        self.role["commander"] = False
        for path, body in (("/api/secagent/scan", {}), ("/api/secagent/accept", {"id": "x"}),
                           ("/api/secagent/propose", {"id": "x"}), ("/api/secagent/brief", {})):
            r = self.client.post(path, json=body).json()
            self.assertFalse(r["ok"], path)
            self.assertIn("OBSERVER", r["error"])
        self.assertEqual(self.audits, [])

    def test_scan_rate_limited(self):
        calls = []

        async def fake_scan(full=True, reason="manual"):
            calls.append(full)
            return {"ok": True}
        self.agent.scan = fake_scan
        r1 = self.client.post("/api/secagent/scan", json={"full": True}).json()
        self.assertTrue(r1["ok"], r1)
        r2 = self.client.post("/api/secagent/scan", json={"full": True}).json()
        self.assertFalse(r2["ok"])
        self.assertIn("rate limited", r2["error"])
        self.assertGreater(r2["retry_after"], 0)
        self.assertIn("secagent.scan.request", self.audits)

    def test_propose_host_only(self):
        run(self.agent.scan(full=True))
        self.role["host"] = False
        r = self.client.post("/api/secagent/propose", json={"id": "dep:jinja2:GHSA-462w-v97r-4m45"}).json()
        self.assertFalse(r["ok"])
        self.assertIn("host machine", r["error"])
        self.assertEqual(self.state["dev"].diagnosed, [])

    def test_accept_via_api(self):
        run(self.agent.scan(full=True))
        r = self.client.post("/api/secagent/accept", json={"id": "exposure:port:6379", "note": "ok"}).json()
        self.assertTrue(r["ok"])
        self.assertNotIn("exposure:port:6379", {f["id"] for f in r["findings"]})
        self.assertIn("secagent.accept", self.audits)


if __name__ == "__main__":
    unittest.main()
