import http.client
import threading
import unittest

from cztvhub.api import StreamError, StreamResolver
from cztvhub.server import Server, make_handler


def start(fetch):
    server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=fetch)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


class ServerTest(unittest.TestCase):
    def setUp(self):
        def fetch(channel):
            if channel == "CH_2":
                raise StreamError("boom")
            return f"https://cdn.example/{channel}.m3u8"

        self.server = start(fetch)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def test_health(self):
        resp, body = get(self.server, "/health")
        self.assertEqual((resp.status, body), (200, b"ok\n"))

    def test_playlist_uses_request_host(self):
        resp, body = get(self.server, "/playlist.m3u")
        self.assertEqual(resp.status, 200)
        self.assertIn(f"http://127.0.0.1:{self.server.server_address[1]}/ct/CH_1.m3u8", body.decode())

    def test_redirects_to_fresh_url(self):
        resp, _ = get(self.server, "/ct/CH_1.m3u8")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "https://cdn.example/CH_1.m3u8")

    def test_upstream_failure_is_502(self):
        resp, _ = get(self.server, "/ct/CH_2.m3u8")
        self.assertEqual(resp.status, 502)

    def test_unknown_channel_is_404(self):
        resp, _ = get(self.server, "/ct/CH_999.m3u8")
        self.assertEqual(resp.status, 404)
