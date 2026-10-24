"""Household default channel list (spec/dvb-gateway.md "Household default channel list"): store, GET /dvb/defaults,
the /admin tab "Výchozí kanály" and "Použít jako výchozí" on the tab "Profily"."""
import base64
import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from cztvhub import channels as ct_channels
from cztvhub.api import StreamResolver
from cztvhub.config import AdminConfig, AdsConfig, Config, DvbConfig
from cztvhub.dvb import defaults as dflt
from cztvhub.dvb.admin import AdminSite
from cztvhub.dvb.defaults import DefaultsStore
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from cztvhub.dvb.profiles import ProfileError, ProfileStore
from cztvhub.server import Server, make_handler
from dvb_fixtures import NOW, FakeClient

PSP = json.loads((Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "psp_cases.json")
                 .read_text(encoding="utf-8"))
KEY = "app-key-" * 5
WORKER_KEY = "worker-key-" * 4
ADMIN_PASSWORD = "admin-password-1"
APP = {"Authorization": "Bearer " + KEY}
BASIC = {"Authorization": "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()}
ADMIN_POST = {**BASIC, "X-Cztvhub-Admin": "1", "Host": "pi:8098"}
V = NOW  # the first version: max(0 + 1, now in epoch seconds)
PREFS = {"order": ["psp:stream2", "ct:CH_24", "dvb:gone"], "hidden": ["ct:CH_5"], "sourcesOff": ["psp"]}


class PspCatalogueTest(unittest.TestCase):
    def test_server_copy_equals_the_shared_catalogue(self):
        self.assertEqual([(s["key"], s["name"]) for s in PSP["streams"]], list(dflt.PSP_STREAMS))
        self.assertEqual(len(dflt.PSP_STREAMS), 5)


class CheckPrefsTest(unittest.TestCase):
    def bad(self, value, status=400):
        with self.assertRaises(ProfileError) as ctx:
            dflt.check_prefs(value)
        self.assertEqual(ctx.exception.status, status, value)

    def test_shape(self):
        self.assertEqual(dflt.check_prefs(PREFS), PREFS)
        self.assertEqual(dflt.check_prefs({"order": [], "hidden": []}), {"order": [], "hidden": []})  # as old apps
        kept = {"order": [], "hidden": [], "sourcesOff": [], "future": {"x": 1}}
        self.assertEqual(dflt.check_prefs(kept), kept)  # fields of a newer app stay as they came
        for value in (None, [], "x", {"order": []}, {"hidden": []}, {"order": "ct:CH_1", "hidden": []},
                      {"order": [], "hidden": [], "sourcesOff": "psp"}, {"order": [], "hidden": None}):
            self.bad(value)

    def test_keys(self):
        for item in (1, None, "", "x" * (dflt.MAX_KEY + 1), "ct:\n1", "ct:\ud800", ["ct:CH_1"]):
            for name in dflt.LISTS:
                self.bad({"order": [], "hidden": [], name: [item]})
        dflt.check_prefs({"order": ["x" * dflt.MAX_KEY], "hidden": [], "sourcesOff": []})

    def test_500_keys_each(self):
        for name in dflt.LISTS:
            ok = {"order": [], "hidden": [], name: [f"dvb:{i}" for i in range(dflt.MAX_KEYS)]}
            dflt.check_prefs(ok)
            self.bad({"order": [], "hidden": [], name: [f"dvb:{i}" for i in range(dflt.MAX_KEYS + 1)]})

    def test_size(self):
        self.bad({"order": [], "hidden": [], "pad": "x" * dflt.MAX_PREFS}, 413)


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.logs = []
        self.store = self.make()

    def make(self):
        return DefaultsStore(self.dir, now=lambda: NOW, log=self.logs.append)

    def test_nothing_set(self):
        self.assertEqual(self.store.get(), {"channelPrefs": None, "version": 0, "updated": None})
        self.assertFalse((self.dir / dflt.FILE).exists())

    def test_version_grows_with_save_and_clear(self):
        self.assertEqual(self.store.set(PREFS)["version"], V)
        self.assertEqual(self.store.set(PREFS)["version"], V + 1)  # the same list again is still a new version
        cleared = self.store.set(None)
        self.assertEqual((cleared["channelPrefs"], cleared["version"]), (None, V + 2))
        self.assertEqual(json.loads((self.dir / dflt.FILE).read_text(encoding="utf-8"))["version"], V + 2)
        self.assertEqual(self.store.set(PREFS)["version"], V + 3)

    def test_persists_across_restart_and_is_private(self):
        doc = self.store.set(PREFS)
        self.assertEqual(doc["updated"], "2026-10-18T05:30:00Z")
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / dflt.FILE).st_mode), 0o600)
        self.assertEqual(self.make().get(), doc)
        self.assertEqual(self.make().set(None)["version"], V + 1)
        self.assertEqual(self.make().get()["version"], V + 1)
        self.assertFalse((self.dir / (dflt.FILE + ".tmp")).exists())

    def test_returned_documents_are_copies(self):
        prefs = json.loads(json.dumps(PREFS))
        self.store.set(prefs)
        prefs["order"].append("ct:CH_1")
        self.store.get()["channelPrefs"]["hidden"].append("ct:CH_1")
        self.assertEqual(self.store.get()["channelPrefs"], PREFS)

    def test_invalid_document_changes_nothing(self):
        self.store.set(PREFS)
        with self.assertRaises(ProfileError):
            self.store.set({"order": "x", "hidden": []})
        self.assertEqual(self.make().get()["version"], V)

    def test_failed_save_changes_nothing(self):
        self.store.set(PREFS)
        self.store._unwritable = "read-only"
        with self.assertRaises(ProfileError) as ctx:
            self.store.set(None)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(self.store.get()["version"], V)
        self.assertTrue(any("cannot save the household default" in m for m in self.logs))

    def test_version_never_goes_back(self):
        clock = [float(NOW)]
        (self.dir / dflt.FILE).write_text(json.dumps({"channelPrefs": None, "version": NOW + 500}), encoding="utf-8")
        store = DefaultsStore(self.dir, now=lambda: clock[0], log=self.logs.append)
        self.assertEqual(store.set(PREFS)["version"], NOW + 501)  # a version ahead of the clock still grows
        self.assertEqual(store.set(PREFS)["version"], NOW + 502)  # several in the same second too
        clock[0] += 10000.7
        self.assertEqual(store.set(None)["version"], NOW + 10000)  # then the clock again (whole seconds)

    def test_broken_file_then_a_set_beats_every_earlier_version(self):
        clock = [float(NOW)]
        DefaultsStore(self.dir, now=lambda: clock[0] - 86400, log=self.logs.append).set(PREFS)  # yesterday's version
        before = json.loads((self.dir / dflt.FILE).read_text(encoding="utf-8"))["version"]
        (self.dir / dflt.FILE).write_text("{nope", encoding="utf-8")
        store = DefaultsStore(self.dir, now=lambda: clock[0], log=self.logs.append)
        self.assertEqual(store.get()["version"], 0)
        first = store.set(PREFS)["version"]
        self.assertGreaterEqual(first, NOW)
        self.assertGreater(first, before)
        second = store.set(PREFS)["version"]  # the same second
        self.assertGreater(second, first)

    def test_broken_file_is_kept_aside(self):
        (self.dir / dflt.FILE).write_text("{nope", encoding="utf-8")
        self.assertEqual(self.make().get()["version"], 0)
        self.assertTrue((self.dir / (dflt.FILE + ".broken")).exists())

    def test_malformed_values_are_not_guessed(self):
        (self.dir / dflt.FILE).write_text(json.dumps({"channelPrefs": {"order": 5}, "version": 7, "updated": 3}),
                                          encoding="utf-8")
        self.assertEqual(self.make().get(), {"channelPrefs": None, "version": 7, "updated": None})
        (self.dir / dflt.FILE).write_text(json.dumps({"channelPrefs": None, "version": True}), encoding="utf-8")
        self.assertEqual(self.make().get()["version"], 0)


class GatewayDefaultsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.dir / "hls", state_dir=self.dir),
                          ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=self.dir / "breaks"),
                          admin=AdminConfig(password=ADMIN_PASSWORD))
        self.logs = []
        self.client = FakeClient()
        self.profiles = ProfileStore(self.dir, iterations=1, now=lambda: NOW, log=self.logs.append)
        self.defaults = DefaultsStore(self.dir, now=lambda: NOW, log=self.logs.append)
        hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(self.cfg, client=self.client, hls=hls, now=lambda: NOW, log=self.logs.append,
                             profiles=self.profiles, defaults=self.defaults)
        self.site = AdminSite(self.cfg, self.gw, admin=object())

    def get(self, headers=APP, method="GET"):
        resp = self.gw.handle("/dvb/defaults", {}, headers, method)
        return resp, json.loads(resp.body)

    def admin(self, method, path, body=None, headers=ADMIN_POST, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        resp = self.site.handle(method, path, headers, data)
        return resp, json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body

    def profile(self, name="Jana", prefs=None):
        settings = {"v": 1, "channelPrefs": prefs if prefs is not None else PREFS, "thumbnails": True}
        token = self.profiles.create(name, "1234", settings)["token"]
        pid = next(p["id"] for p in self.profiles.overview() if p["name"] == name)
        return pid, token

    # -- GET /dvb/defaults -----------------------------------------------------------------
    def test_get(self):
        resp, body = self.get()
        self.assertEqual((resp.status, body), (200, {"channelPrefs": None, "version": 0, "updated": None}))
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.defaults.set(PREFS)
        self.assertEqual(self.get()[1], {"channelPrefs": PREFS, "version": V, "updated": "2026-10-18T05:30:00Z"})

    def test_get_needs_the_app_key(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}, BASIC):
            resp, _ = self.get(headers)
            self.assertEqual(resp.status, 401, headers)
        resp, body = self.get({"Authorization": "Bearer " + WORKER_KEY})
        self.assertEqual(resp.status, 403)
        self.assertEqual(self.get({"Cookie": "cztvhub_key=" + KEY})[0].status, 200)  # as every app GET
        self.assertEqual(self.get(method="POST")[0].status, 405)
        self.assertEqual(self.get(method="PUT")[0].status, 405)

    def test_get_with_dvb_off(self):
        self.cfg.dvb.enabled = False
        self.assertEqual(self.get()[0].status, 404)

    # -- /admin tab "Výchozí kanály" -------------------------------------------------------
    def test_admin_overview_lists_every_known_channel(self):
        resp, body = self.admin("GET", "/admin/api/defaults")
        self.assertEqual(resp.status, 200)
        self.assertEqual((body["channelPrefs"], body["version"], body["dvbError"]), (None, 0, None))
        keys = [c["key"] for c in body["channels"]]
        tvh = json.loads(self.gw.channels().body)["channels"]
        self.assertTrue(tvh)
        self.assertEqual(keys, [f"ct:{c.id}" for c in ct_channels.CHANNELS] + [f"dvb:{c['id']}" for c in tvh]
                         + [s["key"] for s in PSP["streams"]])
        self.assertEqual({c["source"] for c in body["channels"]}, {"ct", "dvb", "psp"})
        self.assertEqual(body["channels"][0], {"key": "ct:CH_1", "name": "ČT1", "source": "ct"})
        self.assertEqual(body["channels"][-1], {"key": "psp:stream5", "name": "Výbory a semináře 3", "source": "psp"})
        page = self.admin("GET", "/admin")[1].decode("utf-8")
        for text in ("Výchozí kanály", "/admin/api/defaults", "Zrušit výchozí", "Použít jako výchozí", "zobrazovat",
                     "Poslanecká sněmovna", "▲", "▼"):
            self.assertIn(text, page)

    def test_admin_overview_without_tvheadend(self):
        self.client.unreachable = True
        resp, body = self.admin("GET", "/admin/api/defaults")
        self.assertEqual(resp.status, 200)
        self.assertTrue(body["dvbError"])
        self.assertEqual([c["source"] for c in body["channels"]], ["ct"] * 6 + ["psp"] * 5)

    def test_admin_save_and_clear(self):
        resp, body = self.admin("POST", "/admin/api/defaults", {"channelPrefs": PREFS})
        self.assertEqual((resp.status, body["channelPrefs"], body["version"]), (200, PREFS, V))
        self.assertEqual(self.get()[1]["channelPrefs"]["order"], ["psp:stream2", "ct:CH_24", "dvb:gone"])  # kept
        resp, body = self.admin("POST", "/admin/api/defaults", {"clear": True})
        self.assertEqual((resp.status, body["channelPrefs"], body["version"]), (200, None, V + 1))
        self.assertEqual(self.get()[1]["version"], V + 1)
        self.assertEqual(self.admin("GET", "/admin/api/defaults")[1]["version"], V + 1)
        self.assertIn("admin: defaults: saved", self.logs)
        self.assertIn("admin: defaults: cleared", self.logs)

    def test_admin_errors(self):
        for body in ({}, {"clear": "yes"}, {"channelPrefs": None}, {"channelPrefs": {"order": []}},
                     {"channelPrefs": {"order": [1], "hidden": []}}):
            resp, _ = self.admin("POST", "/admin/api/defaults", body)
            self.assertEqual(resp.status, 400, body)
        for raw in (b"", b"junk", b"[]", b'{"channelPrefs": {"order": [NaN], "hidden": []}}',
                    b'{"channelPrefs": {"order": ["\\ud800"], "hidden": []}}',
                    b'{"channelPrefs": ' + b"[" * 100000 + b"]" * 100000 + b"}"):
            resp, _ = self.admin("POST", "/admin/api/defaults", raw=raw)
            self.assertIn(resp.status, (400, 413), raw[:40])
        self.assertEqual(self.admin("POST", "/admin/api/defaults", raw=b"[" * 30000 + b"]" * 30000)[0].status, 400)
        big = {"channelPrefs": {"order": [], "hidden": [], "pad": "x" * dflt.MAX_PREFS}}
        self.assertEqual(self.admin("POST", "/admin/api/defaults", big)[0].status, 413)
        self.assertEqual(self.admin("POST", "/admin/api/defaults", raw=b" " * (dflt.MAX_BODY + 1))[0].status, 413)
        self.assertEqual(self.admin("PUT", "/admin/api/defaults", {"clear": True})[0].status, 405)
        self.assertEqual(self.get()[1]["version"], 0)  # nothing was saved
        self.assertFalse(any("admin: defaults" in m for m in self.logs))

    def test_admin_needs_login_and_same_origin(self):
        for headers in ({}, APP, {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}):
            self.assertEqual(self.admin("GET", "/admin/api/defaults", headers=headers)[0].status, 401)
            self.assertEqual(self.admin("POST", "/admin/api/defaults", {"clear": True}, headers)[0].status, 401)
        for headers in (BASIC, {**ADMIN_POST, "Origin": "http://evil.example"}):
            self.assertEqual(self.admin("POST", "/admin/api/defaults", {"channelPrefs": PREFS}, headers)[0].status,
                             403)
        pid, _ = self.profile()
        self.assertEqual(self.admin("POST", f"/admin/api/profiles/{pid}", {"makeDefault": True}, BASIC)[0].status,
                         403)
        self.assertEqual(self.get()[1]["version"], 0)
        resp, _ = self.admin("POST", "/admin/api/defaults", {"channelPrefs": PREFS},
                             {**ADMIN_POST, "Origin": "http://pi:8098"})
        self.assertEqual(resp.status, 200)

    # -- "Použít jako výchozí" -------------------------------------------------------------
    def test_copy_from_profile_stays_unlinked(self):
        pid, token = self.profile("Jana")
        resp, body = self.admin("POST", f"/admin/api/profiles/{pid}", {"makeDefault": True})
        self.assertEqual((resp.status, body["name"], body["channelPrefs"], body["version"]), (200, "Jana", PREFS, V))
        self.assertIn("admin: defaults: copied from profile Jana", self.logs)
        changed = {"v": 1, "channelPrefs": {"order": ["ct:CH_6"], "hidden": [], "sourcesOff": []}, "thumbnails": False}
        self.profiles.put(token, changed)
        self.assertEqual(self.profiles.get(token)["settings"], changed)
        self.assertEqual(self.get()[1]["channelPrefs"], PREFS)  # the profile's later change does not reach it
        self.assertEqual(self.get()[1]["version"], V)
        self.profiles.delete(pid)
        self.assertEqual(self.get()[1]["channelPrefs"], PREFS)  # nor does deleting it
        stored = json.loads((self.dir / dflt.FILE).read_text(encoding="utf-8"))
        self.assertEqual(set(stored), {"channelPrefs", "version", "updated"})  # no profile id, name or link

    def test_copy_takes_only_the_channel_list(self):
        pid, _ = self.profile("Jana")
        self.admin("POST", "/admin/api/defaults", {"channelPrefs": {"order": [], "hidden": []}})
        body = self.admin("POST", f"/admin/api/profiles/{pid}", {"makeDefault": True})[1]
        self.assertEqual(body["version"], V + 1)
        self.assertNotIn("thumbnails", self.get()[1]["channelPrefs"])

    def test_copy_errors(self):
        self.assertEqual(self.admin("POST", "/admin/api/profiles/0123456789abcdef", {"makeDefault": True})[0].status,
                         404)
        pid, _ = self.profile("Jana", prefs={"order": "broken", "hidden": []})
        self.assertEqual(self.admin("POST", f"/admin/api/profiles/{pid}", {"makeDefault": True})[0].status, 400)
        token = self.profiles.create("Petr", "1234", {"v": 1})["token"]
        self.assertTrue(token)
        petr = next(p["id"] for p in self.profiles.overview() if p["name"] == "Petr")
        resp, body = self.admin("POST", f"/admin/api/profiles/{petr}", {"makeDefault": True})
        self.assertEqual(resp.status, 400)
        self.assertIn("Petr", body["error"])
        self.assertEqual(self.admin("POST", f"/admin/api/profiles/{petr}", {"makeDefault": "yes"})[0].status, 400)
        self.assertEqual(self.get()[1]["version"], 0)
        self.assertFalse(any("admin: defaults" in m for m in self.logs))


class DefaultsHttpTest(unittest.TestCase):
    """Through the real HTTP handler: the GET and the admin POST with a full-size document."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=Path(tmp.name) / "hls", state_dir=Path(tmp.name)),
                     admin=AdminConfig(password=ADMIN_PASSWORD))
        hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        gw = DvbGateway(cfg, client=FakeClient(), hls=hls, log=lambda m: None,
                        profiles=ProfileStore(Path(tmp.name), iterations=1, log=lambda m: None))
        site = AdminSite(cfg, gw, admin=object())
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda c: "x"), gw, site))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def call(self, method, path, body=b"", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_flow(self):
        prefs = {"order": [f"dvb:{i:036d}" for i in range(dflt.MAX_KEYS)], "hidden": [], "sourcesOff": []}
        body = json.dumps({"channelPrefs": prefs}).encode()
        self.assertGreater(len(body), 20000)
        status, data = self.call("POST", "/admin/api/defaults", body,
                                 {**ADMIN_POST, "Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"})
        self.assertEqual(status, 200, data)
        status, data = self.call("GET", "/dvb/defaults", headers=APP)
        self.assertEqual((status, json.loads(data)["channelPrefs"]), (200, prefs))


if __name__ == "__main__":
    unittest.main()
