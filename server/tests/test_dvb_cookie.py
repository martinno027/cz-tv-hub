"""The ``cztvhub_key`` cookie never changes anything (spec/web.md "Security", CSRF).

With the web app the cookie becomes a browser-wide credential for /dvb/*: every request that changes state needs the
key (or the worker key) as an ``Authorization`` header, which a foreign page cannot send without a CORS preflight
this server never grants. One test per state-changing route, and a sweep over every route shape.
"""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cztvhub.api import StreamResolver
from cztvhub.config import AdsConfig, Config, DvbConfig
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.server import Server, make_handler
from dvb_fixtures import NOW, REC_COMPLETED, FakeClient

KEY = "s3cret-" * 6
WORKER_KEY = "w0rker-" * 6
AUTH = {"Authorization": "Bearer " + KEY}
COOKIE = {"Cookie": "cztvhub_key=" + KEY}
SETTINGS = {"v": 1, "channelPrefs": {"order": ["ct:CH_2"], "hidden": [], "sourcesOff": []}, "thumbnails": False}
BREAKS = json.dumps({"analyzer": "test", "breaks": []}).encode()


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class CookieChangesNothingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.root / "hls", state_dir=self.root / "state",
                                   buffer_dir=self.root / "buffer"),
                     ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=self.root / "breaks"))
        self.gw = DvbGateway(cfg, client=FakeClient(), now=lambda: NOW, log=lambda m: None)
        self.ct_calls = []
        self.gw.ct_web.fetch = lambda url, body=None: self.ct_calls.append(url) or (200, b"{}")

    def call(self, method, path, body=b"", headers=COOKIE, token=None):
        headers = dict(headers)
        if token:
            headers["X-Cztvhub-Profile"] = token
        return self.gw.handle(path, {}, headers, method, body, "192.168.1.20")

    def create_profile(self):
        body = json.dumps({"name": "Jana", "password": "1234", "settings": SETTINGS}).encode()
        resp = self.call("POST", "/dvb/profiles", body, AUTH)
        self.assertEqual(resp.status, 201)
        return json.loads(resp.body)["token"]

    def test_profile_create(self):
        before = snapshot(self.root)
        body = json.dumps({"name": "Jana", "password": "1234", "settings": SETTINGS}).encode()
        self.assertEqual(self.call("POST", "/dvb/profiles", body).status, 403)
        self.assertEqual(self.gw.profiles.overview(), [])
        self.assertEqual(snapshot(self.root), before)

    def test_profile_login(self):
        self.create_profile()
        before = snapshot(self.root)
        body = json.dumps({"name": "Jana", "password": "1234"}).encode()
        resp = self.call("POST", "/dvb/profiles/login", body)
        self.assertEqual(resp.status, 403)
        self.assertNotIn(b"token", resp.body)
        self.assertEqual(snapshot(self.root), before)  # no new session

    def test_profile_put(self):
        token = self.create_profile()
        before = snapshot(self.root)
        body = json.dumps({"settings": {**SETTINGS, "thumbnails": True}}).encode()
        self.assertEqual(self.call("PUT", "/dvb/profile", body, token=token).status, 403)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(self.call("PUT", "/dvb/profile", body, AUTH, token).status, 200)  # the header works
        self.assertNotEqual(snapshot(self.root), before)

    def test_profile_logout(self):
        token = self.create_profile()
        self.assertEqual(self.call("POST", "/dvb/profile/logout", token=token).status, 403)
        self.assertEqual(self.call("GET", "/dvb/profile", headers=AUTH, token=token).status, 200)  # still in

    def test_breaks_put(self):
        before = snapshot(self.root)
        resp = self.call("PUT", f"/dvb/recording/{REC_COMPLETED}/breaks", BREAKS)
        self.assertEqual(resp.status, 403)
        self.assertEqual(snapshot(self.root), before)

    def test_sweep_every_route_shape(self):
        token = self.create_profile()
        before = snapshot(self.root)
        paths = ["/dvb/status", "/dvb/channels", "/dvb/epg", "/dvb/defaults", "/dvb/details", "/dvb/archive",
                 "/dvb/archive/search", f"/dvb/live/{REC_COMPLETED}.m3u8", f"/dvb/recording/{REC_COMPLETED}.m3u8",
                 f"/dvb/recording/{REC_COMPLETED}/breaks", f"/dvb/recording/{REC_COMPLETED}/thumb.jpg",
                 f"/dvb/timeline/{REC_COMPLETED}", "/dvb/ads/jobs", "/dvb/v/x/index.m3u8", "/dvb/hls/" + "0" * 32 +
                 "/index.m3u8", f"/dvb/icon/{REC_COMPLETED}", "/dvb/ct/live/CH_1", "/dvb/ct/vod/226411010101016",
                 "/dvb/ct/schedule", "/dvb/profiles", "/dvb/profiles/login", "/dvb/profile", "/dvb/profile/logout",
                 "/dvb/watch", "/dvb/settings", "/dvb/admin"]
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            for path in paths:
                status = self.call(method, path, b"{}", token=token).status
                self.assertIn(status, (403, 405), (method, path))
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(self.ct_calls, [])


class CookieOverHttpTest(unittest.TestCase):
    """Through the real handler: methods the handler has no do_* for never reach the gateway."""

    def test_delete_and_patch(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=root / "hls", state_dir=root / "state"))
        gw = DvbGateway(cfg, client=FakeClient(), now=lambda: NOW, log=lambda m: None)
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda ch: "https://x"), gw))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for method in ("DELETE", "PATCH", "OPTIONS"):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request(method, "/dvb/profile", headers=COOKIE)
            resp = conn.getresponse()
            resp.read()
            conn.close()
            self.assertEqual(resp.status, 501, method)
            self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))  # no CORS preflight granted


if __name__ == "__main__":
    unittest.main()
