import tempfile
import threading
import time
import unittest
from pathlib import Path

from cztvhub.dvb import hls
from cztvhub.dvb.hls import Codecs, HlsManager, HlsSession
from dvb_fixtures import FakeResponse

PLAYLIST = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:EVENT
#EXT-X-MAP:URI="init.mp4"
#EXTINF:4.000000,
seg_00000.m4s
#EXT-X-ENDLIST
"""


class FakeStdin:
    def __init__(self):
        self.data, self.closed = bytearray(), False

    def write(self, data):
        if self.closed:
            raise BrokenPipeError
        self.data += data

    def close(self):
        self.closed = True


class FakeProcess:
    """Pretends to be ffmpeg: writes a playlist + media into the output dir of the command."""

    instances = []

    def __init__(self, cmd, stdin=None, stdout=None, exit_code=None, write=True):
        self.cmd, self.stdin, self.returncode = cmd, FakeStdin(), exit_code
        self.terminated = False
        out = Path(cmd[-1]).parent
        if write:
            (out / "init.mp4").write_bytes(b"ftyp....hvc1")
            (out / "seg_00000.m4s").write_bytes(b"0123456789")
            (out / "index.m3u8").write_text(PLAYLIST, encoding="utf-8")
        FakeProcess.instances.append(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class CommandTest(unittest.TestCase):
    def test_live_hevc_mp2(self):
        cmd = hls.build_ffmpeg_command(Path("/x"), Codecs("hevc", "mp2"), live=True)
        joined = " ".join(cmd)
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-i pipe:0", joined)
        self.assertIn("-c:v copy -tag:v hvc1", joined)
        self.assertIn("-c:a aac", joined)
        self.assertIn("-hls_segment_type fmp4", joined)
        self.assertIn("-hls_time 2", joined)  # short live segments: faster start
        self.assertIn("-hls_list_size 20", joined)
        self.assertIn("delete_segments", joined)
        self.assertNotIn("independent_segments", joined)  # open-GOP HEVC: segments depend on the previous one
        self.assertNotIn("-hls_playlist_type", joined)
        self.assertEqual(cmd[-1], "/x/index.m3u8")
        self.assertIn("/x/seg_%05d.m4s", cmd)
        self.assertNotIn("libx265", joined)  # video is never transcoded

    def test_recording_h264_aac(self):
        cmd = hls.build_ffmpeg_command(Path("/x"), Codecs("h264", "aac"), live=False, ffmpeg="/usr/bin/ffmpeg")
        joined = " ".join(cmd)
        self.assertEqual(cmd[0], "/usr/bin/ffmpeg")
        self.assertIn("-tag:v avc1", joined)
        self.assertIn("-c:a copy", joined)
        self.assertIn("-hls_time 4", joined)
        self.assertIn("-hls_list_size 0 -hls_playlist_type event", joined)
        self.assertNotIn("delete_segments", joined)

    def test_unknown_codec_has_no_tag(self):
        cmd = hls.build_ffmpeg_command(Path("/x"), Codecs(), live=True)
        self.assertNotIn("-tag:v", cmd)
        self.assertIn("aac", cmd)

    def test_parse_probe(self):
        out = '{"streams": [{"codec_name": "mp2", "codec_type": "audio"}, {"codec_name": "hevc", "codec_type": "video"}]}'
        self.assertEqual(hls.parse_probe(out), Codecs("hevc", "mp2"))
        self.assertEqual(hls.parse_probe("garbage"), Codecs())

    def test_rewrite_playlist(self):
        out = hls.rewrite_playlist(PLAYLIST, "/dvb/hls/abc/")
        self.assertIn('#EXT-X-MAP:URI="/dvb/hls/abc/init.mp4"', out)
        self.assertIn("\n/dvb/hls/abc/seg_00000.m4s\n", out)
        self.assertIn("#EXT-X-PLAYLIST-TYPE:EVENT", out)
        self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", hls.rewrite_playlist(PLAYLIST, "/p/", vod=True))


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        FakeProcess.instances = []

    def session(self, sid="a" * 32, live=True, source=None, popen=FakeProcess, prober=None):
        source = source if source is not None else FakeResponse(b"TS" * 5000, chunk=1000)
        return HlsSession(sid, "live:x", self.root / sid, live, lambda: source,
                          lambda d, c: hls.build_ffmpeg_command(d, c, live), prober or (lambda data: Codecs("hevc", "mp2")),
                          popen=popen)

    def test_start_pumps_source_into_ffmpeg(self):
        seen = {}
        s = self.session(prober=lambda data: seen.setdefault("probe", len(data)) and Codecs("hevc", "mp2"))
        s.ensure_started()
        proc = FakeProcess.instances[0]
        self.assertIn("hvc1", proc.cmd)
        deadline = time.time() + 2
        while not proc.stdin.closed and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(bytes(proc.stdin.data), b"TS" * 5000)  # probe bytes are not lost
        self.assertEqual(seen["probe"], 10000)
        self.assertIn("#EXTINF", s.wait_playlist(1))
        self.assertIsNotNone(s.media_path("seg_00000.m4s"))
        self.assertIsNone(s.media_path("../secret"))
        self.assertIsNone(s.media_path("index.m3u8"))
        s.stop()
        self.assertTrue(proc.terminated)
        self.assertFalse(s.directory.exists())

    def test_empty_source(self):
        s = self.session(source=FakeResponse(b""))
        with self.assertRaises(hls.EmptySource):
            s.ensure_started()
        with self.assertRaises(hls.EmptySource):  # later viewers get the same error
            s.ensure_started()
        self.assertFalse(s.directory.exists())
        self.assertEqual(FakeProcess.instances, [])

    def test_ffmpeg_dies_before_first_segment(self):
        s = self.session(popen=lambda cmd, **kw: FakeProcess(cmd, exit_code=1, write=False))
        s.ensure_started()
        with self.assertRaises(hls.StreamFailed):
            s.wait_playlist(1)

    def test_usable(self):
        live = self.session(popen=lambda cmd, **kw: FakeProcess(cmd, exit_code=0))
        live.ensure_started()
        self.assertFalse(live.usable())  # a live stream that ended must restart
        rec = self.session(sid="b" * 32, live=False, popen=lambda cmd, **kw: FakeProcess(cmd, exit_code=0))
        rec.ensure_started()
        self.assertTrue(rec.usable() and rec.finished())


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "hls"
        self.manager = HlsManager(self.root, idle_seconds=0.3, reap_interval=0.05, log=lambda m: None)
        self.addCleanup(self.manager.shutdown)
        self.opened = 0
        FakeProcess.instances = []

    def factory(self, sid, directory):
        self.opened += 1
        return HlsSession(sid, "live:x", directory, True, lambda: FakeResponse(b"TS" * 100),
                          lambda d, c: hls.build_ffmpeg_command(d, c, True), lambda data: Codecs("hevc", "mp2"),
                          popen=FakeProcess)

    def test_cleanup_on_start_only_touches_own_files(self):
        self.root.mkdir(parents=True)
        (self.root / ("f" * 32)).mkdir()
        (self.root / ("f" * 32) / "seg_00001.m4s").write_bytes(b"x")
        (self.root / "index.m3u8").write_text("x")
        (self.root / "keep.txt").write_text("x")
        (self.root / "other-dir").mkdir()
        self.manager.start()
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["keep.txt", "other-dir"])

    def test_shared_session_and_idle_removal(self):
        self.manager.start()
        a = self.manager.open("live:x", self.factory)
        b = self.manager.open("live:x", self.factory)
        self.assertIs(a, b)
        self.assertEqual(self.opened, 1)
        self.assertIs(self.manager.get(a.sid), a)
        self.assertTrue(a.directory.exists())
        time.sleep(0.6)
        self.assertIsNone(self.manager.get(a.sid))
        self.assertFalse(a.directory.exists())
        self.assertTrue(a.stopped)

    def test_touch_keeps_session_alive(self):
        self.manager.start()
        a = self.manager.open("live:x", self.factory)
        for _ in range(8):
            time.sleep(0.1)
            self.assertIs(self.manager.get(a.sid), a)

    def test_failed_start_is_not_kept(self):
        def failing(sid, directory):
            return HlsSession(sid, "live:y", directory, True, lambda: FakeResponse(b""),
                              lambda d, c: [], lambda data: Codecs(), popen=FakeProcess)

        with self.assertRaises(hls.EmptySource):
            self.manager.open("live:y", failing)
        self.assertEqual(self.manager.sessions(), [])

    def test_concurrent_open_starts_once(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.manager.open("live:x", self.factory)))
                   for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len({id(r) for r in results}), 1)
        self.assertEqual(len(FakeProcess.instances), 1)

    def test_stop_idle_live(self):
        a = self.manager.open("live:x", self.factory)
        self.assertEqual(self.manager.stop_idle_live(10), 0)
        time.sleep(0.15)
        self.assertEqual(self.manager.stop_idle_live(0.1), 1)
        self.assertTrue(a.stopped)
