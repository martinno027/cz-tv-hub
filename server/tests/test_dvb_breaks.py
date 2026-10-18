"""Ad breaks (spec/ad-breaks.md): config, validation, storage, worker key scope and the app endpoint."""
import dataclasses
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cztvhub.api import StreamResolver
from cztvhub.config import AdsConfig, Config, ConfigError, DvbConfig, parse_config
from cztvhub.dvb import breaks as ads
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import Codecs, HlsManager
from cztvhub.dvb.retention import RetentionLoop
from cztvhub.server import Server, make_handler
from dvb_fixtures import CT1, NOW, REC_COMPLETED, REC_FAILED, REC_RECORDING, FakeClient
from test_dvb_hls import FakeProcess

KEY = "s3cret-" * 6
WORKER_KEY = "w0rker-" * 6
APP = {"Authorization": "Bearer " + KEY}
WORKER = {"Authorization": "Bearer " + WORKER_KEY}
REC_OTHER = "a9a8a7a6a5a4a3a2a1a0b9b8b7b6b5b4"
FIXTURES = Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "ads"


def breaks_body(*spans, analyzer="logo-v1"):
    return json.dumps({"analyzer": analyzer, "breaks": [
        {"start": s, "end": e, "confidence": 0.9} for s, e in spans]}).encode()


class TwoRecordingsClient(FakeClient):
    """The fixtures have one completed recording; the worker tests need a second one."""

    def dvr_entries(self, grid="grid"):
        entries = super().dvr_entries(grid)
        done = next((e for e in entries if e.uuid == REC_COMPLETED), None)
        if done is not None:
            entries.append(dataclasses.replace(done, uuid=REC_OTHER, start=done.start + 7200, stop=done.stop + 7200))
        return entries


class ConfigTest(unittest.TestCase):
    BASE = f"[dvb]\nenabled = true\nkey = {KEY}\n"

    def test_defaults_off(self):
        cfg = parse_config(self.BASE)
        self.assertFalse(cfg.ads.enabled)
        self.assertEqual(cfg.ads.breaks_dir, Path("/var/lib/cztvhub/breaks"))

    def test_section(self):
        cfg = parse_config(self.BASE + f"[ads]\nenabled = true\nchannels = Nova HD, Prima HD\nworker_key = {WORKER_KEY}\n"
                                       "worker_hosts = 192.168.1.10, 100.101.102.103\nbreaks_dir = /tmp/b\n")
        self.assertEqual(cfg.ads.channels, ["Nova HD", "Prima HD"])
        self.assertEqual(cfg.ads.worker_hosts, ["192.168.1.10", "100.101.102.103"])
        self.assertEqual(cfg.ads.breaks_dir, Path("/tmp/b"))

    def test_invalid(self):
        for extra in ("[ads]\nenabled = true\nworker_key = short\n",
                      f"[ads]\nenabled = true\nworker_key = {KEY}\n",
                      "[ads]\nenabled = maybe\n"):
            with self.assertRaises(ConfigError, msg=extra):
                parse_config(self.BASE + extra)
        with self.assertRaises(ConfigError):
            parse_config(f"[ads]\nenabled = true\nworker_key = {WORKER_KEY}\n")  # needs [dvb]


class ValidateAndStoreTest(unittest.TestCase):
    def test_validate(self):
        analyzer, items = ads.validate({"analyzer": "logo-v1", "breaks": [
            {"start": 60, "end": 180, "confidence": 0.9}, {"start": 600, "end": 640}]})
        self.assertEqual(analyzer, "logo-v1")
        self.assertEqual(items[1], {"start": 600.0, "end": 640.0, "confidence": 1.0})
        self.assertEqual(ads.validate({"analyzer": "x", "breaks": []})[1], [])

    def test_validate_rejects(self):
        bad = [
            [], {"breaks": []}, {"analyzer": "a b", "breaks": []}, {"analyzer": "x", "breaks": {}},
            {"analyzer": "x", "breaks": [{"start": 0, "end": 10}]},           # shorter than 20 s
            {"analyzer": "x", "breaks": [{"start": 0, "end": 1300}]},         # longer than 20 min
            {"analyzer": "x", "breaks": [{"start": 100, "end": 200}, {"start": 150, "end": 300}]},  # overlap
            {"analyzer": "x", "breaks": [{"start": "1", "end": 100}]},
            {"analyzer": "x", "breaks": [{"start": True, "end": 100}]},
            {"analyzer": "x", "breaks": [{"start": 0, "end": 100, "confidence": 2}]},
            {"analyzer": "x", "breaks": [{"start": float("nan"), "end": 100}]},
            {"analyzer": "x", "breaks": [{"start": i * 100, "end": i * 100 + 30} for i in range(201)]},
        ]
        for payload in bad:
            with self.assertRaises(ads.InvalidBreaks, msg=payload):
                ads.validate(payload)

    def test_store_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ads.BreakStore(Path(tmp) / "breaks")
            self.assertIsNone(store.get(REC_COMPLETED))
            store.put(REC_COMPLETED, "logo-v1", [{"start": 1.0, "end": 30.0, "confidence": 1.0}])
            store.put(REC_FAILED, "logo-v1", [])
            self.assertEqual(store.get(REC_COMPLETED)["analyzer"], "logo-v1")
            self.assertEqual(store.prune([REC_COMPLETED]), [REC_FAILED])
            self.assertEqual(store.ids(), {REC_COMPLETED})
            self.assertEqual([p.name for p in (Path(tmp) / "breaks").iterdir()], [f"{REC_COMPLETED}.json"])
            with self.assertRaises(ValueError):
                store.put("../etc", "x", [])


class GatewayBreaksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=root / "hls"),
                     ads=AdsConfig(enabled=True, channels=["ČT1 HD"], worker_key=WORKER_KEY,
                                   breaks_dir=root / "breaks"))
        self.client = TwoRecordingsClient()
        self.hls = HlsManager(root / "hls", idle_seconds=30, log=lambda m: None)
        self.addCleanup(self.hls.shutdown)
        FakeProcess.instances = []
        self.gw = DvbGateway(cfg, client=self.client, hls=self.hls, now=lambda: NOW,
                             prober=lambda data: Codecs("hevc", "aac"),
                             popen=lambda cmd, **kw: FakeProcess(cmd, exit_code=0), playlist_timeout=2,
                             log=lambda m: None)
        patcher = mock.patch("cztvhub.dvb.hls.PROBE_SECONDS", 0.3)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, path, headers, method="GET", body=b"", query=None, client="192.168.1.10"):
        resp = self.gw.handle(path, query or {}, headers, method, body, client)
        if resp.content_type.startswith("application/json") and resp.body:
            return resp, json.loads(resp.body)
        return resp, resp.body

    def put(self, recording, body, headers=WORKER):
        return self.call(f"/dvb/recording/{recording}/breaks", headers, "PUT", body)[0].status

    def test_jobs_lists_completed_recordings_of_selected_channels_oldest_first(self):
        resp, body = self.call("/dvb/ads/jobs", WORKER, query={"analyzer": "logo-v1"})
        self.assertEqual(resp.status, 200)
        self.assertEqual([j["recordingId"] for j in body["jobs"]], [REC_COMPLETED, REC_OTHER])
        job = body["jobs"][0]
        self.assertEqual((job["channelId"], job["channelName"]), (CT1, "ČT1 HD"))
        self.assertEqual(job["playlist"], f"/dvb/recording/{REC_COMPLETED}.m3u8")
        self.assertTrue(job["start"].endswith("Z"))
        self.assertEqual(self.put(REC_COMPLETED, breaks_body((60, 180))), 204)
        _, body = self.call("/dvb/ads/jobs", WORKER, query={"analyzer": "logo-v1"})
        self.assertEqual([j["recordingId"] for j in body["jobs"]], [REC_OTHER])
        _, body = self.call("/dvb/ads/jobs", WORKER, query={"analyzer": "logo-v2"})  # newer analyzer redoes all
        self.assertEqual(len(body["jobs"]), 2)
        self.assertEqual(self.call("/dvb/ads/jobs", WORKER)[0].status, 400)
        self.gw.ads.channels = ["Nova HD"]
        self.assertEqual(self.call("/dvb/ads/jobs", WORKER, query={"analyzer": "logo-v1"})[1]["jobs"], [])

    def test_app_reads_breaks_pending_then_done(self):
        resp, body = self.call(f"/dvb/recording/{REC_COMPLETED}/breaks", APP)
        self.assertEqual((resp.status, body), (200, {"status": "pending", "breaks": []}))
        self.assertEqual(self.put(REC_COMPLETED, breaks_body((60, 180), (600, 640))), 204)
        _, body = self.call(f"/dvb/recording/{REC_COMPLETED}/breaks", APP)
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["breaks"][1], {"start": 600.0, "end": 640.0, "confidence": 0.9})
        _, epg = self.call("/dvb/epg", APP, query={"date": "2026-10-16"})  # 03:20 Prague belongs to the 16th
        flagged = {e["recording"]["id"] for e in epg["events"] if e["recording"] and e["recording"].get("breaks")}
        self.assertEqual(flagged, {REC_COMPLETED})

    def test_app_key_cannot_upload(self):
        self.assertEqual(self.put(REC_COMPLETED, breaks_body((60, 180)), headers=APP), 403)
        self.assertIsNone(self.gw.breaks.get(REC_COMPLETED))
        self.assertEqual(self.call("/dvb/ads/jobs", APP)[0].status, 404)  # not an app route

    def test_upload_validation_and_targets(self):
        self.assertEqual(self.put(REC_COMPLETED, b"not json"), 400)
        self.assertEqual(self.put(REC_COMPLETED, breaks_body((60, 65))), 400)
        self.assertEqual(self.put(REC_COMPLETED, b"x" * (70 * 1024)), 400)
        for other in (REC_RECORDING, REC_FAILED, "unknown"):  # not completed / not a selected channel
            self.assertEqual(self.put(other, breaks_body((60, 180))), 403, other)

    def test_worker_key_scope(self):
        for path in ("/dvb/status", "/dvb/channels", "/dvb/epg", f"/dvb/live/{CT1}.m3u8",
                     f"/dvb/recording/{REC_COMPLETED}/breaks", f"/dvb/icon/{CT1}"):
            resp, body = self.call(path, WORKER)
            self.assertEqual(resp.status, 403, path)
        self.assertEqual(self.call(f"/dvb/recording/{REC_RECORDING}.m3u8", WORKER)[0].status, 403)
        self.assertFalse(any(c[0] == "live" for c in self.client.calls if isinstance(c, tuple)))
        # Never as a cookie: that path exists only for Samsung AVPlay and the app key.
        self.assertEqual(self.call("/dvb/ads/jobs", {"Cookie": "cztvhub_key=" + WORKER_KEY})[0].status, 401)

    def test_worker_hosts(self):
        self.gw.ads.worker_hosts = ["192.168.1.10"]
        self.assertEqual(self.call("/dvb/ads/jobs", WORKER, query={"analyzer": "a"})[0].status, 200)
        self.assertEqual(self.call("/dvb/ads/jobs", WORKER, query={"analyzer": "a"}, client="192.168.1.99")[0].status, 401)

    def test_worker_reads_one_recording_at_a_time(self):
        resp, body = self.call(f"/dvb/recording/{REC_COMPLETED}.m3u8", WORKER)
        self.assertEqual(resp.status, 200, body)
        sid = self.hls.sessions()[0].sid
        self.assertEqual(self.call(f"/dvb/hls/{sid}/seg_00000.m4s", WORKER)[0].status, 200)
        self.assertEqual(self.call(f"/dvb/recording/{REC_COMPLETED}.m3u8", WORKER)[0].status, 200)  # same one again
        resp, body = self.call(f"/dvb/recording/{REC_OTHER}.m3u8", WORKER)
        self.assertEqual((resp.status, body["error"]), (409, "Pracovník už zpracovává jinou nahrávku"))
        # An app session's segments are not the worker's.
        self.call(f"/dvb/live/{CT1}.m3u8", APP)
        live_sid = next(s.sid for s in self.hls.sessions() if s.live)
        self.assertEqual(self.call(f"/dvb/hls/{live_sid}/seg_00000.m4s", WORKER)[0].status, 403)
        # Once the previous session is gone the next recording may start.
        self.hls.reap = None
        self.hls._remove(self.hls.get(sid))
        self.assertEqual(self.call(f"/dvb/recording/{REC_OTHER}.m3u8", WORKER)[0].status, 200)

    def test_off_hides_everything(self):
        self.gw.ads.enabled = False
        self.assertEqual(self.call(f"/dvb/recording/{REC_COMPLETED}/breaks", APP)[0].status, 404)
        self.assertEqual(self.call("/dvb/ads/jobs", WORKER)[0].status, 401)  # worker key unknown when off
        self.assertEqual(self.put(REC_COMPLETED, breaks_body((60, 180)), headers=APP), 405)

    def test_retention_prunes_breaks_of_deleted_recordings(self):
        self.gw.breaks.put(REC_COMPLETED, "logo-v1", [])
        self.gw.breaks.put("0123456789abcdef0123456789abcdef", "logo-v1", [])
        self.assertIsNotNone(self.gw.retention.after)
        self.gw.retention.after()
        self.assertEqual(self.gw.breaks.ids(), {REC_COMPLETED})

    def test_retention_loop_calls_after_hook(self):
        calls = []
        loop = RetentionLoop(self.client, 3, interval=0.01, log=lambda m: None, after=lambda: calls.append(1))
        with mock.patch("cztvhub.dvb.retention.enforce_retention", return_value=[]):
            loop._stop.wait = lambda delay, _n=iter([False, True]): next(_n)
            loop._run()
        self.assertEqual(calls, [1])

    def test_put_over_http(self):
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda ch: "https://x"), self.gw))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
        conn.request("PUT", f"/dvb/recording/{REC_COMPLETED}/breaks", body=breaks_body((60, 180)), headers=WORKER)
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 204)
        conn.request("GET", f"/dvb/recording/{REC_COMPLETED}/breaks", headers=APP)
        resp = conn.getresponse()
        self.assertEqual(json.loads(resp.read())["status"], "done")
        conn.close()


class SharedFixturesTest(unittest.TestCase):
    def test_gateway_output_shape_matches_app_fixtures(self):
        done = json.loads((FIXTURES / "breaks_done.json").read_text(encoding="utf-8"))
        cases = json.loads((FIXTURES / "skip_cases.json").read_text(encoding="utf-8"))
        self.assertEqual(done["body"]["status"], "done")
        # What the gateway would store for the clean breaks validates; the fixture's junk entries do not.
        analyzer, items = ads.validate({"analyzer": "logo-v1", "breaks": [
            b for b in cases["breaks"] if b["end"] - b["start"] >= ads.MIN_BREAK]})
        self.assertEqual(len(items), 2)  # the 15.5 s break is a deliberately short app-side case


if __name__ == "__main__":
    unittest.main()
