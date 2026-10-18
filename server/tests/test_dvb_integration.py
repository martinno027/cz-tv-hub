"""End to end: fake TVHeadend (real ffmpeg test streams) -> gateway -> HLS. Skipped without ffmpeg."""
import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cztvhub.api import StreamResolver
from cztvhub.config import Config, DvbConfig, TvheadendConfig
from cztvhub.dvb.fake import FakeTvheadend
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.server import Server, make_handler

KEY = "integration-key-" * 3
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not on PATH")
class DvbEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.fake = FakeTvheadend(root / "fake", port=0, user="tvh", password="pw", clip_seconds=8,
                                 log=lambda m: None)
        cls.fake.start()
        cls.hls_dir = root / "hls"
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=cls.hls_dir, idle_seconds=2),
                     tvheadend=TvheadendConfig(cls.fake.url, "tvh", "pw"))
        cls.gateway = DvbGateway(cfg, log=lambda m: None)
        cls.gateway.start()
        cls.server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda ch: "x"), cls.gateway))
        cls.server.RequestHandlerClass.log_message = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.gateway.stop()
        cls.fake.stop()
        cls.tmp.cleanup()

    def get(self, path, timeout=40):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=timeout)
        conn.request("GET", path, headers={"Authorization": "Bearer " + KEY})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200, f"{path}: {body[:200]!r}")
        return body

    def assert_fmp4_hevc(self, playlist: str):
        self.assertTrue(playlist.startswith("#EXTM3U"))
        init = playlist.split('#EXT-X-MAP:URI="', 1)[1].split('"', 1)[0]
        segment = [line for line in playlist.splitlines() if line and not line.startswith("#")][0]
        init_data, segment_data = self.get(init), self.get(segment)
        self.assertIn(b"ftyp", init_data[:16])
        self.assertIn(b"hvc1", init_data)
        sample = Path(self.tmp.name) / "sample.mp4"
        sample.write_bytes(init_data + segment_data)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "json",
                                str(sample)], capture_output=True, text=True, check=True)
        codecs = {s["codec_name"] for s in json.loads(probe.stdout)["streams"]}
        self.assertEqual(codecs, {"hevc", "aac"})

    def test_live_and_recording(self):
        channels = json.loads(self.get("/dvb/channels"))["channels"]
        self.assertEqual(len(channels), 4)

        live = self.get(f"/dvb/live/{channels[0]['id']}.m3u8").decode()
        self.assertNotIn("#EXT-X-PLAYLIST-TYPE", live)
        self.assert_fmp4_hevc(live)

        events = json.loads(self.get("/dvb/epg"))["events"]
        done = [e for e in events if e["recording"] and e["recording"]["state"] == "completed"]
        if not done:  # shortly after 05:00 today's day may have no finished recording yet
            from datetime import date, timedelta
            yesterday = (date.fromisoformat(json.loads(self.get("/dvb/epg"))["date"]) - timedelta(days=1))
            events = json.loads(self.get(f"/dvb/epg?date={yesterday}"))["events"]
            done = [e for e in events if e["recording"] and e["recording"]["state"] == "completed"]
        recording = self.get(f"/dvb/recording/{done[-1]['recording']['id']}.m3u8").decode()
        self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", recording)
        self.assertIn("#EXT-X-ENDLIST", recording)
        self.assert_fmp4_hevc(recording)

        self.assertTrue(any(self.hls_dir.iterdir()))
        deadline = time.time() + 15
        while any(self.hls_dir.iterdir()) and time.time() < deadline:
            time.sleep(0.25)
        self.assertEqual(list(self.hls_dir.iterdir()), [], "temporary HLS must be deleted after idle")
        self.assertEqual(self.gateway.hls.sessions(), [])
