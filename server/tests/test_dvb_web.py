"""The household web app's files at /web/ (spec/dvb-gateway.md "Web"): paths, headers, the start check, HTTP."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from cztvhub.api import StreamResolver
from cztvhub.config import AdsConfig, Config, DvbConfig, parse_config
from cztvhub.dvb import web
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.server import Server, make_handler
from dvb_fixtures import NOW, FakeClient

KEY = "s3cret-" * 6
INDEX = b"<!DOCTYPE html><title>Televize</title><script src=\"app/js/core/models.js\"></script>"
EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' blob: https://ctfs.ceskatelevize.cz; "
    "media-src 'self' blob: https://ivys-nw-cdn.o2tv.cz https://*.o2-tv.cz https://pspcr-ott-live.ssl.cdn.cra.cz; "
    "connect-src 'self' https://ivys-nw-cdn.o2tv.cz https://*.o2-tv.cz https://pspcr-ott-live.ssl.cdn.cra.cz "
    "https://ctfs.ceskatelevize.cz; "
    "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def make_tree(root: Path) -> Path:
    """A web dir next to a "state" dir with a secret, and a symlink from inside pointing at it."""
    site = root / "web"
    (site / "app" / "js" / "core").mkdir(parents=True)
    (site / "css").mkdir()
    (site / "vendor").mkdir()
    (site / "index.html").write_bytes(INDEX)
    (site / "app" / "js" / "core" / "models.js").write_text("var CT = {};\n", encoding="utf-8")
    (site / "css" / "web.css").write_text("body{}\n", encoding="utf-8")
    (site / "vendor" / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
    (site / "build-info.json").write_text('{"version": "1.0"}\n', encoding="utf-8")
    (site / "logo.png").write_bytes(b"\x89PNG")
    (site / "font.woff2").write_bytes(b"wOF2")
    (site / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (site / ".hidden.js").write_text("secret", encoding="utf-8")
    (site / "folder.js").mkdir()  # a directory with a served suffix
    (site / "notes.txt").write_text("not a served type", encoding="utf-8")
    (site / "page.HTML").write_text("upper case", encoding="utf-8")
    state = root / "state"
    state.mkdir()
    (state / "profiles.json").write_text('{"secret": true}', encoding="utf-8")
    os.symlink(state / "profiles.json", site / "profiles.json")
    os.symlink(state, site / "state")
    os.symlink(site / "app" / "js" / "core" / "models.js", site / "inside.js")  # stays inside: allowed
    return site


class WebSiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.site_dir = make_tree(self.root)
        self.logs = []
        self.site = web.WebSite(self.site_dir, {"state_dir": self.root / "state"}, self.logs.append)

    def get(self, path, headers=None, method="GET"):
        return self.site.handle(method, path, headers or {})

    def test_serves_files_with_types(self):
        cases = {"/web/": ("text/html; charset=utf-8", INDEX), "/web/index.html": ("text/html; charset=utf-8", INDEX),
                 "/web/app/js/core/models.js": ("text/javascript; charset=utf-8", b"var CT = {};\n"),
                 "/web/css/web.css": ("text/css; charset=utf-8", b"body{}\n"),
                 "/web/build-info.json": ("application/json; charset=utf-8", b'{"version": "1.0"}\n'),
                 "/web/logo.png": ("image/png", b"\x89PNG"), "/web/font.woff2": ("font/woff2", b"wOF2"),
                 "/web/icon.svg": ("image/svg+xml", b"<svg/>"), "/web/page.HTML": ("text/html; charset=utf-8",
                                                                                  b"upper case"),
                 "/web/inside.js": ("text/javascript; charset=utf-8", b"var CT = {};\n"),
                 "/web/app/js/core/%6dodels.js": ("text/javascript; charset=utf-8", b"var CT = {};\n")}
        for path, (content_type, body) in cases.items():
            resp = self.get(path)
            self.assertEqual((resp.status, resp.content_type, resp.body), (200, content_type, body), path)
        self.assertIn("web: /web/ from", self.logs[0])

    def test_headers_on_every_answer(self):
        for path, status in (("/web/", 200), ("/web/missing.js", 404), ("/web", 301)):
            resp = self.get(path)
            self.assertEqual(resp.status, status, path)
            self.assertEqual(resp.headers["Content-Security-Policy"], EXPECTED_CSP, path)
            self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")
        resp = self.get("/web/")
        self.assertEqual(resp.headers["Cache-Control"], "no-cache")
        self.assertEqual(self.get("/web/", method="POST").headers["Content-Security-Policy"], EXPECTED_CSP)

    def test_csp_has_no_inline_or_foreign_sources(self):
        self.assertNotIn("unsafe", web.CSP)
        self.assertNotIn("data:", web.CSP)
        self.assertNotIn(" * ", web.CSP + " ")
        hosts = {t for t in web.CSP.replace(";", " ").split() if t.startswith("https://")}
        self.assertEqual(hosts, {"https://ivys-nw-cdn.o2tv.cz", "https://*.o2-tv.cz", "https://ctfs.ceskatelevize.cz",
                                 "https://pspcr-ott-live.ssl.cdn.cra.cz"})  # the Chamber: T28, owner 2026-11-01
        self.assertNotIn("*.cra.cz", web.CSP)  # only the Chamber's own live CDN host, not the provider's domain
        self.assertNotIn("api.ceskatelevize.cz", web.CSP)  # the page asks the gateway, never ČT's API

    def test_redirect(self):
        resp = self.get("/web")
        self.assertEqual((resp.status, resp.headers["Location"], resp.body), (301, "/web/", b""))

    def test_etag_and_304(self):
        first = self.get("/web/app/js/core/models.js")
        etag = first.headers["ETag"]
        self.assertRegex(etag, r'^"[0-9a-f]{32}"$')
        for header in (etag, "W/" + etag, '"other", ' + etag, "*"):
            resp = self.get("/web/app/js/core/models.js", {"If-None-Match": header})
            self.assertEqual((resp.status, resp.body), (304, b""), header)
            self.assertEqual(resp.headers["ETag"], etag)
        self.assertEqual(self.get("/web/app/js/core/models.js", {"If-None-Match": '"other"'}).status, 200)
        self.assertNotEqual(self.get("/web/css/web.css").headers["ETag"], etag)
        (self.site_dir / "app" / "js" / "core" / "models.js").write_text("var CT = {v: 2};\n", encoding="utf-8")
        resp = self.get("/web/app/js/core/models.js", {"If-None-Match": etag})
        self.assertEqual(resp.status, 200)  # changed content, new tag
        self.assertNotEqual(resp.headers["ETag"], etag)

    def test_nothing_outside_dot_files_directories_or_unknown_types(self):
        for path in ("/web/../state/profiles.json", "/web/%2e%2e/state/profiles.json", "/web/%2E%2E/state/profiles.json",
                     "/web/app/../../state/profiles.json", "/web/app/%2e%2e%2f%2e%2e%2fstate/profiles.json",
                     "/web/..%2fstate%2fprofiles.json", "/web/app%2fjs%2fcore%2fmodels.js", "/web/app%2Fjs/core/models.js", "/web/..%5cstate%5cprofiles.json", "/web/..\\state",
                     "/web/profiles.json", "/web/state/profiles.json", "/web/state/", "/web/.hidden.js",
                     "/web/%2ehidden.js", "/web/app/", "/web/app", "/web/app/js", "/web//etc/passwd", "/web/./index.html",
                     "/web/notes.txt", "/web/folder.js", "/web/vendor/LICENSE", "/web/missing.js", "/web/index.html%00.js",
                     "/web/%ff.js", "/web/index.html/", "/webx", "/web-x/index.html", "/web/" + "a/" * 50 + "x.js"):
            resp = self.get(path)
            self.assertEqual(resp.status, 404, path)
            self.assertNotIn(b"secret", resp.body)

    def test_other_methods(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            resp = self.get("/web/index.html", method=method)
            self.assertEqual((resp.status, resp.headers["Allow"]), (405, "GET, HEAD"), method)
        self.assertEqual(self.get("/web/", method="HEAD").status, 200)


class StartCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def site(self, web_dir, **dirs):
        logs = []
        site = web.WebSite(web_dir, dirs, logs.append)
        return site, logs

    def test_off_when_overlapping_recordings_or_state(self):
        var = self.root / "var"
        (var / "hls" / "web").mkdir(parents=True)
        (var / "buffer").mkdir()
        for d in (var, var / "hls", var / "buffer", var / "hls" / "web"):
            (d / "index.html").write_bytes(INDEX)
        dirs = dict(hls_dir=var / "hls", buffer_dir=var / "buffer", state_dir=var)
        for web_dir in (var, var / "hls", var / "buffer", var / "hls" / "web", self.root):
            site, logs = self.site(web_dir, **dirs)
            self.assertFalse(site.enabled, web_dir)
            self.assertIn("web: off (web_dir", logs[0])
            self.assertEqual(site.handle("GET", "/web/index.html", {}).status, 404)

    def test_off_through_a_symlink(self):
        state = self.root / "state"
        state.mkdir()
        (state / "index.html").write_bytes(INDEX)
        os.symlink(state, self.root / "web")
        site, logs = self.site(self.root / "web", state_dir=state)
        self.assertFalse(site.enabled)
        self.assertIn("overlaps state_dir", logs[0])

    def test_off_without_index(self):
        site, logs = self.site(self.root / "missing", state_dir=self.root / "state")
        self.assertFalse(site.enabled)
        self.assertIn("no index.html", logs[0])

    def test_on_beside_them(self):
        (self.root / "web").mkdir()
        (self.root / "web" / "index.html").write_bytes(INDEX)
        site, logs = self.site(self.root / "web", state_dir=self.root / "web-state", hls_dir=self.root / "webhls")
        self.assertTrue(site.enabled, logs)

    def test_protected_dirs_from_the_config(self):
        cfg = Config(dvb=DvbConfig(hls_dir=Path("/a/hls"), buffer_dir=Path("/b"), state_dir=Path("/a")),
                     ads=AdsConfig(breaks_dir=Path("/c")))
        self.assertEqual(web.protected_dirs(cfg, "/etc/cztvhub/cztvhub.conf"),
                         {"hls_dir": Path("/a/hls"), "buffer_dir": Path("/b"), "state_dir": Path("/a"),
                          "breaks_dir": Path("/c"), "config": Path("/etc/cztvhub")})
        self.assertNotIn("config", web.protected_dirs(cfg))

    def test_web_dir_default_and_config(self):
        self.assertEqual(Config().dvb.web_dir, Path("/opt/cztvhub/web"))
        cfg = parse_config(f"[dvb]\nenabled = true\nkey = {KEY}\nweb_dir = /srv/web  ; comment\n")
        self.assertEqual(cfg.dvb.web_dir, Path("/srv/web"))
        template = (Path(__file__).resolve().parents[1] / "deploy" / "cztvhub.conf.example").read_text("utf-8")
        self.assertNotIn("web_dir", template)  # a default in code is not repeated in the template
        # the defaults keep the web away from recordings and state
        self.assertIsNone(next((n for n, p in web.protected_dirs(Config()).items()
                                if web.overlaps(Config().dvb.web_dir, p)), None))


class WebHttpTest(unittest.TestCase):
    """Through the real handler: HEAD, 405 for other methods, 304 without a body, off without the gateway."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_tree(self.root)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.root / "hls", state_dir=self.root / "state",
                                   buffer_dir=self.root / "buffer", web_dir=self.root / "web"),
                     ads=AdsConfig(breaks_dir=self.root / "breaks"))
        self.gw = DvbGateway(cfg, client=FakeClient(), now=lambda: NOW, log=lambda m: None)
        self.web = web.WebSite.from_config(cfg, log=lambda m: None)
        self.assertTrue(self.web.enabled)

    def serve(self, gateway, site):
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda ch: "https://x"), gateway, None,
                                                       site))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_get_head_and_headers(self):
        server = self.serve(self.gw, self.web)
        resp, body = self.request(server, "GET", "/web/")
        self.assertEqual((resp.status, body), (200, INDEX))
        self.assertEqual(resp.getheader("Content-Security-Policy"), EXPECTED_CSP)
        self.assertEqual(resp.getheader("Content-Length"), str(len(INDEX)))
        resp, body = self.request(server, "HEAD", "/web/")
        self.assertEqual((resp.status, body, resp.getheader("Content-Length")), (200, b"", str(len(INDEX))))
        resp, body = self.request(server, "GET", "/web?x=1")
        self.assertEqual((resp.status, resp.getheader("Location")), (301, "/web/"))
        etag = self.request(server, "GET", "/web/css/web.css")[0].getheader("ETag")
        resp, body = self.request(server, "GET", "/web/css/web.css?v=2", {"If-None-Match": etag})
        self.assertEqual((resp.status, body, resp.getheader("Content-Length")), (304, b"", None))
        resp, body = self.request(server, "GET", "/web/%2e%2e/state/profiles.json")
        self.assertEqual(resp.status, 404)
        self.assertNotIn(b"secret", body)

    def test_other_methods_are_405(self):
        server = self.serve(self.gw, self.web)
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            resp, _ = self.request(server, method, "/web/index.html", body=b"{}")
            self.assertEqual((resp.status, resp.getheader("Allow")), (405, "GET, HEAD"), method)
        resp, _ = self.request(server, "DELETE", "/dvb/channels")
        self.assertEqual(resp.status, 501)  # unchanged elsewhere

    def test_no_web_without_the_gateway_or_when_off(self):
        for gateway, site in ((None, None), (self.gw, None),
                              (self.gw, web.WebSite(self.root / "state", {"state_dir": self.root / "state"},
                                                    lambda m: None))):
            server = self.serve(gateway, site)
            self.assertEqual(self.request(server, "GET", "/web/")[0].status, 404)
            self.assertEqual(self.request(server, "GET", "/web/index.html")[0].status, 404)

    def test_web_does_not_need_the_key_but_dvb_does(self):
        server = self.serve(self.gw, self.web)
        self.assertEqual(self.request(server, "GET", "/web/index.html")[0].status, 200)
        resp, body = self.request(server, "GET", "/dvb/channels")
        self.assertEqual(resp.status, 401)
        self.assertIn("error", json.loads(body))


if __name__ == "__main__":
    unittest.main()
