"""The connection manual from /admin (T49, dvb/manual.py, spec/dvb-gateway.md "Connection manual from /admin"):
the pure text, what is gathered (never a secret but the app key), Tailscale through a fake runner, and the route.
No network, no real tailscale, no certificate: everything is injected."""
import base64
import http.client
import json
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cztvhub.api import StreamResolver
from cztvhub.config import AdminConfig, AdsConfig, Config, DvbConfig, TmdbConfig, TvheadendConfig
from cztvhub.dvb import manual
from cztvhub.dvb.admin import AdminSite
from cztvhub.dvb.manual import ManualInfo, Tailnet
from cztvhub.server import Server, make_handler

APP_KEY = "APPKEY-0123456789abcdef0123456789abcdef"
ADMIN_PASSWORD = "ADMINPW-secret-123"
WORKER_KEY = "WORKERKEY-0123456789abcdef0123456789ab"
TMDB_KEY = "TMDBKEY-0123456789abcdef"
TVH_PASSWORD = "TVHPW-secret-456"
AUTH = {"Authorization": "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()}
POST = {**AUTH, "X-Cztvhub-Admin": "1", "Host": "pi:8098"}
TAILNET = Tailnet("pi.tail1234.ts.net", ["100.101.102.103", "fd7a:115c:a1e0::1"])
NOW = 1_793_624_700.0  # 2026-11-02T13:05:00Z = 14:05 in Prague


def info(**over):
    base = dict(generated="2. 11. 2026 14:05", version="94366b4c1a2b", key=APP_KEY, port=8098,
                lan_name="pi.local", lan_ips=["192.168.1.20"], tailnet=TAILNET, tailnet_reason=None, https=True)
    base.update(over)
    return ManualInfo(**base)


def secrets_config():
    return Config(dvb=DvbConfig(enabled=True, key=APP_KEY), admin=AdminConfig(password=ADMIN_PASSWORD),
                  ads=AdsConfig(enabled=True, worker_key=WORKER_KEY), tmdb=TmdbConfig(key=TMDB_KEY),
                  tvheadend=TvheadendConfig(password=TVH_PASSWORD))


class ManualTextTest(unittest.TestCase):
    def test_every_variant_and_the_key(self):
        text = manual.manual_text(info())
        for heading in ("1. Adresy a klíč", "2. Apple TV a Android TV", "3. Samsung TV", "4. Web v prohlížeči",
                        "5. Kodi"):
            self.assertIn(heading, text)
        self.assertEqual(text.count(APP_KEY), 1)
        for line in ("http://pi.local:8098", "http://192.168.1.20:8098", "http://pi.tail1234.ts.net:8098",
                     "100.101.102.103, fd7a:115c:a1e0::1", "http://pi.local:8098/web/",
                     "http://pi.tail1234.ts.net:8098/web/", "https://pi.tail1234.ts.net:8443/web/",
                     "http://192.168.1.20:8098/playlist.m3u", "Verze brány: 94366b4c1a2b", "Vytvořeno: 2. 11. 2026 14:05"):
            self.assertIn(line, text)
        # the apps' own labels (spec/apps.md "Nastavení → Server", the apps' settings screens)
        for label in ("Nastavení → Server", "Adresa brány", "Klíč brány", "Uložit a ověřit",
                      "Připojovat přes Tailscale", "Přihlásit klíčem", "Settings → Keys", "Nainstalovat",
                      "Přidat do Docku", "PVR IPTV Simple Client"):
            self.assertIn(label, text)
        self.assertIn("Samsung Tailscale v aplikaci neumí", text)
        self.assertIn("bránu ani klíč nepotřebuje", text)  # Kodi add-on

    def test_warning_first_plain_lf_utf8(self):
        text = manual.manual_text(info())
        first = text.split("\n", 1)[0]
        self.assertTrue(first.startswith("POZOR"))
        self.assertIn("klíč", first)
        self.assertEqual(text.count("POZOR"), 1)  # one line, no nagging
        self.assertNotIn("\r", text)
        self.assertFalse(text.startswith("﻿"))
        self.assertTrue(text.endswith("\n"))
        self.assertIn("č", text)  # Czech, not ASCII-folded

    def test_https_off(self):
        on, off = manual.manual_text(info()), manual.manual_text(info(https=False))
        self.assertNotEqual(on, off)
        self.assertNotIn(":8443", off)
        self.assertNotIn("Nainstalovat", off)
        self.assertIn("HTTPS je vypnuté", off)
        self.assertIn("DNS → HTTPS Certificates", off)
        self.assertIn("sudo systemctl start cztvhub-cert.service", off)
        self.assertNotIn("HTTPS je vypnuté", on)

    def test_https_on_without_a_known_name(self):
        text = manual.manual_text(info(tailnet=None, tailnet_reason="x", https=True))
        self.assertIn("https://<jméno>.<tailnet>.ts.net:8443/web/", text)
        self.assertIn("tailscale status", text)

    def test_tailscale_not_found(self):
        reason = "na Raspberry není nainstalovaný Tailscale (docs/dvb-setup.md, kapitola 5)"
        text = manual.manual_text(info(tailnet=None, tailnet_reason=reason, https=False))
        self.assertIn(f"Tailscale na Raspberry nebylo zjištěno ({reason})", text)
        self.assertNotIn(".ts.net:8098", text)
        self.assertNotIn("100.101.102.103", text)
        self.assertIn("http://pi.local:8098/web/", text)  # the home variants stay
        self.assertIn(APP_KEY, text)

    def test_no_lan_address(self):
        text = manual.manual_text(info(lan_name=None, lan_ips=[]))
        self.assertIn("adresu se nepodařilo zjistit", text)
        self.assertIn("http://<IP Raspberry>:8098/web/", text)
        self.assertIn("http://<IP Raspberry>:8098/playlist.m3u", text)
        self.assertNotIn("None", text)

    def test_only_the_mdns_name(self):
        text = manual.manual_text(info(lan_ips=[]))
        self.assertIn("http://pi.local:8098/playlist.m3u", text)
        self.assertNotIn("nefunguje, použijte IP", text)

    def test_unknown_version_and_other_port(self):
        text = manual.manual_text(info(version=None, port=8100))
        self.assertIn("Verze brány: neznámá", text)
        self.assertIn("http://pi.local:8100/web/", text)
        self.assertNotIn(":8098", text)


class GatherTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tls = Path(tmp.name)

    def gather(self, cfg=None, tailscale=lambda: (TAILNET, None), version=None):
        return manual.gather(cfg or secrets_config(), version, now=lambda: NOW, hostname=lambda: "Pi.home.arpa",
                             lan=lambda host: ["192.168.1.20"], tailscale=tailscale, tls_dir=self.tls)

    def test_only_the_app_key(self):
        got = self.gather()
        text = manual.manual_text(got)
        self.assertIn(APP_KEY, text)
        for secret in (ADMIN_PASSWORD, WORKER_KEY, TMDB_KEY, TVH_PASSWORD):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, repr(got))

    def test_fields(self):
        got = self.gather(version={"commit": "a" * 40, "installed": None})
        self.assertEqual((got.lan_name, got.lan_ips, got.port), ("pi.local", ["192.168.1.20"], 8098))
        self.assertEqual(got.version, "a" * 12)
        self.assertEqual(got.generated, "2. 11. 2026 14:05")
        self.assertEqual((got.tailnet, got.tailnet_reason), (TAILNET, None))
        self.assertIsNone(self.gather().version)

    def test_https_follows_the_certificate_files(self):
        self.assertFalse(self.gather().https)
        (self.tls / "cert.pem").write_text("x")
        self.assertFalse(self.gather().https)  # both files, as tls.py
        (self.tls / "key.pem").write_text("x")
        self.assertTrue(self.gather().https)

    def test_tailscale_reason_kept(self):
        got = self.gather(tailscale=lambda: (None, "why"))
        self.assertEqual((got.tailnet, got.tailnet_reason), (None, "why"))

    def test_mdns_name(self):
        self.assertEqual(manual.mdns_name("cztvhub"), "cztvhub.local")
        self.assertEqual(manual.mdns_name("Pi.home.arpa"), "pi.local")
        for bad in ("", "-x", "a b", "<script>", "x" * 64):
            self.assertIsNone(manual.mdns_name(bad), bad)

    def test_lan_addresses_private_ipv4_only(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value.getsockname.return_value = ("192.168.1.20", 5000)
        resolved = [(socket.AF_INET, 0, 0, "", (ip, 0)) for ip in
                    ("127.0.1.1", "192.168.1.20", "10.0.0.5", "100.101.102.103", "8.8.8.8", "169.254.1.1")]
        with mock.patch.object(manual.socket, "socket", return_value=sock), \
                mock.patch.object(manual.socket, "getaddrinfo", return_value=resolved):
            self.assertEqual(manual.lan_addresses("pi"), ["192.168.1.20", "10.0.0.5"])
        sock.__enter__.return_value.connect.assert_called_once_with(manual.ROUTE_PROBE)

    def test_lan_addresses_without_network(self):
        with mock.patch.object(manual.socket, "socket", side_effect=OSError("no route")), \
                mock.patch.object(manual.socket, "getaddrinfo", side_effect=socket.gaierror("x")):
            self.assertEqual(manual.lan_addresses("pi"), [])


def status(**over):
    doc = {"BackendState": "Running", "MagicDNSSuffix": "tail1234.ts.net",
           "Self": {"DNSName": "pi.tail1234.ts.net.", "HostName": "pi",
                    "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"]},
           "Peer": {"x": {"DNSName": "phone.tail1234.ts.net.", "TailscaleIPs": ["100.1.1.1"]}}}
    doc.update(over)
    return json.dumps(doc).encode()


class TailscaleTest(unittest.TestCase):
    def test_parse_running(self):
        self.assertEqual(manual.parse_tailscale_status(status()), (TAILNET, None))

    def test_parse_refusals(self):
        cases = {
            b"not json": "JSON",
            b"[]": "JSON",
            status(BackendState="NeedsLogin"): "stav NeedsLogin",
            status(BackendState="<b>evil</b>"): "stav neznámý",
            status(Self=None): "MagicDNS",
            status(Self={"DNSName": "", "TailscaleIPs": []}): "MagicDNS",
            status(Self={"DNSName": "Evil Name.ts.net.", "TailscaleIPs": []}): "MagicDNS",
            status(Self={"DNSName": "pi", "TailscaleIPs": []}): "MagicDNS",  # one label: no tailnet suffix
        }
        for raw, expected in cases.items():
            tn, reason = manual.parse_tailscale_status(raw)
            self.assertIsNone(tn, raw)
            self.assertIn(expected, reason, raw)
            self.assertNotIn("evil", reason)

    def test_parse_drops_bad_ips(self):
        tn, _ = manual.parse_tailscale_status(status(Self={"DNSName": "pi.tail1234.ts.net.",
                                                           "TailscaleIPs": ["100.1.1.1", "x", 7, "100.1.1.1"]}))
        self.assertEqual(tn.ips, ["100.1.1.1"])

    def run_with(self, result=None, raises=None):
        calls = []

        def run(args, **kw):
            calls.append((args, kw))
            if raises is not None:
                raise raises
            return result

        got = manual.tailscale_status(run=run, which=lambda name: "/usr/bin/tailscale")
        return got, calls

    def test_runs_status_json_with_a_timeout(self):
        got, calls = self.run_with(SimpleNamespace(returncode=0, stdout=status()))
        self.assertEqual(got, (TAILNET, None))
        args, kw = calls[0]
        self.assertEqual(args, ["/usr/bin/tailscale", "status", "--json"])
        self.assertEqual(kw["timeout"], manual.TAILSCALE_TIMEOUT)
        self.assertIs(kw["stdin"], subprocess.DEVNULL)
        self.assertFalse(kw["check"])

    def test_not_installed(self):
        self.assertEqual(manual.tailscale_status(run=None, which=lambda name: None)[0], None)
        self.assertIn("není nainstalovaný", manual.tailscale_status(run=None, which=lambda name: None)[1])

    def test_failures(self):
        cases = [
            (dict(raises=subprocess.TimeoutExpired("tailscale", 5)), "do 5 s"),
            (dict(raises=PermissionError(13, "Permission denied")), "nejde spustit (Permission denied)"),
            (dict(result=SimpleNamespace(returncode=1, stdout=status(BackendState="NeedsLogin"))), "stav NeedsLogin"),
            (dict(result=SimpleNamespace(returncode=1, stdout=b"")), "chybou 1"),
            (dict(result=SimpleNamespace(returncode=0, stdout=b" " * (manual.TAILSCALE_MAX_BYTES + 1))), "dlouhým"),
            (dict(result=SimpleNamespace(returncode=0, stdout=None)), "JSON"),
        ]
        for kwargs, expected in cases:
            (tn, reason), _ = self.run_with(**kwargs)
            self.assertIsNone(tn, kwargs)
            self.assertIn(expected, reason, kwargs)


class ManualRouteTest(unittest.TestCase):
    def setUp(self):
        self.logged = []
        self.gathered = 0
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = secrets_config()

        def gather():
            self.gathered += 1
            return manual.gather(cfg, None, now=lambda: NOW, hostname=lambda: "pi", lan=lambda h: ["192.168.1.20"],
                                 tailscale=lambda: (TAILNET, None), tls_dir=Path(tmp.name))

        gateway = SimpleNamespace(log=self.logged.append, client=None, now=lambda: NOW)
        self.site = AdminSite(cfg, gateway, admin=object(), updater=SimpleNamespace(version=lambda: None),
                              manual_info=gather)

    def call(self, method, headers, path="/admin/api/manual"):
        return self.site.handle(method, path, headers, b"{}")

    def test_login_required(self):
        for headers in ({}, {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()},
                        {"Authorization": "Bearer " + APP_KEY}, {"Authorization": "Bearer " + WORKER_KEY},
                        {"Cookie": "cztvhub_key=" + APP_KEY}):
            resp = self.call("POST", {**headers, "X-Cztvhub-Admin": "1"})
            self.assertEqual(resp.status, 401, headers)
            self.assertNotIn(APP_KEY.encode(), resp.body)
        self.assertEqual((self.logged, self.gathered), ([], 0))

    def test_admin_header_and_same_origin(self):
        for headers in (AUTH, {**POST, "X-Cztvhub-Admin": "0"}, {**POST, "Origin": "http://evil.example"},
                        {**POST, "Origin": "http://pi:8098.evil.example"}):
            resp = self.call("POST", headers)
            self.assertEqual(resp.status, 403, headers)
            self.assertNotIn(APP_KEY.encode(), resp.body)
        self.assertEqual((self.logged, self.gathered), ([], 0))
        self.assertEqual(self.call("POST", {**POST, "Origin": "https://pi:8098"}).status, 200)

    def test_attachment(self):
        resp = self.call("POST", POST)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/plain; charset=utf-8")
        self.assertEqual(resp.headers["Content-Disposition"], 'attachment; filename="televize-pripojeni.txt"')
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        text = resp.body.decode("utf-8")
        self.assertTrue(text.startswith("POZOR"))
        self.assertIn(APP_KEY, text)
        for secret in (ADMIN_PASSWORD, WORKER_KEY, TMDB_KEY, TVH_PASSWORD):
            self.assertNotIn(secret, text)
        self.assertEqual(self.gathered, 1)  # gathered at the request, not cached

    def test_logged_without_the_key(self):
        self.call("POST", POST)
        self.assertEqual(self.logged, ["admin: connection manual downloaded"])
        for line in self.logged:
            self.assertNotIn(APP_KEY, line)

    def test_other_methods(self):
        for method in ("GET", "PUT", "DELETE"):
            self.assertEqual(self.call(method, POST).status, 405, method)
        self.assertEqual((self.logged, self.gathered), ([], 0))

    def test_default_gathers_with_the_installed_version(self):
        cfg = secrets_config()
        gateway = SimpleNamespace(log=self.logged.append, client=None, now=lambda: NOW)
        version = {"commit": "b" * 40, "installed": None}
        site = AdminSite(cfg, gateway, admin=object(), updater=SimpleNamespace(version=lambda: version))
        with mock.patch.object(manual, "gather", return_value=info()) as gather:  # no real tailscale or socket
            resp = site.handle("POST", "/admin/api/manual", POST, b"")
        self.assertEqual(resp.status, 200)
        gather.assert_called_once()
        self.assertIs(gather.call_args.args[0], cfg)
        self.assertEqual(gather.call_args.args[1], version)

    def test_page_has_the_tab(self):
        page = self.site.handle("GET", "/admin", AUTH).body.decode("utf-8")
        self.assertIn('id="tab-manual"', page)
        self.assertIn('<section id="manual" hidden>', page)
        self.assertIn("Stáhnout návod pro připojení (TXT)", page)
        self.assertIn('fetch("/admin/api/manual", {method: "POST"', page)
        self.assertIn('"X-Cztvhub-Admin": "1"', page)
        self.assertIn('"update", "manual"]', page)  # in TABS, so the tab switches

    def test_over_http(self):
        # through the real server (port 0, never 8098): the attachment headers reach the browser
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda c: "x"), None, self.site))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("POST", "/admin/api/manual", body=b"",
                     headers={**AUTH, "X-Cztvhub-Admin": "1", "Origin": f"http://127.0.0.1:{port}"})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Disposition"), 'attachment; filename="televize-pripojeni.txt"')
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertIn(APP_KEY, body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
