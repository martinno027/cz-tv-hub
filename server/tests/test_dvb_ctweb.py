"""ČT for the household web app (spec/dvb-gateway.md "Web" → "ČT for the web"): ``/dvb/ct/live|vod|schedule``.

ČT is never called: the bridge's fetch is a fake that records what would have been sent. Streams go to every client
with the app key, at home and away (owner 2026-11-02, replacing the T27 home-LAN-only rule).
"""
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

from cztvhub import api
from cztvhub.config import AdsConfig, Config, DvbConfig
from cztvhub.dvb import ctweb
from cztvhub.dvb.gateway import DvbGateway
from dvb_fixtures import NOW, FakeClient

KEY = "s3cret-" * 6
WORKER_KEY = "w0rker-" * 6
AUTH = {"Authorization": "Bearer " + KEY}
COOKIE = {"Cookie": "cztvhub_key=" + KEY}
WORKER = {"Authorization": "Bearer " + WORKER_KEY}
HOME = "192.168.1.20"
SPEC = Path(__file__).resolve().parents[2] / "spec" / "fixtures"
TODAY = date(2026, 10, 18)  # the broadcast day of NOW


def fixture(name):
    """A ČT answer recorded as {status, body}, as bytes with a layout of its own (not json.dumps defaults), so
    a re-serialised answer would differ."""
    data = json.loads((SPEC / name).read_text(encoding="utf-8"))
    return data["status"], json.dumps(data["body"], ensure_ascii=False, indent=1).encode("utf-8")


class FakeCt:
    def __init__(self):
        self.calls = []
        self.answers = {}  # URL prefix → (status, bytes) or an exception
        self.default = fixture("live_CH_1.json")

    def __call__(self, url, body=None):
        self.calls.append((url, body))
        for prefix, answer in self.answers.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return self.default


class HomeLanTest(unittest.TestCase):
    def test_home_addresses(self):
        for peer in ("192.168.1.20", "10.0.0.5", "172.16.0.1", "172.31.255.254", "169.254.3.4", "127.0.0.1",
                     "::1", "fe80::1", "fe80::1%eth0", "fd00::12", "fc00::1", "::ffff:192.168.1.20",
                     "::ffff:127.0.0.1"):
            self.assertTrue(ctweb.home_lan(peer), peer)

    def test_tailnet_public_and_junk_are_not_home(self):
        for peer in ("100.64.0.1", "100.101.102.103", "100.127.255.254", "fd7a:115c:a1e0::1",
                     "fd7a:115c:a1e0:ab12:4843:cd96:6258:b240", "::ffff:100.101.102.103", "8.8.8.8",
                     "172.32.0.1", "11.0.0.1", "192.169.0.1", "2a00:1028::1", "64:ff9b::c0a8:114", "::c0a8:114",
                     "2002:c0a8:114::1", "0.0.0.0", "::", "", "localhost", "192.168.1.20:5000", "garbage"):
            self.assertFalse(ctweb.home_lan(peer), peer)

    def test_tailnet_stays_excluded_even_if_a_home_range_covered_it(self):
        import ipaddress
        every4, every6 = (ipaddress.ip_network("0.0.0.0/0"),), (ipaddress.ip_network("::/0"),)
        with mock.patch.object(ctweb, "HOME_V4", every4), mock.patch.object(ctweb, "HOME_V6", every6):
            self.assertTrue(ctweb.home_lan("8.8.8.8"))
            self.assertTrue(ctweb.home_lan("2a00::1"))
            for peer in ("100.64.0.1", "100.127.255.254", "::ffff:100.101.102.103", "fd7a:115c:a1e0::1"):
                self.assertFalse(ctweb.home_lan(peer), peer)

    def test_tailnet_ula_is_inside_fc00_so_the_exclusion_matters(self):
        import ipaddress
        self.assertIn(ipaddress.ip_address("fd7a:115c:a1e0::1"), ipaddress.ip_network("fc00::/7"))


class CtWebGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=root / "hls", state_dir=root / "state"),
                     ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=root / "breaks"))
        self.logs = []
        self.gw = DvbGateway(cfg, client=FakeClient(), now=lambda: NOW, log=self.logs.append)
        self.ct = FakeCt()
        self.clock = [1000.0]
        self.gw.ct_web.fetch = self.ct
        self.gw.ct_web.clock = lambda: self.clock[0]

    def get(self, path, query=None, headers=AUTH, client=HOME):
        return self.gw.handle(path, query or {}, headers, "GET", b"", client)

    # -- live and vod ------------------------------------------------------------------------
    def test_live_passes_cts_answer_through_unchanged(self):
        resp = self.get("/dvb/ct/live/CH_1")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.body, self.ct.default[1])  # byte for byte
        self.assertEqual(resp.content_type, "application/json; charset=utf-8")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(self.ct.calls, [(api.LIVE_URL.format(channel="CH_1"), None)])

    def test_exact_request_parameters_of_api_py(self):
        self.get("/dvb/ct/live/CH_24", {"canPlayDrm": "true", "skipIpAddressCheck": "true", "quality": "x"})
        self.get("/dvb/ct/vod/226411010101016", {"canPlayDrm": "true"})
        self.assertEqual([c[0] for c in self.ct.calls], [
            "https://api.ceskatelevize.cz/video/v1/playlist-live/v1/stream-data/channel/CH_24?canPlayDrm=false"
            "&quality=web&streamType=hls&origin=ivysilaniweb&usePlayability=true&clientVersion=0.0.0",
            "https://api.ceskatelevize.cz/video/v1/playlist-vod/v1/stream-data/media/external/226411010101016"
            "?canPlayDrm=false&quality=web&streamType=hls&origin=ivysilaniweb&usePlayability=true"
            "&clientVersion=0.0.0"])
        self.assertTrue(all(body is None for _, body in self.ct.calls))

    def test_blocked_drm_and_licence_answers_are_not_filtered(self):
        cases = {"/dvb/ct/live/CH_4": "live_blocked.json", "/dvb/ct/vod/226411000401016": "vod_drm.json",
                 "/dvb/ct/vod/226411012001016": "vod_no_licence.json", "/dvb/ct/vod/224381492050002":
                 "vod_not_found.json", "/dvb/ct/vod/226411058150032": "vod_still_live.json",
                 "/dvb/ct/vod/226411010101016": "vod_ok.json"}
        for path, name in cases.items():
            self.ct.answers = {api.API_BASE + "/video": fixture(name)}
            resp = self.get(path)
            status, body = fixture(name)
            self.assertEqual((resp.status, resp.body), (status, body), name)
        self.assertEqual({s for s, _ in map(fixture, cases.values())}, {200, 403, 404})  # the cases differ
        drm = json.loads(self.get("/dvb/ct/vod/226411000401016").body)
        self.assertEqual(drm["message"]["status"]["name"], "UNSUPPORTED_DRM")

    def test_unknown_channel_and_bad_idec(self):
        for path in ("/dvb/ct/live/CH_7", "/dvb/ct/live/CH_1x", "/dvb/ct/live/ch_1", "/dvb/ct/live/",
                     "/dvb/ct/live/CH_1/x", "/dvb/ct/vod/../x", "/dvb/ct/vod/1/226411010101016"):
            self.assertEqual(self.get(path).status, 404, path)
        for idec in ("", "abc", "2264110101010a6", "1234567", "1" * 21, "226411010101016%20", "..", "-1",
                     "226411010101016.m3u8", "２２６４１１０１０１０１０１６"):
            resp = self.get("/dvb/ct/vod/" + idec)
            self.assertEqual(resp.status, 400, idec)
            self.assertEqual(json.loads(resp.body), {"error": "Neplatný idec"})
        self.assertEqual(self.get("/dvb/ct/vod/12345678901").status, 200)  # 11 digits, as real idecs have
        self.assertEqual(self.get("/dvb/ct/other").status, 404)
        self.assertEqual(len(self.ct.calls), 1)

    def test_streams_for_every_client_with_the_key_at_home_and_away(self):
        peers = ("192.168.1.20", "127.0.0.1", "100.101.102.103", "fd7a:115c:a1e0::5", "::ffff:100.101.102.103",
                 "8.8.8.8", "2a00::1", "")
        self.assertEqual({ctweb.home_lan(p) for p in peers}, {True, False})  # the peers differ in "home"
        for peer in peers:
            for path in ("/dvb/ct/live/CH_1", "/dvb/ct/vod/226411010101016"):
                self.clock[0] += ctweb.STREAM_SECONDS  # never a cached answer: ČT is asked for every peer
                resp = self.get(path, client=peer)
                self.assertEqual((resp.status, resp.body), (200, self.ct.default[1]), (peer, path))
        self.assertEqual(len(self.ct.calls), 2 * len(peers))

    def test_away_from_home_cts_own_refusal_is_passed_on(self):
        self.ct.answers = {api.API_BASE + "/video": fixture("live_blocked.json")}
        resp = self.get("/dvb/ct/live/CH_1", client="100.101.102.103")
        self.assertEqual((resp.status, resp.body), fixture("live_blocked.json"))

    def test_streams_still_need_the_app_key(self):
        for headers, status in (({}, 401), ({"Authorization": "Bearer wrong"}, 401), (WORKER, 403)):
            for peer in (HOME, "100.101.102.103"):
                self.assertEqual(self.get("/dvb/ct/live/CH_1", headers=headers, client=peer).status, status)
        self.assertEqual(self.ct.calls, [])

    def test_schedule_is_open_on_the_tailnet(self):
        resp = self.get("/dvb/ct/schedule", {"channels": "ct1", "date": "2026-10-18"}, client="100.101.102.103")
        self.assertEqual(resp.status, 200)

    def test_stream_cache_at_most_60_seconds(self):
        self.get("/dvb/ct/live/CH_1")
        self.clock[0] += 59.9
        self.get("/dvb/ct/live/CH_1")
        self.assertEqual(len(self.ct.calls), 1)
        self.clock[0] += 0.1
        self.get("/dvb/ct/live/CH_1")
        self.assertEqual(len(self.ct.calls), 2)
        self.get("/dvb/ct/live/CH_2")
        self.assertEqual(len(self.ct.calls), 3)  # per channel

    def test_cts_server_errors_are_passed_on_but_not_cached(self):
        self.ct.answers = {api.API_BASE: (503, b'{"error": "busy"}')}
        self.assertEqual((self.get("/dvb/ct/live/CH_1").status, self.get("/dvb/ct/live/CH_1").body),
                         (503, b'{"error": "busy"}'))
        self.assertEqual(len(self.ct.calls), 2)

    def test_cache_is_bounded(self):
        for n in range(ctweb.MAX_ENTRIES + 20):
            self.get(f"/dvb/ct/vod/{10 ** 10 + n}")
        self.assertLessEqual(len(self.gw.ct_web._streams), ctweb.MAX_ENTRIES)

    def test_unreachable_or_not_json_is_502_without_urls_in_the_log(self):
        for answer in (ctweb.Unreachable("TimeoutError"), (200, b"<html>maintenance</html>"), (200, b"\xff\xfe"),
                       (502, b"")):
            self.ct.answers = {api.API_BASE: answer}
            resp = self.get("/dvb/ct/live/CH_1")
            self.assertEqual(resp.status, 502, answer)
            self.assertEqual(json.loads(resp.body), {"error": "iVysílání teď neodpovídá"})
        self.assertEqual(len(self.logs), 4)
        self.assertFalse(any("http" in line or "api.ceskatelevize" in line for line in self.logs), self.logs)

    # -- schedule ----------------------------------------------------------------------------
    def test_schedule_sends_the_fixed_query(self):
        self.ct.answers = {api.GRAPHQL_URL: fixture("schedule_ct1_ct24.json")}
        resp = self.get("/dvb/ct/schedule", {"channels": "ct1,ct24", "date": "2026-10-17",
                                             "query": "{ __schema { types { name } } }", "variables": "{}"})
        self.assertEqual((resp.status, resp.body), fixture("schedule_ct1_ct24.json"))
        self.assertEqual(self.ct.calls, [(api.GRAPHQL_URL, {"query": api.SCHEDULE_QUERY,
                                                            "variables": {"channels": ["ct1", "ct24"],
                                                                          "date": "10.17.2026"}})])
        # the same body as the add-on's guide sends
        self.assertEqual(self.ct.calls[0][1], api.schedule_request(["ct1", "ct24"], date(2026, 10, 17)))

    def test_graphql_errors_are_passed_on(self):
        self.ct.answers = {api.GRAPHQL_URL: fixture("graphql_error.json")}
        resp = self.get("/dvb/ct/schedule", {"channels": "art", "date": "2026-10-18"})
        self.assertEqual((resp.status, resp.body), fixture("graphql_error.json"))

    def test_schedule_input_checked(self):
        bad = [{"channels": "", "date": "2026-10-18"}, {"date": "2026-10-18"}, {"channels": "ct1"},
               {"channels": "ct1,nova", "date": "2026-10-18"}, {"channels": "ct1,ct1", "date": "2026-10-18"},
               {"channels": "ct1,", "date": "2026-10-18"}, {"channels": "CT1", "date": "2026-10-18"},
               {"channels": "ct1,ct2,ct24,sport,decko,art,ct1", "date": "2026-10-18"},
               {"channels": "ct1", "date": "18.10.2026"}, {"channels": "ct1", "date": "2026-13-01"},
               {"channels": "ct1", "date": "2026-10-10"}, {"channels": "ct1", "date": "2026-10-21"},
               {"channels": "ct1", "date": "2026-10-18T00:00"}, {"channels": "ct1", "date": "20261018"}]
        for query in bad:
            self.assertEqual(self.get("/dvb/ct/schedule", query).status, 400, query)
        self.assertEqual(self.ct.calls, [])
        for day in ("2026-10-11", "2026-10-20"):  # 7 days back, 2 ahead
            self.assertEqual(self.get("/dvb/ct/schedule", {"channels": "ct1", "date": day}).status, 200, day)
        all_six = "ct1,ct2,ct24,sport,decko,art"
        self.assertEqual(self.get("/dvb/ct/schedule", {"channels": all_six, "date": "2026-10-18"}).status, 200)

    def test_schedule_cache_10_minutes(self):
        self.ct.answers = {api.GRAPHQL_URL: fixture("schedule_ct1_ct24.json")}
        query = {"channels": "ct1", "date": "2026-10-18"}
        self.get("/dvb/ct/schedule", query)
        self.clock[0] += 599
        self.get("/dvb/ct/schedule", query)
        self.assertEqual(len(self.ct.calls), 1)
        self.clock[0] += 1
        self.get("/dvb/ct/schedule", query)
        self.assertEqual(len(self.ct.calls), 2)

    def test_a_schedule_with_errors_is_passed_on_but_not_cached(self):
        # ČT answered its outage of 2026-11-01 with 200 + errors for hours; cached, the web kept showing it
        outage = json.dumps({"data": None, "errors": [{"message": "Omlouváme se, došlo k chybě na naší straně."}]})
        self.ct.answers = {api.API_BASE: (200, outage.encode())}
        query = {"channels": "ct1", "date": "2026-10-18"}
        first = self.get("/dvb/ct/schedule", query)
        self.assertEqual((first.status, first.body), (200, outage.encode()))  # unchanged, the apps show the error
        self.ct.answers = {}
        self.clock[0] += 1
        self.get("/dvb/ct/schedule", query)
        self.assertEqual(len(self.ct.calls), 2)  # asked again at once, not after 10 minutes

    # -- keys --------------------------------------------------------------------------------
    def test_key_rules(self):
        paths = [("/dvb/ct/live/CH_1", None), ("/dvb/ct/vod/226411010101016", None),
                 ("/dvb/ct/schedule", {"channels": "ct1", "date": "2026-10-18"})]
        for path, query in paths:
            self.assertEqual(self.get(path, query, headers={}).status, 401, path)
            self.assertEqual(self.get(path, query, headers={"Authorization": "Bearer nope"}).status, 401, path)
            self.assertEqual(self.get(path, query, headers=WORKER).status, 403, path)
            self.assertEqual(self.get(path, query, headers=COOKIE).status, 200, path)  # GET: the player's cookie
        self.assertEqual(len(self.ct.calls), 3)

    def test_no_other_method(self):
        for method in ("POST", "PUT"):
            resp = self.gw.handle("/dvb/ct/live/CH_1", {}, AUTH, method, b"{}", HOME)
            self.assertEqual(resp.status, 405, method)
        self.assertEqual(self.ct.calls, [])


class FetchRawTest(unittest.TestCase):
    """The real fetch with urllib mocked: only our User-Agent, ČT's error bodies kept, nothing else forwarded."""

    def test_headers_and_body(self):
        seen = {}

        class Answer(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(request, timeout):
            seen.update(url=request.full_url, headers=dict(request.header_items()), data=request.data,
                        timeout=timeout)
            return Answer(b'{"ok": 1}')

        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(ctweb.fetch_raw(api.GRAPHQL_URL, {"query": "q"}), (200, b'{"ok": 1}'))
        self.assertEqual(seen["headers"], {"User-agent": api.USER_AGENT, "Accept": "application/json",
                                           "Content-type": "application/json"})
        self.assertEqual(json.loads(seen["data"]), {"query": "q"})

    def test_http_error_body_is_returned(self):
        err = urllib.error.HTTPError(api.LIVE_URL, 403, "Forbidden", {}, io.BytesIO(b'{"message": {}}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(ctweb.fetch_raw(api.LIVE_URL), (403, b'{"message": {}}'))

    def test_network_error_and_size_cap(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("api.ceskatelevize.cz down")):
            with self.assertRaises(ctweb.Unreachable) as caught:
                ctweb.fetch_raw(api.LIVE_URL)
        self.assertEqual(str(caught.exception), "URLError")  # no host, no URL

        class Big(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Big(b"x" * (ctweb.MAX_BYTES + 1))):
            with self.assertRaises(ctweb.Unreachable):
                ctweb.fetch_raw(api.LIVE_URL)


if __name__ == "__main__":
    unittest.main()
