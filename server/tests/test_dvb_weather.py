"""Weather for Domů (spec/dvb-gateway.md "Weather", T59): the rules of spec/fixtures/weather_cases.json, the
/dvb/weather route (validation, household fallback, cache, limits, failures, answer shape), the household place on
/admin, ČHMÚ warnings (listing, CAP parsing, matching by ORP) and that coordinates never reach a log line.
Open-Meteo and ČHMÚ are fake ``urlopen``s; nothing here touches the network."""
import base64
import io
import json
import math
import os
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from cztvhub import logsafe
from cztvhub.config import AdminConfig, AdsConfig, Config, DvbConfig
from cztvhub.dvb import orp
from cztvhub.dvb import warnings as cap
from cztvhub.dvb import weather as wx
from cztvhub.dvb.admin import PAGE, AdminSite
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from dvb_fixtures import FakeClient

SPEC = Path(__file__).resolve().parents[2] / "spec" / "fixtures"
CASES = json.loads((SPEC / "weather_cases.json").read_text(encoding="utf-8"))
SHARED = {name: json.loads((SPEC / "dvb" / f"dvb_weather_{name}.json").read_text(encoding="utf-8"))
          for name in ("ok", "off", "error")}
CHMI = Path(__file__).resolve().parent / "fixtures" / "chmi"
KEY = "app-key-" * 5
WORKER_KEY = "worker-key-" * 4
ADMIN_PASSWORD = "admin-password-1"
APP = {"Authorization": "Bearer " + KEY}
WORKER = {"Authorization": "Bearer " + WORKER_KEY}
BASIC = {"Authorization": "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()}
ADMIN_POST = {**BASIC, "X-Cztvhub-Admin": "1", "Host": "pi:8098"}
NOW = datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc).timestamp()  # 14:00 Prague, inside the synthetic CAP
# Every way the test coordinates could be written; none may appear in a log line or an error text
COORDINATE_TEXTS = ("50.1", "14.4", "50.08", "14.44", "50,1", "14,4", "49.5", "16.0", "16.3", "49.6")

# Synthetic Open-Meteo answers in Open-Meteo's shape, made so that they give dvb_weather_ok.json
OK_DAYS = SHARED["ok"]["body"]["days"]
FORECAST = {
    "latitude": 50.12, "longitude": 14.42, "timezone": "Europe/Prague",
    "current": {"time": "2026-11-14T14:00", "interval": 900, "temperature_2m": 8.4, "weather_code": 2,
                "wind_speed_10m": 12.0},
    "daily": {"time": [d["date"] for d in OK_DAYS], "weather_code": [d["code"] for d in OK_DAYS],
              "temperature_2m_max": [d["max"] for d in OK_DAYS], "temperature_2m_min": [d["min"] for d in OK_DAYS],
              "precipitation_probability_max": [d["rainChance"] for d in OK_DAYS],
              "sunrise": [f"{d['date']}T07:13" for d in OK_DAYS], "sunset": [f"{d['date']}T16:19" for d in OK_DAYS]},
}
AIR = {"latitude": 50.1, "longitude": 14.4,
       "current": {"time": "2026-11-14T14:00", "european_aqi": 45, "alder_pollen": 5.0, "birch_pollen": 0.0,
                   "grass_pollen": 0.0, "mugwort_pollen": 0.0, "ragweed_pollen": None}}


class FakeHttp:
    """Stands in for urllib's opener: answers by host + path, records every request."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        self.errors = {}
        self.gate = None  # an Event the fake waits on (one request at a time)
        self.active = self.most = 0
        self._lock = threading.Lock()

    def add(self, url, payload, status=200):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.routes[url] = (status, body)

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        url = request.full_url.partition("?")[0]
        with self._lock:
            self.active += 1
            self.most = max(self.most, self.active)
        try:
            if self.gate is not None:
                self.gate.wait(5)
            if url in self.errors:
                raise self.errors[url]
            found = self.routes.get(url)
            if found is None:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            return FakeResponse(*found)
        finally:
            with self._lock:
                self.active -= 1

    def urls(self):
        return [r.full_url.partition("?")[0] for r in self.requests]

    def query(self, index):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.requests[index].full_url).query))


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.headers = status, {"Content-Type": "application/json"}
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def open_meteo(forecast=FORECAST, air=AIR):
    http = FakeHttp()
    http.add(wx.FORECAST_URL, forecast)
    http.add(wx.AIR_URL, air)
    return http


def chmi(listing="listing.html", file="cap_warnings.xml", name="alert_cap_50_140904.xml"):
    http = FakeHttp()
    http.add(cap.LISTING_URL, (CHMI / listing).read_bytes())
    http.add(cap.LISTING_URL + name, (CHMI / file).read_bytes())
    return http


# -- the shared rules ---------------------------------------------------------------------------
class SharedRulesTest(unittest.TestCase):
    def test_wmo_table_equals_the_fixture(self):
        known = {c["code"]: (c["text"], c["icon"]) for c in CASES["wmo"] if c["text"] is not None}
        self.assertEqual(wx.WMO, known)
        for case in CASES["wmo"]:
            self.assertEqual(wx.wmo(case["code"]), (case["text"], case["icon"]), case)
        self.assertEqual(wx.wmo(None), (None, "cloud"))
        self.assertEqual(wx.wmo(True), (None, "cloud"))

    def test_rounding_and_bounds(self):
        for case in CASES["round"]:
            out = wx.round_place(case["lat"], case["lon"])
            self.assertEqual(list(out) if out else None, case["out"], case)
            # as a query sends them: strings
            out = wx.round_place(str(case["lat"]), str(case["lon"]))
            self.assertEqual(list(out) if out else None, case["out"], case)

    def test_rounding_is_half_away_from_zero_as_written(self):
        self.assertEqual(wx.round_place(49.95, 15.25), (50.0, 15.3))  # 49.95 is stored as 49.9500000000000028…
        self.assertEqual(wx.round_place(50.05, 14.35), (50.1, 14.4))  # 14.35 is stored below …35
        self.assertEqual(wx.round_place(50.04999, 14.44999), (50.0, 14.4))

    def test_odd_inputs_are_refused(self):
        for lat in (None, True, [], "", " ", "5_0.1", "50.1e0", "0x32", "1e400", float("nan"), float("inf"),
                    "50.1; drop", "５０.１"):
            self.assertIsNone(wx.round_place(lat, 14.4), repr(lat))
        self.assertEqual(wx.round_place(" 50.1 ", "14.4"), (50.1, 14.4))
        self.assertEqual(wx.round_place(50, 14), (50.0, 14.0))

    def test_aqi_texts(self):
        for case in CASES["aqi"]:
            self.assertEqual(wx.aqi_text(case["value"]), case["text"], case)
        self.assertIsNone(wx.aqi_text(float("nan")))

    def test_pollen_tables_and_levels(self):
        self.assertEqual(wx.POLLEN_NAMES, CASES["pollenNames"])
        self.assertEqual({k: list(v) for k, v in wx.POLLEN_LEVELS.items()},
                         {k: v for k, v in CASES["pollenLevels"].items() if not k.startswith("_")})
        for case in CASES["pollen"]:
            self.assertEqual(wx.pollen_level(case["species"], case["value"]), case["level"], case)
        self.assertIsNone(wx.pollen_level("birch", None))
        self.assertIsNone(wx.pollen_level("oak", 500))

    def test_warning_levels_equal_the_fixture(self):
        self.assertEqual(list(cap.LEVELS), list(CASES["warningLevels"]))


# -- the route ----------------------------------------------------------------------------------
class RouteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.dir / "hls", state_dir=self.dir),
                          ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=self.dir / "breaks"),
                          admin=AdminConfig(password=ADMIN_PASSWORD))
        self.logs = []
        self.clock = Clock()
        hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(self.cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=self.logs.append,
                             clock=self.clock)
        self.http = open_meteo()
        self.chmi = chmi()
        self.gw.warnings._urlopen = self.chmi
        self.gw.warnings.refresh()
        self.gw.weather._urlopen = self.http

    def get(self, query=None, headers=APP):
        resp = self.gw.handle("/dvb/weather", query or {}, headers)
        return resp.status, json.loads(resp.body)

    def test_device_place_gives_the_shared_answer(self):
        status, body = self.get({"lat": "50.0755", "lon": "14.4378"})
        self.assertEqual((status, body), (SHARED["ok"]["status"], SHARED["ok"]["body"]))

    def test_open_meteo_gets_only_the_rounded_place_and_fixed_parameters(self):
        self.get({"lat": "50.0755", "lon": "14.4378", "x": "y", "callback": "z"})
        self.assertEqual(self.http.urls(), [wx.FORECAST_URL, wx.AIR_URL])
        self.assertEqual(self.http.query(0), {
            "latitude": "50.1", "longitude": "14.4", "timezone": "Europe/Prague", "forecast_days": "7",
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"})
        self.assertEqual(self.http.query(1), {
            "latitude": "50.1", "longitude": "14.4", "timezone": "Europe/Prague",
            "current": "european_aqi,alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,ragweed_pollen"})
        for request in self.http.requests:
            self.assertEqual(request.get_header("User-agent"),
                             "cz-tv-hub (household gateway; https://github.com/martinno027/cz-tv-hub)")
        self.assertTrue(wx.FORECAST_URL.startswith("https://api.open-meteo.com/"))
        self.assertTrue(wx.AIR_URL.startswith("https://air-quality-api.open-meteo.com/"))

    def test_household_place(self):
        self.gw.home_place.set(50.08, 14.44)
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body["place"], {"lat": 50.1, "lon": 14.4, "source": "household"})
        self.assertEqual(body["days"], OK_DAYS)
        # a device place wins over the household one
        self.assertEqual(self.get({"lat": "49.5", "lon": "16.0"})[1]["place"]["source"], "device")

    def test_off_without_any_place(self):
        self.assertEqual(self.get(), (SHARED["off"]["status"], SHARED["off"]["body"]))
        self.assertEqual(self.http.requests, [])

    def test_outside_and_invalid(self):
        for query in ({"lat": "47.9", "lon": "14.4"}, {"lat": "50.1", "lon": "19.6"}, {"lat": "NaN", "lon": "14.4"},
                      {"lat": "inf", "lon": "14.4"}, {"lat": "abc", "lon": "14.4"}, {"lat": "50.1"},
                      {"lon": "14.4"}, {"lat": "", "lon": ""}):
            self.assertEqual(self.get(query), (400, {"error": "Poloha mimo Česko"}), query)
        self.gw.home_place.set(50.1, 14.4)  # an invalid device place does not fall back
        self.assertEqual(self.get({"lat": "x", "lon": "14.4"})[0], 400)
        self.assertEqual(self.http.requests, [])

    def test_key_required_worker_refused_only_get(self):
        self.assertEqual(self.get({"lat": "50.1", "lon": "14.4"}, headers={})[0], 401)
        self.assertEqual(self.get({"lat": "50.1", "lon": "14.4"}, headers=WORKER)[0], 403)
        resp = self.gw.handle("/dvb/weather", {}, APP, "POST", b"{}")
        self.assertEqual(resp.status, 405)
        self.assertEqual(self.http.requests, [])

    def test_cache_30_minutes_per_rounded_place(self):
        self.get({"lat": "50.08", "lon": "14.44"})
        self.get({"lat": "50.12", "lon": "14.36"})  # the same rounded place
        self.assertEqual(len(self.http.requests), 2)
        self.clock.t += wx.CACHE_SECONDS - 1
        self.get({"lat": "50.1", "lon": "14.4"})
        self.assertEqual(len(self.http.requests), 2)
        self.clock.t += 1
        self.get({"lat": "50.1", "lon": "14.4"})
        self.assertEqual(len(self.http.requests), 4)

    def test_at_most_50_places_cached(self):
        weather = self.gw.weather
        places = [(48.0 + (i % 30) / 10, 12.0 + (i // 30) / 10) for i in range(wx.MAX_PLACES + 1)]
        for i, (lat, lon) in enumerate(places):
            if i and i % wx.NEW_PER_HOUR == 0:
                self.clock.t += 3600  # stay under the hourly limit
            self.get({"lat": str(lat), "lon": str(lon)})
        self.assertEqual(len(weather._cache), wx.MAX_PLACES)
        self.assertNotIn(wx.round_place(*places[0]), weather._cache)  # the least recently used went
        self.assertIn(wx.round_place(*places[-1]), weather._cache)

    def test_at_most_20_new_places_per_hour(self):
        lats = [48.0 + i / 10 for i in range(wx.NEW_PER_HOUR + 1)]
        for lat in lats[:-1]:
            self.assertNotIn("error", self.get({"lat": str(lat), "lon": "14.0"})[1])
        sent = len(self.http.requests)
        self.assertEqual(sent, 2 * wx.NEW_PER_HOUR)
        status, body = self.get({"lat": str(lats[-1]), "lon": "14.0"})
        self.assertEqual((status, body["error"]), (200, "Počasí teď není k dispozici"))
        self.assertNotIn("days", body)
        self.assertEqual(len(self.http.requests), sent)  # nothing sent for the 21st place
        # a place sent within the hour is refreshed after 30 min without counting as new
        self.clock.t += wx.CACHE_SECONDS
        self.assertNotIn("error", self.get({"lat": str(lats[0]), "lon": "14.0"})[1])
        self.assertEqual(len(self.http.requests), sent + 2)
        self.assertIn("error", self.get({"lat": str(lats[-1]), "lon": "14.0"})[1])
        self.clock.t += 3600
        self.assertNotIn("error", self.get({"lat": str(lats[-1]), "lon": "14.0"})[1])

    def test_air_failing_leaves_air_null(self):
        self.http.errors[wx.AIR_URL] = urllib.error.URLError(OSError("refused"))
        status, body = self.get({"lat": "50.1", "lon": "14.4"})
        self.assertEqual(status, 200)
        self.assertIsNone(body["air"])
        self.assertEqual(body["days"], OK_DAYS)
        self.assertNotIn("error", body)

    def test_forecast_failing_gives_the_shared_error(self):
        self.gw.home_place.set(50.1, 14.4)
        for failure in (urllib.error.HTTPError(wx.FORECAST_URL, 503, "x", {}, None), TimeoutError("slow"),
                        urllib.error.URLError(OSError("refused"))):
            self.http.errors[wx.FORECAST_URL] = failure
            self.clock.t += 3600  # a new hour, no cache
            self.gw.weather._cache.clear()
            self.assertEqual(self.get(), (SHARED["error"]["status"], SHARED["error"]["body"]), failure)

    def test_unusable_forecast_answers(self):
        for payload in ({}, [], {"current": {}, "daily": {}}, {"current": {}, "daily": {"time": []}}, b"not json",
                        b"x" * (wx.MAX_BYTES + 1)):
            self.http.add(wx.FORECAST_URL, payload)
            self.gw.weather._cache.clear()
            self.gw.weather._sent.clear()
            body = self.get({"lat": "50.1", "lon": "14.4"})[1]
            self.assertEqual(body.get("error"), "Počasí teď není k dispozici", repr(payload)[:40])

    def test_stale_cache_served_when_open_meteo_fails(self):
        first = self.get({"lat": "50.1", "lon": "14.4"})[1]
        self.clock.t += wx.CACHE_SECONDS + 1
        self.http.errors[wx.FORECAST_URL] = urllib.error.HTTPError(wx.FORECAST_URL, 500, "x", {}, None)
        status, body = self.get({"lat": "50.1", "lon": "14.4"})
        self.assertEqual((status, body), (200, first))
        self.assertNotIn("error", body)
        self.clock.t += wx.STALE_SECONDS
        self.assertIn("error", self.get({"lat": "50.1", "lon": "14.4"})[1])

    def test_one_request_at_a_time(self):
        self.http.gate = threading.Event()
        results = []
        threads = [threading.Thread(target=lambda lat=lat: results.append(self.get({"lat": lat, "lon": "14.4"})))
                   for lat in ("49.5", "49.6", "49.7")]
        for t in threads:
            t.start()
        time.sleep(0.2)
        self.http.gate.set()
        for t in threads:
            t.join(10)
        self.assertEqual(len(results), 3)
        self.assertEqual(self.http.most, 1)
        self.assertEqual(len(self.http.requests), 6)

    def test_redirects_are_not_followed(self):
        handler = wx._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/"))

    def test_no_coordinate_in_any_log_line_or_error(self):
        # every path: success, cache, household, outside, limit, air and forecast failures with texts that quote
        # the request URL (urllib's own errors can), the admin place and an HTTP-level error line
        site = AdminSite(self.cfg, self.gw, admin=object())
        site.handle("POST", "/admin/api/home", ADMIN_POST, json.dumps({"lat": 50.08, "lon": 14.44}).encode())
        site.handle("POST", "/admin/api/home", ADMIN_POST, json.dumps({"lat": 60.08, "lon": 14.44}).encode())
        bodies = [self.get()[1], self.get({"lat": "50.08", "lon": "14.44"})[1], self.get({"lat": "47", "lon": "1"})[1]]
        url = wx.FORECAST_URL + "?latitude=49.5&longitude=16.0"
        self.http.errors[wx.AIR_URL] = urllib.error.URLError(f"cannot reach {url}")
        bodies.append(self.get({"lat": "49.5", "lon": "16.0"})[1])
        self.http.errors[wx.FORECAST_URL] = urllib.error.HTTPError(url, 500, f"error at {url}", {}, None)
        bodies.append(self.get({"lat": "49.6", "lon": "16.3"})[1])
        self.http.errors[wx.FORECAST_URL] = ValueError(f"bad {url}")
        bodies.append(self.get({"lat": "49.6", "lon": "16.0"})[1])
        for lat in range(wx.NEW_PER_HOUR + 1):
            self.get({"lat": str(48 + lat / 10), "lon": "16.0"})
        site.handle("POST", "/admin/api/home", ADMIN_POST, b'{"remove": true}')
        self.assertTrue(any("weather:" in line for line in self.logs), self.logs)
        self.assertTrue(any("admin: home place set" in line for line in self.logs))
        self.assertTrue(any("admin: home place removed" in line for line in self.logs))
        for line in self.logs:
            for text in COORDINATE_TEXTS:
                self.assertNotIn(text, line, line)
        for body in bodies:
            if "error" in body:
                for text in COORDINATE_TEXTS:
                    self.assertNotIn(text, body["error"])
        self.assertEqual(logsafe.route("/dvb/weather?lat=50.08&lon=14.44"), "/dvb/weather")


# -- the household place -------------------------------------------------------------------------
class HomePlaceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.logs = []

    def test_saved_rounded_0600_and_reloaded(self):
        home = wx.HomePlace(self.dir, log=self.logs.append)
        self.assertIsNone(home.get())
        self.assertEqual(home.set("50.0755", 14.4378), (50.1, 14.4))
        path = self.dir / wx.FILE
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text()), {"lat": 50.1, "lon": 14.4})
        self.assertFalse((self.dir / (wx.FILE + ".tmp")).exists())
        self.assertEqual(wx.HomePlace(self.dir).get(), (50.1, 14.4))

    def test_old_tmp_bits_do_not_leak(self):
        tmp = self.dir / (wx.FILE + ".tmp")
        tmp.write_text("x")
        os.chmod(tmp, 0o644)
        wx.HomePlace(self.dir).set(50.1, 14.4)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / wx.FILE).st_mode), 0o600)

    def test_invalid_not_saved(self):
        home = wx.HomePlace(self.dir)
        self.assertIsNone(home.set(60, 14))
        self.assertIsNone(home.set(None, None))
        self.assertFalse((self.dir / wx.FILE).exists())

    def test_remove(self):
        home = wx.HomePlace(self.dir)
        home.set(50.1, 14.4)
        self.assertTrue(home.remove())
        self.assertIsNone(home.get())
        self.assertFalse((self.dir / wx.FILE).exists())
        self.assertFalse(home.remove())

    def test_broken_file_means_no_place_and_no_coordinates_logged(self):
        (self.dir / wx.FILE).write_text('{"lat": 50.1, "lon": ')
        self.assertIsNone(wx.HomePlace(self.dir, log=self.logs.append).get())
        (self.dir / wx.FILE).write_text('{"lat": 70.1, "lon": 14.4}')
        self.assertIsNone(wx.HomePlace(self.dir, log=self.logs.append).get())
        self.assertEqual(len(self.logs), 2)
        for line in self.logs:
            self.assertNotIn("50.1", line)
            self.assertNotIn("70.1", line)


class AdminHomeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.dir / "hls", state_dir=self.dir),
                     admin=AdminConfig(password=ADMIN_PASSWORD))
        self.logs = []
        hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=self.logs.append)
        self.site = AdminSite(cfg, self.gw, admin=object())

    def call(self, method, body=None, headers=ADMIN_POST):
        resp = self.site.handle(method, "/admin/api/home", headers, json.dumps(body).encode() if body is not None else b"")
        return resp.status, json.loads(resp.body)

    def test_tab_on_the_page(self):
        self.assertIn('id="tab-home"', PAGE)
        self.assertIn('<section id="home" hidden>', PAGE)
        self.assertIn('"home"', PAGE.split("var TABS = ")[1].split(";")[0])

    def test_set_rounded_and_remove(self):
        self.assertEqual(self.call("GET", headers=BASIC), (200, {"place": None}))
        self.assertEqual(self.call("POST", {"lat": 50.0755, "lon": 14.4378}), (200, {"place": {"lat": 50.1, "lon": 14.4}}))
        self.assertEqual(self.gw.home_place.get(), (50.1, 14.4))
        self.assertEqual(self.call("GET", headers=BASIC)[1], {"place": {"lat": 50.1, "lon": 14.4}})
        self.assertEqual(self.call("POST", {"remove": True}), (200, {"place": None}))
        self.assertFalse((self.dir / wx.FILE).exists())
        self.assertEqual([l for l in self.logs if l.startswith("admin:")],
                         ["admin: home place set", "admin: home place removed"])

    def test_invalid_place(self):
        for body in ({"lat": 60, "lon": 14}, {"lat": "x", "lon": 14}, {"lat": 50.1}, {}, {"remove": "yes"}):
            status, data = self.call("POST", body)
            self.assertEqual(status, 400, body)
            self.assertIn("51,5", data["error"])
        self.assertEqual(self.call("POST", None)[0], 400)  # not JSON
        self.assertIsNone(self.gw.home_place.get())

    def test_same_origin_and_login(self):
        self.assertEqual(self.call("POST", {"lat": 50.1, "lon": 14.4}, headers=BASIC)[0], 403)  # no admin header
        self.assertEqual(self.call("POST", {"lat": 50.1, "lon": 14.4},
                                   headers={**ADMIN_POST, "Origin": "https://evil.example"})[0], 403)
        resp = self.site.handle("GET", "/admin/api/home", {})
        self.assertEqual(resp.status, 401)
        self.assertEqual(self.call("PUT", {"lat": 50.1, "lon": 14.4})[0], 405)
        self.assertIsNone(self.gw.home_place.get())


# -- ČHMÚ warnings --------------------------------------------------------------------------------
class WarningsTest(unittest.TestCase):
    def make(self, http=None, now=NOW, in_use=lambda: False):
        self.logs = []
        self.now = [now]
        return cap.Warnings(urlopen=http or chmi(), now=lambda: self.now[0], log=self.logs.append, in_use=in_use)

    def test_newest_by_listing_date(self):
        listing = (CHMI / "listing.html").read_text()
        name, moment = cap.newest_file(listing)
        self.assertEqual(name, "alert_cap_50_140904.xml")  # not the later-named October file, not cap_70
        self.assertEqual(moment, datetime(2026, 11, 14, 9, 5))
        self.assertIsNone(cap.newest_file("<html>nothing</html>"))

    def test_kept_blocks(self):
        sent, alerts = cap.parse((CHMI / "cap_warnings.xml").read_bytes())
        self.assertEqual(sent, datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc))
        self.assertEqual([(a.level, a.event) for a in alerts],
                         [("yellow", "Silný vítr"), ("orange", "Riziko požárů"), ("yellow", "Silné bouřky"),
                          ("red", "Extrémní zátěž teplem")])  # no en-GB, no all-clear, no outlook
        self.assertEqual(alerts[0].codes, frozenset({"1100", "2105"}))
        self.assertIsNone(alerts[1].end)

    def test_for_place_matches_the_nearest_orp_and_drops_ended(self):
        warnings = self.make()
        warnings.refresh()
        self.assertEqual(warnings.for_place(50.1, 14.4), SHARED["ok"]["body"]["warnings"])  # stormy morning ended
        self.assertEqual([w["level"] for w in warnings.for_place(49.2, 16.6)], ["red"])  # Brno
        self.assertEqual(warnings.for_place(49.7, 13.4), [])  # Plzeň
        self.now[0] = datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual([w["event"] for w in warnings.for_place(50.1, 14.4)], ["Riziko požárů"])  # open-ended

    def test_all_clear_file(self):
        warnings = self.make(chmi(file="cap_clear.xml"))
        warnings.refresh()
        self.assertEqual(warnings.for_place(50.1, 14.4), [])

    def test_same_file_not_fetched_again(self):
        http = chmi()
        warnings = self.make(http)
        warnings.refresh()
        warnings.refresh()
        self.assertEqual(http.urls(), [cap.LISTING_URL, cap.LISTING_URL + "alert_cap_50_140904.xml",
                                       cap.LISTING_URL])

    def test_older_sent_refused(self):
        http = chmi()
        warnings = self.make(http)
        warnings.refresh()
        older = (CHMI / "cap_warnings.xml").read_text().replace("2026-11-14T10:00:00+01:00", "2026-11-13T10:00:00+01:00")
        listing = (CHMI / "listing.html").read_text().replace("15-Nov-2026 00:00", "15-Nov-2026 00:00").replace(
            "01-Nov-2026 07:00", "15-Nov-2026 07:00")
        http.add(cap.LISTING_URL, listing.encode())
        http.add(cap.LISTING_URL + "alert_cap_50_010700.xml", older.encode())
        warnings.refresh()
        self.assertTrue(any("older" in line for line in self.logs), self.logs)
        self.assertEqual(len(warnings.for_place(50.1, 14.4)), 2)  # the last good data stays

    def test_dtd_and_entities_refused(self):
        body = (CHMI / "cap_warnings.xml").read_bytes()
        bomb = body.replace(b'<alert xmlns', b'<!DOCTYPE alert [<!ENTITY a "aaaa">]>\n<alert xmlns', 1)
        with self.assertRaises(cap.WarningsError):
            cap.parse(bomb)
        with self.assertRaises(cap.WarningsError):
            cap.parse(b"<?xml version='1.0'?><!ENTITY x 'y'><alert/>")
        with self.assertRaises(cap.WarningsError):
            cap.parse(b"not xml")
        with self.assertRaises(cap.WarningsError):
            cap.parse(body.replace(b"<status>Actual</status>", b"<status>Exercise</status>"))
        with self.assertRaises(cap.WarningsError):
            cap.parse(body.replace(b"urn:oasis:names:tc:emergency:cap:1.2", b"urn:other"))

    def test_size_cap(self):
        http = chmi()
        http.add(cap.LISTING_URL + "alert_cap_50_140904.xml", b" " * (cap.MAX_FILE + 1))
        warnings = self.make(http)
        warnings.refresh()
        self.assertTrue(any("larger than" in line for line in self.logs), self.logs)
        self.assertEqual(warnings.for_place(50.1, 14.4), [])

    def test_failures_keep_last_good_for_6_hours(self):
        http = chmi()
        warnings = self.make(http)
        warnings.refresh()
        http.errors[cap.LISTING_URL] = urllib.error.HTTPError(cap.LISTING_URL, 503, "x", {}, None)
        self.now[0] += cap.KEEP_SECONDS - 60
        warnings.refresh()
        self.assertTrue(any("HTTP 503" in line for line in self.logs))
        self.assertEqual(len(warnings.for_place(50.1, 14.4)), 1)  # the wind ended, the fire risk stays
        self.now[0] += 60
        self.assertEqual(warnings.for_place(50.1, 14.4), [])
        del http.errors[cap.LISTING_URL]
        warnings.refresh()  # the same file is still the newest: current again
        self.assertEqual(len(warnings.for_place(50.1, 14.4)), 1)

    def test_fetches_only_while_weather_is_in_use(self):
        home = [False]
        warnings = self.make(in_use=lambda: home[0])
        self.assertFalse(warnings.wanted())
        home[0] = True
        self.assertTrue(warnings.wanted())
        home[0] = False
        warnings.used()
        self.assertTrue(warnings.wanted())
        self.assertTrue(warnings._wake.is_set())  # the first use after a pause fetches at once
        self.now[0] += cap.IN_USE_SECONDS
        self.assertFalse(warnings.wanted())

    def test_loop_starts_and_stops(self):
        http = chmi()
        warnings = self.make(http, in_use=lambda: True)
        warnings.start()
        for _ in range(100):
            if warnings.for_place(50.1, 14.4):
                break
            time.sleep(0.02)
        warnings.stop()
        self.assertEqual(len(warnings.for_place(50.1, 14.4)), 2)
        self.assertIsNone(warnings._thread)

    def test_no_url_or_place_in_logs(self):
        http = chmi()
        http.errors[cap.LISTING_URL] = urllib.error.URLError(f"cannot reach {cap.LISTING_URL}")
        warnings = self.make(http)
        warnings.refresh()
        self.assertEqual(len(self.logs), 1)
        self.assertNotIn("opendata", self.logs[0])


# -- ORP table ------------------------------------------------------------------------------------
class OrpTest(unittest.TestCase):
    def test_table(self):
        codes = [o[0] for o in orp.ORP]
        self.assertEqual(len(codes), 206)
        self.assertEqual(len(set(codes)), 206)
        self.assertIn("1100", codes)  # ČHMÚ's code for Praha (ČSÚ: 1000)
        self.assertNotIn("1000", codes)
        for code, name, lat, lon in orp.ORP:
            self.assertRegex(code, r"^[1-8][0-9]{3}$")
            self.assertTrue(name)
            self.assertTrue(48.5 < lat < 51.1 and 12.0 < lon < 18.9, (code, name, lat, lon))

    def test_nearest(self):
        self.assertEqual(orp.nearest(50.1, 14.4), ("1100", "Hlavní město Praha"))
        self.assertEqual(orp.nearest(49.2, 16.6), ("6203", "Brno"))
        self.assertEqual(orp.nearest(49.8, 18.3), ("8119", "Ostrava"))
        self.assertEqual(orp.nearest(50.2, 12.2), ("4101", "Aš"))
        self.assertEqual(orp.nearest(50.1, 12.4), ("4102", "Cheb"))

    def test_nearest_uses_distance_scaled_by_latitude(self):
        # a point at equal degree offsets east and north: a degree of longitude is shorter here, so east is nearer
        lat, lon = 49.75, 15.0
        table = (("A", "north", lat + 0.3, lon), ("B", "east", lat, lon + 0.3))
        original = orp.ORP
        orp.ORP = table
        try:
            self.assertEqual(orp.nearest(lat, lon)[0], "B")
            self.assertTrue(math.cos(math.radians(lat)) < 1)
        finally:
            orp.ORP = original


if __name__ == "__main__":
    unittest.main()
