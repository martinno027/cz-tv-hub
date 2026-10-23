"""End to end with real ffmpeg: a TS source with two audio tracks → continuous recorder → programme playlists with
one rendition per track, live window and a thumbnail (spec/dvb-gateway.md "Continuous recording").
Runs only when ffmpeg/ffprobe are on PATH."""
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cztvhub.dvb.archive import Archive, ArchiveError, recording_id
from cztvhub.dvb.buffer import BufferStore
from cztvhub.dvb.recorder import ChannelRecorder, RecorderManager
from cztvhub.dvb.thumbs import Settings, Thumbnails

CH = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


class Source:
    """ffmpeg generating a live TS like TVHeadend's pass profile: H.264 + MPEG audio (ces) + AC-3 (eng)."""

    def __init__(self, seconds: int):
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
             "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
             "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000", "-t", str(seconds),
             "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
             "-g", "25", "-c:a:0", "mp2", "-c:a:1", "ac3",
             "-metadata:s:a:0", "language=ces", "-metadata:s:a:1", "language=eng", "-f", "mpegts", "pipe:1"],
            stdout=subprocess.PIPE)

    def read(self, size):
        return self.proc.stdout.read1(size)

    def close(self):
        self.proc.kill()
        self.proc.wait()


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not on PATH")
class RecordingEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = BufferStore(self.root / "buffer")
        self.logs = []
        self.sources = []

    def open_source(self):
        src = Source(30)
        self.sources.append(src)
        return src

    def probe(self, path):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=codec_type,codec_name:stream_tags=language", "-of", "json", str(path)],
                             capture_output=True, text=True)
        return [(s["codec_type"], s["codec_name"], s.get("tags", {}).get("language"))
                for s in json.loads(out.stdout or "{}").get("streams", [])]

    def test_record_then_play_every_track(self):
        rec = ChannelRecorder(CH, self.store, self.open_source, retry_seconds=60, log=self.logs.append)
        manager = RecorderManager(self.store, lambda ch: None, log=self.logs.append, factory=lambda ch: rec)
        settings = Settings(self.root / "state")
        settings.update(recordChannels=[CH])
        thumbs = Thumbnails(None, settings)
        clock = {"now": None}
        archive = Archive(self.store, manager, settings, thumbs, 3,
                          now=lambda: clock["now"] if clock["now"] is not None else time.time(),
                          log=self.logs.append, offered=lambda: {CH})
        archive.sync()
        self.addCleanup(manager.stop_all)
        deadline = time.time() + 40
        while time.time() < deadline and len(self.store.between(CH, 0, 1e12)) < 5:
            time.sleep(0.5)
        segments = self.store.between(CH, 0, 1e12)
        self.assertGreaterEqual(len(segments), 5, self.logs)
        meta = self.store.run_meta(CH, segments[0].run)
        self.assertEqual([(a["codec"], a["language"], a["label"]) for a in meta["audio"]],
                         [("mp2", "ces", "Čeština"), ("ac3", "eng", "Angličtina (Dolby)")])

        # live from the recording, while it records
        self.assertTrue(archive.live_ready(CH))
        live, _ = archive.live(CH)
        self.assertIn(b"#EXT-X-MEDIA:TYPE=AUDIO", live)

        # an ended programme over the recorded span: master with video + two audio renditions
        start, end = int(segments[0].start) + 1, int(segments[-1].end) - 1
        clock["now"] = end + 120
        rid = recording_id(CH, start, end)
        master, _ = archive.programme(rid)
        text = master.decode()
        self.assertIn('NAME="Čeština",LANGUAGE="cs",DEFAULT=YES', text)
        self.assertIn('NAME="Angličtina (Dolby)",LANGUAGE="en",DEFAULT=NO', text)
        vid = re.search(r"/dvb/v/([0-9a-f]{32})/v\.m3u8", text).group(1)
        for role, expect in (("v", [("video", "h264", None)]), ("a0", [("audio", "aac", "ces")]),
                             ("a1", [("audio", "ac3", "eng")])):
            playlist, _ = archive.view_file(f"{vid}/{role}.m3u8")
            pl = playlist.decode()
            self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", pl)
            self.assertIn("#EXT-X-ENDLIST", pl)
            self.assertIn("#EXT-X-PROGRAM-DATE-TIME:", pl)
            uris = [line for line in pl.splitlines() if line.startswith("/dvb/v/")]
            init_uri = re.search(r'URI="(/dvb/v/[^"]+)"', pl).group(1)
            data, _ = archive.view_file(init_uri[len("/dvb/v/"):])
            for uri in uris[:3]:
                chunk, _ = archive.view_file(uri[len("/dvb/v/"):])
                data += chunk
            out = self.root / f"{role}.mp4"
            out.write_bytes(data)
            streams = self.probe(out)
            self.assertEqual([(t, c) for t, c, _ in streams], [(t, c) for t, c, _ in expect], role)
            if expect[0][2]:
                self.assertEqual(streams[0][2], expect[0][2])
            dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"],
                                 capture_output=True, text=True)
            self.assertEqual(dec.stderr.strip(), "", role)

        # only through a view, only inside the programme
        with self.assertRaises(ArchiveError):
            archive.view_file(f"{'0' * 32}/v.m3u8")
        far = segments[-1].g + 1000
        with self.assertRaises(ArchiveError):
            archive.view_file(f"{vid}/v/{far}.m4s")
        with self.assertRaises(ArchiveError):
            archive.programme(recording_id(CH, start - 86400, start - 80000))  # nothing recorded then

        jpeg = archive.thumbnail(rid)
        self.assertTrue(jpeg and jpeg.startswith(b"\xff\xd8"), self.logs)

        # switching recording off deletes the channel's recording at once
        clock["now"] = None
        archive.set_record([CH], False)
        self.assertEqual(self.store.between(CH, 0, 1e12), [])


if __name__ == "__main__":
    unittest.main()
