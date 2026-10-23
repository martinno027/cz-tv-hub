import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cztvhub.config import Config, DvbConfig
from cztvhub.dvb import thumbs
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from cztvhub.dvb.tvheadend import DvrEntry
from dvb_fixtures import CT1, NOW, REC_COMPLETED, REC_FAILED, REC_RECORDING, REC_SCHEDULED, FakeClient, FakeResponse

KEY = "thumb-key-" * 4
AUTH = {"Authorization": "Bearer " + KEY}
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class FakeRun:
    def __init__(self, stdout=JPEG, returncode=0):
        self.calls, self.stdout, self.returncode = [], stdout, returncode

    def __call__(self, cmd, input=b"", capture_output=True, timeout=None):
        self.calls.append((cmd, len(input)))
        return SimpleNamespace(stdout=self.stdout, returncode=self.returncode)


def any_offset(client):
    """The fake serves 2 MB of TS at any offset (a real recording is that long)."""
    def open_recording(uuid, offset=0):
        client.calls.append(("recording", uuid, offset))
        return FakeResponse(b"\x47" * (2 * 1024 * 1024 + 10), 206 if offset else 200)
    client.open_recording = open_recording


def entry(uuid="r1", start=0, stop=3600, size=360 * 1024 * 1024, status="completed"):
    return DvrEntry(uuid, CT1, "t", "", "", [], start, stop, status, filesize=size)


class CommandTest(unittest.TestCase):
    def test_pipes_only_one_small_frame(self):
        cmd = thumbs.ffmpeg_command("/usr/bin/ffmpeg")
        self.assertEqual(cmd[0], "/usr/bin/ffmpeg")
        self.assertEqual(cmd[cmd.index("-i") + 1], "pipe:0")
        self.assertEqual(cmd[-1], "pipe:1")
        self.assertIn("-frames:v 1", " ".join(cmd))
        self.assertIn("min(480,iw)", " ".join(cmd))
        # no file outputs of any kind (spec/dvb-gateway.md "Thumbnails")
        outputs = [a for a in cmd[1:] if "/" in a or a.endswith((".jpg", ".png", ".ts", ".m3u8"))]
        self.assertEqual(outputs, [])
        self.assertNotIn("segment", cmd)

    def test_frame_offset(self):
        # finished 1 h recording, 100 kB/s: 300 s in (cap), final
        self.assertEqual(thumbs.frame_offset(360_000_000, 3600), (30_000_000, True))
        # 10 min recorded so far: 20 % = 120 s in, not final yet
        self.assertEqual(thumbs.frame_offset(60_000_000, 600), (12_000_000, False))
        # tiny file: read from the start, never past the end
        self.assertEqual(thumbs.frame_offset(1_000_000, 60), (0, False))
        offset, _ = thumbs.frame_offset(3_000_000, 3600)
        self.assertLessEqual(offset + thumbs.READ_BYTES, 3_000_000)


class ThumbnailsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = FakeClient()
        any_offset(self.client)
        self.run = FakeRun()
        self.clock = [100.0]
        self.settings = thumbs.Settings(Path(self.tmp.name))
        self.t = thumbs.Thumbnails(self.client, self.settings, run=self.run, now=lambda: 1800,
                                   clock=lambda: self.clock[0], log=lambda m: None)

    def test_settings_default_on_and_persist(self):
        self.assertTrue(self.settings.thumbnails)
        self.t.set_enabled(False)
        self.assertFalse(thumbs.Settings(Path(self.tmp.name)).thumbnails)
        self.assertEqual(json.loads((Path(self.tmp.name) / "settings.json").read_text()), {"thumbnails": False})

    def test_finished_recording_once(self):
        self.assertEqual(self.t.get(entry(), recording=False), JPEG)
        self.assertEqual(self.t.get(entry(), recording=False), JPEG)
        self.assertEqual(len(self.run.calls), 1)  # cached for good
        _, uuid, offset = self.client.calls[0]
        self.assertEqual(offset, 30 * 1024 * 1024)  # 300 s of 3600 s
        self.assertEqual(self.run.calls[0][1], thumbs.READ_BYTES)  # never more than 2 MB read

    def test_growing_recording_refreshed_until_final(self):
        growing = entry(stop=7200, size=180 * 1024 * 1024)  # 30 min recorded (now=1800): 20 % = 360 s → 300 s cap
        self.t.get(growing, recording=True)
        self.t.get(growing, recording=True)
        self.assertEqual(len(self.run.calls), 1)  # final already
        early = entry(uuid="r2", stop=7200, size=180 * 1024 * 1024)
        t2 = thumbs.Thumbnails(self.client, self.settings, run=self.run, now=lambda: 600,
                               clock=lambda: self.clock[0], log=lambda m: None)
        t2.get(early, recording=True)
        t2.get(early, recording=True)
        self.assertEqual(len(self.run.calls), 2)  # within 60 s: cached
        self.clock[0] += 61
        t2.get(early, recording=True)
        self.assertEqual(len(self.run.calls), 3)  # refreshed

    def test_failures(self):
        self.assertIsNone(self.t.get(entry(size=0), recording=False))
        self.t.run = FakeRun(stdout=b"not a jpeg")
        self.assertIsNone(self.t.get(entry(), recording=False))
        self.t.run = FakeRun(returncode=1)
        self.assertIsNone(self.t.get(entry(), recording=False))
        self.client.recording_data = b"x"
        self.t.client.open_recording = lambda uuid, offset=0: FakeResponse(b"x" * 10, 200)  # Range ignored
        self.t.run = FakeRun()
        self.assertIsNone(self.t.get(entry(), recording=False))

    def test_cache_bounded_pruned_cleared(self):
        self.assertEqual(thumbs.CACHE_SIZE, 600)  # spec/dvb-gateway.md: room for several TVs prefetching
        for i in range(thumbs.CACHE_SIZE + 5):
            self.t.get(entry(uuid=f"r{i}"), recording=False)
        self.assertEqual(len(self.t._cache), thumbs.CACHE_SIZE)
        self.assertNotIn("r4", self.t._cache)  # least recently used go first
        self.assertIn("r5", self.t._cache)
        self.t.prune(["r200", "r201"])
        self.assertEqual(sorted(self.t._cache), ["r200", "r201"])
        self.t.set_enabled(False)
        self.assertEqual(len(self.t._cache), 0)

    def test_busy_slots_raise_instead_of_no_frame(self):
        self.t.get(entry(uuid="hit"), recording=False)
        self.t.run.calls.clear()
        for _ in range(thumbs.MAX_JOBS):
            self.t._jobs.acquire()
        self.addCleanup(lambda: [self.t._jobs.release() for _ in range(thumbs.MAX_JOBS)])
        with mock.patch.object(thumbs, "TIMEOUT", 0.01):
            with self.assertRaises(thumbs.ThumbsBusy):
                self.t.get(entry(uuid="new"), recording=False)
            with self.assertRaises(thumbs.ThumbsBusy):
                self.t.get_from("rid", True, lambda: JPEG)
            self.assertEqual(self.t.get(entry(uuid="hit"), recording=False), JPEG)  # cache hits need no slot
        self.assertEqual(self.t.run.calls, [])
        self.assertNotIn("new", self.t._cache)


class GatewayThumbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=root / "hls", state_dir=root / "state"))
        self.client = FakeClient()
        any_offset(self.client)
        hls = HlsManager(root / "hls", idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(cfg, client=self.client, hls=hls, now=lambda: NOW, log=lambda m: None)
        self.gw.thumbs.run = FakeRun()

    def get(self, uuid, headers=AUTH):
        return self.gw.handle(f"/dvb/recording/{uuid}/thumb.jpg", {}, headers)

    def test_completed_and_recording(self):
        for uuid in (REC_COMPLETED, REC_RECORDING):
            resp = self.get(uuid)
            self.assertEqual((resp.status, resp.content_type, resp.body), (200, "image/jpeg", JPEG), uuid)
            self.assertEqual(resp.headers["Cache-Control"], "private, no-store")

    def test_not_available(self):
        self.assertEqual(self.get(REC_COMPLETED, headers={}).status, 401)
        for uuid in (REC_SCHEDULED, REC_FAILED, "unknown"):
            self.assertEqual(self.get(uuid).status, 404, uuid)
        self.gw.thumbs.set_enabled(False)
        self.assertEqual(self.get(REC_COMPLETED).status, 404)
        status = json.loads(self.gw.handle("/dvb/status", {}, AUTH).body)
        self.assertFalse(status["thumbnails"])

    def test_busy_is_503_with_retry_after_then_served(self):
        for _ in range(thumbs.MAX_JOBS):
            self.gw.thumbs._jobs.acquire()
        with mock.patch.object(thumbs, "TIMEOUT", 0.01):
            resp = self.get(REC_COMPLETED)
        self.assertEqual(resp.status, 503)
        self.assertEqual(resp.headers["Retry-After"], "5")
        for _ in range(thumbs.MAX_JOBS):
            self.gw.thumbs._jobs.release()
        self.assertEqual(self.get(REC_COMPLETED).status, 200)

    def test_gone_recording_is_dropped_from_the_cache(self):
        self.assertEqual(self.get(REC_COMPLETED).status, 200)
        self.client.removed.append(REC_COMPLETED)
        self.assertEqual(self.get(REC_COMPLETED).status, 404)
        self.assertNotIn(REC_COMPLETED, self.gw.thumbs._cache)

    def test_retention_prunes(self):
        self.get(REC_COMPLETED)
        self.client.removed.append(REC_COMPLETED)
        self.gw._after_retention()
        self.assertNotIn(REC_COMPLETED, self.gw.thumbs._cache)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not on PATH")
class RealFfmpegTest(unittest.TestCase):
    def test_synthetic_frame(self):
        ts = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                             "testsrc=size=1280x720:rate=25", "-t", "3", "-c:v", "libx264", "-g", "25",
                             "-f", "mpegts", "pipe:1"], capture_output=True, check=True).stdout
        out = subprocess.run(thumbs.ffmpeg_command(), input=ts, capture_output=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith(thumbs.JPEG_SOI))
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width", "-of", "csv=p=0",
                                "pipe:0"], input=out.stdout, capture_output=True)
        self.assertEqual(probe.stdout.strip(), b"480")


if __name__ == "__main__":
    unittest.main()
