"""HTTPS for the installed web app (spec/dvb-gateway.md "HTTPS for the installed web app", T47): tls.py + server.py.

The certificates are made here with ``openssl`` into a temp dir (self-signed, for ``localhost``, test-only). None is
committed: tools/check_public.py refuses any private key in the public repo, also a test one. Without ``openssl``
these tests are skipped with that reason. Only ephemeral ports on 127.0.0.1.
"""
import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path

from cztvhub import server as http_server
from cztvhub import tls
from cztvhub.api import StreamResolver
from cztvhub.config import Config, DvbConfig
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.web import WebSite
from dvb_fixtures import NOW, FakeClient

KEY = "s3cret-" * 6
OPENSSL = shutil.which("openssl")


def make_pair(directory: Path, name: str) -> tuple[Path, Path]:
    """A self-signed certificate + key for localhost / 127.0.0.1 (the subject's O names the pair)."""
    cert, key = directory / f"{name}.crt", directory / f"{name}.key"
    subprocess.run([OPENSSL, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", f"/CN=localhost/O={name}",
                    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                   check=True, capture_output=True)
    return cert, key


def install(pair: tuple[Path, Path], tls_dir: Path) -> None:
    """As cert.sh: write next to the target, then rename over it (a new inode, a new mtime)."""
    tls_dir.mkdir(parents=True, exist_ok=True)
    for src, name in zip(pair, (tls.CERT_FILE, tls.KEY_FILE)):
        tmp = tls_dir / (name + ".new")
        shutil.copyfile(src, tmp)
        os.replace(tmp, tls_dir / name)


def peer_subject(port: int, cafile: Path) -> str:
    """The O of the certificate the server presents (verified against ``cafile``)."""
    context = ssl.create_default_context(cafile=str(cafile))
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname="localhost") as conn:
            subject = dict(item for rdn in conn.getpeercert()["subject"] for item in rdn)
            return subject["organizationName"]


@unittest.skipUnless(OPENSSL, "openssl is not on PATH: no way to make a test certificate with the standard library")
class HttpsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.certs_tmp = tempfile.TemporaryDirectory()
        certs = Path(cls.certs_tmp.name)
        cls.a = make_pair(certs, "pair-a")
        cls.b = make_pair(certs, "pair-b")

    @classmethod
    def tearDownClass(cls):
        cls.certs_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tls_dir = self.root / "tls"
        self.logs = []
        web_dir = self.root / "web"
        web_dir.mkdir()
        (web_dir / "index.html").write_text("<!doctype html><title>Televize</title>", encoding="utf-8")
        (web_dir / "manifest.webmanifest").write_text('{"start_url": "/web/"}', encoding="utf-8")
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.root / "hls", state_dir=self.root / "state",
                                   web_dir=web_dir))
        self.gateway = DvbGateway(cfg, client=FakeClient(), now=lambda: NOW, log=lambda line: None)
        self.web = WebSite.from_config(cfg, None, log=lambda line: None)
        self.handler = http_server.make_handler(StreamResolver(fetch=lambda c: "https://cdn.example.invalid/x"),
                                                self.gateway, None, self.web)

    def listener(self, retry=0.05):
        listener = http_server.https_listener("127.0.0.1", 0, self.tls_dir, self.handler, log=self.logs.append,
                                              retry=retry)
        listener.start()
        self.addCleanup(listener.stop)
        return listener

    def started(self, listener) -> int:
        self.assertTrue(listener.wait_started(10), self.logs)
        return listener.server.server_address[1]

    def plain(self) -> int:
        server = http_server.Server(("127.0.0.1", 0), self.handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def get(self, conn, path, headers=None):
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()

    # -- off without the files -----------------------------------------------------------------
    def test_off_without_the_certificate_and_on_once_it_appears(self):
        listener = self.listener()
        time.sleep(0.3)  # several retries
        self.assertIsNone(listener.server)
        self.assertEqual(self.logs, [f"https: off (no certificate in {self.tls_dir} (cztvhub-cert.service makes it))"])
        install(self.a, self.tls_dir)  # the cert job ran: no restart needed
        port = self.started(listener)
        self.assertEqual(peer_subject(port, self.a[0]), "pair-a")

    def test_only_the_certificate_without_its_key_is_off(self):
        self.tls_dir.mkdir()
        shutil.copyfile(self.a[0], self.tls_dir / tls.CERT_FILE)
        listener = self.listener()
        self.assertFalse(listener.wait_started(0.3))

    def test_a_pair_that_does_not_load_is_off_and_logged_once(self):
        self.tls_dir.mkdir()
        shutil.copyfile(self.a[0], self.tls_dir / tls.CERT_FILE)
        shutil.copyfile(self.b[1], self.tls_dir / tls.KEY_FILE)  # someone else's key
        listener = self.listener()
        self.assertFalse(listener.wait_started(0.3))
        self.assertEqual(len([line for line in self.logs if "does not load" in line]), 1, self.logs)
        self.assertIn("https stays off", self.logs[0])
        self.assertNotIn("BEGIN", " ".join(self.logs))

    def test_port_taken_is_logged_and_the_plain_port_is_not_touched(self):
        install(self.a, self.tls_dir)
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        self.addCleanup(blocker.close)
        listener = http_server.https_listener("127.0.0.1", blocker.getsockname()[1], self.tls_dir, self.handler,
                                              log=self.logs.append, retry=0.05)
        listener.start()
        self.addCleanup(listener.stop)
        self.assertFalse(listener.wait_started(0.3))
        self.assertEqual(len(self.logs), 1, self.logs)
        self.assertTrue(self.logs[0].startswith(f"https: off (port {blocker.getsockname()[1]}: "), self.logs)

    # -- same routes over TLS -------------------------------------------------------------------
    def test_routes_are_the_same_as_on_the_plain_port(self):
        install(self.a, self.tls_dir)
        port = self.started(self.listener())
        context = ssl.create_default_context(cafile=str(self.a[0]))
        tls_conn = http.client.HTTPSConnection("localhost", port, context=context, timeout=10)
        plain_conn = http.client.HTTPConnection("127.0.0.1", self.plain(), timeout=10)
        self.addCleanup(tls_conn.close)
        self.addCleanup(plain_conn.close)
        auth = {"Authorization": "Bearer " + KEY}
        for path, headers in (("/web/", {}), ("/web/manifest.webmanifest", {}), ("/dvb/status", auth),
                              ("/dvb/status", {}), ("/health", {}), ("/nothing", {})):
            over_tls = self.get(tls_conn, path, headers)  # one kept-alive TLS connection for all of them
            over_plain = self.get(plain_conn, path, headers)
            self.assertEqual(over_tls[0], over_plain[0], path)
            if path != "/dvb/status":  # status carries times
                self.assertEqual(over_tls[2], over_plain[2], path)
            self.assertEqual(over_tls[1].get("Content-Security-Policy"), over_plain[1].get("Content-Security-Policy"))
        statuses = [self.get(tls_conn, p, h)[0] for p, h in (("/web/", {}), ("/dvb/status", auth),
                                                             ("/dvb/status", {}))]
        self.assertEqual(statuses, [200, 200, 401])  # the cases differ: the key is checked over TLS too
        self.assertEqual(json.loads(self.get(tls_conn, "/dvb/status", auth)[2])["enabled"], True)
        self.assertEqual(tls_conn.sock.version() in ("TLSv1.2", "TLSv1.3"), True)

    def test_playlist_links_keep_the_scheme(self):
        install(self.a, self.tls_dir)
        port = self.started(self.listener())
        conn = http.client.HTTPSConnection("localhost", port, timeout=10,
                                           context=ssl.create_default_context(cafile=str(self.a[0])))
        self.addCleanup(conn.close)
        body = self.get(conn, "/playlist.m3u")[2].decode("utf-8")
        self.assertIn(f"https://localhost:{port}/ct/CH_1.m3u8", body)
        self.assertNotIn("http://", body)

    def test_tls_below_1_2_is_refused_and_plain_http_gets_nothing(self):
        install(self.a, self.tls_dir)
        port = self.started(self.listener())
        old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        old.load_verify_locations(str(self.a[0]))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)  # TLS 1.1 itself: that is the point
                old.minimum_version = old.maximum_version = ssl.TLSVersion.TLSv1_1
            old.set_ciphers("DEFAULT:@SECLEVEL=0")
        except (ValueError, ssl.SSLError):
            self.skipTest("this OpenSSL cannot even offer TLS 1.1")
        with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
            with self.assertRaises((ssl.SSLError, OSError)):
                old.wrap_socket(raw, server_hostname="localhost").do_handshake()
        with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
            raw.sendall(b"GET /dvb/status HTTP/1.1\r\nHost: x\r\n\r\n")
            try:
                answer = raw.recv(4096)
            except ConnectionResetError:
                answer = b""
            self.assertNotIn(b"HTTP/1.", answer)  # no plain answer on the TLS port (nor a redirect)
        self.assertEqual(peer_subject(port, self.a[0]), "pair-a")  # the listener still answers

    def test_a_browser_dropping_the_connection_is_not_an_exception_in_the_log(self):
        install(self.a, self.tls_dir)
        port = self.started(self.listener())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                peer_subject(port, self.a[0])  # handshake, then gone without a request (as speculative connects)
            conn = http.client.HTTPSConnection("localhost", port, timeout=10,
                                               context=ssl.create_default_context(cafile=str(self.a[0])))
            self.assertEqual(self.get(conn, "/health")[0], 200)  # and the next one is answered
            conn.close()
            time.sleep(0.2)
        self.assertEqual(err.getvalue(), "")

    # -- renewal ---------------------------------------------------------------------------------
    def test_a_renewed_certificate_is_used_from_the_next_connection_without_a_restart(self):
        install(self.a, self.tls_dir)
        listener = self.listener()
        port = self.started(listener)
        server = listener.server
        self.assertEqual(peer_subject(port, self.a[0]), "pair-a")
        install(self.b, self.tls_dir)
        self.assertEqual(peer_subject(port, self.b[0]), "pair-b")
        self.assertIn("https: certificate reloaded", self.logs)
        self.assertIs(listener.server, server)  # the same listening server: no restart

    def test_a_half_written_renewal_keeps_the_old_pair_and_takes_the_new_one_once_complete(self):
        install(self.a, self.tls_dir)
        port = self.started(self.listener())
        self.assertEqual(peer_subject(port, self.a[0]), "pair-a")
        tmp = self.tls_dir / "c.new"
        shutil.copyfile(self.b[0], tmp)
        os.replace(tmp, self.tls_dir / tls.CERT_FILE)  # the new certificate, the old key: does not load
        self.assertEqual(peer_subject(port, self.a[0]), "pair-a")
        self.assertEqual(peer_subject(port, self.a[0]), "pair-a")
        self.assertEqual(len([line for line in self.logs if "does not load" in line]), 1, self.logs)
        self.assertTrue(any("the previous one stays in use" in line for line in self.logs), self.logs)
        install(self.b, self.tls_dir)  # the key arrives
        self.assertEqual(peer_subject(port, self.b[0]), "pair-b")

    def test_context_is_tls_1_2_or_newer(self):
        self.assertEqual(tls.make_context(*self.a).minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_unchanged_files_are_not_loaded_again(self):
        install(self.a, self.tls_dir)
        files = tls.TlsFiles(self.tls_dir, log=self.logs.append)
        first = files.context()
        self.assertIsNotNone(first)
        self.assertIs(files.context(), first)
        install(self.b, self.tls_dir)
        self.assertIsNot(files.context(), first)


class DefaultsTest(unittest.TestCase):
    def test_config_free_defaults(self):
        self.assertEqual(tls.HTTPS_PORT, 8443)
        self.assertEqual(tls.TLS_DIR, Path("/var/lib/cztvhub/tls"))
        self.assertEqual((tls.CERT_FILE, tls.KEY_FILE), ("cert.pem", "key.pem"))
        template = (Path(__file__).resolve().parents[1] / "deploy" / "cztvhub.conf.example").read_text("utf-8")
        self.assertNotIn("https", template)  # a default in code is not repeated in the template

    def test_the_cert_and_key_names_match_cert_sh(self):
        script = (Path(__file__).resolve().parents[1] / "deploy" / "cert.sh").read_text("utf-8")
        self.assertIn('"$dir/' + tls.CERT_FILE, script)
        self.assertIn('"$dir/' + tls.KEY_FILE, script)
        self.assertIn("/var/lib/cztvhub/tls", script)

if __name__ == "__main__":
    unittest.main()
