"""Source links: every external datum on the dashboard carries a valid http(s) link to its
original source (or none — never a guessed or unsafe one). Recorded/sample upstream payloads,
no network, plus an isolated-server check of the real endpoints (skips gracefully offline)."""

import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

import source_links as sl  # noqa: E402
import disaster  # noqa: E402
import globalmap_sources as gs  # noqa: E402
import livefacts  # noqa: E402
import secagent  # noqa: E402
from harness import ROOT  # noqa: E402
from test_disaster import EONET, GDACS, IODA, KEV, SACHET, USGS  # noqa: E402

HTTP = re.compile(r"^https?://[^\s\"'<>\\`]+$")
BAD = ["javascript:alert(1)", "JAVASCRIPT:alert(1)", " javascript:alert(1)", "data:text/html,<b>x</b>",
       "file:///C:/Windows/win.ini", "vbscript:x", "http://", "https://exa mple.com", "https://x.com/\"onmouseover=1",
       "https://x.com/<script>", "//evil.example/x", "ftp://x.com/a", "", None, 42, "https://x.com/`x`"]


def assert_source(tc, url, allow_none=False):
    if url is None and allow_none:
        return
    tc.assertIsInstance(url, str)
    tc.assertRegex(url, HTTP)
    tc.assertNotIn("javascript:", url.lower())


class SafeUrl(unittest.TestCase):
    def test_rejects_unsafe(self):
        for u in BAD:
            self.assertIsNone(sl.safe_url(u), u)

    def test_accepts_http_and_https(self):
        for u in ("https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd", "http://example.com/a?b=1&c=2"):
            self.assertEqual(sl.safe_url(u), u)
        self.assertIsNone(sl.safe_url("https://x.com/" + "a" * 3000))   # bounded length


class Builders(unittest.TestCase):
    def test_good_ids(self):
        self.assertEqual(sl.nvd_cve("cve-2024-3400"), "https://nvd.nist.gov/vuln/detail/CVE-2024-3400")
        self.assertEqual(sl.osv_vuln("GHSA-abcd-1234-wxyz"), "https://osv.dev/vulnerability/GHSA-abcd-1234-wxyz")
        self.assertEqual(sl.usgs_event("us7000abcd"), "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd")
        self.assertEqual(sl.gdacs_report("eq", 1234, 5), "https://www.gdacs.org/report.aspx?eventtype=EQ&eventid=1234&episodeid=5")
        self.assertEqual(sl.ioda_country("in"), "https://ioda.inetintel.cc.gatech.edu/country/IN")
        self.assertEqual(sl.adsbx_aircraft("A0B1C2"), "https://globe.adsbexchange.com/?icao=a0b1c2")
        self.assertEqual(sl.n2yo_satellite(25544), "https://www.n2yo.com/satellite/?s=25544")
        self.assertEqual(sl.coingecko_coin("bitcoin"), "https://www.coingecko.com/en/coins/bitcoin")
        self.assertEqual(sl.wikipedia_article("Indian Space Research Organisation"),
                         "https://en.wikipedia.org/wiki/Indian_Space_Research_Organisation")
        self.assertEqual(sl.open_meteo(17.385, 78.4867), "https://open-meteo.com/en/docs?latitude=17.3850&longitude=78.4867")
        self.assertEqual(sl.gmail_message("18c2f3a4b5d6e7f8"), "https://mail.google.com/mail/u/0/#all/18c2f3a4b5d6e7f8")

    def test_bad_ids_give_no_link(self):
        bad = ["", None, "javascript:alert(1)", "../../etc", "x y", "<script>", "a" * 300]
        for b in bad:
            for fn in (sl.nvd_cve, sl.osv_vuln, sl.usgs_event, sl.ioda_country, sl.adsbx_aircraft,
                       sl.n2yo_satellite, sl.celestrak_elements, sl.coingecko_coin, sl.gmail_message):
                self.assertIsNone(fn(b), (fn.__name__, b))
        self.assertIsNone(sl.gdacs_report("XX", 1))
        self.assertIsNone(sl.gdacs_report("EQ", "1;drop"))
        self.assertIsNone(sl.n2yo_satellite(0))
        self.assertIsNone(sl.n2yo_satellite(-5))
        self.assertIsNone(sl.open_meteo(95, 0))
        self.assertIsNone(sl.open_meteo("x", 0))
        self.assertIsNone(sl.wikipedia_article("X", lang="javascript:"))
        self.assertIsNone(sl.google_link("http://www.google.com/calendar/event?eid=1"))   # https only
        self.assertIsNone(sl.google_link("https://evil.example/calendar"))               # Google hosts only
        self.assertEqual(sl.google_link("https://www.google.com/calendar/event?eid=abc"),
                         "https://www.google.com/calendar/event?eid=abc")


class DisasterFeeds(unittest.TestCase):
    def _all(self, items):
        self.assertTrue(items)
        for i in items:
            assert_source(self, i["link"])
            self.assertEqual(i["source_url"], i["link"])

    def test_every_feed_item_links_to_source(self):
        self._all(disaster.parse_usgs(USGS))
        self._all(disaster.parse_gdacs(GDACS))
        self._all(disaster.parse_eonet(EONET))
        self._all(disaster.parse_sachet(SACHET, now=time.time()))
        self._all(disaster.parse_kev(KEV))
        self._all(disaster.parse_ioda(IODA, now=time.time()))

    def test_gdacs_without_report_builds_documented_page(self):
        flood = [i for i in disaster.parse_gdacs(GDACS) if i["id"] == "gdacs:FL:2"][0]
        self.assertEqual(flood["link"], "https://www.gdacs.org/report.aspx?eventtype=FL&eventid=2")

    def test_eonet_prefers_the_cited_agency_page(self):
        ev = {"events": [{"id": "EONET_9", "title": "Wildfire Y", "link": "https://eonet.gsfc.nasa.gov/api/v3/events/EONET_9",
                          "categories": [{"id": "wildfires"}],
                          "sources": [{"id": "InciWeb", "url": "javascript:alert(1)"},
                                      {"id": "IRWIN", "url": "https://inciweb.wildfire.gov/incident-information/x"}],
                          "geometry": [{"date": "2026-11-02T00:00:00Z", "type": "Point", "coordinates": [1, 1]}]}]}
        self.assertEqual(disaster.parse_eonet(ev)[0]["link"], "https://inciweb.wildfire.gov/incident-information/x")

    def test_statuspages(self):
        svc, inc = disaster.parse_statuspage(
            "GitHub", {"status": {"indicator": "major", "description": "Outage"}},
            {"incidents": [{"id": "x", "name": "Actions", "impact": "major", "shortlink": "javascript:alert(1)"}]},
            "https://www.githubstatus.com")
        self.assertEqual(svc["url"], "https://www.githubstatus.com")
        self.assertEqual(inc[0]["link"], "https://www.githubstatus.com")      # unsafe shortlink → the status page
        svc, _ = disaster.parse_slack({"status": "ok", "active_incidents": []})
        assert_source(self, svc["url"])


class GlobalMapFeeds(unittest.TestCase):
    def test_map_items_carry_source(self):
        for it in gs.parse_usgs(USGS) + gs.parse_gdacs(GDACS) + gs.split_eonet(disaster.parse_eonet(EONET)):
            assert_source(self, it["link"])
            self.assertEqual(it["source_url"], it["link"])

    def test_nws(self):
        payload = {"features": [{"id": "https://api.weather.gov/alerts/urn:oid:2.49.0.1.840.0.1",
                                 "properties": {"status": "Actual", "severity": "Severe", "event": "Flood Warning",
                                                "areaDesc": "X County", "sent": "2026-11-05T00:00:00Z"},
                                 "geometry": {"type": "Polygon", "coordinates": [[[-90, 30], [-89, 30], [-89, 31], [-90, 30]]]}},
                                {"properties": {"status": "Actual", "severity": "Minor", "event": "Wind Advisory",
                                                "geocode": {"UGC": ["TXZ001"]}}}]}
        items = gs.parse_nws(payload)
        self.assertEqual(len(items), 2)
        for it in items:
            assert_source(self, it["link"])

    def test_satellites_and_flights(self):
        iss = ("ISS (ZARYA)\n"
               "1 25544U 98067A   26310.50000000  .00016717  00000-0  10270-3 0  9005\n"
               "2 25544  51.6400 208.9163 0006317  69.9862  25.2906 15.49970000 12345\n")
        sats = gs.parse_tle(iss, "stations")
        self.assertEqual(sats[0]["url"], "https://www.n2yo.com/satellite/?s=25544")
        state = [["a0b1c2", "AIC101 ", "India", 1, 2, 78.4, 17.4, 10000, False, 230, 90, 0, None, 10200, "7700", False, 0]]
        rows, meta = gs.parse_opensky({"time": 1, "states": state})
        self.assertEqual(meta["emergencies"][0]["url"], "https://globe.adsbexchange.com/?icao=a0b1c2")
        self.assertEqual(meta["link_template"].format(icao24=rows[0][0]), "https://globe.adsbexchange.com/?icao=a0b1c2")

    def test_space_weather(self):
        rows = [{"product_id": "K05A", "issue_datetime": time.strftime("%Y-%m-%d %H:%M:%S.000", time.gmtime()),
                 "message": "ALERT: Geomagnetic K-index of 5\nThreshold Reached"}]
        a = gs.parse_swpc_alerts(rows)
        self.assertTrue(a)
        assert_source(self, a[0]["link"])
        for u in sl.SWPC.values():
            assert_source(self, u)


class CyberFindings(unittest.TestCase):
    def test_dependency_finding_links_osv(self):
        hits = {("pkg", "1.0"): [("GHSA-aaaa-bbbb-cccc", None)]}
        details = {"GHSA-aaaa-bbbb-cccc": {"id": "GHSA-aaaa-bbbb-cccc", "summary": "bad", "aliases": ["CVE-2026-1111"],
                                           "affected": []}}
        f = secagent.dep_findings(hits, details, {"pkg"}, set())
        self.assertTrue(f)
        self.assertEqual(f[0]["url"], "https://osv.dev/vulnerability/GHSA-aaaa-bbbb-cccc")
        self.assertEqual(f[0]["source_url"], f[0]["url"])

    def test_local_finding_is_labelled_not_linked(self):
        f = secagent._finding("cfg:x", "config", "r", "low", "t", "e", "fix", url="javascript:alert(1)")
        self.assertIsNone(f["url"])
        self.assertIn("this install", f["source"])

    def test_kev_name_match_links_nvd(self):
        from datetime import date
        kev = {"recent": [{"cveID": "CVE-2026-2222", "vendorProject": "X", "product": "httpx",
                           "vulnerabilityName": "n", "dateAdded": date.today().isoformat()}]}
        f = secagent.kev_findings(kev, [("httpx", "0.1")], None)
        self.assertEqual(f[0]["url"], "https://nvd.nist.gov/vuln/detail/CVE-2026-2222")


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>ISRO launches X - The Hindu</title><link>https://news.google.com/rss/articles/CBMiAbc?oc=5</link>
<pubDate>Thu, 05 Nov 2026 10:00:00 GMT</pubDate><source url="https://www.thehindu.com">The Hindu</source></item>
<item><title>Bad link item</title><link>javascript:alert(1)</link>
<pubDate>Thu, 05 Nov 2026 09:00:00 GMT</pubDate><source url="https://www.example.org">Example</source></item>
</channel></rss>"""


def _handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    if "news.google.com" in u:
        return httpx.Response(200, text=RSS)
    if "w/api.php" in u:
        return httpx.Response(200, json={"query": {"search": [{"title": "Bitcoin"}]}})
    if "page/summary" in u:
        return httpx.Response(200, json={"extract": "Bitcoin is a cryptocurrency.", "timestamp": "2026-11-01T00:00:00Z",
                                         "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Bitcoin"}}})
    if "coingecko" in u:
        return httpx.Response(200, json={"bitcoin": {"usd": 1, "inr": 2, "usd_24h_change": 0.5, "last_updated_at": 1}})
    return httpx.Response(404)


class LiveFactsCitations(unittest.TestCase):
    def test_rss_items_link_to_source(self):
        items = livefacts.parse_news_rss(RSS)
        self.assertEqual(items[0]["url"], "https://news.google.com/rss/articles/CBMiAbc?oc=5")
        self.assertEqual(items[1]["url"], "https://www.example.org")         # unsafe link → publisher site

    def test_ground_records_citations(self):
        q = "what is the latest bitcoin price"
        livefacts._cache.clear()
        livefacts._sources.clear()

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
                return await livefacts.ground(None, q, client=c)
        block = asyncio.run(go())
        self.assertIn("LIVE SOURCES", block)
        self.assertNotIn("http", block.split("LIVE SOURCES", 1)[1])          # prompt unchanged: no URLs spoken
        cites = livefacts.sources_for(q)
        kinds = {c["kind"] for c in cites}
        self.assertEqual(kinds, {"Google News", "Wikipedia", "CoinGecko"})
        for c in cites:
            assert_source(self, c["url"])
        self.assertIn("https://en.wikipedia.org/wiki/Bitcoin", [c["url"] for c in cites])
        self.assertIn("https://www.coingecko.com/en/coins/bitcoin", [c["url"] for c in cites])
        self.assertEqual(livefacts.sources_for("something never asked"), [])


class AgendaAndThreats(unittest.TestCase):
    def test_calendar_event_link(self):
        import google_sync
        ev = google_sync.parse_event({"summary": "Standup", "start": {"dateTime": "2026-11-06T10:00:00+05:30"},
                                      "end": {"dateTime": "2026-11-06T10:15:00+05:30"},
                                      "htmlLink": "https://www.google.com/calendar/event?eid=abc"})
        self.assertEqual(ev["url"], "https://www.google.com/calendar/event?eid=abc")
        bad = google_sync.parse_event({"summary": "x", "start": {"dateTime": "2026-11-06T10:00:00"},
                                       "htmlLink": "javascript:alert(1)"})
        self.assertEqual(bad["url"], "")

    def test_agenda_snapshot_carries_urls(self):
        from datetime import datetime, timedelta
        import agenda
        a = agenda.Agenda()
        a.set_events([{"title": "T", "start": datetime.now() + timedelta(hours=1), "url": "https://www.google.com/calendar/event?eid=1"},
                      {"title": "U", "start": datetime.now() + timedelta(hours=2), "url": "javascript:alert(1)"}])
        a.set_emails([{"subject": "S", "sender": "a@b.c", "received_ms": int(time.time() * 1000),
                       "url": "https://mail.google.com/mail/u/0/#all/18c2f3a4b5d6e7f8"}])
        snap = a.snapshot()
        self.assertEqual([e["url"] for e in snap["events"]], ["https://www.google.com/calendar/event?eid=1", ""])
        self.assertEqual(snap["emails"][0]["url"], "https://mail.google.com/mail/u/0/#all/18c2f3a4b5d6e7f8")

    def test_security_headline_incident_links_article(self):
        import threats

        class Hub:
            async def broadcast(self, m):
                pass

        class News:
            recent = [{"title": "Major ransomware attack hits X", "source": "BBC World",
                       "url": "https://www.bbc.co.uk/news/articles/abc"},
                      {"title": "New zero-day exploit found", "source": "Y", "url": "javascript:alert(1)"}]
        eng = threats.ThreatEngine(Hub(), news=News())
        asyncio.run(eng.tick())
        ext = {e["title"]: e for e in eng.events if e["category"] == "external"}
        self.assertEqual(ext["Security headline: Major ransomware attack hits X"]["url"], "https://www.bbc.co.uk/news/articles/abc")
        self.assertIsNone(ext["Security headline: New zero-day exploit found"]["url"])
        local = [e for e in eng.events if e["category"] != "external"]
        for e in local:
            self.assertIsNone(e["url"])


class DashboardCode(unittest.TestCase):
    JS = ["app.js", "disaster.js", "globalmap.js", "secagent.js", "devices.js", "computeruse.js",
          "phone.js", "evolution.js", "agentos.js"]

    def test_no_javascript_urls_and_every_new_tab_is_isolated(self):
        for name in self.JS:
            src = (ROOT / "static" / name).read_text(encoding="utf-8")
            self.assertNotRegex(src, r"href=[\"']\s*javascript:", name)
            for m in re.finditer(r"target=\"_blank\"[^>`]*", src):
                self.assertIn("noopener noreferrer", m.group(0), f"{name}: {m.group(0)[:120]}")
            for m in re.finditer(r"window\.open\(([^)]*)\)", src):
                self.assertIn("noopener", m.group(1), f"{name}: window.open without noopener")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_url_helpers(self):
        src = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = src.index("const escapeHTML")
        end = src.index("window.BUCS_SRC")
        script = src[start:end] + r"""
const out = {
  bad: ["javascript:alert(1)", "data:text/html,x", "http://", "https://a b", "//x.com", 7, null].map(safeHttpUrl),
  good: safeHttpUrl("https://www.usgs.gov/x?a=1&b=2"),
  link: srcLink("javascript:alert(1)", "X<y>"),
  link2: srcLink("https://osv.dev/vulnerability/GHSA-1", "OSV"),
  lk: linkifyEscaped(escapeHTML("see https://example.com/a?b=1&c=2. and javascript:alert(1)")),
  adsbx: [SRC_URL.adsbx("A0B1C2"), SRC_URL.adsbx("zzz")], n2yo: [SRC_URL.n2yo(25544), SRC_URL.n2yo("1;x")],
};
console.log(JSON.stringify(out));
"""
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["bad"], [None] * 7)
        self.assertEqual(out["good"], "https://www.usgs.gov/x?a=1&b=2")
        self.assertNotIn("href", out["link"])                    # unsafe → plain label, escaped
        self.assertIn("X&lt;y&gt;", out["link"])
        self.assertIn('rel="noopener noreferrer"', out["link2"])
        self.assertIn('target="_blank"', out["link2"])
        self.assertIn('href="https://example.com/a?b=1&amp;c=2"', out["lk"])
        self.assertNotIn('href="javascript', out["lk"])
        self.assertEqual(out["adsbx"], ["https://globe.adsbexchange.com/?icao=a0b1c2", None])
        self.assertEqual(out["n2yo"], ["https://www.n2yo.com/satellite/?s=25544", None])


class LiveEndpoints(unittest.TestCase):
    """Real endpoints on an isolated server. Upstream feeds may be unreachable (offline CI):
    those checks skip instead of failing; the link contract is still asserted on whatever arrives."""

    @classmethod
    def setUpClass(cls):
        from harness import IsolatedServer
        cls.s = IsolatedServer().start()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_livefacts_sources_endpoint(self):
        d = self.s._req("POST", "/api/livefacts/sources", {"query": "nothing asked yet"})
        self.assertEqual(d, {"sources": []})

    def test_weather_payload_names_its_source(self):
        try:
            d = self.s._req("GET", "/api/weather", timeout=20)
        except Exception as e:
            self.skipTest(f"weather endpoint unreachable: {e}")
        if not isinstance(d, dict) or d.get("error"):
            self.skipTest("Open-Meteo unreachable from this machine")
        self.assertEqual(d.get("source"), "Open-Meteo")
        assert_source(self, d.get("source_url"))

    def test_disaster_items_link_to_sources(self):
        deadline, items, d = time.time() + 60, [], {}
        while time.time() < deadline:
            d = self.s._req("GET", "/api/disaster")
            items = d.get("items") or []
            if items and all((s.get("status") != "pending") for s in d.get("sources") or []):
                break
            time.sleep(3)
        if not items:
            self.skipTest("no disaster feed reachable (offline?)")
        for i in items:
            assert_source(self, i.get("link"), allow_none=True)
            self.assertEqual(i.get("source_url"), i.get("link"))
        linked = [i for i in items if i.get("link")]
        self.assertGreaterEqual(len(linked), len(items) * 0.9, "most live items must link to their source")
        for s in d.get("services") or []:
            assert_source(self, s.get("url"), allow_none=True)

    def test_globalmap_items_link_to_sources(self):
        try:
            d = self.s._req("GET", "/api/globalmap", timeout=30)
        except Exception as e:
            self.skipTest(f"globalmap unavailable: {e}")
        for i in d.get("items") or []:
            assert_source(self, i.get("link"), allow_none=True)


if __name__ == "__main__":
    unittest.main()
