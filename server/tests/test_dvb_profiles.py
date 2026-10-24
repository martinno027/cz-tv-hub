"""Profiles (spec/dvb-gateway.md "Profiles"): store, /dvb/profile* routes, the /admin tab "Profily"."""
import base64
import hashlib
import http.client
import inspect
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cztvhub.api import StreamResolver
from cztvhub.config import AdminConfig, AdsConfig, Config, DvbConfig
from cztvhub.dvb import profiles as prof
from cztvhub.dvb.admin import AdminSite
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from cztvhub.dvb.profiles import ProfileStore, normalize_name, normalize_password
from cztvhub.server import Server, make_handler
from dvb_fixtures import NOW, FakeClient

CASES = json.loads((Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "profile_cases.json")
                   .read_text(encoding="utf-8"))
KEY = "app-key-" * 5
WORKER_KEY = "worker-key-" * 4
ADMIN_PASSWORD = "admin-password-1"
APP = {"Authorization": "Bearer " + KEY}
SETTINGS = {"v": 1, "channelPrefs": {"order": ["ct:CH_2"], "hidden": [], "sourcesOff": []}, "thumbnails": False}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class GoldenCasesTest(unittest.TestCase):
    """spec/fixtures/profile_cases.json "names" and "passwords", shared with the three apps."""

    def test_names(self):
        self.assertTrue(CASES["names"])
        for case in CASES["names"]:
            got = normalize_name(case["name"])
            if case["valid"]:
                self.assertEqual(got, (case["normalized"], case["key"]), case)
            else:
                self.assertIsNone(got, case)

    def test_passwords(self):
        self.assertTrue(CASES["passwords"])
        for case in CASES["passwords"]:
            self.assertEqual(normalize_password(case["password"]) is not None, case["valid"], case)

    def test_names_beyond_the_golden_cases(self):
        self.assertEqual(normalize_name("Jana\u0301"), ("Jan\u00e1", "jana"))  # NFC: one code point
        self.assertIsNone(normalize_name("a\ud800"))  # lone surrogate: cannot be stored as UTF-8
        self.assertIsNone(normalize_name(5))
        self.assertIsNone(normalize_password("abc\ud800"))
        self.assertEqual(normalize_password("he\u0301slo"), "h\u00e9slo")  # decomposed and composed agree


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.clock = Clock()
        self.logs = []
        self.store = self.make()

    def make(self):
        return ProfileStore(self.dir, iterations=1, now=lambda: NOW + self.clock.t, clock=self.clock,
                            log=self.logs.append)

    def raw(self):
        return json.loads((self.dir / prof.FILE).read_text(encoding="utf-8"))

    def test_default_iterations(self):
        self.assertEqual(prof.ITERATIONS, 200_000)
        self.assertEqual(inspect.signature(ProfileStore).parameters["iterations"].default, 200_000)
        store = ProfileStore(self.dir / "real", log=lambda m: None)
        store.admin_create("Jana", "1234")
        stored = json.loads((self.dir / "real" / prof.FILE).read_text())["profiles"][0]
        self.assertEqual(stored["iterations"], 200_000)
        self.assertEqual(stored["hash"], hashlib.pbkdf2_hmac("sha256", b"1234", bytes.fromhex(stored["salt"]),
                                                             200_000).hex())

    def test_file_holds_no_secret_and_is_private(self):
        created = self.store.create("Jana", "tajne-heslo", SETTINGS)
        text = (self.dir / prof.FILE).read_text(encoding="utf-8")
        self.assertNotIn("tajne-heslo", text)
        self.assertNotIn(created["token"], text)
        self.assertIn(hashlib.sha256(created["token"].encode()).hexdigest(), text)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / prof.FILE).st_mode), 0o600)
        self.assertEqual(len(bytes.fromhex(self.raw()["profiles"][0]["salt"])), 16)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [prof.FILE])  # no tmp file left
        joined = "\n".join(self.logs)
        self.assertNotIn("tajne-heslo", joined)
        self.assertNotIn(created["token"], joined)

    def test_salts_differ(self):
        self.store.admin_create("Jana", "1234")
        self.store.admin_create("Petr", "1234")
        a, b = self.raw()["profiles"]
        self.assertNotEqual(a["salt"], b["salt"])
        self.assertNotEqual(a["hash"], b["hash"])

    def test_persists_across_restart(self):
        created = self.store.create("Jana", "1234", SETTINGS)
        self.store.put(created["token"], {**SETTINGS, "thumbnails": True})
        again = self.make()
        self.assertEqual(again.get(created["token"])["version"], 2)
        self.assertTrue(again.get(created["token"])["settings"]["thumbnails"])
        self.assertEqual(again.login("jána", "1234")["name"], "Jana")

    def test_failed_save_changes_nothing(self):
        created = self.store.create("Jana", "1234", SETTINGS)
        before = (self.dir / prof.FILE).read_bytes()
        self.store._unwritable = "disk full"
        with self.assertRaises(prof.ProfileError) as ctx:
            self.store.put(created["token"], {**SETTINGS, "thumbnails": True})
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(self.store.get(created["token"])["version"], 1)
        self.assertEqual((self.dir / prof.FILE).read_bytes(), before)

    def test_broken_file_is_kept_aside(self):
        (self.dir / prof.FILE).write_text("{not json")
        store = self.make()
        self.assertEqual(store.overview(), [])
        self.assertEqual((self.dir / (prof.FILE + ".broken")).read_text(), "{not json")
        self.assertTrue(any("not valid JSON" in m for m in self.logs))

    def test_malformed_entries_are_left_out(self):
        self.store.admin_create("Jana", "1234")
        data = self.raw()
        data["profiles"].append({"id": "x", "name": "Petr"})
        (self.dir / prof.FILE).write_text(json.dumps(data))
        self.assertEqual([p["name"] for p in self.make().overview()], ["Jana"])

    def test_session_cap_drops_the_one_unused_longest(self):
        tokens = []
        for i in range(prof.MAX_SESSIONS):
            self.clock.t += 10
            tokens.append(self.store.login("Jana", "1234")["token"] if i else
                          self.store.create("Jana", "1234", SETTINGS)["token"])
        self.clock.t += prof.TOUCH_SECONDS
        self.store.get(tokens[0])  # the first TV is still in use
        self.clock.t += 10
        tokens.append(self.store.login("Jana", "1234")["token"])
        self.assertEqual(self.store.overview()[0]["sessions"], prof.MAX_SESSIONS)
        self.store.get(tokens[0])
        with self.assertRaises(prof.ProfileError):
            self.store.get(tokens[1])
        self.store.get(tokens[-1])

    def test_last_used_written_at_most_hourly(self):
        token = self.store.create("Jana", "1234", SETTINGS)["token"]
        before = (self.dir / prof.FILE).read_bytes()
        self.clock.t += 60
        self.store.get(token)
        self.assertEqual((self.dir / prof.FILE).read_bytes(), before)
        self.clock.t += prof.TOUCH_SECONDS
        self.store.get(token)
        self.assertEqual(self.raw()["profiles"][0]["sessions"][0]["lastUsed"], prof.iso(NOW + self.clock.t))

    def test_parallel_wrong_logins_cannot_pass_the_limit(self):
        self.store.admin_create("Jana", "1234")
        results = []
        derive = self.store._derive

        def slow(*args):  # like PBKDF2 on the Pi: the attempts overlap
            time.sleep(0.02)
            return derive(*args)

        self.store._derive = slow

        def attempt():
            try:
                self.store.login("Jana", "wrong")
            except prof.ProfileError as exc:
                results.append(exc.status)

        threads = [threading.Thread(target=attempt) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(401), prof.FAIL_LIMIT)
        self.assertEqual(results.count(429), 20 - prof.FAIL_LIMIT)

    def test_failure_counters_are_bounded(self):
        for i in range(prof.MAX_TRACKED_NAMES + 50):
            with self.assertRaises(prof.ProfileError):
                self.store.login(f"n{i}", "1234")
        self.assertLessEqual(len(self.store._failures), prof.MAX_TRACKED_NAMES)


class GatewayProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.dir / "hls", state_dir=self.dir),
                          ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=self.dir / "breaks"),
                          admin=AdminConfig(password=ADMIN_PASSWORD))
        self.clock = Clock()
        self.logs = []
        self.store = ProfileStore(self.dir, iterations=1, now=lambda: NOW + self.clock.t, clock=self.clock,
                                  log=self.logs.append)
        hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(self.cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=self.logs.append,
                             profiles=self.store)

    def call(self, method, path, body=None, token=None, headers=None, raw=None):
        sent = dict(APP if headers is None else headers)
        if token is not None:
            sent["X-Cztvhub-Profile"] = token
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        resp = self.gw.handle(path, {}, sent, method, data)
        payload = json.loads(resp.body) if resp.body else None
        return resp, payload

    def create(self, name="Jana", password="1234", settings=SETTINGS):
        resp, body = self.call("POST", "/dvb/profiles", {"name": name, "password": password, "settings": settings})
        self.assertEqual(resp.status, 201, body)
        return body

    def login(self, name="Jana", password="1234"):
        return self.call("POST", "/dvb/profiles/login", {"name": name, "password": password})

    # -- create ----------------------------------------------------------------------------
    def test_create(self):
        body = self.create("  Jana ")
        self.assertEqual((body["name"], body["settings"], body["version"]), ("Jana", SETTINGS, 1))
        self.assertGreaterEqual(len(body["token"]), 40)
        resp, got = self.call("GET", "/dvb/profile", token=body["token"])
        self.assertEqual((resp.status, got["name"], got["version"], got["settings"]), (200, "Jana", 1, SETTINGS))
        self.assertRegex(got["updated"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_create_errors(self):
        self.create("Jana")
        for name in ("jána", "JANA"):
            resp, body = self.call("POST", "/dvb/profiles", {"name": name, "password": "1234", "settings": SETTINGS})
            self.assertEqual((resp.status, body["error"]), (409, "Profil s tímto jménem už existuje"), name)
        for payload in ({"name": "", "password": "1234", "settings": SETTINGS},
                        {"name": "Petr", "password": "123", "settings": SETTINGS},
                        {"name": "Petr", "password": "1234"},
                        {"name": "Petr", "password": "1234", "settings": {"channelPrefs": {}}},
                        {"name": "Petr", "password": "1234", "settings": {"v": "1"}},
                        {"name": "Petr", "password": "1234", "settings": {"v": True}},
                        {"name": "Petr", "password": "1234", "settings": [1]}):
            resp, body = self.call("POST", "/dvb/profiles", payload)
            self.assertEqual(resp.status, 400, payload)
            self.assertIn("error", body)
        for raw in (b"", b"[]", b"not json", b"\xff", b'{"name": NaN}', b"[" * 5000):
            resp, _ = self.call("POST", "/dvb/profiles", raw=raw)
            self.assertEqual(resp.status, 400, raw[:10])
        self.assertEqual([p["name"] for p in self.store.overview()], ["Jana"])

    def test_create_cap(self):
        for i in range(prof.MAX_PROFILES):
            self.create(f"P{i}")
        resp, body = self.call("POST", "/dvb/profiles", {"name": "Navíc", "password": "1234", "settings": SETTINGS})
        self.assertEqual(resp.status, 507)
        self.assertIn("20", body["error"])

    def test_settings_size(self):
        filler = "x" * (prof.MAX_SETTINGS - 100)
        resp, _ = self.call("POST", "/dvb/profiles",
                            {"name": "Jana", "password": "1234", "settings": {"v": 1, "f": filler}})
        self.assertEqual(resp.status, 201)
        token = json.loads(resp.body)["token"]
        resp, body = self.call("PUT", "/dvb/profile", {"settings": {"v": 1, "f": filler + "y" * 200}}, token)
        self.assertEqual(resp.status, 413, body)
        resp, body = self.call("PUT", "/dvb/profile", raw=b" " * (prof.MAX_BODY + 1), token=token)
        self.assertEqual(resp.status, 413)
        resp, body = self.call("POST", "/dvb/profiles", raw=b" " * (prof.MAX_BODY + 1))
        self.assertEqual(resp.status, 413)

    def test_unknown_fields_are_kept(self):
        settings = {"v": 2, "channelPrefs": {"order": [], "hidden": [], "sourcesOff": [], "futurePref": 3},
                    "thumbnails": True, "future": {"x": 1}}
        token = self.create(settings=settings)["token"]
        self.assertEqual(self.call("GET", "/dvb/profile", token=token)[1]["settings"], settings)

    # -- login -----------------------------------------------------------------------------
    def test_login(self):
        self.create("Jana", "heslo s mezerou")
        resp, body = self.login("JÁNA", "heslo s mezerou")
        self.assertEqual(resp.status, 200, body)
        self.assertEqual((body["name"], body["version"], body["settings"]), ("Jana", 1, SETTINGS))
        self.assertEqual(self.call("GET", "/dvb/profile", token=body["token"])[0].status, 200)

    def test_login_same_answer_for_unknown_name_and_wrong_password(self):
        self.create("Jana", "1234")
        unknown, wrong = self.login("Petr", "1234"), self.login("Jana", "12345")
        self.assertEqual(unknown[0].status, 401)
        self.assertEqual(wrong[0].status, 401)
        self.assertEqual(unknown[1], wrong[1])
        self.assertEqual(unknown[1], {"error": "Nesprávné jméno nebo heslo"})
        self.assertEqual(self.login("", "1234")[0].status, 401)  # an impossible name says nothing more

    def test_login_body_cap(self):
        self.create()
        resp, _ = self.call("POST", "/dvb/profiles/login", {"name": "Jana", "password": "1234", "x": "y" * 1024})
        self.assertEqual(resp.status, 413)
        resp, _ = self.call("POST", "/dvb/profiles/login", raw=b"[1]")
        self.assertEqual(resp.status, 400)

    def test_rate_limit(self):
        self.create("Jana", "1234")
        for _ in range(prof.FAIL_LIMIT):
            self.assertEqual(self.login("Jana", "wrong")[0].status, 401)
        for password in ("wrong", "1234"):  # locked even for the right password
            resp, body = self.login("jána", password)
            self.assertEqual((resp.status, resp.headers.get("Retry-After")), (429, "60"))
            self.assertEqual(body["error"], "Příliš mnoho pokusů, zkuste to za minutu")
        self.assertEqual(self.login("Petr", "x1234")[0].status, 401)  # another name is not affected
        self.clock.t += prof.LOCK_SECONDS - 1
        self.assertEqual(self.login("Jana", "1234")[0].status, 429)
        self.clock.t += 1
        self.assertEqual(self.login("Jana", "1234")[0].status, 200)
        # a success clears the count: four new failures do not lock
        for _ in range(prof.FAIL_LIMIT - 1):
            self.assertEqual(self.login("Jana", "wrong")[0].status, 401)
        self.assertEqual(self.login("Jana", "1234")[0].status, 200)
        self.assertTrue(any("locked for 60 s" in m for m in self.logs))

    def test_rate_limit_window(self):
        self.create("Jana", "1234")
        for _ in range(prof.FAIL_LIMIT - 1):
            self.login("Jana", "wrong")
        self.clock.t += prof.FAIL_WINDOW + 1  # the old failures are out of the 10-minute window
        self.assertEqual(self.login("Jana", "wrong")[0].status, 401)
        self.assertEqual(self.login("Jana", "wrong")[0].status, 401)

    def test_rate_limit_after_the_lock(self):
        self.create("Jana", "1234")
        for _ in range(prof.FAIL_LIMIT):
            self.login("Jana", "wrong")
        self.clock.t += prof.LOCK_SECONDS
        self.assertEqual(self.login("Jana", "wrong")[0].status, 401)  # one try, then locked again
        self.assertEqual(self.login("Jana", "1234")[0].status, 429)

    # -- session ---------------------------------------------------------------------------
    def test_put_last_write_wins(self):
        a = self.create()["token"]
        b = self.login()[1]["token"]
        resp, body = self.call("PUT", "/dvb/profile", {"settings": {**SETTINGS, "thumbnails": True}}, a)
        self.assertEqual((resp.status, body["version"]), (200, 2))
        resp, body = self.call("PUT", "/dvb/profile", {"settings": {"v": 1, "from": "b"}}, b)
        self.assertEqual(body["version"], 3)
        got = self.call("GET", "/dvb/profile", token=a)[1]
        self.assertEqual((got["version"], got["settings"]), (3, {"v": 1, "from": "b"}))
        self.assertEqual(self.call("PUT", "/dvb/profile", {"thumbnails": True}, a)[0].status, 400)

    def test_signed_out_tokens(self):
        token = self.create()["token"]
        for bad in (None, "", "nope", token + "x", "x" * 500):
            for method, path, body in (("GET", "/dvb/profile", None), ("PUT", "/dvb/profile", {"settings": SETTINGS}),
                                       ("POST", "/dvb/profile/logout", None)):
                resp, payload = self.call(method, path, body, bad)
                self.assertEqual((resp.status, payload), (401, {"error": "Profil byl odhlášen",
                                                                 "code": "profileSignedOut"}), (bad, path))
                self.assertNotIn("WWW-Authenticate", resp.headers)
        self.assertEqual(self.call("PUT", "/dvb/profile", raw=b"junk", token="nope")[0].status, 401)

    def test_logout_ends_this_session_only(self):
        a = self.create()["token"]
        b = self.login()[1]["token"]
        resp, _ = self.call("POST", "/dvb/profile/logout", token=a)
        self.assertEqual((resp.status, resp.body), (204, b""))
        self.assertEqual(self.call("GET", "/dvb/profile", token=a)[0].status, 401)
        self.assertEqual(self.call("GET", "/dvb/profile", token=b)[0].status, 200)

    # -- access ----------------------------------------------------------------------------
    def test_app_key_required(self):
        token = self.create()["token"]
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            resp, body = self.call("POST", "/dvb/profiles", {"name": "J", "password": "1234", "settings": SETTINGS},
                                   headers=headers)
            self.assertEqual(resp.status, 401)
            # a wrong app key is not "signed out": the apps keep the profile and show a gateway error
            resp, body = self.call("GET", "/dvb/profile", token=token, headers=headers)
            self.assertEqual(resp.status, 401)
            self.assertNotIn("code", body)
            self.assertIn("Bearer", resp.headers["WWW-Authenticate"])
        self.assertEqual([p["name"] for p in self.store.overview()], ["Jana"])

    def test_cookie_key_is_not_enough(self):
        # the cookie is for players only; a header needs a CORS preflight, which this server never grants
        resp, _ = self.call("POST", "/dvb/profiles", {"name": "J", "password": "1234", "settings": SETTINGS},
                            headers={"Cookie": "cztvhub_key=" + KEY})
        self.assertEqual(resp.status, 403)
        self.assertEqual(self.store.overview(), [])

    def test_worker_key_refused(self):
        token = self.create()["token"]
        worker = {"Authorization": "Bearer " + WORKER_KEY}
        for method, path in (("POST", "/dvb/profiles"), ("POST", "/dvb/profiles/login"), ("GET", "/dvb/profile"),
                             ("PUT", "/dvb/profile"), ("POST", "/dvb/profile/logout")):
            resp, _ = self.call(method, path, {"name": "Jana", "password": "1234"}, token, headers=worker)
            self.assertEqual(resp.status, 403, path)

    def test_unknown_paths_and_methods(self):
        self.assertEqual(self.call("GET", "/dvb/profiles")[0].status, 405)
        self.assertEqual(self.call("DELETE", "/dvb/profile")[0].status, 405)
        self.assertEqual(self.call("GET", "/dvb/profiles/x")[0].status, 404)

    def test_no_secret_in_logs(self):
        token = self.create("Jana", "tajne-heslo")["token"]
        for _ in range(prof.FAIL_LIMIT):
            self.login("Jana", "spatne-heslo")
        joined = "\n".join(self.logs)
        for secret in ("tajne-heslo", "spatne-heslo", token, hashlib.sha256(token.encode()).hexdigest()):
            self.assertNotIn(secret, joined)

    # -- /admin tab "Profily" --------------------------------------------------------------
    def admin(self, method, path, body=None, headers=None):
        site = AdminSite(self.cfg, self.gw, admin=object())
        auth = {"Authorization": "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()}
        sent = {**auth, "X-Cztvhub-Admin": "1", "Host": "pi:8098"} if headers is None else headers
        resp = site.handle(method, path, sent, json.dumps(body).encode() if body is not None else b"")
        return resp, json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body

    def test_admin_overview(self):
        self.create("Jana")
        self.login()
        resp, body = self.admin("GET", "/admin/api/profiles")
        self.assertEqual(resp.status, 200)
        (row,) = body["profiles"]
        self.assertEqual((row["name"], row["sessions"]), ("Jana", 2))
        self.assertEqual(set(row), {"id", "name", "created", "updated", "sessions"})  # nothing secret
        page = self.admin("GET", "/admin")[1].decode("utf-8")
        self.assertIn("Profily", page)
        self.assertIn("/admin/api/profiles", page)

    def test_admin_needs_login_and_csrf_header(self):
        self.assertEqual(self.admin("GET", "/admin/api/profiles", headers={})[0].status, 401)
        self.assertEqual(self.admin("GET", "/admin/api/profiles", headers=APP)[0].status, 401)  # app key: no
        auth = {"Authorization": "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()}
        for headers in (auth, {**auth, "X-Cztvhub-Admin": "1", "Host": "pi:8098", "Origin": "http://evil"}):
            resp, _ = self.admin("POST", "/admin/api/profiles", {"name": "Jana", "password": "1234"}, headers)
            self.assertEqual(resp.status, 403)
        self.assertEqual(self.store.overview(), [])
        resp, _ = self.admin("POST", "/admin/api/profiles", {"name": "Jana", "password": "1234"},
                             {**auth, "X-Cztvhub-Admin": "1", "Host": "pi:8098", "Origin": "http://pi:8098"})
        self.assertEqual(resp.status, 201)

    def test_admin_create(self):
        resp, body = self.admin("POST", "/admin/api/profiles", {"name": " Petr ", "password": "admin-dal-heslo"})
        self.assertEqual((resp.status, body), (201, {"name": "Petr"}))
        resp, login = self.login("Petr", "admin-dal-heslo")
        self.assertEqual((resp.status, login["settings"], login["version"]), (200, prof.DEFAULT_SETTINGS, 1))
        self.assertEqual(self.admin("POST", "/admin/api/profiles", {"name": "petr", "password": "1234"})[0].status,
                         409)
        self.assertEqual(self.admin("POST", "/admin/api/profiles", {"name": "Eva", "password": "12"})[0].status, 400)
        self.assertIn("admin: profile Petr: created", self.logs)
        self.assertFalse(any("admin-dal-heslo" in m for m in self.logs))

    def test_admin_new_password_ends_all_sessions(self):
        a = self.create("Jana", "1234")["token"]
        b = self.login()[1]["token"]
        pid = self.store.overview()[0]["id"]
        resp, _ = self.admin("POST", f"/admin/api/profiles/{pid}", {"password": "nove-heslo"})
        self.assertEqual(resp.status, 200)
        for token in (a, b):
            self.assertEqual(self.call("GET", "/dvb/profile", token=token)[0].status, 401)
        self.assertEqual(self.login("Jana", "1234")[0].status, 401)
        self.assertEqual(self.login("Jana", "nove-heslo")[0].status, 200)
        self.assertEqual(self.admin("POST", f"/admin/api/profiles/{pid}", {"password": "x"})[0].status, 400)
        self.assertIn("admin: profile Jana: new password", self.logs)
        self.assertFalse(any("nove-heslo" in m for m in self.logs))

    def test_admin_new_password_lifts_the_lock(self):
        self.create("Jana", "1234")
        for _ in range(prof.FAIL_LIMIT):
            self.login("Jana", "wrong")
        pid = self.store.overview()[0]["id"]
        self.admin("POST", f"/admin/api/profiles/{pid}", {"password": "nove-heslo"})
        self.assertEqual(self.login("Jana", "nove-heslo")[0].status, 200)

    def test_admin_delete(self):
        token = self.create("Jana")["token"]
        self.create("Petr")
        pid = self.store.overview()[0]["id"]
        self.assertEqual(self.admin("POST", f"/admin/api/profiles/{pid}", {"delete": "yes"})[0].status, 400)
        resp, body = self.admin("POST", f"/admin/api/profiles/{pid}", {"delete": True})
        self.assertEqual((resp.status, body), (200, {"name": "Jana"}))
        self.assertEqual(self.call("GET", "/dvb/profile", token=token)[0].status, 401)
        self.assertEqual(self.login("Jana", "1234")[0].status, 401)
        self.assertEqual([p["name"] for p in self.store.overview()], ["Petr"])
        self.assertNotIn("Jana", (self.dir / prof.FILE).read_text(encoding="utf-8"))
        self.assertEqual(self.admin("POST", f"/admin/api/profiles/{pid}", {"delete": True})[0].status, 404)
        self.assertEqual(self.admin("POST", "/admin/api/profiles/zzz", {"delete": True})[0].status, 405)
        self.assertIn("admin: profile Jana: deleted", self.logs)
        self.create("Jana")  # the name is free again


class ProfilesHttpTest(unittest.TestCase):
    """Through the real HTTP handler: POST reaches /dvb/, oversized bodies are refused unread."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=Path(tmp.name) / "hls", state_dir=Path(tmp.name)))
        store = ProfileStore(Path(tmp.name), iterations=1, log=lambda m: None)
        hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        gw = DvbGateway(cfg, client=FakeClient(), hls=hls, log=lambda m: None, profiles=store)
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda c: "x"), gw, None))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def call(self, method, path, body=b"", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers={**APP, **(headers or {})})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_flow(self):
        status, data = self.call("POST", "/dvb/profiles",
                                 json.dumps({"name": "Jana", "password": "1234", "settings": SETTINGS}).encode())
        self.assertEqual(status, 201, data)
        token = json.loads(data)["token"]
        status, data = self.call("PUT", "/dvb/profile", json.dumps({"settings": SETTINGS}).encode(),
                                 {"X-Cztvhub-Profile": token})
        self.assertEqual((status, json.loads(data)["version"]), (200, 2))
        self.assertEqual(self.call("GET", "/dvb/profile", headers={"X-Cztvhub-Profile": token})[0], 200)
        self.assertEqual(self.call("POST", "/dvb/profile/logout", headers={"X-Cztvhub-Profile": token})[0], 204)

    def test_oversized_body(self):
        status, data = self.call("PUT", "/dvb/profile", b"x" * (prof.MAX_BODY + 1))
        self.assertEqual(status, 413)
        self.assertIn("error", json.loads(data))
        self.assertEqual(self.call("POST", "/playlist.m3u", b"{}")[0], 400)


if __name__ == "__main__":
    unittest.main()
