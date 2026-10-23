import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from cztvhub.dvb import fmp4, hls


def box(typ: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), typ) + body


def full(typ: bytes, version: int, body: bytes) -> bytes:
    return box(typ, bytes([version, 0, 0, 0]) + body)


def trak(track_id: int, scale: int, edits: list[tuple[int, int]]) -> bytes:
    tkhd = full(b"tkhd", 0, struct.pack(">III", 0, 0, track_id) + bytes(68))
    elst = full(b"elst", 0, struct.pack(">I", len(edits)) + b"".join(struct.pack(">IiI", d, m, 1 << 16) for d, m in edits))
    mdhd = full(b"mdhd", 0, struct.pack(">IIII", 0, 0, scale, 0) + bytes(4))
    return box(b"trak", tkhd + box(b"edts", elst) + box(b"mdia", mdhd))


def init(*traks: bytes, movie_scale: int = 1000) -> bytes:
    mvhd = full(b"mvhd", 0, struct.pack(">IIII", 0, 0, movie_scale, 0) + bytes(80))
    return box(b"ftyp", b"iso5\0\0\0\0") + box(b"moov", mvhd + b"".join(traks))


def fragment(*tracks: tuple[int, int, int]) -> bytes:
    """moof with one traf per (track id, tfdt version, decode time)."""
    trafs = b"".join(box(b"traf", full(b"tfhd", 0, struct.pack(">I", tid))
                         + full(b"tfdt", v, struct.pack(">Q" if v else ">I", t))) for tid, v, t in tracks)
    return box(b"moof", full(b"mfhd", 0, struct.pack(">I", 1)) + trafs) + box(b"mdat", b"\0" * 16)


def tfdts(segment: bytes) -> dict[int, int]:
    out = {}
    for tb, te in fmp4.find(segment, 0, len(segment), [b"moof", b"traf"]):
        tid = next(struct.unpack(">I", segment[b + 4:b + 8])[0] for b, _ in fmp4.find(segment, tb, te, [b"tfhd"]))
        for db, _ in fmp4.find(segment, tb, te, [b"tfdt"]):
            out[tid] = struct.unpack(">Q" if segment[db] == 1 else ">I", segment[db + 4:db + (12 if segment[db] == 1 else 8)])[0]
    return out


class Fmp4Test(unittest.TestCase):
    # ffmpeg's shape: video starts 1.0 s after the audio (empty edit) and has a 0.08 s CTS offset
    INIT = init(trak(1, 90000, [(1000, -1), (5000, 7200)]), trak(2, 48000, [(6000, 0)]))

    def test_shifts(self):
        # video: 1.0 − 0.08 = 0.92 s, audio 0 s; nothing negative, so no common offset
        self.assertEqual(fmp4.track_shifts(self.INIT), {1: 82800, 2: 0})
        # audio starting later: its own empty edit; the video's CTS offset makes the audio's shift the base
        later = init(trak(1, 90000, [(5000, 7200)]), trak(2, 48000, [(600, -1), (5000, 0)]))
        self.assertEqual(fmp4.track_shifts(later), {1: 0, 2: round((0.6 + 0.08) * 48000)})
        self.assertEqual(fmp4.track_shifts(b"garbage"), {})

    def test_neutralised_init_keeps_its_size(self):
        out = fmp4.neutralise_edits(self.INIT)
        self.assertEqual(len(out), len(self.INIT))
        self.assertEqual(fmp4.track_shifts(out), {1: 0, 2: 0})  # identity edit lists

    def test_fragment_shift_both_tfdt_versions(self):
        seg = fragment((1, 1, 900000), (2, 0, 480000))
        out = fmp4.shift_fragments(seg, {1: 82800, 2: 0})
        self.assertEqual(len(out), len(seg))
        self.assertEqual(tfdts(out), {1: 982800, 2: 480000})
        self.assertEqual(tfdts(fmp4.shift_fragments(seg, {2: 100})), {1: 900000, 2: 480100})
        self.assertIs(fmp4.shift_fragments(seg, {1: 0}), seg)
        # a 32-bit tfdt that would overflow stays as it was rather than wrapping
        big = fragment((1, 0, 0xFFFFFFF0))
        self.assertEqual(tfdts(fmp4.shift_fragments(big, {1: 100})), {1: 0xFFFFFFF0})


class GatewayServesSyncedFilesTest(unittest.TestCase):
    def test_init_neutralised_segments_shifted(self):
        from types import SimpleNamespace
        from cztvhub.dvb.gateway import DvbGateway
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / hls.INIT).write_bytes(Fmp4Test.INIT)
            session = SimpleNamespace(directory=Path(tmp), shifts=None)
            self.assertEqual(fmp4.track_shifts(DvbGateway._synced(session, hls.INIT, Fmp4Test.INIT)), {1: 0, 2: 0})
            seg = DvbGateway._synced(session, "seg_00000.m4s", fragment((1, 1, 0), (2, 1, 0)))
            self.assertEqual(tfdts(seg), {1: 82800, 2: 0})
            self.assertEqual(session.shifts, {1: 82800, 2: 0})  # computed once per session


def _encoders() -> str:
    try:
        return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    except OSError:
        return ""


HAVE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe")) and " libx264 " in _encoders()
MARKS = (1.3, 2.9, 4.45, 6.1, 7.7, 9.25)  # irregular, so a wrong pairing cannot look right


@unittest.skipUnless(HAVE, "ffmpeg with libx264 needed")
class AvSyncTest(unittest.TestCase):
    """A flash and a beep at the same instants; the gateway's remux + fmp4 fix must keep them together
    when read like HLS players read fMP4 (edit lists ignored), however the viewer joins the stream."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cond = "+".join(f"between(t\\,{m}\\,{m + 0.04})" for m in MARKS)
        cls.base = cls.root / "base.ts"
        cls.run_ok(["ffmpeg", "-f", "lavfi", "-i",
                    f"color=black:size=320x180:rate=25,drawbox=color=white:t=fill:enable='{cond}'",
                    "-f", "lavfi", "-i", f"aevalsrc='if({cond}\\,0.8*sin(2*PI*1000*t)\\,0)':s=48000",
                    "-t", "11", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-bf", "2",
                    "-c:a", "mp2", "-b:a", "192k", "-f", "mpegts", str(cls.base)])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def run_ok(cmd, **kw):
        return subprocess.run(cmd[:1] + ["-hide_banner", "-loglevel", "error"] + cmd[1:], check=True,
                              capture_output=True, **kw)

    def joined(self, audio_late: float, cut: float = 0.0) -> bytes:
        """Same content and PTS, but one track starts later in the stream (and optionally a byte cut)."""
        out = self.root / f"join_{audio_late}.ts"
        late_v = ["-ss", str(-audio_late)] if audio_late < 0 else []
        late_a = ["-ss", str(audio_late)] if audio_late > 0 else []
        self.run_ok(["ffmpeg", "-y", "-copyts", *late_v, "-i", str(self.base), *late_a, "-i", str(self.base),
                     "-map", "0:v", "-map", "1:a", "-c", "copy", "-muxdelay", "0", "-f", "mpegts", str(out)])
        data = out.read_bytes()
        return data[(int(len(data) * cut) // 188) * 188:]

    def offsets_ms(self, data: bytes) -> list[int]:
        work = Path(tempfile.mkdtemp(dir=self.root))
        codecs = hls.probe_codecs(data[:hls.PROBE_BYTES])
        self.run_ok(hls.build_ffmpeg_command(work, codecs, live=False), input=data)
        init = (work / hls.INIT).read_bytes()
        shifts = fmp4.track_shifts(init)
        joined = work / "joined.mp4"
        joined.write_bytes(fmp4.neutralise_edits(init) + b"".join(
            fmp4.shift_fragments(p.read_bytes(), shifts) for p in sorted(work.glob("seg_*.m4s"))))
        ignore = ["-ignore_editlist", "1", "-i", str(joined)]
        frames = self.run_ok(["ffmpeg", *ignore, "-map", "0:v", "-vf", "scale=8:8,format=gray", "-f", "rawvideo",
                              "-fps_mode", "passthrough", "pipe:1"]).stdout
        times = [float(x.strip(",")) for x in self.run_ok(
            ["ffprobe", "-v", "error", *ignore[:2], "-select_streams", "v", "-show_entries", "frame=pts_time",
             "-of", "csv=p=0", str(joined)]).stdout.decode().split()]
        flashes, prev = [], False
        for i, t in enumerate(times):
            bright = sum(frames[i * 64:(i + 1) * 64]) / 64 > 128
            if bright and not prev:
                flashes.append(t)
            prev = bright
        pcm = memoryview(self.run_ok(["ffmpeg", *ignore, "-map", "0:a", "-f", "s16le", "-ac", "1", "-ar", "48000",
                                      "pipe:1"]).stdout).cast("h")
        a0 = float(self.run_ok(["ffprobe", "-v", "error", *ignore[:2], "-select_streams", "a", "-show_entries",
                                "stream=start_time", "-of", "csv=p=0", str(joined)]).stdout.decode().strip().strip(",") or 0)
        beeps, i = [], 0
        while i < len(pcm):
            if abs(pcm[i]) > 8000:
                beeps.append(a0 + i / 48000)
                i += 9600
            else:
                i += 1
        self.assertTrue(flashes and beeps, "markers not found")
        return [round((min(beeps, key=lambda b: abs(b - f)) - f) * 1000) for f in flashes]

    def test_in_sync_however_the_stream_starts(self):
        for audio_late, cut in ((-1.0, 0.0), (1.2, 0.0), (0.6, 0.07), (0.6, 0.21)):
            with self.subTest(audio_late=audio_late, cut=cut):
                offsets = self.offsets_ms(self.joined(audio_late, cut))
                self.assertTrue(all(abs(o) <= 60 for o in offsets), offsets)


if __name__ == "__main__":
    unittest.main()
