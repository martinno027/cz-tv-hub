import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cztvhub.api import StreamResolver
from cztvhub.config import Config, DvbConfig
from cztvhub.dvb import tvheadend as tvh
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import Codecs, HlsManager
from cztvhub.server import Server, make_handler
from dvb_fixtures import CT1, NOW, REC_COMPLETED, REC_FAILED, REC_RECORDING, REC_SCHEDULED, FakeClient
from test_dvb_hls import FakeProcess

KEY = "s3cret-" * 6
AUTH = {"Authorization": "Bearer " + KEY}


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, retention_days=3, hls_dir=Path(self.tmp.name)))
        self.client = FakeClient()
        self.hls = HlsManager(Path(self.tmp.name), idle_seconds=30, log=lambda m: None)
        self.addCleanup(self.hls.shutdown)
        FakeProcess.instances = []
        self.gw = DvbGateway(cfg, client=self.client, hls=self.hls, now=lambda: NOW,
                             prober=lambda data: Codecs("hevc", "mp2"), popen=self.popen, playlist_timeout=2,
                             log=lambda m: None)
        self.exit_code = None
        patcher = mock.patch("cztvhub.dvb.hls.PROBE_SECONDS", 0.3)  # in-progress recordings never hit EOF
        patcher.start()
        self.addCleanup(patcher.stop)

    def popen(self, cmd, **kwargs):
        return FakeProcess(cmd, exit_code=self.exit_code)

    def get(self, path, query=None, headers=None):
        resp = self.gw.handle(path, query or {}, AUTH if headers is None else headers)
        payload = json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body
        return resp, payload

    def test_auth_required(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic " + KEY},
                        {"Authorization": KEY}):
            resp, body = self.get("/dvb/channels", headers=headers)
            self.assertEqual(resp.status, 401, headers)
            self.assertIn("error", body)
            self.assertIn("Bearer", resp.headers["WWW-Authenticate"])
        self.assertEqual(self.client.calls, [])

    def test_cookie_key_for_players_without_headers(self):
        resp, _ = self.get("/dvb/channels", headers={"Cookie": "other=1; cztvhub_key=" + KEY})
        self.assertEqual(resp.status, 200)
        for cookie in ("cztvhub_key=wrong", "key=" + KEY, "cztvhub_key", '"broken'):
            resp, _ = self.get("/dvb/channels", headers={"Cookie": cookie})
            self.assertEqual(resp.status, 401, cookie)

    def test_disabled(self):
        self.gw.dvb.enabled = False
        resp, body = self.get("/dvb/channels")
        self.assertEqual(resp.status, 404)

    def test_status(self):
        resp, body = self.get("/dvb/status")
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["retentionDays"], 3)
        self.assertEqual([t["inUse"] for t in body["tuners"]], [True, False])
        self.assertGreater(body["disk"]["totalBytes"], 0)
        self.assertTrue(body["tvheadendReachable"])
        self.client.unreachable = True
        resp, body = self.get("/dvb/status")
        self.assertEqual((resp.status, body["tuners"], body["tvheadendReachable"]), (200, [], False))

    def test_channels(self):
        resp, body = self.get("/dvb/channels")
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["channels"][0], {"id": CT1, "number": 1, "name": "ČT1 HD", "icon": f"/dvb/icon/{CT1}"})
        self.assertIsNone(body["channels"][2]["icon"])
        self.get("/dvb/channels")
        self.assertEqual(self.client.calls.count("channels"), 1)  # cached

    def test_tvheadend_down_is_502(self):
        self.client.unreachable = True
        resp, body = self.get("/dvb/channels")
        self.assertEqual((resp.status, body["error"]), (502, "TVHeadend není dostupný"))

    def test_epg(self):
        resp, body = self.get("/dvb/epg", {"date": "2026-10-18"})
        self.assertEqual(resp.status, 200)
        states = {e["recording"]["id"]: e["recording"]["state"] for e in body["events"] if e["recording"]}
        self.assertEqual(states, {REC_RECORDING: "recording", REC_SCHEDULED: "scheduled"})
        _, start, stop = self.client.calls[-1]
        self.assertEqual((start, stop), (1792292400, 1792378800))  # 05:00 CEST .. 05:00 next day

    def test_epg_default_today_and_range(self):
        resp, body = self.get("/dvb/epg")
        self.assertEqual(body["date"], "2026-10-18")
        self.assertEqual(self.get("/dvb/epg", {"date": "2026-10-15"})[0].status, 200)
        self.assertEqual(self.get("/dvb/epg", {"date": "2026-10-20"})[0].status, 200)
        for bad in ("2026-10-14", "2026-10-21", "18.10.2026"):
            resp, body = self.get("/dvb/epg", {"date": bad})
            self.assertEqual(resp.status, 400, bad)
            self.assertIn("error", body)

    def test_icon(self):
        resp, body = self.get(f"/dvb/icon/{CT1}")
        self.assertEqual((resp.status, body, resp.content_type), (200, b"PNG", "image/png"))
        self.assertEqual(self.get("/dvb/icon/abcdefabcdefabcdefabcdefabcdef12")[0].status, 404)

    def test_live_playlist_and_media(self):
        resp, body = self.get(f"/dvb/live/{CT1}.m3u8")
        self.assertEqual(resp.status, 200, body)
        self.assertEqual(resp.content_type, "application/vnd.apple.mpegurl")
        text = body.decode()
        session = self.hls.sessions()[0]
        self.assertIn(f'URI="/dvb/hls/{session.sid}/init.mp4"', text)
        self.assertIn(f"/dvb/hls/{session.sid}/seg_00000.m4s", text)
        self.assertIn("-tag:v", FakeProcess.instances[0].cmd)
        self.assertEqual(self.client.calls.count(("live", CT1)), 1)
        self.get(f"/dvb/live/{CT1}.m3u8")
        self.assertEqual(self.client.calls.count(("live", CT1)), 1)  # shared session

        resp, body = self.get(f"/dvb/hls/{session.sid}/seg_00000.m4s")
        self.assertEqual((resp.status, body, resp.content_type), (200, b"0123456789", "video/iso.segment"))
        resp, body = self.get(f"/dvb/hls/{session.sid}/seg_00000.m4s", headers={**AUTH, "Range": "bytes=2-4"})
        self.assertEqual((resp.status, body, resp.headers["Content-Range"]), (206, b"234", "bytes 2-4/10"))
        resp, _ = self.get(f"/dvb/hls/{session.sid}/seg_00000.m4s", headers={**AUTH, "Range": "bytes=50-"})
        self.assertEqual(resp.status, 416)
        self.assertEqual(self.get(f"/dvb/hls/{session.sid}/index.m3u8")[0].status, 404)
        self.assertEqual(self.get(f"/dvb/hls/{'0' * 32}/seg_00000.m4s")[0].status, 404)
        self.assertEqual(self.get(f"/dvb/hls/{session.sid}/seg_00000.m4s", headers={})[0].status, 401)

    def test_live_unknown_channel(self):
        self.assertEqual(self.get("/dvb/live/nope.m3u8")[0].status, 404)

    def test_live_no_tuner(self):
        self.client.live_error = tvh.TunerBusy("503")
        resp, body = self.get(f"/dvb/live/{CT1}.m3u8")
        self.assertEqual((resp.status, body), (503, {"error": "Tuner je obsazený"}))
        self.assertEqual(self.hls.sessions(), [])

    def test_live_no_data_with_busy_tuners(self):
        self.client.live_data = b""
        self.client.tuners = lambda: [tvh.Tuner("a", True)]
        self.assertEqual(self.get(f"/dvb/live/{CT1}.m3u8")[0].status, 503)
        self.client.tuners = lambda: [tvh.Tuner("a", False)]
        self.assertEqual(self.get(f"/dvb/live/{CT1}.m3u8")[0].status, 502)

    def test_busy_tuner_frees_idle_live_session(self):
        self.get(f"/dvb/live/{CT1}.m3u8")
        old = self.hls.sessions()[0]
        old.last_access -= 60  # its viewer left
        calls = {"n": 0}

        def open_live(uuid):
            calls["n"] += 1
            if not old.stopped:
                raise tvh.TunerBusy("503")
            return FakeClient.open_live(self.client, uuid)

        self.client.open_live = open_live
        resp, _ = self.get("/dvb/live/0b3e1d5a7c9f4e2a8b6d4c2e0f1a3b5c.m3u8")
        self.assertEqual(resp.status, 200)
        self.assertTrue(old.stopped)
        self.assertEqual(calls["n"], 2)

    def test_recording_completed_is_vod(self):
        self.exit_code = 0
        resp, body = self.get(f"/dvb/recording/{REC_COMPLETED}.m3u8")
        self.assertEqual(resp.status, 200, body)
        self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", body.decode())
        self.assertIn("#EXT-X-ENDLIST", body.decode())
        self.assertIn(("recording", REC_COMPLETED, 0), self.client.calls)
        self.assertNotIn("-tag:v hvc1 -hls_list_size 10", " ".join(FakeProcess.instances[0].cmd))
        self.assertIn("event", FakeProcess.instances[0].cmd)

    def test_recording_in_progress_is_event(self):
        resp, body = self.get(f"/dvb/recording/{REC_RECORDING}.m3u8")
        self.assertEqual(resp.status, 200, body)
        self.assertIn("#EXT-X-PLAYLIST-TYPE:EVENT", body.decode())

    def test_recording_not_playable(self):
        for uuid in (REC_FAILED, REC_SCHEDULED, "unknown"):
            resp, body = self.get(f"/dvb/recording/{uuid}.m3u8")
            self.assertEqual(resp.status, 404, uuid)
            self.assertEqual(body["error"], "Nahrávka není k dispozici")

    def test_no_download_routes(self):
        for path in (f"/dvb/dvrfile/{REC_COMPLETED}", f"/dvb/recording/{REC_COMPLETED}.ts",
                     f"/dvb/recording/{REC_COMPLETED}", "/dvb/download", "/dvb/"):
            self.assertEqual(self.get(path)[0].status, 404, path)


class ServerRoutingTest(unittest.TestCase):
    def serve(self, gateway):
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda ch: "https://x"), gateway))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def test_without_gateway_dvb_is_404_and_ct_unchanged(self):
        server = self.serve(None)
        resp, body = self.request(server, "/dvb/channels")
        self.assertEqual(resp.status, 404)
        self.assertEqual(json.loads(body)["error"], "DVB není zapnuté")
        self.assertEqual(self.request(server, "/ct/CH_1.m3u8")[0].status, 302)

    def test_gateway_through_http(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=Path(tmp.name)))
        gw = DvbGateway(cfg, client=FakeClient(), now=lambda: NOW, log=lambda m: None)
        server = self.serve(gw)
        self.assertEqual(self.request(server, "/dvb/status")[0].status, 401)
        resp, body = self.request(server, "/dvb/epg?date=2026-10-18", AUTH)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(json.loads(body)["date"], "2026-10-18")
