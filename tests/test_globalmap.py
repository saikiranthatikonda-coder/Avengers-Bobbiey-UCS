"""GLOBAL ACTIVITY MAP (globalmap.py / globalmap_sources.py) — no network.

Parsers run on small fixture payloads shaped like the real feeds (verified live
2026-11-03); the engine runs with an injected fake fetcher and a fake clock.
"""

import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import globalmap as gm                      # noqa: E402
import globalmap_sources as gs              # noqa: E402

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc).timestamp()

USGS = {"features": [
    {"id": "us7000abcd", "properties": {"mag": 6.4, "place": "100 km S of Somewhere", "time": int((NOW - 3600) * 1000),
                                        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd",
                                        "title": "M 6.4 - 100 km S of Somewhere", "alert": "yellow", "tsunami": 0},
     "geometry": {"type": "Point", "coordinates": [142.1, 38.2, 10.0]}},
    {"id": "nc1", "properties": {"mag": 2.6, "time": int(NOW * 1000), "title": "M 2.6 - CA"},
     "geometry": {"type": "Point", "coordinates": [-122.0, 37.0, 5]}},
    {"id": "bad", "properties": {"mag": None}, "geometry": {"type": "Point", "coordinates": [0, 0, 0]}},
]}

EONET = {"events": [
    {"id": "EONET_1", "title": "Wildfire X", "link": "https://eonet.gsfc.nasa.gov/api/v3/events/EONET_1",
     "categories": [{"id": "wildfires", "title": "Wildfires"}],
     "geometry": [{"date": "2026-11-02T00:00:00Z", "type": "Point", "coordinates": [-120.0, 40.0]}]},
    {"id": "EONET_2", "title": "Etna Volcano", "categories": [{"id": "volcanoes", "title": "Volcanoes"}],
     "geometry": [{"date": "2026-11-01T00:00:00Z", "type": "Point", "coordinates": [14.99, 37.75]}]},
]}

GVP = """<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel><title>Weekly</title>
<item><title>Krakatau (Indonesia) - Report for 8 October-14 October 2026 - New Eruptive Activity</title>
<description>&lt;p&gt;PVMBG reported ash.&lt;/p&gt;</description>
<guid isPermaLink="true">https://volcano.si.edu/reports_weekly.cfm#vn_262000</guid>
<pubDate>Thu, 15 Oct 2026 03:11:08 -0400</pubDate><georss:point>-6.1009 105.4233</georss:point></item>
<item><title>Etna (Italy) - Report for 8 October-14 October 2026 - Continuing Eruptive Activity</title>
<description>x</description><guid>https://volcano.si.edu/reports_weekly.cfm#vn_211060</guid>
<pubDate>Thu, 15 Oct 2026 03:11:08 -0400</pubDate><georss:point>37.748 14.999</georss:point></item>
<item><title>No position (Nowhere) - Report - New Eruptive Activity</title></item>
</channel></rss>"""

POLY = {"type": "Polygon", "coordinates": [[[-108.0, 36.0], [-107.0, 36.0], [-107.0, 37.0], [-108.0, 37.0], [-108.0, 36.0]]]}
NWS = {"features": [
    {"id": "https://api.weather.gov/alerts/a1", "geometry": POLY, "properties": {
        "id": "a1", "status": "Actual", "severity": "Severe", "event": "Flood Warning", "areaDesc": "San Juan, NM",
        "sent": "2026-11-03T01:00:00-06:00", "expires": "2026-11-03T09:00:00-06:00", "urgency": "Immediate", "certainty": "Likely"}},
    {"id": "https://api.weather.gov/alerts/a1u", "geometry": POLY, "properties": {
        "id": "a1u", "status": "Actual", "severity": "Severe", "event": "Flood Warning", "areaDesc": "San Juan, NM",
        "sent": "2026-11-03T03:00:00-06:00", "expires": "2026-11-03T09:00:00-06:00"}},
    {"id": "z1", "geometry": None, "properties": {"id": "z1", "status": "Actual", "severity": "Moderate", "event": "Wind Advisory",
                                                  "sent": "2026-11-03T00:00:00-06:00", "geocode": {"UGC": ["TXZ073", "TXZ074"]}}},
    {"id": "z2", "geometry": None, "properties": {"id": "z2", "status": "Actual", "severity": "Extreme", "event": "Fire Weather",
                                                  "sent": "2026-11-03T02:00:00-06:00", "geocode": {"UGC": ["TXC001"]}}},
    {"id": "m1", "geometry": None, "properties": {"id": "m1", "status": "Actual", "severity": "Minor", "event": "Small Craft Advisory",
                                                  "geocode": {"UGC": ["AMZ250"]}}},
    {"id": "t1", "geometry": POLY, "properties": {"id": "t1", "status": "Test", "severity": "Extreme", "event": "Test"}},
]}

IODA = {"data": [
    {"entity": {"code": "KM", "name": "Comoros", "type": "country"}, "from": NOW - 7200, "until": NOW, "score": 33817, "datasource": "bgp"},
    {"entity": {"code": "FI", "name": "Finland", "type": "country"}, "from": NOW - 3600, "until": NOW, "score": 2_000_000, "datasource": "gtr"},
    {"entity": {"code": "XX", "name": "Nowhere", "type": "country"}, "from": NOW - 3600, "until": NOW, "score": 5, "datasource": "gtr"},
    {"entity": {"code": "FI-01", "type": "region"}, "from": NOW - 3600, "until": NOW, "score": 5, "datasource": "gtr"},
]}


def state(icao, cs, lon, lat, ground=False, squawk=None, alt=10000.0):
    return [icao, cs, "India", int(NOW), int(NOW), lon, lat, alt, ground, 230.0, 90.0, 0.0, None, alt + 100, squawk, False, 0]


OPENSKY = {"time": int(NOW), "states": [
    state("80162a", "IGO7345 ", 80.16, 13.13), state("aa9321", "UAL57", -8.5, 51.1, squawk="7700"),
    state("abc123", "GND1", 1.0, 1.0, ground=True), state("dead00", "NOPOS", None, None), ["short"],
]}

TLE = """ISS (ZARYA)
1 25544U 98067A   26306.75682168  .00005437  00000+0  10620-3 0  9990
2 25544  51.6331 334.3796 0007124 340.7366  19.3352 15.49154261593232
BROKEN
1 99999U garbage
POISK
1 36086U 09060A   26306.75682168  .00005437  00000+0  10620-3 0  9998
2 36086  51.6331 334.3796 0007124 340.7366  19.3352 15.49154261593239
"""


class ParserTests(unittest.TestCase):
    def test_usgs(self):
        out = gs.parse_usgs(USGS)
        self.assertEqual(len(out), 2)
        q = out[0]
        self.assertEqual((q["layer"], q["lat"], q["lon"], q["mag"]), ("quakes", 38.2, 142.1, 6.4))
        self.assertEqual(q["severity"], 3)
        self.assertTrue(q["link"].startswith("https://earthquake.usgs.gov"))
        self.assertAlmostEqual(q["time"], NOW - 3600, places=0)

    def test_eonet_split_volcano_layer(self):
        import disaster
        out = gs.split_eonet(disaster.parse_eonet(EONET))
        self.assertEqual({i["title"]: i["layer"] for i in out}, {"Wildfire X": "events", "Etna Volcano": "volcanoes"})

    def test_gvp(self):
        out = gs.parse_gvp(GVP)
        self.assertEqual([i["title"] for i in out], ["Krakatau (Indonesia)", "Etna (Italy)"])
        self.assertEqual(out[0]["severity"], 3)          # new eruptive activity
        self.assertEqual(out[1]["severity"], 2)
        self.assertEqual((out[0]["lat"], out[0]["lon"]), (-6.1009, 105.4233))
        self.assertEqual(out[0]["id"], "gvp:vn_262000")
        self.assertIn("Report for 8 October", out[0]["detail"])
        self.assertTrue(out[0]["time"])

    def test_nws_polygons_zones_dedupe(self):
        out = gs.parse_nws(NWS)
        polys = [i for i in out if i.get("poly")]
        zones = [i for i in out if i.get("approx")]
        self.assertEqual(len(polys), 1, "an alert and its update collapse to one")
        self.assertEqual(polys[0]["id"], "nws:a1u")
        self.assertEqual(polys[0]["severity"], 3)
        self.assertAlmostEqual(polys[0]["lat"], 36.4, places=1)
        self.assertEqual(len(zones), 1, "zone-only alerts grouped per state; marine zones skipped; tests ignored")
        z = zones[0]
        self.assertEqual((z["id"], z["count"], z["severity"]), ("nws-zone:TX", 2, 4))
        self.assertIn("Wind Advisory", z["detail"])
        self.assertTrue(all(i["source"] == "NWS" and i["layer"] == "wxalerts" for i in out))

    def test_ioda_country_centroid(self):
        out = gs.parse_ioda(IODA, NOW)
        codes = {i["country"]: i for i in out}
        self.assertEqual(set(codes), {"KM", "FI"}, "unknown codes and regions are dropped, never guessed")
        self.assertTrue(codes["FI"]["approx"])
        self.assertEqual(codes["FI"]["iso_num"], "246")
        self.assertEqual(codes["FI"]["severity"], 3)
        merged = gs.merge_ioda([{"id": "ioda:KM:bgp", "title": "x", "severity": 2, "lat": None, "lon": None}])
        self.assertEqual(merged[0]["country"], "KM")

    def test_opensky(self):
        rows, meta = gs.parse_opensky(OPENSKY)
        self.assertEqual([r[0] for r in rows], ["80162a", "aa9321"])
        self.assertEqual(rows[0][1], "IGO7345")
        self.assertEqual((rows[0][3], rows[0][4]), (80.16, 13.13))
        self.assertEqual(meta["states"], 4)
        self.assertEqual(meta["airborne"], 2)
        self.assertEqual(meta["emergencies"][0]["squawk"], "7700")

    def test_opensky_sampling_keeps_emergencies(self):
        payload = {"time": NOW, "states": [state(f"{n:06x}", f"C{n}", -170 + n * 0.01, 10.0) for n in range(1000)]}
        payload["states"][999][14] = "7500"
        rows, meta = gs.parse_opensky(payload, max_rows=100)
        self.assertTrue(meta["sampled"])
        self.assertLessEqual(len(rows), 101)
        self.assertIn("7500", [r[10] for r in rows])
        self.assertEqual(meta["airborne"], 1000)

    def test_tle(self):
        out = gs.parse_tle(TLE, "stations")
        self.assertEqual([(s["id"], s["name"]) for s in out], [(25544, "ISS (ZARYA)"), (36086, "POISK")])
        self.assertEqual(len(out[0]["l1"]), 69)
        self.assertEqual(gs.sample_evenly(list(range(10)), 3), [0, 3, 6])
        self.assertEqual(len(gs.merge_sats([out, out])), 2)

    def test_space_weather(self):
        self.assertEqual(gs.parse_kp_1m([{"time_tag": "2026-11-03T05:00:00", "kp_index": 2, "estimated_kp": 2.33}])["kp"], 2.33)
        new = gs.parse_kp_3h([{"time_tag": "2026-11-03T03:00:00", "Kp": 1.67}])
        old = gs.parse_kp_3h([["time_tag", "Kp", "a_running"], ["2026-11-03 03:00:00.000", "1.67", "6"]])
        self.assertEqual(new[0]["kp"], 1.67)
        self.assertEqual(old[0]["kp"], 1.67)
        self.assertEqual(new[0]["time"], old[0]["time"])
        sc = gs.parse_scales({"0": {"DateStamp": "2026-11-03", "TimeStamp": "05:03:00", "G": {"Scale": "1"}, "S": {"Scale": "0"}, "R": {"Scale": None}}})
        self.assertEqual((sc["G"], sc["S"], sc["R"]), (1, 0, None))
        w = gs.parse_wind([{"proton_speed": 386, "time_tag": "2026-11-03T04:55:00Z"}], [{"bt": 4, "bz_gsm": -2}])
        self.assertEqual((w["speed_kms"], w["bz_nt"]), (386, -2))
        self.assertEqual(gs.kp_label(5.3), "G1 storm")
        self.assertEqual(gs.kp_label(None), "unknown")

    def test_swpc_alerts(self):
        rows = [{"product_id": "K05W", "issue_datetime": "2026-11-03 01:00:00.000",
                 "message": "Space Weather Message Code: WARK05\r\nWARNING: Geomagnetic K-index of 5 expected\r\n"},
                {"product_id": "OLD", "issue_datetime": "2026-10-01 01:00:00.000", "message": "ALERT: old"}]
        out = gs.parse_swpc_alerts(rows, NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "Warning")
        self.assertEqual(out[0]["title"], "Geomagnetic K-index of 5 expected")

    def test_ovation(self):
        coords = [[10, 60, 2], [10, 65, 12], [10, 70, 30], [200, 66, 15], [10, -70, 20], [10, -62, 11], [10, 0, 50]]
        a = gs.parse_ovation({"coordinates": coords, "Observation Time": "2026-11-03T04:51:00Z", "Forecast Time": "2026-11-03T06:04:00Z"})
        self.assertIn([10, 65], a["north"])
        self.assertIn([-160, 66], a["north"], "longitudes > 180 wrap to the western hemisphere")
        self.assertEqual(a["south"], [[10, -62]])
        self.assertTrue(all(abs(c[1]) >= 40 for c in a["cells"]))
        self.assertTrue(a["forecast"] > a["observation"])


class FakeNet:
    """url-substring -> Resp | Exception | callable; records calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def __call__(self, url):
        self.calls.append(url)
        for k, v in self.routes.items():
            if k in url:
                v = v() if callable(v) else v
                if isinstance(v, Exception):
                    raise v
                return v
        raise ConnectionError("no route " + url)


def J(obj, status=200, headers=None):
    return gs.Resp(status, json.dumps(obj), headers)


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def run(coro):
    return asyncio.run(coro)


class EngineTests(unittest.TestCase):
    def eng(self, routes, keys=None, st=None):
        net, clock = FakeNet(routes), Clock()
        srcs = [s for s in gm.SOURCES if keys is None or s[0] in keys]
        return gm.GlobalMap(st if st is not None else {}, fetch=net, sources=srcs, now=clock), net, clock

    def test_demand_gating(self):
        e, net, _ = self.eng({"2.5_day": J(USGS)}, keys={"usgs"})
        run(e.tick())
        self.assertEqual(net.calls, [], "nothing is fetched until a dashboard asks")
        e.touch("core")
        run(e.tick())
        self.assertTrue(net.calls)
        self.assertEqual(e.layer_info("quakes")["count"], 2)

    def test_ttl_and_stale_while_error(self):
        e, net, clock = self.eng({"2.5_day": J(USGS)}, keys={"usgs"})
        e.touch("core")
        run(e.tick())
        n = len(net.calls)
        clock.t += 100
        e.touch("core")
        run(e.tick())
        self.assertEqual(len(net.calls), n, "within TTL: served from cache")
        net.routes["2.5_day"] = ConnectionError("down")
        clock.t += 301
        e.touch("core")
        run(e.tick())
        info = e.source_info("usgs")
        self.assertEqual(info["status"], "stale")
        self.assertIn("ConnectionError", info["error"])
        self.assertEqual(e.layer_info("quakes")["count"], 2, "last good data kept")
        self.assertTrue(all(i.get("stale") for i in e.layer_items("quakes")), "and marked stale")
        # back-off: not retried immediately
        n = len(net.calls)
        clock.t += 5
        run(e.tick())
        self.assertEqual(len(net.calls), n)

    def test_old_data_becomes_stale(self):
        e, _, clock = self.eng({"2.5_day": J(USGS)}, keys={"usgs"})
        e.touch("core")
        run(e.tick())
        self.assertEqual(e.status_of("usgs"), "ok")
        clock.t += 3 * 300 + 1
        self.assertEqual(e.status_of("usgs"), "stale")

    def test_unavailable_and_isolation(self):
        e, _, _ = self.eng({"2.5_day": J(USGS), "WeeklyVolcano": ConnectionError("x")}, keys={"usgs", "gvp"})
        e.touch("core")
        run(e.tick())
        self.assertEqual(e.source_info("gvp")["status"], "unavailable")
        self.assertEqual(e.source_info("usgs")["status"], "ok", "one feed failing never affects another")
        snap = e.snapshot()
        self.assertEqual(snap["layers"]["volcanoes"]["count"], 0)
        self.assertEqual(snap["layers"]["quakes"]["count"], 2)
        json.dumps(snap)

    def test_http_error_is_failure(self):
        e, _, _ = self.eng({"2.5_day": J({}, status=503)}, keys={"usgs"})
        e.touch("core")
        run(e.tick())
        self.assertEqual(e.source_info("usgs")["status"], "unavailable")
        self.assertIn("503", e.source_info("usgs")["error"])

    def test_reuses_disaster_cache(self):
        import disaster

        class D:
            cache = {"gdacs": {"status": "ok", "last_ok": NOW - 10,
                               "items": [{"id": "gdacs:TC:1", "type": "cyclone", "title": "TC", "severity": 3,
                                          "sev_label": "HIGH", "lat": 10.0, "lon": 120.0, "time": NOW, "source": "GDACS"}]}}
        e, net, _ = self.eng({}, keys={"gdacs"}, st={"disaster": D()})
        e.touch("core")
        run(e.tick())
        self.assertEqual(net.calls, [], "fresh Disaster Response data is reused, not re-fetched")
        self.assertEqual(e.layer_items("disasters")[0]["layer"], "disasters")
        self.assertTrue(disaster.parse_gdacs)       # imported read-only

    def test_space_partial(self):
        e, _, _ = self.eng({"planetary_k_index_1m": J([{"time_tag": "2026-11-03T05:00:00", "estimated_kp": 4.33}]),
                            "noaa-scales": J({"0": {"DateStamp": "2026-11-03", "TimeStamp": "05:00:00", "G": {"Scale": "0"}}})},
                           keys={"swpc"})
        e.touch("core")
        run(e.tick())
        sp = e.snapshot()["space"]
        self.assertEqual((sp["kp"], sp["kp_label"]), (4.33, "active"))
        self.assertEqual(len(sp["errors"]), 4, "missing sub-feeds are listed, not invented")
        self.assertIsNone(sp["wind"])

    def test_satellites_group(self):
        e, net, _ = self.eng({"celestrak": gs.Resp(200, TLE)}, keys={"celestrak"})
        e.touch("core")
        run(e.tick())
        self.assertEqual(net.calls, [], "satellites only poll while that layer is on")
        e.touch("satellites")
        run(e.tick())
        lay = e.layer("satellites")
        self.assertEqual(lay["count"], 2)
        self.assertEqual(lay["sats"][0]["id"], 25544)
        self.assertEqual(lay["meta"]["provider"], "CelesTrak")

    def test_satellites_amsat_fallback(self):
        e, _, _ = self.eng({"celestrak": ConnectionError("blocked"), "amsat.org": gs.Resp(200, TLE)}, keys={"celestrak"})
        e.touch("satellites")
        run(e.tick())
        lay = e.layer("satellites")
        self.assertEqual(lay["count"], 2)
        self.assertIn("AMSAT", lay["meta"]["provider"], "the fallback provider is named, not passed off as CelesTrak")
        self.assertEqual(len(lay["meta"]["errors"]), 3)


class OpenSkyTests(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet({"opensky": lambda: J(OPENSKY, headers={"X-Rate-Limit-Remaining": "396"})})
        self.clock = Clock()
        self.e = gm.GlobalMap({}, fetch=self.net, sources=[s for s in gm.SOURCES if s[0] == "opensky"], now=self.clock)

    def calls(self):
        return sum("opensky" in c for c in self.net.calls)

    def test_min_interval_and_pacing(self):
        self.e.touch("flights")
        run(self.e.tick())
        self.assertEqual(self.calls(), 1)
        self.assertEqual(self.e.layer("flights")["count"], 2)
        g = self.e.opensky
        self.assertEqual(g.remaining, 396)
        iv = g.interval(self.clock.t)
        self.assertGreaterEqual(iv, g.MIN_INTERVAL)
        # 18 h to reset, 97 calls left -> ~11 min between calls
        self.assertAlmostEqual(iv, 18 * 3600 / ((396 - 8) // 4), delta=5)
        for dt in (30, 60, 119):
            self.clock.t = NOW + dt
            self.e.touch("flights")
            run(self.e.tick())
        self.assertEqual(self.calls(), 1, "never faster than the paced interval")
        self.clock.t = NOW + iv + 1
        self.e.touch("flights")
        run(self.e.tick())
        self.assertEqual(self.calls(), 2)

    def test_429_backs_off(self):
        self.net.routes["opensky"] = J({}, status=429, headers={"X-Rate-Limit-Retry-After-Seconds": "3600"})
        self.e.touch("flights")
        run(self.e.tick())
        self.assertEqual(self.e.status_of("opensky"), "limited")
        self.assertIn("rate limited", self.e.source_info("opensky")["error"])
        self.clock.t += 1800
        self.e.touch("flights")
        run(self.e.tick())
        self.assertEqual(self.calls(), 1, "Retry-After honoured")

    def test_budget_exhausted_waits_for_reset(self):
        g = gs.OpenSkyGate()
        g.record(NOW, 200, {"x-rate-limit-remaining": "8"})
        self.assertFalse(g.allowed(NOW + 3600))
        self.assertGreaterEqual(g.next_allowed(NOW), NOW + g.secs_to_reset(NOW) - 1)

    def test_no_demand_no_calls(self):
        run(self.e.tick())
        self.assertEqual(self.calls(), 0)

    def test_local_budget_without_headers(self):
        g = gs.OpenSkyGate()
        for n in range(3):
            g.record(NOW + n, 200, {})
        self.assertEqual(g.budget(NOW + 5), 400 - 12)


class RouterTests(unittest.TestCase):
    def test_endpoints(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        st = {"globalmap": gm.GlobalMap({}, fetch=FakeNet({}), sources=[s for s in gm.SOURCES if s[0] == "usgs"])}
        app = FastAPI()
        app.include_router(gm.build_router({"state": st}))
        with TestClient(app) as c:
            d = c.get("/api/globalmap").json()
            self.assertIn("quakes", d["layers"])
            self.assertIn(d["layers"]["quakes"]["status"], ("pending", "unavailable"))
            self.assertEqual(c.get("/api/globalmap/layer/nope").status_code, 404)
            self.assertEqual(c.get("/api/globalmap/layer/flights").json()["rows"], [])
            self.assertIn("sources", c.get("/api/globalmap/status").json())


if __name__ == "__main__":
    unittest.main()
