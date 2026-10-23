import base64
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

from cztvhub.api import StreamResolver
from cztvhub.config import AdminConfig, Config, DvbConfig
from cztvhub.dvb.admin import AdminSite, ChannelAdmin
from cztvhub.dvb.fake import FakeTvheadend, stable_uuid
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from cztvhub.dvb.setup import run_setup
from cztvhub.dvb.tvheadend import TvheadendClient
from cztvhub.server import Server, make_handler

KEY = "app-key-" * 5
PASSWORD = "admin-password-1"
CT1, CT2, NOVA, PRIMA = (stable_uuid("channel", n) for n in ("ČT1 HD", "ČT2 HD", "Nova HD", "Prima HD"))
MUX_CT, MUX_PRIMA = stable_uuid("mux", "626MHz"), stable_uuid("mux", "690MHz")


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


AUTH = {"Authorization": basic("admin", PASSWORD)}
POST = {**AUTH, "X-Cztvhub-Admin": "1", "Host": "pi:8098"}


class InProcess:
    """urllib opener that answers from FakeTvheadend.api() without a socket."""

    def __init__(self, fake):
        self.fake, self.calls = fake, []

    def __call__(self, request, timeout=None):
        path = urllib.parse.urlparse(request.full_url).path[len("/api/"):]
        args = dict(urllib.parse.parse_qsl(request.data.decode("utf-8"))) if request.data else {}
        self.calls.append((path, args))
        return io.BytesIO(json.dumps(self.fake.api(path, args) or {}).encode("utf-8"))


class AdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeTvheadend(Path(self.tmp.name), log=lambda m: None)
        self.fake.clips = [Path(self.tmp.name) / "clip.ts"]
        self.fake.build_dvr(time.time())  # no tick(): that would start ffmpeg recorders
        self.addCleanup(self.fake.stop)
        self.opener = InProcess(self.fake)
        self.client = TvheadendClient("http://tvh", opener=self.opener)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=Path(self.tmp.name) / "hls"),
                          admin=AdminConfig(password=PASSWORD))
        self.hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.gateway = DvbGateway(self.cfg, client=self.client, hls=self.hls, log=lambda m: None)
        self.site = AdminSite(self.cfg, self.gateway)

    def request(self, method, path, headers=None, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        resp = self.site.handle(method, path, AUTH if headers is None else headers, raw)
        data = json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body
        return resp, data

    def hide(self, channel, offered=False):
        resp, data = self.request("POST", f"/admin/api/channels/{channel}", POST, {"offered": offered})
        self.assertEqual(resp.status, 200, data)
        return data["changes"]

    def row(self, channel):
        _, data = self.request("GET", "/admin/api/channels")
        return next(c for c in data["channels"] if c["id"] == channel)

    def app_channels(self):
        resp = self.gateway.handle("/dvb/channels", {}, {"Authorization": "Bearer " + KEY})
        return [c["id"] for c in json.loads(resp.body)["channels"]]

    # -- auth ------------------------------------------------------------------------------
    def test_login_required(self):
        for headers in ({}, {"Authorization": basic("admin", "wrong")}, {"Authorization": basic("root", PASSWORD)},
                        {"Authorization": "Bearer " + KEY}, {"Authorization": "Basic !!!"}):
            resp, _ = self.request("GET", "/admin", headers)
            self.assertEqual(resp.status, 401, headers)
            self.assertIn("Basic", resp.headers["WWW-Authenticate"])
        self.assertEqual(self.opener.calls, [])

    def test_page(self):
        resp, body = self.request("GET", "/admin")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.content_type)
        self.assertIn("frame-ancestors 'none'", resp.headers["Content-Security-Policy"])
        self.assertIn("/admin/api/channels", body.decode("utf-8"))

    def test_post_needs_same_origin(self):
        for headers in (AUTH, {**POST, "Origin": "http://evil.example"}):
            resp, _ = self.request("POST", f"/admin/api/channels/{PRIMA}", headers, {"offered": False})
            self.assertEqual(resp.status, 403)
        resp, _ = self.request("POST", f"/admin/api/channels/{PRIMA}", {**POST, "Origin": "http://pi:8098"},
                               {"offered": False})
        self.assertEqual(resp.status, 200)

    def test_bad_requests(self):
        resp, _ = self.request("POST", f"/admin/api/channels/{PRIMA}", POST, {"offered": "no"})
        self.assertEqual(resp.status, 400)
        resp, _ = self.request("POST", "/admin/api/channels/unknown", POST, {"offered": False})
        self.assertEqual(resp.status, 404)
        resp, _ = self.request("GET", f"/admin/api/channels/{PRIMA}")
        self.assertEqual(resp.status, 405)

    # -- overview --------------------------------------------------------------------------
    def test_overview(self):
        _, data = self.request("GET", "/admin/api/channels")
        self.assertEqual([c["name"] for c in data["channels"]], ["ČT1 HD", "ČT2 HD", "Nova HD", "Prima HD"])
        ct1 = data["channels"][0]
        self.assertEqual((ct1["offered"], ct1["received"], ct1["multiplexes"], ct1["recordingRule"]),
                         (True, True, ["626MHz"], None))
        self.assertGreater(ct1["recordings"], 0)
        self.assertFalse(data["recordingSetUp"])
        self.assertTrue(data["status"]["tvheadendReachable"])

    # -- hide / show -----------------------------------------------------------------------
    def test_hide_last_channel_of_a_multiplex_stops_reception(self):
        changes = self.hide(PRIMA)
        self.assertIn("multiplex 690MHz vypnut", changes)
        self.assertEqual(self.fake.muxes[MUX_PRIMA]["enabled"], 0)
        self.assertFalse(self.fake.services[stable_uuid("service", "Prima HD")]["enabled"])
        row = self.row(PRIMA)
        self.assertEqual((row["offered"], row["received"]), (False, False))
        self.assertNotIn(PRIMA, self.app_channels())

        self.hide(PRIMA, offered=True)
        self.assertEqual(self.fake.muxes[MUX_PRIMA]["enabled"], 1)
        self.assertEqual(self.row(PRIMA)["received"], True)
        self.assertIn(PRIMA, self.app_channels())

    def test_show_repairs_reception_of_an_offered_channel(self):
        # e.g. after an older version re-enabled only the channel: service and multiplex still off
        self.fake.services[stable_uuid("service", "Prima HD")]["enabled"] = False
        self.fake.muxes[MUX_PRIMA]["enabled"] = 0
        self.assertEqual(self.row(PRIMA)["received"], False)
        self.assertEqual(self.hide(PRIMA, offered=True), ["služby zapnuty: 1", "multiplex 690MHz zapnut"])
        self.assertEqual(self.row(PRIMA)["received"], True)

    def test_shared_multiplex_stays_on(self):
        changes = self.hide(CT1)
        self.assertEqual(self.fake.muxes[MUX_CT]["enabled"], 1)  # ČT2 still uses it
        self.assertFalse(any(c.startswith("multiplex") for c in changes))
        self.assertTrue(self.fake.services[stable_uuid("service", "ČT2 HD")]["enabled"])
        self.hide(CT2)
        self.assertEqual(self.fake.muxes[MUX_CT]["enabled"], 0)

    def bulk(self, ids, offered):
        resp, data = self.request("POST", "/admin/api/channels", POST, {"ids": ids, "offered": offered})
        return resp.status, data

    def test_bulk_hide_and_show(self):
        saves = lambda: [args for path, args in self.opener.calls if path == "idnode/save"]  # noqa: E731
        status, data = self.bulk([CT1, CT2, PRIMA], False)
        self.assertEqual(status, 200)
        self.assertEqual(data["changes"][:2], ["kanály vypnuty: 3", "služby vypnuty: 3"])
        self.assertIn("multiplex 626MHz vypnut", data["changes"])  # both of its channels went at once
        self.assertIn("multiplex 690MHz vypnut", data["changes"])
        self.assertEqual(len(json.loads(saves()[0]["node"])), 3)  # one TVHeadend call for all channels
        self.assertEqual(self.app_channels(), [NOVA])
        status, data = self.bulk([CT1, CT2, PRIMA], True)
        self.assertEqual(data["changes"][:2], ["kanály zapnuty: 3", "služby zapnuty: 3"])
        self.assertEqual(sorted(self.app_channels()), sorted([CT1, CT2, NOVA, PRIMA]))
        self.assertEqual({m["enabled"] for m in self.fake.muxes.values()}, {1})

    def test_bulk_validation(self):
        before = len(self.opener.calls)
        status, _ = self.bulk([PRIMA, "unknown"], False)
        self.assertEqual(status, 404)
        self.assertFalse(any(path == "idnode/save" for path, _ in self.opener.calls[before:]))  # nothing changed
        for ids in ([], "x", [1], ["../x"], ["a"] * 501):
            self.assertEqual(self.bulk(ids, False)[0], 400, ids)

    def test_now_playing(self):
        ct1 = self.row(CT1)
        self.assertTrue(ct1["now"]["title"])
        self.assertLess(ct1["now"]["start"], ct1["now"]["end"])
        self.hide(PRIMA)
        self.assertIsNone(self.row(PRIMA)["now"])  # multiplex not received: no EPG

    def test_short_names(self):
        from cztvhub.dvb.admin import short_name
        cases = {"CT 1 PHA HD T2": "ČT1", "CT 24 HD T2": "ČT24", "CT 1 JM HD T2": "ČT1", "CT sport HD T2": "ČT sport",
                 "CT :D/art HD T2": "ČT :D/art", "Nova HD": "Nova", "CS Mystery": "CS Mystery", "DB Test 1": "DB Test 1",
                 "ČT2 HD": "ČT2", "HD": "HD"}
        self.assertEqual({n: short_name(n) for n in cases}, cases)
        self.assertEqual(self.row(CT1)["shortName"], "ČT1")

    def rename(self, payload, path="/admin/api/channels"):
        resp, data = self.request("POST", path, POST, payload)
        return resp.status, data

    def test_rename(self):
        status, data = self.rename({"names": {CT1: " ČT1 ", NOVA: "Nova"}})
        self.assertEqual((status, data["changes"]), (200, ["ČT1 HD → ČT1", "Nova HD → Nova"]))
        channel = next(c for c in self.fake.channels if c["uuid"] == CT1)
        self.assertEqual((channel["name"], channel["autoname"]), ("ČT1", False))
        app = json.loads(self.gateway.handle("/dvb/channels", {}, {"Authorization": "Bearer " + KEY}).body)
        self.assertEqual([c["name"] for c in app["channels"]][:3], ["ČT1", "ČT2 HD", "Nova"])
        self.assertEqual(self.rename({"name": "Prima"}, f"/admin/api/channels/{PRIMA}")[1]["changes"],
                         ["Prima HD → Prima"])
        self.assertEqual(self.rename({"names": {CT1: "ČT1"}})[1]["changes"], [])  # unchanged

    def test_rename_validation(self):
        for payload in ({"names": {CT1: ""}}, {"names": {CT1: "x" * 65}}, {"names": {CT1: "a\tb\x00"}},
                        {"names": {}}, {"names": [CT1]}, {"names": {"../x": "a"}}, {"names": {CT1: 5}}):
            self.assertEqual(self.rename(payload)[0], 400, payload)
        self.assertEqual(self.rename({"names": {CT1: "ČT1", "unknown": "x"}})[0], 404)
        self.assertEqual(next(c for c in self.fake.channels if c["uuid"] == CT1)["name"], "ČT1 HD")

    def test_rename_warns_about_config_names(self):
        self.cfg.dvb.record_channels = ["ČT1 HD"]
        changes = self.rename({"names": {CT1: "ČT1"}})[1]["changes"]
        self.assertIn("record_channels", changes[-1])

    def test_hide_cancels_and_deletes_recordings(self):
        scheduled = sorted((e for e in self.fake.entries.values() if e["channel"] == CT1
                            and e["sched_status"] == "scheduled"), key=lambda e: e["start"])
        scheduled[0]["sched_status"] = "recording"  # the airing programme
        changes = self.hide(CT1)
        self.assertIn(f"zrušeno plánovaných nahrávek: {len(scheduled)}", changes)
        self.assertTrue(any(c.startswith("smazáno nahrávek") for c in changes), changes)
        # the aborted running recording is deleted too
        self.assertEqual([e for e in self.fake.entries.values() if e["channel"] == CT1], [])
        self.assertEqual(self.hide(CT1), [])  # idempotent

    def test_recording_rules_follow_when_set_up(self):
        run_setup(self.client, self.cfg.dvb, out=lambda m: None)
        rule = next(r for r in self.fake.autorecs.values() if r["channel"] == NOVA)
        self.assertIn("nahrávání vypnuto", self.hide(NOVA))
        self.assertFalse(rule["enabled"])
        self.assertEqual(self.row(NOVA)["recordingRule"], False)
        # dvb-setup keeps the hidden channel's rule, disabled
        run_setup(self.client, self.cfg.dvb, out=lambda m: None)
        self.assertIn(rule["uuid"], self.fake.autorecs)
        self.assertFalse(rule["enabled"])
        self.assertIn("nahrávání zapnuto", self.hide(NOVA, offered=True))
        self.assertTrue(rule["enabled"])

    def test_no_rules_created_before_dvb_setup(self):
        self.hide(NOVA)
        self.hide(NOVA, offered=True)
        self.assertEqual(self.fake.autorecs, {})

    def test_gateway_drops_hidden_channel_from_epg(self):
        app = {"Authorization": "Bearer " + KEY}
        events = json.loads(self.gateway.handle("/dvb/epg", {}, app).body)["events"]
        self.assertIn(CT1, {e["channel"] for e in events})
        self.hide(CT1)
        events = json.loads(self.gateway.handle("/dvb/epg", {}, app).body)["events"]
        self.assertNotIn(CT1, {e["channel"] for e in events})

    def test_hidden_recordings_not_playable(self):
        # Channel disabled directly in TVHeadend's UI: its recordings still exist but are not offered.
        uuid = next(u for u, e in self.fake.entries.items() if e["channel"] == CT2 and e["sched_status"] == "completed")
        next(c for c in self.fake.channels if c["uuid"] == CT2)["enabled"] = False
        resp = self.gateway.handle(f"/dvb/recording/{uuid}.m3u8", {}, {"Authorization": "Bearer " + KEY})
        self.assertEqual(resp.status, 404)


class AdminHttpTest(unittest.TestCase):
    def start(self, admin):
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda c: "x"), None, admin))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def call(self, port, method, path, headers=None, body=b""):
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_admin_off_by_default(self):
        port = self.start(None)
        self.assertEqual(self.call(port, "GET", "/admin")[0], 404)
        self.assertEqual(self.call(port, "POST", "/admin/api/channels/x", body=b"{}")[0], 404)
        self.assertEqual(self.call(port, "POST", "/playlist.m3u", body=b"{}")[0], 400)

    def test_routes_to_admin(self):
        class Site:
            def __init__(self):
                self.seen = []

            def handle(self, method, path, headers, body):
                self.seen.append((method, path, body))
                from cztvhub.dvb.gateway import json_response
                return json_response(200, {})

        site = Site()
        port = self.start(site)
        self.assertEqual(self.call(port, "GET", "/admin")[0], 200)
        self.assertEqual(self.call(port, "POST", "/admin/api/channels/x?y=1", body=b'{"offered": true}')[0], 200)
        self.assertEqual(site.seen, [("GET", "/admin", b""), ("POST", "/admin/api/channels/x", b'{"offered": true}')])


if __name__ == "__main__":
    unittest.main()
