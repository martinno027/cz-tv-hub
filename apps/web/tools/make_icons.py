"""Writes the web app's icons (apps/web/icons/) from the Samsung app's icon, standard library only (spec/web.md T35).

The artwork is the one of apps/tizen/tools/make_icon.py (``pixel``: the neutral TV outline with the red "live" dot,
no ČT logo), drawn again at each size with 4 x 4 samples per pixel instead of scaling the 512 px PNG, so the small
icons stay sharp without an image library. The maskable icon draws the same artwork at 80 % around the centre, so
it sits well inside the safe zone (a circle of 40 % of the size) that Android/Chrome masks may cut down to.

Usage (from the repository root): ``python3 apps/web/tools/make_icons.py``
"""
from __future__ import annotations

import importlib.util
import pathlib
import struct
import zlib

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "icons"
_spec = importlib.util.spec_from_file_location("tizen_icon", HERE.parents[1] / "tizen" / "tools" / "make_icon.py")
tizen_icon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tizen_icon)

SOURCE = tizen_icon.SIZE  # the artwork's own coordinate space (512)
SAMPLES = 4
MASKABLE_SCALE = 0.8

# file name -> (size in px, scale of the artwork around the centre)
ICONS = {
    "icon-192.png": (192, 1.0),
    "icon-512.png": (512, 1.0),
    "icon-maskable-512.png": (512, MASKABLE_SCALE),
    "apple-touch-icon.png": (180, 1.0),  # iOS/macOS Safari round the corners themselves; opaque, no transparency
}


def render(size: int, scale: float) -> bytes:
    """Raw RGB rows (each with PNG filter byte 0)."""
    unit = SOURCE / size
    centre = SOURCE / 2
    offsets = [(i + 0.5) / SAMPLES for i in range(SAMPLES)]
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            r = g = b = 0
            for oy in offsets:
                sy = centre + ((y + oy) * unit - centre) / scale
                for ox in offsets:
                    sx = centre + ((x + ox) * unit - centre) / scale
                    pr, pg, pb = tizen_icon.pixel(sx, sy)
                    r, g, b = r + pr, g + pg, b + pb
            n = SAMPLES * SAMPLES
            rows += bytes((round(r / n), round(g / n), round(b / n)))
    return bytes(rows)


def png(size: int, rows: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, (size, scale) in ICONS.items():
        target = OUT / name
        target.write_bytes(png(size, render(size, scale)))
        print(target)


if __name__ == "__main__":
    main()
