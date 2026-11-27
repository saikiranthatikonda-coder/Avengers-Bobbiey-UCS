"""Build static/geo/india_ts_ll.json (lon/lat GeoJSON) from static/geo/india_ts.json.

india_ts.json holds the Survey-of-India-conformant outlines pre-projected to Web Mercator SVG
paths (datta07/INDIAN-SHAPEFILES, MIT). The immersive MapLibre region maps need lon/lat, so this
inverts the documented projection exactly (x = k*lon + tx, y = -k*ln(tan(pi/4 + lat/2)) + ty).
Each sub-path becomes one polygon ring; label points are the area-weighted centroid of the
largest ring. Run:  python tools/geo_lonlat.py
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "static" / "geo" / "india_ts.json"
OUT = ROOT / "static" / "geo" / "india_ts_ll.json"
TS_RENAME = {"Warangal Urban": "Hanumakonda", "Warangal Rural": "Warangal"}


def inv(p: dict):
    k, tx, ty = p["k"], p["tx"], p["ty"]

    def f(x: float, y: float) -> list[float]:
        lon = math.degrees((x - tx) / k)
        lat = math.degrees(2 * math.atan(math.exp((ty - y) / k)) - math.pi / 2)
        return [round(lon, 4), round(lat, 4)]
    return f


def rings(d: str, f) -> list[list[list[float]]]:
    out = []
    for sub in re.split(r"(?=M)", d):
        pts = [f(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+),(-?[\d.]+)", sub)]
        if len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            out.append(pts)
    return out


def area_centroid(r):
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(r, r[1:]):
        c = x1 * y2 - x2 * y1
        a += c; cx += (x1 + x2) * c; cy += (y1 + y2) * c
    if abs(a) < 1e-12:
        return abs(a), r[0]
    return abs(a / 2), [round(cx / (3 * a), 4), round(cy / (3 * a), 4)]


def feature(name: str, d: str, f, kind: str) -> tuple[dict, dict]:
    rs = rings(d, f)
    best = max((area_centroid(r) for r in rs), key=lambda t: t[0])
    geom = {"type": "MultiPolygon", "coordinates": [[r] for r in rs]}
    props = {"name": name, "kind": kind, "lon": best[1][0], "lat": best[1][1]}
    return {"type": "Feature", "properties": props, "geometry": geom}, props


def main() -> None:
    g = json.loads(SRC.read_text(encoding="utf-8"))
    fi, ft = inv(g["india"]["proj"]), inv(g["telangana"]["proj"])
    states = [feature(s["name"], s["d"], fi, "state")[0] for s in g["india"]["states"]]
    dists = [feature(TS_RENAME.get(s["name"], s["name"]), s["d"], ft, "district")[0] for s in g["telangana"]["districts"]]
    india_outline = {"type": "Feature", "properties": {"name": "India", "kind": "outline"},
                     "geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in rings(g["india"]["outline"], fi)]}}
    ts_outline = {"type": "Feature", "properties": {"name": "Telangana", "kind": "outline"},
                  "geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in rings(g["telangana"]["outline"], ft)]}}
    out = {"source": g["source"], "note": "lon/lat inverse of india_ts.json (tools/geo_lonlat.py)",
           "india": {"outline": india_outline, "states": {"type": "FeatureCollection", "features": states}},
           "telangana": {"outline": ts_outline, "districts": {"type": "FeatureCollection", "features": dists}}}
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(OUT, OUT.stat().st_size, "bytes", len(states), "states", len(dists), "districts")


if __name__ == "__main__":
    main()
