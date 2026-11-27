"""v8.8: immersive INDIA / TELANGANA maps (regionmap.py + lon/lat boundaries), the disaster map's
EONET tracks, and the voice intents that drive the region maps. Offline: every fetch is faked."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import regionmap as rm  # noqa: E402

run = asyncio.run


def fake_fetch(calls):
    async def f(url, params):
        calls.append((url, params))
        n = len(params["latitude"].split(","))
        if "air-quality" in url:
            return [{"current": {"us_aqi": 40 + i, "pm2_5": 10.0, "pm10": 20.0, "time": "2026-11-27T20:00"}} for i in range(n)]
        return [{"current": {"temperature_2m": 20.0 + i * 0.1, "apparent_temperature": 21.0, "relative_humidity_2m": 60,
                             "precipitation": 0.0, "weather_code": 0, "wind_speed_10m": 5.0, "wind_direction_10m": 90,
                             "cloud_cover": 10, "time": "2026-11-27T20:15"}} for i in range(n)]
    return f


class Geo(unittest.TestCase):
    def test_lonlat_file_matches_known_places(self):
        g = json.loads((ROOT / "static" / "geo" / "india_ts_ll.json").read_text(encoding="utf-8"))
        self.assertEqual(len(g["india"]["states"]["features"]), 36)
        self.assertEqual(len(g["telangana"]["districts"]["features"]), 33)
        names = {f["properties"]["name"] for f in g["telangana"]["districts"]["features"]}
        self.assertIn("Hanumakonda", names)                  # today's district names
        regs = rm.load_regions()
        self.assertTrue(regs["telangana"].contains(17.385, 78.4867))          # Hyderabad
        self.assertTrue(regs["india"].contains(28.6139, 77.2090))             # Delhi
        self.assertFalse(regs["india"].contains(27.7172, 85.3240))            # Kathmandu is outside
        self.assertFalse(regs["telangana"].contains(13.0827, 80.2707))        # Chennai is outside

    def test_converter_is_reproducible(self):
        import subprocess
        before = (ROOT / "static" / "geo" / "india_ts_ll.json").read_bytes()
        subprocess.run([sys.executable, str(ROOT / "tools" / "geo_lonlat.py")], check=True, capture_output=True)
        self.assertEqual((ROOT / "static" / "geo" / "india_ts_ll.json").read_bytes(), before)


class RegionData(unittest.TestCase):
    def test_snapshot_batches_and_labels(self):
        calls = []
        eng = rm.RegionMaps({}, fetch=fake_fetch(calls), now=lambda: 1000.0)
        s = run(eng.snapshot("telangana"))
        self.assertEqual(len(calls), 2)                                      # ONE weather + ONE air request
        self.assertEqual(len(s["points"]), 34)                               # 33 districts + Hyderabad city
        self.assertTrue(all(p["wx"] and p["aq"] for p in s["points"]))
        self.assertEqual(s["weather"]["source"], "Open-Meteo")
        self.assertIn("not a town", s["point_note"])
        run(eng.snapshot("telangana"))
        self.assertEqual(len(calls), 2)                                      # cached
        self.assertEqual(s["extremes"]["worst_air"], {"name": s["points"][-1]["name"], "aqi": 73, "band": "moderate"})

    def test_failure_is_reported_never_invented(self):
        async def boom(url, params):
            raise OSError("offline")
        s = run(rm.RegionMaps({}, fetch=boom).snapshot("india"))
        self.assertFalse(s["weather"]["ok"])
        self.assertEqual(s["weather"]["error"], "OSError")
        self.assertTrue(all(p["wx"] is None and p["aq"] is None for p in s["points"]))
        self.assertIsNone(s["extremes"]["hottest"])

    def test_hazards_inside_and_near(self):
        items = [
            {"id": "a", "lat": 17.4, "lon": 78.5, "severity": 2, "sev_label": "MEDIUM", "type": "rain", "title": "Hyderabad rain", "source": "NDMA SACHET"},
            {"id": "b", "lat": 17.0, "lon": 81.4, "severity": 3, "sev_label": "HIGH", "type": "flood", "title": "near border", "source": "GDACS"},
            {"id": "c", "lat": 28.6, "lon": 77.2, "severity": 4, "sev_label": "CRITICAL", "type": "heat", "title": "Delhi", "source": "NDMA SACHET"},
            {"id": "d", "lat": 17.4, "lon": 78.5, "severity": 4, "sev_label": "CRITICAL", "type": "x", "title": "muted", "source": "x", "muted": True},
        ]
        eng = rm.RegionMaps({"disaster": SimpleNamespace(items=lambda loc: items, cache={})}, fetch=fake_fetch([]))
        ev = eng.events("telangana")
        got = {i["id"]: i for i in ev["items"]}
        self.assertTrue(got["a"]["inside"])
        self.assertNotIn("c", got)                                           # Delhi is far outside Telangana
        self.assertNotIn("d", got)                                           # muted alerts stay out
        if "b" in got:
            self.assertFalse(got["b"]["inside"])
            self.assertLessEqual(got["b"]["outside_km"], rm.NEAR_KM["telangana"])
        self.assertIn("c", {i["id"] for i in eng.events("india")["items"]})

    def test_flights_filtered_to_region(self):
        rows = [["abc123", "AIC101", "India", 78.4, 17.4, 9000, 230, 90, 0, 0, None],
                ["def456", "BAW1", "UK", -0.4, 51.5, 9000, 230, 90, 0, 0, None]]
        gm = SimpleNamespace(touch=lambda g: None, kick=lambda: None,
                             cache={"opensky": {"extra": {"rows": rows}, "meta": {"time": 990}, "fetched_at": 995, "status": "ok"}})
        f = rm.RegionMaps({"globalmap": gm}, fetch=fake_fetch([])).flights("india")
        self.assertEqual([r[0] for r in f["rows"]], ["abc123"])
        self.assertEqual(f["source"], "OpenSky Network")


class EonetTrack(unittest.TestCase):
    def test_storm_track(self):
        import disaster
        payload = {"events": [{"id": "EONET_1", "title": "Cyclone Test", "categories": [{"id": "severeStorms", "title": "Severe Storms"}],
                               "sources": [], "geometry": [
                                   {"date": "2026-11-25T00:00:00Z", "type": "Point", "coordinates": [88.0, 12.0], "magnitudeValue": 40, "magnitudeUnit": "kts"},
                                   {"date": "2026-11-26T00:00:00Z", "type": "Point", "coordinates": [87.0, 13.5], "magnitudeValue": 55, "magnitudeUnit": "kts"}]}]}
        it = disaster.parse_eonet(payload)[0]
        self.assertEqual(it["type"], "cyclone")
        self.assertEqual([t[:2] for t in it["track"]], [[12.0, 88.0], [13.5, 87.0]])


class Voice(unittest.TestCase):
    def test_region_intents(self):
        import intents
        r = run(intents.match({}, "Switch the Telangana map to live."))
        self.assertEqual(r["ui"][0], {"type": "ui", "action": "region-mode", "region": "telangana", "mode": "live"})
        r = run(intents.match({}, "show air quality on the India map"))
        self.assertEqual(r["ui"][0]["metric"], "aqi")
        r = run(intents.match({}, "switch the disaster map to night lights"))
        self.assertEqual((r["ui"][0]["region"], r["ui"][0]["mode"]), ("disaster", "night"))
        r = run(intents.match({}, "switch the map to satellite"))        # the global map still owns plain "map"
        self.assertEqual(r["ui"][0]["action"], "map-mode")

    def test_agents_can_read_regions(self):
        import dashboard_context as dc
        self.assertIn("regions", dc.route("which telangana district is the hottest right now"))


if __name__ == "__main__":
    unittest.main()
