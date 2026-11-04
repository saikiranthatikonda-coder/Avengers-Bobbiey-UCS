"""Render the BUCS app icon (arc-reactor rings on a dark rounded square) without any
image library, at every size the installers need.

    python tools/desktop/make_icon.py OUT_DIR
      -> OUT_DIR/bucs.ico            (Windows: 16-256 px PNG entries)
         OUT_DIR/BUCS.iconset/*.png  (macOS: `iconutil -c icns` turns it into BUCS.icns)
         OUT_DIR/bucs-1024.png
"""

import math
import struct
import sys
import zlib
from pathlib import Path

BG_TOP, BG_BOT = (10, 18, 34), (4, 8, 16)
CYAN, WHITE = (64, 220, 255), (225, 250, 255)


def _smooth(edge0: float, edge1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3 - 2 * t)


def render(size: int) -> bytes:
    """RGBA rows (with PNG filter byte) for a size x size icon."""
    s = float(size)
    aa = 1.2 / s                          # anti-aliasing width in unit coordinates
    rows = bytearray()
    radius = 0.2                          # macOS-style rounded square
    for y in range(size):
        rows.append(0)
        v = (y + 0.5) / s
        for x in range(size):
            u = (x + 0.5) / s
            # rounded square mask (inset 4 %)
            qx, qy = abs(u - 0.5) - (0.46 - radius), abs(v - 0.5) - (0.46 - radius)
            d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
            a = 1 - _smooth(-aa, aa, d)
            if a <= 0:
                rows += b"\0\0\0\0"
                continue
            col = [BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * v for i in range(3)]
            dx, dy = u - 0.5, v - 0.5
            r = math.hypot(dx, dy)
            ang = math.atan2(dy, dx)
            glow = math.exp(-((r / 0.30) ** 2)) * 0.55
            light = glow
            # outer ring and inner ring
            for rr, w, k in ((0.36, 0.022, 1.0), (0.205, 0.016, 0.9)):
                light += k * (1 - _smooth(w - aa, w + aa, abs(r - rr)))
            # ten segments between the rings
            seg = (ang / (2 * math.pi) * 10) % 1.0
            if 0.24 < r < 0.33 and 0.18 < seg < 0.82:
                edge = min(seg - 0.18, 0.82 - seg) * 2 * math.pi * r / 10
                light += 0.75 * _smooth(0, aa * 1.5, edge) * (1 - _smooth(0.32 - aa, 0.33, r)) * _smooth(0.24, 0.24 + aa, r)
            # bright core
            light += 1 - _smooth(0.085 - aa, 0.085 + aa, r)
            light = min(light, 1.6)
            tint = [CYAN[i] + (WHITE[i] - CYAN[i]) * max(0.0, light - 1.0) / 0.6 for i in range(3)]
            mix = min(light, 1.0)
            px = [int(max(0, min(255, col[i] * (1 - mix) + tint[i] * mix))) for i in range(3)]
            rows += bytes(px) + bytes([int(255 * a)])
    return bytes(rows)


def png(size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        c = kind + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(render(size), 9))
            + chunk(b"IEND", b""))


def ico(images: dict) -> bytes:
    sizes = sorted(images)
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for sz in sizes:
        data = images[sz]
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/icon")
    out.mkdir(parents=True, exist_ok=True)
    cache: dict = {}

    def get(sz: int) -> bytes:
        if sz not in cache:
            cache[sz] = png(sz)
        return cache[sz]

    (out / "bucs.ico").write_bytes(ico({sz: get(sz) for sz in (16, 24, 32, 48, 64, 128, 256)}))
    iconset = out / "BUCS.iconset"
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        (iconset / f"icon_{base}x{base}.png").write_bytes(get(base))
        (iconset / f"icon_{base}x{base}@2x.png").write_bytes(get(base * 2))
    (out / "bucs-1024.png").write_bytes(get(1024))
    print(f"icons written to {out}")


if __name__ == "__main__":
    main()
