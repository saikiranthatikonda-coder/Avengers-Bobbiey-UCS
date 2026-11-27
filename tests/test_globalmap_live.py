"""Global map ◉ LIVE mode (v8.5): satellite choice by longitude, newest-frame parsing from the
providers' own metadata, day/night, radar, and the Windy webcam proxy (key private, URLs
sanitised). Offline: every network call goes to a fake fetch."""
import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import globalmap_live as gl  # noqa: E402

NOW = datetime(2026, 11, 27, 8, 55, tzinfo=timezone.utc).timestamp()   # 14:25 IST, daylight in India

DOMAIN = ("<Domains><DimensionDomain><ows:Identifier>time</ows:Identifier><Domain>2026-11-27T06:00:00Z/"
          "2026-11-27T07:50:00Z/PT10M,2026-11-27T08:10:00Z/2026-11-27T08:10:00Z/PT10M</Domain></DimensionDomain></Domains>")
WMS = ('<Layer queryable="1"><Name>rgb_naturalenhncd</Name><Dimension name="time" default="2026-11-27T08:30:00Z" '
       'units="ISO8601">2020-08-01T00:00:00.000Z/2026-11-27T08:30:00.000Z/PT15M</Dimension></Layer>'
       '<Layer><Name>ir108</Name><Dimension name="time" default="2026-11-27T08:45:00Z" units="ISO8601">x</Dimension></Layer>')
RADAR = {"host": "https://tilecache.rainviewer.com", "radar": {"past": [{"time": int(NOW) - 1200, "path": "/v2/radar/a"},
                                                                        {"time": int(NOW) - 600, "path": "/v2/radar/b"}]}}


def fake(routes, seen=None):
    async def f(url, headers):
        if seen is not None:
            seen.append((url, headers))
        for frag, (code, body) in routes.items():
            if frag in url:
                return code, body if isinstance(body, bytes) else body.encode()
        return 404, b""
    return f


class Pick(unittest.TestCase):
    def test_longitude_picks_the_covering_satellite(self):
        for lon, key in ((-122, "goes-west"), (-74, "goes-east"), (0, "mtg"), (78.5, "iodc"), (139.7, "himawari"),
                         (179.9, "himawari"), (-180, "goes-west"), (200, "goes-west")):
            self.assertEqual(gl.pick_sat(lon)[0], key, lon)

    def test_solar_elevation_day_and_night(self):
        noon = datetime(2026, 11, 27, 6, 40, tzinfo=timezone.utc)       # ~12:10 IST
        self.assertGreater(gl.solar_elevation(17.4, 78.5, noon), 40)
        self.assertLess(gl.solar_elevation(17.4, 78.5, datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)), -30)


class Parse(unittest.TestCase):
    def test_gibs_domain_newest_time(self):
        self.assertEqual(gl.latest_from_domain(DOMAIN), "2026-11-27T08:10:00Z")
        self.assertIsNone(gl.latest_from_domain("<html>error</html>"))

    def test_eumetsat_default_time_with_and_without_workspace_prefix(self):
        self.assertEqual(gl.latest_from_wms(WMS, "msg_iodc:rgb_naturalenhncd"), "2026-11-27T08:30:00Z")
        self.assertEqual(gl.latest_from_wms(WMS, "ir108"), "2026-11-27T08:45:00Z")
        self.assertIsNone(gl.latest_from_wms(WMS, "msg_iodc:nothing"))


class Live(unittest.TestCase):
    def lm(self, routes, seen=None, key_file=None):
        return gl.LiveMap(fetch=fake(routes, seen), now=lambda: NOW,
                          key_file=key_file or Path(tempfile.mkdtemp()) / "livecams.json")

    def test_india_daytime_uses_meteosat_iodc_with_real_frame_time(self):
        m = self.lm({"msg_iodc/ows": (200, WMS), "weather-maps.json": (200, json.dumps(RADAR))})
        d = asyncio.run(m.live(17.38, 78.48))
        s = d["satellite"]
        self.assertTrue(s["ok"])
        self.assertEqual((s["key"], s["time"], s["age_s"]), ("iodc", "2026-11-27T08:30:00Z", 25 * 60))
        self.assertIn("time=2026-11-27T08:30:00Z", s["tiles"][0])
        self.assertIn("{bbox-epsg-3857}", s["tiles"][0])
        self.assertIn("daylight", s["band"])
        r = d["radar"]
        self.assertTrue(r["ok"])
        self.assertIn("/v2/radar/b/256/{z}/{x}/{y}", r["tiles"][0])
        self.assertEqual(r["age_s"], 600)
        self.assertIn("NOT live", d["ground"])
        self.assertFalse(d["webcams"]["configured"])

    def test_night_switches_to_infrared(self):
        night = datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc).timestamp()
        m = gl.LiveMap(fetch=fake({"msg_iodc/ows": (200, WMS)}), now=lambda: night,
                       key_file=Path(tempfile.mkdtemp()) / "k.json")
        s = asyncio.run(m.satellite(17.38, 78.48))
        self.assertIn("ir108", s["tiles"][0])
        self.assertIn("infrared", s["band"])

    def test_gibs_satellite_uses_describe_domains(self):
        seen = []
        m = self.lm({"DescribeDomains": (500, ""), "GOES-East_ABI_GeoColor/default/GoogleMapsCompatible_Level7/all/": (200, DOMAIN)}, seen)
        s = asyncio.run(m.satellite(40.75, -73.98))
        self.assertEqual(s["time"], "2026-11-27T08:10:00Z")
        self.assertIn("/GOES-East_ABI_GeoColor/default/2026-11-27T08:10:00Z/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png", s["tiles"][0])
        self.assertIn("GeoColor", s["band"])

    def test_provider_failure_is_reported_not_faked(self):
        m = self.lm({})
        d = asyncio.run(m.live(17.38, 78.48))
        self.assertFalse(d["satellite"]["ok"])
        self.assertIn("no recent frame", d["satellite"]["error"])
        self.assertFalse(d["radar"]["ok"])

    def test_last_good_frame_is_kept_but_marked_stale(self):
        routes = {"msg_iodc/ows": (200, WMS)}
        t = [NOW]
        m = gl.LiveMap(fetch=fake(routes), now=lambda: t[0], key_file=Path(tempfile.mkdtemp()) / "k.json")
        asyncio.run(m.satellite(17.38, 78.48))
        routes.clear()
        t[0] += gl.TIME_TTL + 5
        s = asyncio.run(m.satellite(17.38, 78.48))
        self.assertTrue(s["ok"] and s["stale"])
        self.assertEqual(s["time"], "2026-11-27T08:30:00Z")


class Webcams(unittest.TestCase):
    WINDY = {"total": 3, "webcams": [
        {"webcamId": 101, "title": "Hussain Sagar", "status": "active", "lastUpdatedOn": "2026-11-27T08:50:00.000Z",
         "location": {"city": "Hyderabad", "country": "India", "latitude": 17.42, "longitude": 78.47},
         "images": {"current": {"preview": "https://imgproxy.windy.com/x/preview.jpg"}},
         "player": {"live": "https://webcams.windy.com/webcams/public/embed/player/101/live", "day": "https://webcams.windy.com/webcams/public/embed/player/101/day"},
         "urls": {"detail": "https://www.windy.com/webcams/101"}},
        {"webcamId": 102, "title": "Evil", "location": {"latitude": 17.4, "longitude": 78.4},
         "images": {"current": {"preview": "javascript:alert(1)"}},
         "player": {"day": {"embed": "https://evil.example.com/player"}}, "urls": {"detail": "http://www.windy.com/x"}},
        {"webcamId": 103, "title": "no coords", "location": {}},
    ]}

    def lm(self, routes, seen=None):
        kf = Path(tempfile.mkdtemp()) / "livecams.json"
        return gl.LiveMap(fetch=fake(routes, seen), now=lambda: NOW, key_file=kf), kf

    def test_no_key_explains_how(self):
        m, _ = self.lm({})
        d = asyncio.run(m.webcams(17.4, 78.5, 30))
        self.assertFalse(d["configured"])
        self.assertIn("api.windy.com/keys", d["how"])

    def test_key_is_sent_as_header_and_urls_are_sanitised(self):
        seen = []
        m, kf = self.lm({"api.windy.com/webcams/api/v3/webcams": (200, json.dumps(self.WINDY))}, seen)
        m.set_key("abcdefghijklmnop1234")
        d = asyncio.run(m.webcams(17.4, 78.5, 30))
        url, headers = seen[0]
        self.assertIn("nearby=17.4000,78.5000,30", url)
        self.assertNotIn("abcdefghijklmnop1234", url)                     # key only in the header
        self.assertEqual(headers["X-WINDY-API-KEY"], "abcdefghijklmnop1234")
        self.assertNotIn("abcdefghijklmnop1234", json.dumps(d))           # never echoed back
        self.assertEqual([c["id"] for c in d["webcams"]], ["101", "102"])
        good, bad = d["webcams"]
        self.assertEqual(good["player_kind"], "live stream")
        self.assertTrue(good["player"].startswith("https://webcams.windy.com/"))
        self.assertEqual(good["place"], "Hyderabad, India")
        self.assertIsNone(bad["preview"]); self.assertIsNone(bad["player"]); self.assertIsNone(bad["detail"])
        self.assertIn("windy_key", kf.read_text())

    def test_rejected_key_is_reported(self):
        m, _ = self.lm({"api.windy.com": (401, "{}")})
        m.set_key("abcdefghijklmnop1234")
        d = asyncio.run(m.webcams(17.4, 78.5, 30))
        self.assertFalse(d["ok"])
        self.assertIn("rejected", d["error"])


class Routes(unittest.TestCase):
    def test_key_endpoint_is_commander_only_and_validates(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        role = {"cmd": False}
        audits = []

        async def audit(a, d=""):
            audits.append((a, d))
        st = {"globalmap_live": gl.LiveMap(fetch=fake({}), key_file=Path(tempfile.mkdtemp()) / "k.json")}
        app = FastAPI()
        app.include_router(gl.build_router({"state": st, "audit": audit, "commander": lambda: role["cmd"]}))
        c = TestClient(app)
        self.assertEqual(c.post("/api/globalmap/webcams/key", json={"key": "abcdefghijklmnop1234"}).status_code, 403)
        role["cmd"] = True
        self.assertEqual(c.post("/api/globalmap/webcams/key", json={"key": "bad key!"}).status_code, 400)
        r = c.post("/api/globalmap/webcams/key", json={"key": "abcdefghijklmnop1234"}).json()
        self.assertEqual(r, {"ok": True, "configured": True})
        self.assertEqual(audits, [("globalmap.webcams.key", "set")])         # audit never holds the key
        self.assertEqual(c.get("/api/globalmap/live?lat=200&lon=0").status_code, 400)


if __name__ == "__main__":
    unittest.main()
