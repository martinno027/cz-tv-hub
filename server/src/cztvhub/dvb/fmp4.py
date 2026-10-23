"""Audio/video sync fix for the remuxed fMP4 HLS (spec/dvb-gateway.md "A/V sync").

A DVB mux carries audio and video with different start times (and a viewer joins mid-stream, so
the video starts at the next keyframe). ffmpeg's fMP4 writer starts every track's fragments at 0
and keeps the real offsets only in the init segment's edit lists (``elst``). HLS players (AVPlayer,
and in practice the others) ignore edit lists, so the sound ran up to a second ahead of or behind
the picture. The gateway therefore serves:
- the init segment with neutral edit lists (no empty edit, media time 0), and
- every media segment with each track's ``tfdt`` moved by what its edit list said,
so the timing is in the fragments themselves. In-place edits only: sizes never change.
"""
from __future__ import annotations

import struct
from typing import Iterator

Box = tuple[bytes, int, int, int]  # type, start, body start, end


def boxes(data: bytes, start: int = 0, end: int | None = None) -> Iterator[Box]:
    end = len(data) if end is None else end
    off = start
    while off + 8 <= end:
        size, typ = struct.unpack(">I4s", data[off:off + 8])
        head = 8
        if size == 1:
            if off + 16 > end:
                return
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
            head = 16
        elif size == 0:
            size = end - off
        if size < head or off + size > end:
            return
        yield typ, off, off + head, off + size
        off += size


def find(data: bytes, start: int, end: int, path: list[bytes]) -> Iterator[tuple[int, int]]:
    """(body start, end) of every box at ``path`` below [start, end)."""
    for typ, _, body, stop in boxes(data, start, end):
        if typ == path[0]:
            if len(path) == 1:
                yield body, stop
            else:
                yield from find(data, body, stop, path[1:])


def _full_box_u32(data: bytes, body: int, v0: int, v1: int) -> int:
    """A 32-bit field of a version 0/1 full box at offset ``v0`` / ``v1`` of its body."""
    return struct.unpack(">I", data[body + (v0 if data[body] == 0 else v1):][:4])[0]


def _elst_entries(data: bytes, body: int) -> Iterator[tuple[int, int, int]]:
    """(offset of the entry, segment duration, media time) of an ``elst`` body."""
    version = data[body]
    count = struct.unpack(">I", data[body + 4:body + 8])[0]
    fmt, size = (">Qq", 20) if version == 1 else (">Ii", 12)
    p = body + 8
    for _ in range(count):
        if p + size > len(data):
            return
        duration, media_time = struct.unpack(fmt, data[p:p + struct.calcsize(fmt)])
        yield p, duration, media_time
        p += size


def track_shifts(init: bytes) -> dict[int, int]:
    """track id → amount (in the track's timescale) to add to its ``tfdt`` so that the fragments carry
    the presentation timing the edit lists described: ``empty edit − first media time`` per track,
    plus one constant that keeps every shift non-negative."""
    try:
        movie_scale = next(_full_box_u32(init, b, 12, 20) for b, _ in find(init, 0, len(init), [b"moov", b"mvhd"]))
    except StopIteration:
        return {}
    tracks: dict[int, tuple[int, float]] = {}
    for tb, te in find(init, 0, len(init), [b"moov", b"trak"]):
        try:
            tid = next(_full_box_u32(init, b, 12, 20) for b, _ in find(init, tb, te, [b"tkhd"]))
            scale = next(_full_box_u32(init, b, 12, 20) for b, _ in find(init, tb, te, [b"mdia", b"mdhd"]))
        except StopIteration:
            continue
        if not movie_scale or not scale:
            continue
        empty, media = 0, 0
        for eb, _ in find(init, tb, te, [b"edts", b"elst"]):
            for _, duration, media_time in _elst_entries(init, eb):
                if media_time == -1:
                    empty += duration
                else:
                    media = media_time
                    break
        tracks[tid] = (scale, empty / movie_scale - media / scale)
    if not tracks:
        return {}
    base = max(0.0, -min(shift for _, shift in tracks.values()))
    return {tid: round((shift + base) * scale) for tid, (scale, shift) in tracks.items()}


def neutralise_edits(init: bytes) -> bytes:
    """The init segment with identity edit lists (empty edits of length 0, media time 0), so players that
    do apply edit lists see the same timeline as the ones that ignore them."""
    data = bytearray(init)
    for tb, te in find(init, 0, len(init), [b"moov", b"trak"]):
        for eb, _ in find(init, tb, te, [b"edts", b"elst"]):
            wide = init[eb] == 1
            for p, _, media_time in _elst_entries(init, eb):
                if media_time == -1:
                    data[p:p + (8 if wide else 4)] = bytes(8 if wide else 4)  # duration 0
                else:
                    q = p + (8 if wide else 4)
                    data[q:q + (8 if wide else 4)] = bytes(8 if wide else 4)  # media time 0
    return bytes(data)


def shift_fragments(segment: bytes, shifts: dict[int, int]) -> bytes:
    """``segment`` with every track fragment's ``tfdt`` moved by ``shifts[track id]``."""
    if not any(shifts.values()):
        return segment
    data = bytearray(segment)
    for mb, me in find(segment, 0, len(segment), [b"moof"]):
        for tb, te in find(segment, mb, me, [b"traf"]):
            tid = next((struct.unpack(">I", segment[b + 4:b + 8])[0] for b, _ in find(segment, tb, te, [b"tfhd"])), None)
            delta = shifts.get(tid or 0, 0)
            if not delta:
                continue
            for db, _ in find(segment, tb, te, [b"tfdt"]):
                fmt = ">Q" if segment[db] == 1 else ">I"
                n = struct.calcsize(fmt)
                value = struct.unpack(fmt, segment[db + 4:db + 4 + n])[0] + delta
                if value < 1 << (8 * n):
                    data[db + 4:db + 4 + n] = struct.pack(fmt, value)
    return bytes(data)


# -- open-GOP leading pictures ------------------------------------------------------------------
# ffmpeg's HLS muxer (seen with 9.0.2) writes a too small composition offset for the keyframe that starts
# each fragment when the stream has an open GOP (ČT's DVB-T2 HEVC: CRA + RASL leading pictures): the CRA
# is then presented before its leading pictures, which belong before it. At every scene cut the TV showed
# one frame of the new shot, then a few frames of the old one, then the new shot ("the picture jerks").
# Its plain MP4 muxer gets the same data right. fix_leading_pictures() restores the CRA's offset so that
# it is presented right after its last leading picture, in place.
IRAP = range(16, 22)  # BLA, IDR, CRA
LEADING = (6, 7, 8, 9)  # RADL, RASL


def _first_slice_type(sample: bytes) -> int | None:
    """NAL unit type of the first slice of a length-prefixed (4 bytes) HEVC sample."""
    p = 0
    while p + 5 <= len(sample):
        length = struct.unpack(">I", sample[p:p + 4])[0]
        if length <= 0:
            return None
        nal = (sample[p + 4] >> 1) & 0x3F
        if nal < 32:
            return nal
        p += 4 + length
    return None


def video_track(init: bytes) -> int | None:
    for tb, te in find(init, 0, len(init), [b"moov", b"trak"]):
        handler = next((init[b + 8:b + 12] for b, _ in find(init, tb, te, [b"mdia", b"hdlr"])), b"")
        if handler == b"vide":
            return next((_full_box_u32(init, b, 12, 20) for b, _ in find(init, tb, te, [b"tkhd"])), None)
    return None


def hevc_track(init: bytes) -> int | None:
    """The video track id if it is HEVC (hvc1/hev1), else None."""
    track = video_track(init)
    return track if track is not None and (b"hvc1" in init or b"hev1" in init) else None


def fix_leading_pictures(segment: bytes, track: int | None) -> bytes:
    if track is None:
        return segment
    data = None
    for mb, me in find(segment, 0, len(segment), [b"moof"]):
        moof_start = mb - 8
        for tb, te in find(segment, mb, me, [b"traf"]):
            hd = next(find(segment, tb, te, [b"tfhd"]), None)
            if hd is None or struct.unpack(">I", segment[hd[0] + 4:hd[0] + 8])[0] != track:
                continue
            hflags = int.from_bytes(segment[hd[0] + 1:hd[0] + 4], "big")
            p = hd[0] + 8 + (8 if hflags & 1 else 0) + (4 if hflags & 2 else 0)
            default_duration = struct.unpack(">I", segment[p:p + 4])[0] if hflags & 8 else 0
            p += 4 if hflags & 8 else 0
            default_size = struct.unpack(">I", segment[p:p + 4])[0] if hflags & 0x10 else 0
            for rb, _ in find(segment, tb, te, [b"trun"]):
                version, rflags = segment[rb], int.from_bytes(segment[rb + 1:rb + 4], "big")
                if not rflags & 0x800:
                    continue  # no composition offsets at all
                count = struct.unpack(">I", segment[rb + 4:rb + 8])[0]
                q, offset, dts = rb + 8, moof_start, 0
                if rflags & 1:
                    offset += struct.unpack(">i", segment[q:q + 4])[0]
                    q += 4
                if rflags & 4:
                    q += 4
                rows = []  # (nal type, dts, cto, position of the cto field, duration)
                for _ in range(count):
                    duration, size = default_duration, default_size
                    if rflags & 0x100:
                        duration = struct.unpack(">I", segment[q:q + 4])[0]
                        q += 4
                    if rflags & 0x200:
                        size = struct.unpack(">I", segment[q:q + 4])[0]
                        q += 4
                    if rflags & 0x400:
                        q += 4
                    cto = struct.unpack(">i" if version == 1 else ">I", segment[q:q + 4])[0]
                    rows.append((_first_slice_type(segment[offset:offset + size]), dts, cto, q, duration))
                    q += 4
                    offset += size
                    dts += duration
                for i, (nal, dts0, cto0, pos, duration) in enumerate(rows):
                    if nal not in IRAP:
                        continue
                    leading = []
                    for nal2, dts2, cto2, _, _ in rows[i + 1:]:
                        if nal2 not in LEADING:
                            break
                        leading.append(dts2 + cto2)
                    if not leading or dts0 + cto0 > max(leading):
                        continue
                    wanted = max(leading) + (duration or 1) - dts0
                    if data is None:
                        data = bytearray(segment)
                    data[pos:pos + 4] = struct.pack(">i" if version == 1 else ">I", wanted)
    return segment if data is None else bytes(data)
