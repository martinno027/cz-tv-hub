"""Continuous recording: store, retention rules, recorder helpers (spec/dvb-gateway.md "Continuous recording")."""
import json
import tempfile
import unittest
from pathlib import Path

from cztvhub.dvb import space
from cztvhub.dvb.archive import parse_recording_id, recording_id
from cztvhub.dvb.buffer import BlockInfo, BufferStore, block_name, block_start
from cztvhub.dvb.recorder import audio_label, build_command, parse_rolling, parse_streams, segment_number

CH = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
T0 = 1792300800  # 2026-10-18 05:20 UTC, a block boundary
GB = 1000 ** 3


def add(store, work, channel, run, n, start, duration=2.5, size=1000):
    path = Path(work) / f"seg_{n:06d}.m4s"
    path.write_bytes(b"x" * size)
    return store.add(channel, run, n, start, duration, path)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "buffer"
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()

    def test_blocks_index_reload_and_queries(self):
        store = BufferStore(self.root)
        run = "1792300800-ab12"
        for n in range(300):  # 300 x 2.5 s = 12.5 min → two blocks
            add(store, self.work, CH, run, n, T0 + n * 2.5)
        self.assertEqual(block_name(T0), "20261018T0520")
        self.assertEqual(block_start("20261018T0520"), T0)
        blocks = store.blocks(CH)
        self.assertEqual([b.name for b in blocks], ["20261018T0520", "20261018T0530"])
        self.assertEqual(blocks[0].bytes, 240 * 1000)
        self.assertAlmostEqual(store.covered(CH, T0 + 10, T0 + 70), 60.0)
        self.assertEqual([s.n for s in store.between(CH, T0 + 4, T0 + 6)], [1, 2])
        self.assertEqual(store.latest(CH, 6)[0].n, 297)
        # a restart finds everything again, numbering continues
        store2 = BufferStore(self.root)
        self.assertEqual(len(store2.between(CH, T0, T0 + 10 ** 6)), 300)
        seg = add(store2, self.work, CH, run, 300, T0 + 750)
        self.assertEqual(seg.g, 300)
        self.assertEqual(store2.by_g(CH, 150).n, 150)
        stats = store2.stats(CH)
        self.assertEqual(stats["gaps"], 0)
        self.assertEqual(stats["bytesPerHour"], int(301 * 1000 / (301 * 2.5) * 3600))

    def test_gaps_delete_and_runs(self):
        store = BufferStore(self.root)
        add(store, self.work, CH, "1792300800-ab12", 0, T0)
        add(store, self.work, CH, "1792300900-cd34", 0, T0 + 100)  # 97.5 s hole
        self.assertEqual(store.stats(CH)["gaps"], 1)
        store.save_run(CH, "1792300800-ab12", b"init", {"audio": []})
        store.save_run(CH, "1792300900-cd34", b"init", {"audio": []})
        store.save_run(CH, "1792301000-ef56", b"init", {"audio": []})  # never got a segment
        store.prune_runs(CH)
        self.assertEqual(sorted(p.name for p in (self.root / CH / "runs").iterdir()),
                         ["1792300800-ab12", "1792300900-cd34"])
        self.assertEqual(store.delete_block(CH, "20261018T0520"), 2000)
        self.assertEqual(store.between(CH, 0, 10 ** 12), [])
        self.assertGreater(store.delete_channel(CH) + 1, 0)
        self.assertFalse((self.root / CH).exists())
        with self.assertRaises(ValueError):
            store.delete_block(CH, "../x")
        with self.assertRaises(ValueError):
            store.run_dir("../..", "1792300800-ab12")


def blk(channel, i, hours=1 / 6, size=GB, base=T0):
    start = base + i * 600
    return BlockInfo(channel, block_name(start), start, start + 600, hours * 3600, size)


class SpaceTest(unittest.TestCase):
    def test_age_always_wins(self):
        now = T0 + 5 * 86400
        blocks = {CH: [blk(CH, i) for i in range(0, 1000, 50)]}
        plan = space.plan(blocks, now, 3, free=900 * GB, total=1000 * GB)
        self.assertTrue(plan.delete)
        self.assertTrue(all(b.end <= now - 3 * 86400 for b in plan.delete))
        self.assertTrue(all(r == "age" for r in plan.reasons.values()))
        # the day limit is clamped to 1..14 even if settings say otherwise
        self.assertEqual(space.clamp_days(99), 14)
        self.assertEqual(space.clamp_days(0), 1)
        far = space.plan({CH: [blk(CH, 0)]}, T0 + 20 * 86400, 99, free=900 * GB, total=1000 * GB)
        self.assertEqual(len(far.delete), 1)

    def test_fair_share_deletes_from_the_channel_with_most_hours(self):
        other = "aaaabbbbccccddddeeeeffff00001111"
        blocks = {CH: [blk(CH, i) for i in range(12)], other: [blk(other, i) for i in range(4)]}
        # reserve 10 % of 100 GB = 10 GB; 4 GB free → 6 GB missing → six blocks, all from CH (2 h vs 40 min)
        plan = space.plan(blocks, T0 + 7200, 3, free=4 * GB, total=100 * GB)
        self.assertEqual(len(plan.delete), 6)
        self.assertEqual({b.channel for b in plan.delete}, {CH})
        self.assertEqual([b.name for b in plan.delete], [b.name for b in blocks[CH][:6]])
        self.assertEqual(plan.shortfall, 0)
        # once equal, both lose blocks in turn
        plan = space.plan(blocks, T0 + 7200, 3, free=0, total=100 * GB)
        counts = {c: sum(1 for b in plan.delete if b.channel == c) for c in blocks}
        self.assertEqual(counts[CH] - counts[other], 8)

    def test_newest_block_kept_and_shortfall_pauses(self):
        blocks = {CH: [blk(CH, 0), blk(CH, 1)]}
        plan = space.plan(blocks, T0 + 1300, 3, free=0, total=1000 * GB)
        self.assertEqual([b.name for b in plan.delete], [blocks[CH][0].name])
        self.assertGreater(plan.shortfall, 0)  # → recording pauses
        self.assertEqual(space.reserve_bytes(20 * GB), 5 * GB)  # at least 5 GB
        self.assertEqual(space.reserve_bytes(128 * GB), int(12.8 * GB))

    def test_hours_per_channel_estimate(self):
        blocks = {CH: [blk(CH, i, size=GB // 6) for i in range(6)]}  # 1 GB/h
        self.assertEqual(space.hours_per_channel(blocks, free=50 * GB, total=128 * GB, max_days=14), 38.2)
        self.assertEqual(space.hours_per_channel(blocks, free=500 * GB, total=1000 * GB, max_days=1), 24)
        self.assertIsNone(space.hours_per_channel({}, 1, 1, 3))


class RecorderHelpersTest(unittest.TestCase):
    STREAMS = [{"type": "video", "codec": "hevc", "language": "", "impaired": False},
               {"type": "audio", "codec": "mp2", "language": "ces", "impaired": False},
               {"type": "audio", "codec": "eac3", "language": "eng", "impaired": False},
               {"type": "audio", "codec": "mp2", "language": "ces", "impaired": True}]

    def test_every_audio_track_kept_mpeg_audio_to_aac(self):
        cmd = build_command(Path("/w"), self.STREAMS, "ffmpeg")
        joined = " ".join(cmd)
        for i in range(3):
            self.assertIn(f"-map 0:a:{i}", joined)
        self.assertIn("-c:a:0 aac", joined)
        self.assertIn("-c:a:1 copy", joined)
        self.assertIn("-c:a:2 aac", joined)
        self.assertIn("-c:v copy -tag:v hvc1", joined)
        self.assertIn("program_date_time", joined)
        # pipes in, segments into the working dir only (no other outputs)
        self.assertEqual(cmd[cmd.index("-i") + 1], "pipe:0")
        self.assertEqual(cmd[-1], "/w/live.m3u8")

    def test_labels_and_probe_parsing(self):
        self.assertEqual(audio_label(self.STREAMS[1], 0), "Čeština")
        self.assertEqual(audio_label(self.STREAMS[2], 1), "Angličtina (Dolby)")
        self.assertEqual(audio_label(self.STREAMS[3], 2), "Čeština (pro nevidomé)")
        self.assertEqual(audio_label({"language": "xyz"}, 3), "Zvuk 4")
        out = json.dumps({"streams": [
            {"codec_type": "video", "codec_name": "hevc"},
            {"codec_type": "audio", "codec_name": "mp2", "tags": {"language": "CES"},
             "disposition": {"visual_impaired": 1}},
            {"codec_type": "subtitle", "codec_name": "dvb_teletext"}, {"codec_type": "audio"}]})
        self.assertEqual(parse_streams(out), [
            {"type": "video", "codec": "hevc", "language": "", "impaired": False},
            {"type": "audio", "codec": "mp2", "language": "ces", "impaired": True}])
        self.assertEqual(parse_streams("garbage"), [])

    def test_rolling_playlist(self):
        text = ("#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:2.600000,\n"
                "#EXT-X-PROGRAM-DATE-TIME:2026-10-18T07:20:00.500+0200\nseg_000007.m4s\n#EXTINF:2.4,\n"
                "/abs/seg_000008.m4s\n")
        segs = parse_rolling(text)
        self.assertEqual(segs[0], ("seg_000007.m4s", 2.6, T0 + 0.5))
        self.assertEqual(segs[1], ("seg_000008.m4s", 2.4, None))
        self.assertEqual(segment_number("seg_000008.m4s"), 8)
        self.assertIsNone(segment_number("init.mp4"))


class GuardTest(unittest.TestCase):
    """Never onto the SD card, never more channels than the Pi manages (Pi 4 overload, 2026-10-23)."""

    def archive(self, root, allow, limit=3):
        from cztvhub.dvb.archive import Archive
        from cztvhub.dvb.recorder import RecorderManager
        from cztvhub.dvb.thumbs import Settings, Thumbnails
        store = BufferStore(Path(root) / "buffer")
        settings = Settings(Path(root) / "state")
        started = []

        class Rec:
            def __init__(self, ch):
                self.ch, self.run, self.state = ch, None, "x"

            def start(self):
                started.append(self.ch)

            def stop(self, wait=0):
                pass

        manager = RecorderManager(store, lambda ch: None, factory=Rec, log=lambda m: None)
        channels = {f"c{i}" for i in range(6)}
        archive = Archive(store, manager, settings, Thumbnails(None, settings), 3, log=lambda m: None,
                          offered=lambda: channels, max_recordings=limit, allow_system_disk=allow)
        return archive, started

    def test_system_disk_refused_and_limit_kept(self):
        with tempfile.TemporaryDirectory() as tmp:  # a temp dir is on the system disk here
            archive, started = self.archive(tmp, allow=False)
            self.assertTrue(archive.on_system_disk())
            self.assertIn("buffer_dir", archive.can_record())
            archive.settings.update(recordChannels=[f"c{i}" for i in range(6)])
            archive.sync()
            self.assertEqual(started, [])
            archive, started = self.archive(tmp, allow=True, limit=3)
            self.assertIsNone(archive.can_record())
            archive.settings.update(recordChannels=[f"c{i}" for i in range(6)])
            archive.sync()
            self.assertEqual(sorted(started), ["c0", "c1", "c2"])

    def test_cheap_audio_conversion(self):
        cmd = " ".join(build_command(Path("/w"), [{"type": "video", "codec": "hevc"},
                                                 {"type": "audio", "codec": "aac_latm"}]))
        self.assertIn("-threads 1", cmd)
        self.assertIn("-c:a:0 aac -b:a:0 128k -aac_coder:a:0 fast", cmd)


class RecordingIdTest(unittest.TestCase):
    def test_round_trip_and_limits(self):
        rid = recording_id(CH, T0, T0 + 3600)
        self.assertEqual(parse_recording_id(rid), (CH, T0, T0 + 3600))
        self.assertLessEqual(len(rid), 64)
        for bad in (CH, f"b{CH}-{T0}-{T0}", f"b{CH}-{T0}-{T0 + 13 * 3600}", f"b../x-{T0}-{T0 + 10}", "bx-1-2"):
            self.assertIsNone(parse_recording_id(bad), bad)


if __name__ == "__main__":
    unittest.main()
