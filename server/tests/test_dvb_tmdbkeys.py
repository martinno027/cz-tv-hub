"""TMDb keys entered on /admin (T39, spec/dvb-gateway.md "Programme details"): format checks, the 0600 key file,
which key wins, the check with TMDb before saving, live on/off, and that no value ever reaches a log line or an
answer. TMDb is faked; nothing here touches the network."""
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from cztvhub.config import AdminConfig, Config, DvbConfig, TmdbConfig
from cztvhub.dvb import details as dt
from cztvhub.dvb import tmdbkeys as tk
from cztvhub.dvb import tmdbstore as ts
from cztvhub.dvb.admin import AdminSite
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from dvb_fixtures import NOW, FakeClient

API_KEY = "0123456789abcdef0123456789abcdef"
API_KEY_2 = "fedcba9876543210fedcba9876543210"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ0ZXN0IiwibmJmIjoxfQ.c2lnbmF0dXJlLXRlc3QtdmFsdWUtMTIz"
CONFIG_KEY = "abcdefabcdefabcdefabcdefabcdefab"
APP_KEY = "tmdb-keys-app-key-" * 2
PASSWORD = "admin-password-1"
AUTH = {"Authorization": "Basic YWRtaW46YWRtaW4tcGFzc3dvcmQtMQ=="}  # admin:admin-password-1
POST = {**AUTH, "X-Cztvhub-Admin": "1", "Host": "pi:8098"}


class FakeTmdb:
    """urllib's opener for /3/configuration: 200, 401, or an error whose text carries the request URL."""

    def __init__(self):
        self.status = 200
        self.fail = None
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.fail == "down":
            raise urllib.error.URLError(f"connection refused for {request.full_url}")
        if self.status != 200:
            raise urllib.error.HTTPError(request.full_url, self.status, "Unauthorized", {}, None)
        return Response(b'{"images": {}}')


class Response:
    status = 200

    def __init__(self, body):
        self.body = body
        self.headers = {"Content-Type": "application/json"}

    def read(self, size=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class KeyFormatTest(unittest.TestCase):
    def test_accepts_only_tmdb_shaped_values(self):
        self.assertEqual(tk.check(tk.API_KEY, f"  {API_KEY}\n"), API_KEY)
        self.assertEqual(tk.check(tk.TOKEN, TOKEN), TOKEN)
        for kind, bad in ((tk.API_KEY, API_KEY.upper()), (tk.API_KEY, API_KEY[:-1]), (tk.API_KEY, TOKEN),
                          (tk.TOKEN, API_KEY), (tk.TOKEN, "eyJabc"), (tk.TOKEN, TOKEN + "\n.x"),
                          (tk.TOKEN, "eyJ" + "a" * 1100 + ".b.c"), ("password", API_KEY)):
            with self.assertRaises(tk.KeyFormatError) as ctx:
                tk.check(kind, bad)
            self.assertNotIn(bad.strip()[:20], str(ctx.exception))  # never echoed back

    def test_hint_shows_only_the_end(self):
        self.assertEqual(tk.hint(API_KEY), "…cdef")
        self.assertEqual(tk.hint("short"), "…")

    def test_config_wins_then_token_then_api_key(self):
        both = {tk.API_KEY: API_KEY, tk.TOKEN: TOKEN}
        self.assertEqual(tk.effective(CONFIG_KEY, both), (CONFIG_KEY, "config"))
        self.assertEqual(tk.effective("", both), (TOKEN, tk.TOKEN))
        self.assertEqual(tk.effective("", {tk.API_KEY: API_KEY}), (API_KEY, tk.API_KEY))
        self.assertEqual(tk.effective("", {}), ("", None))


class KeyFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.file = tk.KeyFile(self.dir)

    def test_stored_with_mode_0600_and_gone_when_empty(self):
        self.file.set(tk.API_KEY, API_KEY)
        self.file.set(tk.TOKEN, TOKEN)
        self.assertEqual(self.file.load(), {tk.API_KEY: API_KEY, tk.TOKEN: TOKEN})
        self.assertEqual(self.file.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(tk.KeyFile(self.dir).load(), {tk.API_KEY: API_KEY, tk.TOKEN: TOKEN})
        self.file.set(tk.TOKEN, None)
        self.assertEqual(self.file.load(), {tk.API_KEY: API_KEY})
        self.file.set(tk.API_KEY, None)
        self.assertFalse(self.file.path.exists())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [])  # no tmp file left behind

    def test_an_older_file_with_wide_bits_is_rewritten_0600(self):
        self.file.path.write_text("{}")
        self.file.path.chmod(0o644)
        leftover = self.file.path.with_name(tk.FILE + ".tmp")  # from an interrupted write
        leftover.write_text("")
        leftover.chmod(0o644)
        self.file.set(tk.API_KEY, API_KEY)
        self.assertEqual(self.file.path.stat().st_mode & 0o777, 0o600)

    def test_damaged_or_foreign_content_counts_as_no_key(self):
        self.file.path.write_text("{not json")
        self.assertEqual(self.file.load(), {})
        self.file.path.write_text(json.dumps({tk.API_KEY: "nope", tk.TOKEN: TOKEN, "other": API_KEY}))
        self.assertEqual(self.file.load(), {tk.TOKEN: TOKEN})


class AdminKeysTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.logs = []
        self.tmdb = FakeTmdb()
        patcher = mock.patch.object(dt._OPENER, "open", self.tmdb)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gw, self.site = self.make("")

    def make(self, config_key):
        cfg = Config(dvb=DvbConfig(enabled=True, key=APP_KEY, hls_dir=self.dir / "hls", state_dir=self.dir / "state"),
                     tmdb=TmdbConfig(key=config_key), admin=AdminConfig(password=PASSWORD))
        hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        gw = DvbGateway(cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=self.logs.append)
        self.addCleanup(lambda: gw.tmdb_worker and gw.tmdb_worker.stop())
        return gw, AdminSite(cfg, gw)

    def request(self, method, body=None, headers=None):
        resp = self.site.handle(method, "/admin/api/tmdb", headers if headers is not None else (POST if method == "POST" else AUTH),
                                json.dumps(body).encode() if body is not None else b"", {})
        return resp, (json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body)

    def set_key(self, kind, value):
        return self.request("POST", {"setKey": {"kind": kind, "value": value}})

    def assert_no_secret(self, *texts):
        everything = "\n".join(list(texts) + self.logs)
        for secret in (API_KEY, API_KEY_2, TOKEN, CONFIG_KEY):
            self.assertNotIn(secret, everything)
            self.assertNotIn(secret[4:-4], everything)

    def test_off_without_a_key_and_the_form_says_so(self):
        resp, data = self.request("GET")
        self.assertEqual((resp.status, data["enabled"]), (200, False))
        self.assertEqual(data["keys"], {"source": None, "editable": True, "apiKey": None, "token": None})
        self.assertIsNone(self.gw.details)

    def test_a_checked_api_key_turns_tmdb_on_without_restart(self):
        resp, data = self.set_key(tk.API_KEY, API_KEY)
        self.assertEqual((resp.status, data["enabled"]), (200, True), data)
        self.assertEqual(data["keys"], {"source": "apiKey", "editable": True, "apiKey": "…cdef", "token": None})
        self.assertIsNotNone(self.gw.details)
        self.assertIsNotNone(self.gw.tmdb_worker)
        # exactly one check, the configuration call, with the key only in the request itself
        self.assertEqual([urllib.parse.urlsplit(r.full_url).path for r in self.tmdb.requests], ["/3/configuration"])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(self.tmdb.requests[0].full_url).query),
                         {"api_key": [API_KEY]})
        self.assertEqual(tk.KeyFile(self.dir / "state").load(), {tk.API_KEY: API_KEY})
        self.assertTrue(any("API Key saved" in line for line in self.logs))
        self.assert_no_secret(resp.body.decode())

    def test_the_token_wins_and_goes_as_bearer(self):
        self.set_key(tk.API_KEY, API_KEY)
        resp, data = self.set_key(tk.TOKEN, TOKEN)
        self.assertEqual((resp.status, data["keys"]["source"]), (200, "token"))
        self.assertEqual(self.tmdb.requests[-1].get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn("api_key", self.tmdb.requests[-1].full_url)
        self.assertTrue(self.gw.details.client.bearer)
        self.assert_no_secret(resp.body.decode())

    def test_a_refused_key_is_not_stored(self):
        self.set_key(tk.API_KEY, API_KEY)
        self.tmdb.status = 401
        resp, data = self.set_key(tk.API_KEY, API_KEY_2)
        self.assertEqual(resp.status, 400)
        self.assertIn("odmítlo", data["error"])
        self.assertEqual(tk.KeyFile(self.dir / "state").load(), {tk.API_KEY: API_KEY})  # the old one stays
        self.assertEqual(self.gw.tmdb_source, "apiKey")
        self.assert_no_secret(resp.body.decode())

    def test_tmdb_down_saves_nothing_and_leaks_nothing(self):
        self.tmdb.fail = "down"  # the error text carries the URL with ?api_key=
        resp, data = self.set_key(tk.API_KEY, API_KEY)
        self.assertEqual(resp.status, 502)
        self.assertFalse((self.dir / "state" / tk.FILE).exists())
        self.assertIsNone(self.gw.details)
        self.assert_no_secret(resp.body.decode())

    def test_a_malformed_value_never_reaches_tmdb(self):
        resp, data = self.set_key(tk.API_KEY, "not-a-key")
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.tmdb.requests, [])
        for body in ({"setKey": {"kind": "password", "value": API_KEY}}, {"setKey": API_KEY},
                     {"setKey": {"kind": tk.API_KEY, "value": 5}}, {"removeKey": "password"}):
            self.assertEqual(self.request("POST", body)[0].status, 400, body)

    def test_the_check_counts_toward_the_shared_limit(self):
        self.assertIs(self.gw.tmdb_limiter, self.gw.tmdb_limiter)
        self.gw.tmdb_limiter.per_minute = 0
        resp, data = self.set_key(tk.API_KEY, API_KEY)
        self.assertEqual(resp.status, 503)
        self.assertEqual(self.tmdb.requests, [])
        self.gw.tmdb_limiter.per_minute = dt.PER_MINUTE
        self.set_key(tk.API_KEY, API_KEY)
        self.assertIs(self.gw.details.client.limiter, self.gw.tmdb_limiter)  # lookups share it

    def test_removing_falls_back_then_turns_off_and_deletes_the_store(self):
        self.set_key(tk.API_KEY, API_KEY)
        self.set_key(tk.TOKEN, TOKEN)
        self.gw.details.store.put(ts.store_key("movie", 2015, "Tichá hora"), "movie", 2015, "Tichá hora", None)
        resp, data = self.request("POST", {"removeKey": tk.TOKEN})
        self.assertEqual((resp.status, data["enabled"], data["keys"]["source"], data["total"]), (200, True, "apiKey", 1))
        resp, data = self.request("POST", {"removeKey": tk.API_KEY})
        self.assertEqual((resp.status, data["enabled"], data["keys"]["source"]), (200, False, None))
        self.assertIsNone(self.gw.details)
        self.assertIsNone(self.gw.tmdb_worker)
        self.assertFalse((self.dir / "state" / ts.FILE).exists())
        self.assertTrue(any("API Key removed, TMDb off, deleted 1 stored entry" in line for line in self.logs))
        self.assertTrue(any("API Read Access Token removed, in use: apiKey" in line for line in self.logs))
        self.assert_no_secret(resp.body.decode())

    def test_a_key_saved_on_admin_survives_a_restart(self):
        self.set_key(tk.TOKEN, TOKEN)
        gw, _site = self.make("")
        self.assertEqual(gw.tmdb_source, "token")
        self.assertIsNotNone(gw.details)

    def test_a_started_gateway_runs_the_worker_of_the_new_key(self):
        self.gw._started = True
        self.set_key(tk.API_KEY, API_KEY)
        first = self.gw.tmdb_worker
        self.assertTrue(first._thread.is_alive())
        self.set_key(tk.TOKEN, TOKEN)
        self.assertIsNot(self.gw.tmdb_worker, first)
        self.assertFalse(first._thread.is_alive())  # the old one stopped before the new one writes
        self.assertTrue(self.gw.tmdb_worker._thread.is_alive())
        self.request("POST", {"removeKey": tk.TOKEN})
        self.request("POST", {"removeKey": tk.API_KEY})
        self.assertIsNone(self.gw.tmdb_worker)

    def test_the_config_key_wins_and_admin_cannot_change_keys(self):
        self.set_key(tk.API_KEY, API_KEY)
        gw, self.site = self.make(CONFIG_KEY)
        self.gw = gw
        resp, data = self.request("GET")
        self.assertEqual(data["keys"], {"source": "config", "editable": False, "apiKey": "…cdef", "token": None})
        before = len(self.tmdb.requests)
        for body in ({"setKey": {"kind": tk.TOKEN, "value": TOKEN}}, {"removeKey": tk.API_KEY}):
            resp, data = self.request("POST", body)
            self.assertEqual(resp.status, 409, body)
            self.assertIn("[tmdb] key", data["error"])
        self.assertEqual(len(self.tmdb.requests), before)
        self.assertEqual(tk.KeyFile(self.dir / "state").load(), {tk.API_KEY: API_KEY})
        self.assert_no_secret(resp.body.decode())

    def test_the_admin_key_takes_over_when_the_config_key_goes(self):
        self.set_key(tk.API_KEY, API_KEY)
        gw, _site = self.make(CONFIG_KEY)
        gw.details.store.put(ts.store_key("tv", None, "Dílna"), "tv", None, "Dílna", None)
        gw, _site = self.make("")  # [tmdb] key removed from the config, a key from /admin remains
        self.assertEqual(gw.tmdb_source, "apiKey")
        self.assertEqual(len(gw.details.store), 1)  # not deleted: TMDb never went off

    def test_needs_login_header_and_same_origin(self):
        body = {"setKey": {"kind": tk.API_KEY, "value": API_KEY}}
        self.assertEqual(self.request("POST", body, headers={})[0].status, 401)
        self.assertEqual(self.request("POST", body, headers=AUTH)[0].status, 403)
        self.assertEqual(self.request("POST", body, headers={**POST, "Origin": "http://evil.example"})[0].status, 403)
        self.assertEqual(self.tmdb.requests, [])
        self.assertFalse((self.dir / "state" / tk.FILE).exists())


if __name__ == "__main__":
    unittest.main()
