"""OPEN INTELLIGENCE: parsers against recorded upstream samples, source-link safety, settings
validation/persistence/gating, and the agent facts reader. No test needs the network except the
IsolatedServer endpoint test, which skips gracefully when upstreams are unreachable."""
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import openintel  # noqa: E402
import openintel_sources as os_  # noqa: E402
from source_links import safe_url  # noqa: E402

S = json.loads((ROOT / "tests" / "fixtures" / "openintel_samples.json").read_text(encoding="utf-8"))


def _links(d: dict) -> list[str]:
    out = []
    for i in (d.get("items") or []):
        for k in ("url", "discuss_url"):
            if i.get(k):
                out.append(i[k])
    return out


class Parsers(unittest.TestCase):
    def check(self, d: dict, min_items: int = 1):
        self.assertIsInstance(d, dict)
        items = d.get("items") or []
        self.assertGreaterEqual(len(items), min_items, d)
        for u in _links(d):
            self.assertEqual(safe_url(u), u, f"unsafe or non-http link {u!r}")
            self.assertFalse(u.lower().startswith("javascript:"))
        for i in items:
            self.assertTrue(str(i.get("title") or "").strip(), i)
        return d

    def test_air(self):
        d = self.check(os_.parse_air(S["air"], page=os_.page_air(51.5, -0.1)))
        self.assertIn("us_aqi", d["summary"])

    def test_who(self):
        self.check(os_.parse_who(S["who"]))

    def test_launches(self):
        self.check(os_.parse_launches(S["launches"], now=0))

    def test_neo(self):
        self.check(os_.parse_neo(S["neo"]))

    def test_iss(self):
        d = os_.parse_iss(S["iss"], home=(51.5, -0.1))
        self.assertIsInstance(d, dict)
        s = d.get("summary") or {}
        self.assertTrue(-90 <= float(s["lat"]) <= 90 and -180 <= float(s["lon"]) <= 180)

    def test_fx(self):
        d = self.check(os_.parse_fx(S["fx"], watch=("INR", "EUR")))
        ids = [str(i.get("id")) for i in d["items"]]
        self.assertTrue(any("INR" in x for x in ids), ids)

    def test_arxiv(self):
        self.check(os_.parse_arxiv(S["arxiv"]))

    def test_hn(self):
        self.check(os_.parse_hn(S["hn"]))

    def test_co2(self):
        d = os_.parse_co2(S["co2"])
        self.assertGreater(float(d["summary"]["ppm"]), 300)

    def test_holidays(self):
        rows = os_.parse_holidays(S["holidays"], "US")
        self.assertTrue(rows)
        for r in rows:
            if r.get("url"):
                self.assertEqual(safe_url(r["url"]), r["url"])


class LinkBuilders(unittest.TestCase):
    def test_builders_reject_bad_ids(self):
        for fn, bad in ((os_.launch_url, "../../etc"), (os_.who_item_url, "javascript:alert(1)"),
                        (os_.hn_item_url, "abc;rm"), (os_.neo_url, "<script>")):
            self.assertIsNone(fn(bad), f"{fn.__name__} accepted {bad!r}")

    def test_builders_accept_real_ids(self):
        self.assertTrue(os_.hn_item_url(41234567).startswith("https://news.ycombinator.com/item?id="))
        u = os_.fx_pair_url("USD", "INR")
        self.assertTrue(u is None or u.startswith("https://"))


class Settings(unittest.TestCase):
    def test_clean_settings_enforces_limits_and_codes(self):
        s, errs = openintel.clean_settings({"fx_base": "XXX", "fx_watch": ["INR", "EUR", "<b>"],
                                            "arxiv_cats": ["cs.AI", "nope"], "refresh_s": 5,
                                            "holiday_countries": ["in", "zzz"], "order": ["space", "air"]})
        self.assertNotEqual(s.get("fx_base"), "XXX")
        self.assertNotIn("<b>", s.get("fx_watch", []))
        self.assertNotIn("nope", s.get("arxiv_cats", []))
        self.assertGreaterEqual(s.get("refresh_s", openintel.REFRESH_MIN), openintel.REFRESH_MIN)
        self.assertTrue(errs)

    def test_defaults_are_valid(self):
        s, errs = openintel.clean_settings(dict(openintel.DEFAULTS))
        self.assertEqual(errs, [])
        self.assertEqual(sorted(s["order"]), sorted(openintel.PANELS))


class Endpoints(unittest.TestCase):
    """Real server in a temp copy: settings CRUD, persistence across restart, commander gating."""

    @classmethod
    def setUpClass(cls):
        from harness import IsolatedServer
        cls.srv = IsolatedServer(owner=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _get(self, path):
        import httpx
        return httpx.get(self.srv.base + path, timeout=30).json()

    def _post(self, path, body):
        import httpx
        return httpx.post(self.srv.base + path, json=body, timeout=30).json()

    def test_deck_and_settings_roundtrip_and_persist(self):
        deck = self._get("/api/openintel")
        self.assertIn("sources", deck)
        self.assertIn("panels", deck)
        for k, src in deck["sources"].items():
            self.assertIn(src.get("status"), ("ok", "stale", "error", "pending", "idle", None), k)
            if src.get("home"):
                self.assertEqual(safe_url(src["home"]), src["home"])
        r = self._post("/api/openintel/settings", {"fx_base": "EUR", "fx_watch": ["INR", "USD"]})
        self.assertTrue(r.get("ok"), r)
        self.srv.restart()
        s = self._get("/api/openintel/settings")
        s = s.get("settings", s)
        self.assertEqual(s["fx_base"], "EUR")
        self.assertIn("INR", s["fx_watch"])
        self._post("/api/openintel/settings/reset", {})

    def test_live_source_links_are_safe_when_reachable(self):
        deadline = time.time() + 20
        deck = {}
        while time.time() < deadline:
            deck = self._get("/api/openintel")
            if any((s.get("data") or {}).get("items") for s in deck.get("sources", {}).values()):
                break
            time.sleep(2)
        got = [s for s in deck.get("sources", {}).values() if (s.get("data") or {}).get("items")]
        if not got:
            self.skipTest("no upstream reachable from this machine right now")
        for src in got:
            for u in _links(src["data"]):
                self.assertEqual(safe_url(u), u)


class ObserverGating(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from harness import IsolatedServer
        cls.srv = IsolatedServer(owner=False).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_observer_cannot_change_settings(self):
        import httpx
        r = httpx.post(self.srv.base + "/api/openintel/settings", json={"fx_base": "GBP"}, timeout=30).json()
        self.assertFalse(r.get("ok", False), r)


class AgentFacts(unittest.TestCase):
    def test_reader_reports_unavailable_when_module_absent(self):
        import asyncio
        out = asyncio.run(openintel.agent_facts({}, "what is the air quality?"))
        self.assertIn("unavailable", out)

    def test_routing_sends_open_data_questions_here(self):
        import dashboard_context as dc
        for q in ("what is the air quality?", "any rocket launches this week?", "USD to INR rate?",
                  "top arxiv papers today?"):
            self.assertIn("openintel", dc.route(q), q)
        self.assertEqual(dc.route("tell me a joke"), [])


if __name__ == "__main__":
    unittest.main()
