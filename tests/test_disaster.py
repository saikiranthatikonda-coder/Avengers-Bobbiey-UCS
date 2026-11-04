"""Disaster response: feed parsers, severity ordering, source isolation, distance. No network."""

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import disaster  # noqa: E402

NOW = time.time()


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


USGS = {"features": [
    {"id": "us7000a", "properties": {"mag": 6.4, "title": "M 6.4 - southeast of Easter Island", "time": (NOW - 3600) * 1000,
                                     "url": "https://earthquake.usgs.gov/x", "alert": None, "tsunami": 0},
     "geometry": {"coordinates": [-110.0, -35.0, 10.0]}},
    {"id": "us7000b", "properties": {"mag": 4.6, "title": "M 4.6 - near X", "time": NOW * 1000, "alert": "orange",
                                     "url": "javascript:alert(1)", "tsunami": 1},
     "geometry": {"coordinates": [78.0, 17.0, 35.0]}},
    {"id": "bad", "properties": {"mag": None}, "geometry": {"coordinates": [0, 0]}},
]}

GDACS = {"features": [
    {"properties": {"eventtype": "TC", "eventid": 1, "name": "Tropical Cyclone MURASU-26", "alertlevel": "Red",
                    "iscurrent": "true", "country": "India", "datemodified": "2026-11-03T01:00:00",
                    "severitydata": {"severitytext": "Tropical storm"}, "url": {"report": "https://www.gdacs.org/r"}},
     "geometry": {"type": "Point", "coordinates": [80.5, 15.0]}},
    {"properties": {"eventtype": "FL", "eventid": 2, "name": "Flood in X", "alertlevel": "Orange", "iscurrent": "true"},
     "geometry": {"type": "Point", "coordinates": [10, 10]}},
    {"properties": {"eventtype": "DR", "eventid": 3, "name": "Old drought", "alertlevel": "Green", "iscurrent": "false"},
     "geometry": {"type": "Point", "coordinates": [10, 10]}},
    {"properties": {"eventtype": "EQ", "eventid": 4, "name": "Earthquake dup", "alertlevel": "Green", "iscurrent": "true",
                    "datemodified": iso(NOW - 3000)},
     "geometry": {"type": "Point", "coordinates": [-110.2, -35.1]}},
]}

EONET = {"events": [
    {"id": "EONET_1", "title": "Tropical Cyclone Murasu", "closed": None, "link": "https://eonet.gsfc.nasa.gov/e/1",
     "categories": [{"id": "severeStorms", "title": "Severe Storms"}], "sources": [{"id": "JTWC"}],
     "geometry": [{"date": "2026-11-01T00:00:00Z", "type": "Point", "coordinates": [86.2, 11.4], "magnitudeValue": 35, "magnitudeUnit": "kts"},
                  {"date": "2026-11-02T00:00:00Z", "type": "Point", "coordinates": [81.5, 13.9], "magnitudeValue": 65, "magnitudeUnit": "kts"}]},
    {"id": "EONET_2", "title": "Wildfire X", "closed": None, "categories": [{"id": "wildfires", "title": "Wildfires"}],
     "geometry": [{"date": "2026-11-02T00:00:00Z", "type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2]]]}]},
    {"id": "EONET_3", "title": "Closed one", "closed": "2026-10-01", "categories": [{"id": "volcanoes"}], "geometry": []},
]}

SACHET = [
    {"severity": "WARNING", "identifier": 11, "effective_start_time": "Tue Nov 03 07:00:00 IST 2026",
     "effective_end_time": "Tue Nov 03 23:59:00 IST 2099", "disaster_type": "Heavy Rain", "area_description": "Guntur district",
     "actual_lang": "en", "warning_message": "Heavy rain very likely.", "severity_color": "red",
     "centroid": "80.99,16.65", "alert_source": "IMD Visakhapatnam"},
    {"severity": "ALERT", "identifier": 12, "effective_start_time": "Tue Nov 03 07:00:00 IST 2026",
     "effective_end_time": "Tue Nov 03 23:59:00 IST 2099", "disaster_type": "Lightning", "area_description": "x_y_z mandals",
     "actual_lang": "te", "warning_message": "తెలుగు", "severity_color": "orange", "centroid": "81.7,16.3", "alert_source": "AP SDMA"},
    {"severity": "WATCH", "identifier": 13, "effective_start_time": "Mon Jan 01 07:00:00 IST 2001",
     "effective_end_time": "Mon Jan 01 09:00:00 IST 2001", "disaster_type": "Cyclone", "severity_color": "yellow"},
]

KEV = {"catalogVersion": "2026.10.30", "count": 3, "vulnerabilities": [
    {"cveID": "CVE-2026-0001", "vendorProject": "A", "product": "B", "vulnerabilityName": "Old", "dateAdded": "2026-01-01",
     "knownRansomwareCampaignUse": "Unknown"},
    {"cveID": "CVE-2026-0003", "vendorProject": "C", "product": "D", "vulnerabilityName": "New", "dateAdded": "2026-10-30",
     "knownRansomwareCampaignUse": "Known", "dueDate": "2026-11-20"},
    {"cveID": "CVE-2026-0002", "vendorProject": "E", "product": "F", "vulnerabilityName": "Mid", "dateAdded": "2026-06-01"},
]}

IODA = {"data": [
    {"entity": {"code": "FI", "name": "Finland", "type": "country"}, "from": NOW - 7200, "until": NOW, "score": 2_000_000, "datasource": "bgp"},
    {"entity": {"code": "FI", "name": "Finland", "type": "country"}, "from": NOW - 7200, "until": NOW, "score": 50, "datasource": "ping-slash24"},
    {"entity": {"code": "KM", "name": "Comoros", "type": "country"}, "from": NOW - 7200, "until": NOW - 86000, "score": 9, "datasource": "bgp"},
    {"entity": {"code": "1234", "name": "AS1234", "type": "asn"}, "from": NOW, "until": NOW, "score": 9, "datasource": "bgp"},
]}


class Parsers(unittest.TestCase):
    def test_usgs(self):
        items = disaster.parse_usgs(USGS)
        self.assertEqual(len(items), 2)
        big, small = items
        self.assertEqual(big["severity"], 3)                 # M6.4
        self.assertEqual(big["type"], "earthquake")
        self.assertAlmostEqual(big["lat"], -35.0)
        self.assertEqual(small["severity"], 3)               # PAGER orange + tsunami flag
        self.assertIsNone(small["link"])                     # javascript: URL dropped

    def test_gdacs(self):
        items = disaster.parse_gdacs(GDACS)
        ids = [i["id"] for i in items]
        self.assertNotIn("gdacs:DR:3", ids)                  # not current
        tc = items[0]
        self.assertEqual((tc["type"], tc["severity"], tc["sev_label"]), ("cyclone", 4, "CRITICAL"))
        self.assertEqual(items[1]["severity"], 3)            # Orange
        self.assertEqual(tc["link"], "https://www.gdacs.org/r")

    def test_eonet(self):
        items = disaster.parse_eonet(EONET)
        self.assertEqual(len(items), 2)                      # closed event skipped
        cyc = items[0]
        self.assertEqual(cyc["type"], "cyclone")
        self.assertEqual(cyc["severity"], 3)                 # latest point: 65 kts
        self.assertAlmostEqual(cyc["lat"], 13.9)
        fire = items[1]
        self.assertEqual((fire["type"], fire["lat"], fire["lon"]), ("wildfire", 1.0, 1.0))  # polygon centroid

    def test_sachet(self):
        items = disaster.parse_sachet(SACHET, now=NOW)
        self.assertEqual(len(items), 2)                      # expired alert dropped
        rain, light = items
        self.assertEqual((rain["type"], rain["severity"]), ("rain", 3))
        self.assertIn("Heavy rain very likely", rain["detail"])
        self.assertEqual(light["type"], "thunderstorm")
        self.assertNotIn("తెలుగు", light["detail"])          # non-English text not shown as detail
        self.assertIn("x y z mandals", light["title"])
        self.assertAlmostEqual(rain["lat"], 16.65)
        self.assertEqual(disaster._sachet_ts("Tue Nov 03 09:20:00 IST 2026"),
                         disaster._iso_ts("2026-11-03T03:50:00Z"))

    def test_kev(self):
        items = disaster.parse_kev(KEV, limit=2)
        self.assertEqual([i["title"].split(" ")[0] for i in items], ["CVE-2026-0003", "CVE-2026-0002"])
        self.assertEqual(items[0]["severity"], 3)            # ransomware-linked
        self.assertEqual(items[1]["severity"], 2)
        self.assertEqual(items[0]["link"], "https://nvd.nist.gov/vuln/detail/CVE-2026-0003")

    def test_statuspage(self):
        svc, inc = disaster.parse_statuspage(
            "GitHub", {"status": {"indicator": "major", "description": "Partial outage"}},
            {"incidents": [{"id": "x", "name": "Actions delayed", "impact": "major", "status": "investigating",
                            "updated_at": "2026-11-03T01:00:00Z", "shortlink": "https://stspg.io/x"}]},
            "https://www.githubstatus.com")
        self.assertEqual((svc["indicator"], svc["severity"], svc["incidents"]), ("major", 3, 1))
        self.assertEqual(inc[0]["severity"], 3)
        svc, inc = disaster.parse_statuspage("Zoom", {"status": {"indicator": "none", "description": "OK"}},
                                             {"incidents": []}, "https://status.zoom.us")
        self.assertEqual((svc["severity"], inc), (0, []))
        svc, inc = disaster.parse_statuspage("CF", {"status": {"indicator": "minor", "description": "Degraded"}},
                                             None, "https://x")
        self.assertEqual(len(inc), 1)                        # degraded indicator without incident still listed
        svc, inc = disaster.parse_slack({"status": "ok", "active_incidents": []})
        self.assertEqual((svc["indicator"], inc), ("none", []))

    def test_ioda(self):
        items = disaster.parse_ioda(IODA, now=NOW)
        self.assertEqual(len(items), 1)                      # one row per country, ended + asn dropped
        self.assertEqual(items[0]["severity"], 3)
        self.assertEqual(items[0]["link"], "https://ioda.inetintel.cc.gatech.edu/country/FI")


class Ordering(unittest.TestCase):
    def test_severity_then_near_then_time(self):
        mk = lambda i, s, near, t: {"id": i, "severity": s, "near": near, "time": t}
        out = disaster.sort_items([mk("a", 1, False, 10), mk("b", 4, False, 1), mk("c", 3, False, 50),
                                   mk("d", 3, True, 5), mk("e", 3, False, 60)])
        self.assertEqual([i["id"] for i in out], ["b", "d", "e", "c", "a"])

    def test_dedupe_gdacs_quake(self):
        items = disaster.parse_usgs(USGS) + disaster.parse_gdacs(GDACS)
        for i in items:
            i.setdefault("time", NOW)
        ids = [i["id"] for i in disaster.dedupe_quakes(items)]
        self.assertNotIn("gdacs:EQ:4", ids)
        self.assertIn("gdacs:TC:1", ids)


class Distance(unittest.TestCase):
    def test_haversine(self):
        self.assertAlmostEqual(disaster.haversine_km(0, 0, 0, 0), 0)
        # Hyderabad → Delhi ≈ 1255 km
        self.assertAlmostEqual(disaster.haversine_km(17.385, 78.4867, 28.6139, 77.2090), 1255, delta=10)
        # quarter of the equator ≈ 10008 km
        self.assertAlmostEqual(disaster.haversine_km(0, 0, 0, 90), 10007.6, delta=2)

    def test_location_fallbacks(self):
        class W: lat, lon, city = 17.385, 78.4867, "Hyderabad"
        d = disaster.Disaster({"weather": W()}, sources=[])
        self.assertEqual(d.location()["label"], "Hyderabad")
        self.assertEqual(d.location(10, 20)["source"], "dashboard")
        self.assertEqual(d.location(999, 20)["label"], "Hyderabad")   # invalid override ignored
        self.assertIsNone(disaster.Disaster({}, sources=[]).location())


def _fake_sources():
    async def ok_phys(get):
        return {"items": disaster.parse_gdacs(await get("gdacs"))}

    async def boom(get):
        await get("kev")
        raise RuntimeError("feed down")

    async def ok_dig(get):
        return {"items": disaster.parse_kev(await get("kev2"))}

    return [("gdacs", "GDACS", "physical", ok_phys, 300, None),
            ("broken", "Broken", "digital", boom, 300, None),
            ("kev", "KEV", "digital", ok_dig, 300, None)]


class Hub:
    def __init__(self): self.msgs = []
    async def broadcast(self, m): self.msgs.append(m)


class Engine(unittest.TestCase):
    def setUp(self):
        self.payloads = {"gdacs": GDACS, "kev": KEV, "kev2": KEV}

        async def fetch(url):
            return self.payloads[url]

        class W: lat, lon, city = 16.0, 80.0, "Test"
        self.hub = Hub()
        self.d = disaster.Disaster({"weather": W(), "hub": self.hub}, fetch=fetch, sources=_fake_sources())

    def test_source_isolation_and_stale(self):
        asyncio.run(self.d.poll(force=True))
        snap = self.d.snapshot()
        st = {s["key"]: s["status"] for s in snap["sources"]}
        self.assertEqual(st, {"gdacs": "ok", "broken": "unavailable", "kev": "ok"})
        self.assertIn("feed down", next(s["error"] for s in snap["sources"] if s["key"] == "broken"))
        self.assertTrue(any(i["source"] == "GDACS" for i in snap["items"]))
        self.assertTrue(any(i["source"] == "CISA KEV" for i in snap["items"]))
        sev = [i["severity"] for i in snap["items"]]
        self.assertEqual(sev, sorted(sev, reverse=True))
        tc = next(i for i in snap["items"] if i["id"] == "gdacs:TC:1")
        self.assertTrue(tc["near"])                          # 80.5,15.0 vs 80,16 ≈ 124 km
        self.assertLess(tc["distance_km"], disaster.NEAR_KM)
        self.assertGreater(snap["counts"]["by_type"]["cyclone"], 0)
        self.assertIn("cyclone", snap["preparedness"])
        # GDACS goes down → its last items are kept but marked stale
        del self.payloads["gdacs"]
        asyncio.run(self.d.poll(force=True))
        snap = self.d.snapshot()
        g = next(s for s in snap["sources"] if s["key"] == "gdacs")
        self.assertEqual(g["status"], "stale")
        self.assertTrue(all(i["stale"] for i in snap["items"] if i["source"] == "GDACS"))
        self.assertTrue(any(i["source"] == "GDACS" for i in snap["items"]))

    def test_alerts_seed_then_new_only(self):
        asyncio.run(self.d.poll(force=True))
        self.assertEqual(len(self.hub.msgs), 1)              # one summary, not a flood
        self.assertEqual(self.hub.msgs[0]["type"], "log")
        asyncio.run(self.d.poll(force=True))
        self.assertEqual(len(self.hub.msgs), 1)              # nothing new
        self.payloads["gdacs"] = {"features": GDACS["features"] + [
            {"properties": {"eventtype": "VO", "eventid": 9, "name": "Volcano Z", "alertlevel": "Red", "iscurrent": "true"},
             "geometry": {"type": "Point", "coordinates": [100, 0]}}]}
        asyncio.run(self.d.poll(force=True))
        self.assertEqual(len(self.hub.msgs), 2)
        self.assertIn("Volcano Z", self.hub.msgs[-1]["msg"])

    def test_refresh_rate_limited(self):
        ok1, _ = asyncio.run(self.d.refresh())
        ok2, wait = asyncio.run(self.d.refresh())
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertGreater(wait, 0)

    def test_ttl_skips_fresh_sources(self):
        calls = []

        async def fetch(url):
            calls.append(url)
            return self.payloads[url]
        self.d._fetch = fetch
        asyncio.run(self.d.poll())
        n = len(calls)
        asyncio.run(self.d.poll())                           # within TTL: only the failing source retries
        self.assertEqual(len(calls) - n, 1)


class DashboardContract(unittest.TestCase):
    """static/disaster.js: view prefs survive a reload (guarded localStorage), failures are surfaced."""
    JS = (Path(__file__).resolve().parent.parent / "static" / "disaster.js").read_text(encoding="utf-8")

    def test_view_prefs_persist_in_guarded_localstorage(self):
        js = self.JS
        self.assertRegex(js, r'function pref\(k, v\) \{\s*try \{')            # every access inside try/catch
        for key in ('pref("filter", filter)', 'pref("tab", t)', 'pref("prep", prepSel)', 'pref("filter")'):
            self.assertIn(key, js)
        self.assertIn("showTab(tab);", js)                                    # remembered tab restored on load

    def test_refresh_failure_is_reported(self):
        self.assertIn("refresh failed: ${esc(j.error", self.JS)               # non-429 errors are not silent

    def test_show_on_map_ids_match_global_map(self):
        """SHOW ON MAP passes the Disaster item id; the global map must carry the same id."""
        import globalmap_sources as gs
        pairs = [(disaster.parse_gdacs(GDACS), gs.parse_gdacs(GDACS)),
                 (disaster.parse_usgs(USGS), gs.parse_usgs(USGS)),
                 (disaster.parse_eonet(EONET), gs.split_eonet(disaster.parse_eonet(EONET))),
                 (disaster.parse_sachet(SACHET), gs.parse_sachet(SACHET))]
        for dz_items, gm_items in pairs:
            gm_ids = {i["id"] for i in gm_items}
            located = [i for i in dz_items if i.get("lat") is not None]
            self.assertTrue(located)
            for it in located:
                self.assertIn(it["id"], gm_ids, it["id"])


if __name__ == "__main__":
    unittest.main()
