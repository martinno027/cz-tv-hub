"""Writes apps/tizen/app/icon.png: a neutral placeholder icon (no CT logo), standard library only."""
import pathlib
import struct
import zlib

SIZE = 512
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "icon.png"


def pixel(x: int, y: int) -> tuple:
    # Dark blue gradient background with a white TV screen outline and a red "live" dot.
    r, g, b = 18 + y * 20 // SIZE, 26 + y * 30 // SIZE, 46 + y * 50 // SIZE
    left, top, right, bottom, line = 96, 136, 416, 344, 18
    inside = left <= x <= right and top <= y <= bottom
    border = inside and (x < left + line or x > right - line or y < top + line or y > bottom - line)
    stand = 216 <= x <= 296 and 360 <= y <= 378
    if border or stand:
        return 242, 244, 248
    if (x - 352) ** 2 + (y - 300) ** 2 <= 22 ** 2:
        return 232, 64, 47
    return r, g, b


def png() -> bytes:
    rows = b"".join(b"\x00" + bytes(c for x in range(SIZE) for c in pixel(x, y)) for y in range(SIZE))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    OUT.write_bytes(png())
    print(OUT)
