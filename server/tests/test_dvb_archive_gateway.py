"""The gateway on top of the continuous recording: guide states, programme playlists, live from the recording,
hidden channels, the ad-break worker (spec/dvb-gateway.md "Continuous recording")."""
import json
import tempfile
import unittest
from pathlib import Path

from cztvhub.config import AdsConfig, Config, DvbConfig
from cztvhub.dvb.archive import Archive, recording_id
from cztvhub.dvb.buffer import BufferStore
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.recorder import RecorderManager
from cztvhub.dvb.thumbs import Settings, Thumbnails
from dvb_fixtures import CT1, NOVA, NOW, REC_RECORDING, FakeClient

KEY = "s3cret-" * 6
WORKER_KEY = "w0rker-" * 6
AUTH = {"Authorization": "Bearer " + KEY}
WORKER = {"Authorization": "Bearer " + WORKER_KEY}
NEWS = (1792300000, 1792303600)  # Nova "Televizní noviny" in the EPG fixture, airing at NOW
RUN = "1792300000-ab12"


class HealthyRecorder:
    state = "nahrává"
    run = RUN

    def healthy(self, now=None, within=20.0):
        return True

    def stop(self, wait=0):
        pass

    def start(self):
        pass


class ArchiveGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.clock = {"now": NOW}
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=root / "hls", state_dir=root / "state",
                                   buffer_dir=root / "buffer"),
                     ads=AdsConfig(enabled=True, channels=["Nova HD"], worker_key=WORKER_KEY,
                                   breaks_dir=root / "breaks"))
        self.client = FakeClient()
        self.store = BufferStore(root / "buffer")
        self.settings = Settings(root / "state")
        self.settings.update(recordChannels=[NOVA])
        self.recorders = RecorderManager(self.store, lambda ch: None, log=lambda m: None,
                                         factory=lambda ch: HealthyRecorder())
        now = lambda: self.clock["now"]  # noqa: E731
        self.gw = None
        archive = Archive(self.store, self.recorders, self.settings, Thumbnails(self.client, self.settings), 3,
                          now=now, log=lambda m: None, offered=lambda: self.gw._offered_ids(),
                          is_programme=lambda c, s, e: self.gw._is_programme(c, s, e))
        self.gw = DvbGateway(cfg, client=self.client, now=now, log=lambda m: None, archive=archive)
        self.store.save_run(NOVA, RUN, b"not an mp4", {"audio": [{"label": "Čeština", "language": "ces"}]})
        work = root / "work"
        work.mkdir()
        t = NEWS[0] - 60.0
        n = 0
        while t < NOW:  # recorded from a minute before the news until now
            path = work / f"s{n}.m4s"
            path.write_bytes(bytes([n % 256]) * 100)
            self.store.add(NOVA, RUN, n, t, 2.5, path)
            t += 2.5
            n += 1
        archive.sync()

    def get(self, path, headers=AUTH, query=None, method="GET"):
        resp = self.gw.handle(path, query or {}, headers, method, b"", "192.168.1.10")
        body = json.loads(resp.body) if resp.content_type.startswith("application/json") and resp.body else resp.body
        return resp, body

    def test_guide_takes_recorded_channels_from_the_recording(self):
        resp, body = self.get("/dvb/epg", query={"date": "2026-10-18"})
        self.assertEqual(resp.status, 200)
        news = next(e for e in body["events"] if e["channel"] == NOVA and e["title"] == "Televizní noviny")
        self.assertEqual(news["recording"], {"id": recording_id(NOVA, *NEWS), "state": "recording"})
        # other channels keep TVHeadend's recordings
        ct1 = [e["recording"]["id"] for e in body["events"] if e["channel"] == CT1 and e["recording"]]
        self.assertIn(REC_RECORDING, ct1)

    def test_programme_playlist_grows_then_ends(self):
        rid = recording_id(NOVA, *NEWS)
        resp, body = self.get(f"/dvb/recording/{rid}.m3u8")
        self.assertEqual(resp.status, 200, body)
        text = body.decode()
        self.assertIn("#EXT-X-PLAYLIST-TYPE:EVENT", text)
        self.assertNotIn("#EXT-X-ENDLIST", text)
        uris = [line for line in text.splitlines() if line.startswith("/dvb/v/")]
        # recorded from a minute before; the playlist starts 30 s before the programme (segment 12 of 2.5 s)
        self.assertTrue(uris[0].endswith("/m/12.m4s"), uris[0])
        resp, data = self.get(uris[2], headers={**AUTH, "Range": "bytes=10-19"})
        self.assertEqual((resp.status, data), (206, bytes([14]) * 10))
        self.assertEqual(self.get(uris[2], headers={})[0].status, 401)
        # an arbitrary range that is not an EPG programme gets nothing
        self.assertEqual(self.get(f"/dvb/recording/{recording_id(NOVA, NEWS[0] + 1, NEWS[1])}.m3u8")[0].status, 404)
        # later: VOD with an end
        self.clock["now"] = NEWS[1] + 3600
        resp, body = self.get(f"/dvb/recording/{rid}.m3u8")
        self.assertEqual(resp.status, 200)
        self.assertIn(b"#EXT-X-PLAYLIST-TYPE:VOD", body)
        self.assertIn(b"#EXT-X-ENDLIST", body)

    def test_live_of_a_recorded_channel_comes_from_the_recording(self):
        resp, body = self.get(f"/dvb/live/{NOVA}.m3u8")
        self.assertEqual(resp.status, 200, body)
        self.assertIn(b"/dvb/v/", body)
        self.assertIn(b"#EXT-X-MEDIA-SEQUENCE:", body)
        self.assertNotIn(("live", NOVA), self.client.calls)  # no extra tuner

    def test_hidden_channel_loses_its_recording(self):
        self.gw.channels_changed(hidden=[NOVA])
        self.assertEqual(self.store.between(NOVA, 0, 10 ** 12), [])

    def test_worker_reads_ended_programmes_only(self):
        rid = recording_id(NOVA, *NEWS)
        self.assertEqual(self.get(f"/dvb/recording/{rid}.m3u8", headers=WORKER)[0].status, 403)  # still airing
        self.clock["now"] = NEWS[1] + 600
        resp, body = self.get(f"/dvb/recording/{rid}.m3u8", headers=WORKER)
        self.assertEqual(resp.status, 200, body)
        uri = next(line for line in body.decode().splitlines() if line.startswith("/dvb/v/"))
        self.assertEqual(self.get(uri, headers=WORKER)[0].status, 200)
        # the app's views are not the worker's
        app_body = self.get(f"/dvb/recording/{rid}.m3u8")[1].decode()
        app_uri = next(line for line in app_body.splitlines() if line.startswith("/dvb/v/"))
        self.assertEqual(self.get(app_uri, headers=WORKER)[0].status, 404)
        resp, body = self.get(f"/dvb/recording/{rid}/breaks", headers=WORKER, method="PUT")
        self.assertEqual(resp.status, 400)  # analysable, only the body is wrong


if __name__ == "__main__":
    unittest.main()
