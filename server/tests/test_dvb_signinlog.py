"""Sign-in and connection log (spec/dvb-gateway.md "Sign-in log", T37): store, gateway hooks, /admin tab
"Přihlášení", trimming at start, and the HTTP server's journal (no per-request log, O019)."""
import argparse
import base64
import contextlib
import hashlib
import http.client
import io
import json
import os
import socket
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cztvhub import cli, logsafe
from cztvhub.api import StreamResolver
from cztvhub.config import AdminConfig, AdsConfig, Config, DvbConfig
from cztvhub.dvb import profiles as prof
from cztvhub.dvb import signinlog as sl
from cztvhub.dvb.admin import AdminSite
from cztvhub.dvb.gateway import DvbGateway, Response
from cztvhub.dvb.hls import Codecs, HlsManager, HlsSession
from cztvhub.dvb.tvheadend import TvhError
from cztvhub.dvb.profiles import ProfileStore
from cztvhub.dvb.signinlog import SigninLog
from cztvhub.server import Server, make_handler
from dvb_fixtures import NOW, FakeClient

KEY = "app-key-" * 5
WORKER_KEY = "worker-key-" * 4
ADMIN_PASSWORD = "admin-password-1"
APP = {"Authorization": "Bearer " + KEY}
SETTINGS = {"v": 1, "channelPrefs": {"order": [], "hidden": [], "sourcesOff": []}, "thumbnails": True}
CHROME_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                 "Version/18.1 Mobile/15E148 Safari/604.1")
EDGE_WINDOWS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
                "Safari/537.36 Edg/130.0.0.0")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
CHROME_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0.0.0 Mobile Safari/537.36")
CHROME_IPAD = ("Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
               "CriOS/130.0.6723.90 Mobile/15E148 Safari/604.1")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class ClientHeaderTest(unittest.TestCase):
    def test_valid_header_and_platform(self):
        for header, platform in (("tvos/1.4 (57)", "tvos"), ("androidtv/2.0.1 (210)", "androidtv"),
                                 ("tizen/1.0 (3)", "tizen"), ("web/2026.11.01", "web"), ("kodi/21.1", "unknown"),
                                 ("tvos", "tvos"), ("tvosx/1", "unknown")):
            info = sl.client_info({sl.CLIENT_HEADER: header})
            self.assertEqual((info["client"], info["platform"]), (header, platform), header)

    def test_invalid_header_is_ignored(self):
        for header in ("tvos/" + "1" * 60, "tvos/1.4 <script>", "tvos/1.4\nx", "tvos/1.4;rm", "tvos/ž", ""):
            info = sl.client_info({sl.CLIENT_HEADER: header})
            self.assertEqual((info["client"], info["platform"]), (None, "unknown"), header)
        self.assertEqual(sl.client_info({sl.CLIENT_HEADER: "a" * 64})["client"], "a" * 64)  # the limit itself
        self.assertIsNone(sl.client_info({sl.CLIENT_HEADER: "a" * 65})["client"])

    def test_browser_only_for_web_and_unknown(self):
        self.assertEqual(sl.client_info({sl.CLIENT_HEADER: "web/1", "User-Agent": CHROME_MAC})["browser"], "Chrome")
        self.assertEqual(sl.client_info({"User-Agent": SAFARI_IPHONE})["system"], "iPhone")  # no header: unknown
        info = sl.client_info({sl.CLIENT_HEADER: "androidtv/2.0 (1)", "User-Agent": CHROME_ANDROID})
        self.assertEqual((info["browser"], info["system"]), (None, None))

    def test_coarse_browser(self):
        cases = {CHROME_MAC: ("Chrome", "macOS"), SAFARI_IPHONE: ("Safari", "iPhone"),
                 EDGE_WINDOWS: ("Edge", "Windows"), FIREFOX_LINUX: ("Firefox", "Linux"),
                 CHROME_ANDROID: ("Chrome", "Android"), CHROME_IPAD: ("Chrome", "iPad"),
                 "curl/8.7.1": (None, None), "Kodi/21.1 (Linux; Android 12)": (None, "Android"),
                 "": (None, None), CHROME_MAC + " " * 600: (None, None)}  # too long: not looked at
        for agent, expected in cases.items():
            self.assertEqual(sl.coarse_browser(agent), expected, agent)


class NetworkTest(unittest.TestCase):
    def test_kinds(self):
        cases = {"192.168.1.20": "home", "10.0.0.5": "home", "172.16.3.4": "home", "fe80::1%en0": "home",
                 "fd12:3456::1": "home", "::ffff:192.168.1.5": "home", "100.101.102.103": "tailscale",
                 "fd7a:115c:a1e0::1": "tailscale", "127.0.0.1": "local", "::1": "local", "8.8.8.8": "other",
                 "2a00:1450::1": "other", "": "other", "nonsense": "other"}
        for peer, kind in cases.items():
            self.assertEqual(sl.network(peer), kind, peer)


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.wall = Clock(NOW)
        self.clock = Clock()
        self.logs = []
        self.log = self.make()

    def make(self):
        return SigninLog(self.dir, now=self.wall, clock=self.clock, log=self.logs.append)

    def lines(self):
        path = self.dir / sl.FILE
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def advance(self, seconds):
        self.wall.t += seconds
        self.clock.t += seconds

    def test_connection_fields(self):
        self.log.connection("192.168.1.20", {sl.CLIENT_HEADER: "tvos/1.4 (57)", "User-Agent": "AppleCoreMedia"})
        self.log.connection("100.101.102.103", {sl.CLIENT_HEADER: "web/1", "User-Agent": CHROME_MAC})
        self.log.connection("8.8.8.8", {})
        self.assertEqual(self.lines(), [
            {"time": "2026-10-18T05:30:00Z", "event": "connect", "address": "192.168.1.20", "network": "home",
             "client": "tvos/1.4 (57)", "platform": "tvos"},
            {"time": "2026-10-18T05:30:00Z", "event": "connect", "address": "100.101.102.103",
             "network": "tailscale", "client": "web/1", "platform": "web", "browser": "Chrome", "system": "macOS"},
            {"time": "2026-10-18T05:30:00Z", "event": "connect", "address": "8.8.8.8", "network": "other",
             "platform": "unknown"}])
        self.assertNotIn("AppleWebKit", (self.dir / sl.FILE).read_text())  # never the full User-Agent

    def test_file_is_private(self):
        self.log.connection("192.168.1.20", {})
        self.assertEqual(stat.S_IMODE((self.dir / sl.FILE).stat().st_mode), 0o600)
        os.chmod(self.dir / sl.FILE, 0o644)
        self.log.connection("192.168.1.21", {})  # an append repairs the mode
        self.assertEqual(stat.S_IMODE((self.dir / sl.FILE).stat().st_mode), 0o600)
        os.chmod(self.dir / sl.FILE, 0o644)
        sl.trim_file(self.dir, NOW)  # so does the trim at start
        self.assertEqual(stat.S_IMODE((self.dir / sl.FILE).stat().st_mode), 0o600)

    def test_connection_once_per_12_hours_per_address_and_platform(self):
        tv = {sl.CLIENT_HEADER: "tvos/1.4 (57)"}
        for _ in range(5):
            self.log.connection("192.168.1.20", tv)
        self.log.connection("192.168.1.20", {sl.CLIENT_HEADER: "tvos/1.5 (58)"})  # same platform, other version
        self.log.connection("192.168.1.20", {sl.CLIENT_HEADER: "web/1"})  # another platform
        self.log.connection("192.168.1.21", tv)  # another address
        self.assertEqual(len(self.lines()), 3)
        self.advance(sl.CONNECTION_SECONDS - 1)
        self.log.connection("192.168.1.20", tv)
        self.assertEqual(len(self.lines()), 3)
        self.advance(1)
        self.log.connection("192.168.1.20", tv)
        self.assertEqual(len(self.lines()), 4)

    def test_restart_forgets_the_dedup(self):
        self.log.connection("192.168.1.20", {})
        self.make().connection("192.168.1.20", {})
        self.assertEqual(len(self.lines()), 2)

    def test_at_most_20_connections_per_address_per_day(self):
        # With 12 h per platform the cap is reached only once the dedup forgets (bounded memory, restart); a short
        # dedup shows the cap itself.
        with mock.patch.object(sl, "CONNECTION_SECONDS", 60):
            for _ in range(30):
                self.log.connection("192.168.1.20", {})
                self.advance(61)
            self.log.connection("192.168.1.99", {})  # another address is not affected
            events = [e["event"] for e in self.lines()]
            self.assertEqual(events, ["connect"] * 20 + ["connectLimit", "connect"])
            self.assertEqual(self.lines()[20], {"time": self.lines()[20]["time"], "event": "connectLimit",
                                                "address": "192.168.1.20", "network": "home"})
            self.advance(sl.ADDRESS_WINDOW)  # the window moved on: entries again
            self.log.connection("192.168.1.20", {})
            self.assertEqual(self.lines()[-1]["event"], "connect")

    def test_at_most_20_failed_sign_ins_per_address_per_day(self):
        for i in range(15):
            self.log.login_failed(None, "192.168.1.20", {})  # unknown names
        for i in range(10):
            self.log.login_failed("Jana", "192.168.1.20", {})  # wrong passwords: the same cap
        self.log.login_locked("x", None, "192.168.1.20", {})  # too many attempts: also capped
        self.log.login_failed(None, "192.168.1.99", {})  # another address is not affected
        self.log.login("Jana", "192.168.1.20", {})  # a success is never capped
        self.log.connection("192.168.1.20", {})  # nor a connection
        events = [e["event"] for e in self.lines()]
        self.assertEqual(events, ["loginFailed"] * 20 + ["loginFailedLimit", "loginFailed", "login", "connect"])
        self.assertEqual({k: v for k, v in self.lines()[20].items() if k != "time"},
                         {"event": "loginFailedLimit", "address": "192.168.1.20", "network": "home"})
        self.advance(sl.ADDRESS_WINDOW)
        self.log.login_failed(None, "192.168.1.20", {})
        self.assertEqual(self.lines()[-1]["event"], "loginFailed")

    def test_many_names_locked_are_capped_too(self):
        for i in range(30):
            self.log.login_locked(f"name{i}", None, "10.0.0.7", {})
        self.advance(sl.LOCKED_SECONDS)
        for i in range(30):
            self.log.login_locked(f"name{i}", None, "10.0.0.7", {})
        self.log.page(1)
        events = [e["event"] for e in self.lines()]
        self.assertEqual(events, ["loginLocked"] * 20 + ["loginFailedLimit"])

    def test_dedup_memory_is_bounded(self):
        with mock.patch.object(sl, "MAX_TRACKED", 10):
            for i in range(50):
                self.log.connection(f"10.0.0.{i}", {})
            self.assertLessEqual(len(self.log._seen), 10)
            self.assertLessEqual(len(self.log._per_address), 10)

    def test_profile_events(self):
        h = {sl.CLIENT_HEADER: "androidtv/2.0 (210)"}
        self.log.login("Jana", "192.168.1.20", h)
        self.log.login_failed("Jana", "192.168.1.20", h)
        self.log.login_failed(None, "192.168.1.20", h)
        self.log.profile_created("Petr", "100.64.0.9", h)
        self.log.logout("Petr", "100.64.0.9", h)
        self.log.sessions_ended("Jana", "newPassword", 2, "127.0.0.1", {"User-Agent": FIREFOX_LINUX})
        self.log.sessions_ended("Jana", "deleted", 0, "127.0.0.1", {})  # nothing ended: nothing written
        got = [{k: v for k, v in e.items() if k != "time"} for e in self.lines()]
        base = {"address": "192.168.1.20", "network": "home", "client": "androidtv/2.0 (210)",
                "platform": "androidtv"}
        tail = {"address": "100.64.0.9", "network": "tailscale", "client": "androidtv/2.0 (210)",
                "platform": "androidtv"}
        self.assertEqual(got, [
            {"event": "login", "profile": "Jana", **base},
            {"event": "loginFailed", "profile": "Jana", **base},
            {"event": "loginFailed", **base},
            {"event": "profileCreated", "profile": "Petr", **tail},
            {"event": "logout", "profile": "Petr", **tail},
            {"event": "sessionsEnded", "profile": "Jana", "address": "127.0.0.1", "network": "local",
             "platform": "unknown", "browser": "Firefox", "system": "Linux", "reason": "newPassword", "count": 2}])

    def test_too_many_attempts_once_a_minute_with_the_count(self):
        h = {}
        self.log.login_locked("jana", "Jana", "192.168.1.20", h)
        for _ in range(7):
            self.advance(5)
            self.log.login_locked("jana", "Jana", "192.168.1.20", h)
        self.log.login_locked("petr", None, "192.168.1.21", h)  # another name: its own minute
        self.assertEqual([(e["event"], e.get("profile"), e["count"]) for e in self.lines()],
                         [("loginLocked", "Jana", 1), ("loginLocked", None, 1)])
        self.advance(30)  # 65 s after the first entry
        self.log.login_locked("jana", "Jana", "192.168.1.20", h)
        self.assertEqual(self.lines()[-1]["count"], 8)  # the 7 counted plus this one
        for _ in range(3):
            self.log.login_locked("jana", "Jana", "192.168.1.20", h)
        self.assertEqual(len(self.lines()), 3)
        self.advance(sl.LOCKED_SECONDS)
        self.log.connection("192.168.1.30", {})  # any later entry writes the pending count first
        self.assertEqual([(e["event"], e.get("count")) for e in self.lines()[-2:]],
                         [("loginLocked", 3), ("connect", None)])

    def test_admin_read_writes_pending_counts(self):
        self.log.login_locked("jana", "Jana", "192.168.1.20", {})
        self.advance(10)
        self.log.login_locked("jana", "Jana", "192.168.1.20", {})
        self.log.login_locked("jana", "Jana", "192.168.1.20", {})
        page = self.log.page(1)
        self.assertEqual([e["count"] for e in page["entries"]], [2, 1])  # newest first
        self.assertEqual(len(self.lines()), 2)

    def test_page_filter_and_order(self):
        for i in range(sl.PAGE_SIZE + 5):
            self.advance(1)
            self.log.login("Jana" if i % 2 else "Petr", f"10.0.1.{i}", {})
        self.log.login_failed(None, "10.0.2.1", {})
        first = self.log.page(1)
        self.assertEqual((first["total"], first["pages"], len(first["entries"])), (sl.PAGE_SIZE + 6, 2, sl.PAGE_SIZE))
        self.assertEqual(first["entries"][0]["event"], "loginFailed")
        times = [e["time"] for e in first["entries"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(self.log.page(2)["entries"]), 6)
        self.assertEqual(first["profiles"], ["Jana", "Petr"])
        jana = self.log.page(1, "Jana")
        self.assertEqual(jana["total"], (sl.PAGE_SIZE + 5) // 2)
        self.assertTrue(all(e["profile"] == "Jana" for e in jana["entries"]))
        self.assertEqual(set(first["entries"][0]), set(sl.FIELDS))

    def test_newest_1000_kept(self):
        for i in range(sl.MAX_ENTRIES + sl.SLACK + 1):
            self.log.login("Jana", f"10.{i // 250}.0.{i % 250}", {})
        self.assertLessEqual(len(self.lines()), sl.MAX_ENTRIES + sl.SLACK)
        page = self.log.page(1)
        self.assertEqual(page["total"], sl.MAX_ENTRIES)
        self.assertEqual(page["entries"][0]["address"], "10.4.0.100")  # the newest (entry 1100)
        sl.trim_file(self.dir, self.wall())  # a restart leaves exactly the newest 1 000
        self.assertEqual(len(self.lines()), sl.MAX_ENTRIES)
        self.assertEqual(self.lines()[-1]["address"], "10.4.0.100")

    def test_older_than_90_days_dropped_when_written(self):
        self.log.login("Jana", "10.0.0.1", {})
        self.advance(sl.MAX_AGE + 1)
        self.log.login("Petr", "10.0.0.2", {})
        self.assertEqual([e["profile"] for e in self.lines()], ["Petr"])

    def test_daily_trim_and_read_drop_old_entries(self):
        self.log.login("Jana", "10.0.0.1", {})
        self.advance(sl.MAX_AGE + 1)
        self.log.trim()
        self.assertEqual(self.lines(), [])
        self.log.login("Jana", "10.0.0.1", {})
        self.advance(sl.MAX_AGE + 1)
        self.assertEqual(self.log.page(1)["total"], 0)
        self.assertEqual(self.lines(), [])

    def test_trim_at_start(self):
        old = {"time": "2026-07-01T10:00:00Z", "event": "login", "profile": "Stará", "address": "10.0.0.1",
               "network": "home"}
        young = [{"time": f"2026-10-17T10:{m:02d}:00Z", "event": "connect", "address": "10.0.0.2",
                  "network": "home"} for m in range(5)]
        path = self.dir / sl.FILE
        path.write_text("\n".join([json.dumps(old), "not json", json.dumps({"event": "x"})]
                                  + [json.dumps(e) for e in young]) + "\n", encoding="utf-8")
        os.chmod(path, 0o644)
        with mock.patch.object(sl, "MAX_ENTRIES", 3):
            sl.trim_file(self.dir, NOW)
        self.assertEqual(self.lines(), young[-3:])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        sl.trim_file(self.dir / "nothing-here", NOW)  # no file: nothing created
        self.assertFalse((self.dir / "nothing-here").exists())

    def test_trim_at_start_failure_does_not_stop_the_service(self):
        (self.dir / sl.FILE).write_text("{}\n", encoding="utf-8")
        with mock.patch.object(sl, "write_entries", side_effect=OSError("read-only")):
            sl.trim_file(self.dir, NOW, self.logs.append)
        self.assertTrue(any("cannot trim" in m for m in self.logs))

    def test_unwritable_file_is_logged_not_raised(self):
        (self.dir / sl.FILE).mkdir()  # a directory where the file should be
        self.log.login("Jana", "10.0.0.1", {})
        self.assertTrue(any("cannot write" in m for m in self.logs))

    def test_delete_all(self):
        self.log.connection("10.0.0.1", {})
        self.log.login("Jana", "10.0.0.1", {})
        self.assertEqual(self.log.delete_all(), 2)
        self.assertFalse((self.dir / sl.FILE).exists())
        self.assertEqual(self.log.page(1)["total"], 0)
        self.log.connection("10.0.0.1", {})  # the dedup started afresh too
        self.assertEqual(len(self.lines()), 1)


class LogSafeTest(unittest.TestCase):
    """Journal lines name no channel, recording, programme or session (coordinator decision 2026-11-01)."""

    def test_route(self):
        self.assertEqual(logsafe.route("/dvb/live/0123456789abcdef0123456789abcdef.m3u8?x=1"), "/dvb/live/*.m3u8")
        self.assertEqual(logsafe.route("/dvb/status"), "/dvb/status")
        self.assertEqual(logsafe.route("/dvb/profiles/login"), "/dvb/profiles/login")
        self.assertEqual(logsafe.route("/dvb/archive/show/ct:123"), "/dvb/archive/show/*")
        self.assertEqual(logsafe.route("/admin/api/channels/x"), "/admin/api/channels/x")  # not playback
        self.assertEqual(logsafe.route("/dvb/live/\x01" + "x" * 300), "/dvb/live/*")

    def test_scrub(self):
        uuid = "0123456789abcdef0123456789abcdef"
        for text in (f"/stream/channel/{uuid}?profile=pass: HTTP 503", f"/dvrfile/{uuid}: timed out",
                     f"/api/dvr/entry/grid: {uuid} gone", "rec ch1-1792300800-1792304400 failed"):
            out = logsafe.scrub(text)
            self.assertNotIn(uuid, out, text)
            self.assertNotIn("1792300800", out, text)
        self.assertEqual(logsafe.scrub("/stream/channel/x?profile=pass: HTTP 503"), "/stream/channel/…: HTTP 503")
        self.assertEqual(logsafe.scrub("ffmpeg exited before the first segment"),
                         "ffmpeg exited before the first segment")

    def test_session_kind(self):
        self.assertEqual(logsafe.session_kind("live:abc"), "a live session")
        self.assertEqual(logsafe.session_kind("rec:abc"), "a recording session")
        self.assertEqual(logsafe.session_kind("x"), "a session")


class PlaybackLogLinesTest(unittest.TestCase):
    """Every gateway log line on playback: the timing or the error, never which channel or recording."""
    UUID = "0123456789abcdef0123456789abcdef"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=d / "hls", state_dir=d))
        self.logs = []
        self.hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=self.logs.append)
        self.addCleanup(self.hls.shutdown)
        self.gw = DvbGateway(cfg, client=FakeClient(), hls=self.hls, now=lambda: NOW, log=self.logs.append,
                             profiles=ProfileStore(d, iterations=1, log=self.logs.append))

    def assert_anonymous(self):
        self.assertTrue(self.logs)
        for line in self.logs:
            self.assertNotIn(self.UUID, line)
            self.assertNotIn("ch1", line)

    def test_live_start_line(self):
        session = HlsSession("s" * 32, f"live:{self.UUID}", Path("/nonexistent"), True, None, None, None)
        session.timing = {"start": 1.0, "opened": 1.4, "first_bytes": 1.6, "first_segment": 3.0}
        self.gw._log_startup(session, 6.0)
        (line,) = self.logs
        self.assertTrue(line.startswith("dvb: live start: tuner+TVHeadend +0.4s, first bytes +0.2s"), line)
        self.assert_anonymous()

    def test_idle_line(self):
        session = HlsSession("s" * 32, f"rec:{self.UUID}", Path("/nonexistent"), False, None, None, None)
        session.last_access -= 3600
        with mock.patch.object(self.hls, "sessions", return_value=[session]), \
                mock.patch.object(self.hls, "_remove") as remove:
            self.hls.reap()
        remove.assert_called_once_with(session)
        self.assertEqual(self.logs, ["hls: a recording session idle, removing"])
        self.assert_anonymous()

    def test_codec_line(self):
        with mock.patch.object(self.hls, "open", side_effect=lambda key, factory: factory("s" * 32, Path("/x"))):
            session = self.gw._session(f"live:{self.UUID}", True, lambda: None)
        session._build_command(Path("/x"), Codecs("mpeg2video", "mp2"))
        self.assertEqual(self.logs, ["dvb: a live session: unexpected video codec 'mpeg2video'"])
        self.assert_anonymous()

    def test_error_line(self):
        err = TvhError(f"/stream/channel/{self.UUID}?profile=pass: HTTP 500")
        with mock.patch.object(self.gw, "live", side_effect=err):
            resp = self.gw.handle(f"/dvb/live/{self.UUID}.m3u8", {}, APP, "GET", b"", "192.168.1.20")
        self.assertEqual(resp.status, 502)
        self.assertEqual(self.logs, ["dvb: /dvb/live/*.m3u8: /stream/channel/…: HTTP 500"])
        self.assert_anonymous()

    def test_thumbnail_line(self):
        from cztvhub.dvb.thumbs import Thumbnails

        class Client:
            def open_recording(self, uuid, offset=0):
                raise TvhError(f"/dvrfile/{uuid}: HTTP 500")

        thumbs = Thumbnails(Client(), self.gw.settings, "ffmpeg", log=self.logs.append)
        self.assertIsNone(thumbs._make(self.UUID, 0))
        self.assertEqual(self.logs, ["dvb: thumbnail: /dvrfile/…: HTTP 500"])
        self.assert_anonymous()

    def test_ct_web_line(self):
        def fail(url, body=None):
            raise sl.ctweb.Unreachable("timeout")
        self.gw.ct_web.fetch = fail
        resp = self.gw.handle("/dvb/ct/vod/123456789012", {}, APP, "GET", b"", "192.168.1.20")
        self.assertEqual(resp.status, 502)
        self.assertEqual(self.logs, ["dvb: ct web: vod: timeout"])
        self.assertNotIn("123456789012", "\n".join(self.logs))


class ServeTrimsAtStartTest(unittest.TestCase):
    def test_cmd_serve_trims_also_without_dvb(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            old = {"time": "2020-01-01T00:00:00Z", "event": "login", "profile": "X", "address": "10.0.0.1",
                   "network": "home"}
            (state / sl.FILE).write_text(json.dumps(old) + "\n", encoding="utf-8")
            conf = state / "c.conf"
            conf.write_text(f"[dvb]\nenabled = false\nstate_dir = {state}\n", encoding="utf-8")
            with mock.patch.object(cli, "serve") as serve, mock.patch.object(cli, "_sigterm_as_interrupt"):
                cli.cmd_serve(argparse.Namespace(config=str(conf), host=None, port=None))
            serve.assert_called_once()
            self.assertIsNone(serve.call_args.kwargs["gateway"])  # CT-only server
            self.assertEqual((state / sl.FILE).read_text(encoding="utf-8"), "")


class GatewaySigninTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.dir / "hls", state_dir=self.dir),
                          ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=self.dir / "breaks"),
                          admin=AdminConfig(password=ADMIN_PASSWORD))
        self.clock = Clock()
        self.logs = []
        self.store = ProfileStore(self.dir, iterations=1, now=lambda: NOW, clock=self.clock, log=self.logs.append)
        self.signin = SigninLog(self.dir, now=lambda: NOW, clock=self.clock, log=self.logs.append)
        hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(self.cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=self.logs.append,
                             profiles=self.store, signin=self.signin)

    def call(self, method, path, body=None, headers=None, client="192.168.1.20", query=None):
        data = json.dumps(body).encode() if body is not None else b""
        resp = self.gw.handle(path, query or {}, APP if headers is None else headers, method, data, client)
        return resp, (json.loads(resp.body) if resp.body else None)

    def entries(self):
        return [{k: v for k, v in e.items() if k != "time" and v is not None}
                for e in reversed(self.signin.page(1)["entries"])]

    def raw(self):
        path = self.dir / sl.FILE
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_status_is_a_connection(self):
        hdr = {**APP, sl.CLIENT_HEADER: "tizen/1.0 (3)"}
        self.assertEqual(self.call("GET", "/dvb/status", headers=hdr)[0].status, 200)
        self.assertEqual(self.call("GET", "/dvb/status", headers=APP, client="100.70.1.2")[0].status, 200)
        self.assertEqual(self.entries(), [
            {"event": "connect", "address": "192.168.1.20", "network": "home", "client": "tizen/1.0 (3)",
             "platform": "tizen"},
            {"event": "connect", "address": "100.70.1.2", "network": "tailscale", "platform": "unknown"}])
        self.assertNotIn("192.168.1.20", self.call("GET", "/dvb/status")[0].body.decode())

    def test_status_without_the_key_or_with_the_worker_key_is_not_logged(self):
        self.assertEqual(self.call("GET", "/dvb/status", headers={})[0].status, 401)
        self.assertEqual(self.call("GET", "/dvb/status", headers={"Authorization": "Bearer wrong"})[0].status, 401)
        worker = {"Authorization": "Bearer " + WORKER_KEY, sl.CLIENT_HEADER: "web/1"}
        self.assertEqual(self.call("GET", "/dvb/status", headers=worker)[0].status, 403)
        self.assertEqual(self.raw(), "")
        self.assertNotIn(WORKER_KEY, "\n".join(self.logs))

    def test_header_is_ignored_on_other_routes(self):
        hdr = {**APP, sl.CLIENT_HEADER: "tvos/9.9 (999)", "User-Agent": CHROME_MAC}
        token = self.call("POST", "/dvb/profiles", {"name": "Jana", "password": "1234", "settings": SETTINGS})[1][
            "token"]
        self.signin.delete_all()
        for path in ("/dvb/channels", "/dvb/epg", "/dvb/defaults", "/dvb/live/ch1.m3u8", "/dvb/recording/x.m3u8",
                     "/dvb/details", "/dvb/archive", "/dvb/hls/" + "0" * 32 + "/a.ts", "/dvb/v/x/index.m3u8",
                     "/dvb/timeline/x.m3u8", "/dvb/icon/ch1", "/dvb/ct/schedule"):
            self.call("GET", path, headers=hdr)
        self.call("GET", "/dvb/profile", headers={**hdr, "X-Cztvhub-Profile": token})
        self.call("PUT", "/dvb/profile", {"settings": SETTINGS}, headers={**hdr, "X-Cztvhub-Profile": token})
        self.assertEqual(self.raw(), "")
        self.assertFalse(any("9.9 (999)" in m for m in self.logs))  # written nowhere else either

    def test_profile_events(self):
        hdr = {**APP, sl.CLIENT_HEADER: "web/2026.11", "User-Agent": SAFARI_IPHONE}
        token = self.call("POST", "/dvb/profiles", {"name": "Jana", "password": "tajne-heslo", "settings": SETTINGS},
                          headers=hdr)[1]["token"]
        self.assertEqual(self.call("POST", "/dvb/profiles/login", {"name": "jána", "password": "tajne-heslo"},
                                   headers=hdr)[0].status, 200)
        self.assertEqual(self.call("POST", "/dvb/profiles/login", {"name": "Jana", "password": "spatne"},
                                   headers=hdr)[0].status, 401)
        self.assertEqual(self.call("POST", "/dvb/profiles/login", {"name": "Tajnenapsane", "password": "x1234"},
                                   headers=hdr)[0].status, 401)
        self.assertEqual(self.call("POST", "/dvb/profile/logout", headers={**hdr, "X-Cztvhub-Profile": token})[0]
                         .status, 204)
        web = {"address": "192.168.1.20", "network": "home", "client": "web/2026.11", "platform": "web",
               "browser": "Safari", "system": "iPhone"}
        self.assertEqual(self.entries(), [
            {"event": "profileCreated", "profile": "Jana", **web},
            {"event": "login", "profile": "Jana", **web},
            {"event": "loginFailed", "profile": "Jana", **web},
            {"event": "loginFailed", **web},
            {"event": "logout", "profile": "Jana", **web}])
        raw = self.raw()
        for secret in ("tajne-heslo", "spatne", "x1234", "Tajnenapsane", token,
                       hashlib.sha256(token.encode()).hexdigest()):
            self.assertNotIn(secret, raw)

    def test_refused_requests_write_nothing(self):
        self.call("POST", "/dvb/profiles", {"name": "", "password": "1234", "settings": SETTINGS})  # 400
        self.call("POST", "/dvb/profiles/login", None)  # 400, no body
        self.call("POST", "/dvb/profile/logout", headers={**APP, "X-Cztvhub-Profile": "nope"})  # 401
        cookie = {"Cookie": "cztvhub_key=" + KEY}
        self.call("POST", "/dvb/profiles/login", {"name": "Jana", "password": "1234"}, headers=cookie)  # 403
        self.assertEqual(self.raw(), "")

    def test_too_many_attempts(self):
        self.call("POST", "/dvb/profiles", {"name": "Jana", "password": "1234", "settings": SETTINGS})
        for _ in range(prof.FAIL_LIMIT):
            self.call("POST", "/dvb/profiles/login", {"name": "Jana", "password": "wrong"})
        for _ in range(4):
            self.assertEqual(self.call("POST", "/dvb/profiles/login", {"name": "JANA", "password": "1234"})[0]
                             .status, 429)
        for _ in range(prof.FAIL_LIMIT):
            self.call("POST", "/dvb/profiles/login", {"name": "Neznámá", "password": "wrong"})
        self.call("POST", "/dvb/profiles/login", {"name": "Neznámá", "password": "wrong"})
        got = [(e["event"], e.get("profile"), e.get("count")) for e in self.entries()]
        self.assertEqual(got, [("profileCreated", "Jana", None)] + [("loginFailed", "Jana", None)] * prof.FAIL_LIMIT
                         + [("loginLocked", "Jana", 1)] + [("loginFailed", None, None)] * prof.FAIL_LIMIT
                         + [("loginLocked", None, 1), ("loginLocked", "Jana", 3)])  # the 3 pending, on reading
        self.assertNotIn("Neznámá", self.raw() + "\n".join(self.logs))  # the typed name: nowhere

    def test_admin_ends_sessions(self):
        self.call("POST", "/dvb/profiles", {"name": "Jana", "password": "1234", "settings": SETTINGS})
        self.call("POST", "/dvb/profiles/login", {"name": "Jana", "password": "1234"})
        self.call("POST", "/dvb/profiles", {"name": "Petr", "password": "1234", "settings": SETTINGS})
        self.call("POST", "/dvb/profiles", {"name": "Eva", "password": "1234", "settings": SETTINGS})
        ids = {p["name"]: p["id"] for p in self.store.overview()}
        self.signin.delete_all()
        site = AdminSite(self.cfg, self.gw, admin=object())
        auth = {"Authorization": "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode(),
                "X-Cztvhub-Admin": "1", "Host": "pi:8098", "User-Agent": EDGE_WINDOWS}
        for pid, body in ((ids["Jana"], {"password": "nove-heslo"}), (ids["Petr"], {"delete": True}),
                          (ids["Jana"], {"password": "jeste-jine"})):  # no session left: nothing written
            self.assertEqual(site.handle("POST", f"/admin/api/profiles/{pid}", auth, json.dumps(body).encode(),
                                         {}, "192.168.1.5").status, 200)
        admin = {"address": "192.168.1.5", "network": "home", "platform": "unknown", "browser": "Edge",
                 "system": "Windows"}
        self.assertEqual(self.entries(), [
            {"event": "sessionsEnded", "profile": "Jana", "reason": "newPassword", "count": 2, **admin},
            {"event": "sessionsEnded", "profile": "Petr", "reason": "deleted", "count": 1, **admin}])
        self.assertNotIn("nove-heslo", self.raw())

    def test_no_dvb_route_returns_the_log(self):
        self.call("POST", "/dvb/profiles/login", {"name": "Jana", "password": "1234"}, client="192.168.7.77")
        self.assertIn("192.168.7.77", self.raw())
        for path in ("/dvb/signin", "/dvb/signin-log.jsonl", "/dvb/admin/api/signin", "/dvb/v/../signin-log.jsonl",
                     "/dvb/status", "/dvb/profile"):
            resp, _ = self.call("GET", path)
            self.assertNotIn("192.168.7.77", resp.body.decode("utf-8", "replace"), path)
            if path != "/dvb/status":
                self.assertGreaterEqual(resp.status, 400, path)


class AdminSigninTabTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=d / "hls", state_dir=d),
                          ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=d / "breaks"),
                          admin=AdminConfig(password=ADMIN_PASSWORD))
        self.logs = []
        hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(self.cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=self.logs.append,
                             profiles=ProfileStore(d, iterations=1, log=self.logs.append))
        self.site = AdminSite(self.cfg, self.gw, admin=object())
        self.auth = {"Authorization": "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()}
        self.gw.signin.login("Jana", "192.168.1.20", {sl.CLIENT_HEADER: "tvos/1.4 (57)"})
        self.gw.signin.connection("8.8.8.8", {})

    def admin(self, method, path, body=None, headers=None, query=None):
        sent = {**self.auth, "X-Cztvhub-Admin": "1", "Host": "pi:8098"} if headers is None else headers
        resp = self.site.handle(method, path, sent, json.dumps(body).encode() if body is not None else b"", query)
        return resp, json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body

    def test_overview(self):
        resp, body = self.admin("GET", "/admin/api/signin", query={"page": "1"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertEqual([e["event"] for e in body["entries"]], ["connect", "login"])
        self.assertEqual((body["total"], body["page"], body["pages"], body["profiles"]), (2, 1, 1, ["Jana"]))
        _, jana = self.admin("GET", "/admin/api/signin", query={"profile": "Jana"})
        self.assertEqual([e["profile"] for e in jana["entries"]], ["Jana"])
        for bad in ({"page": "0"}, {"page": "x"}, {"profile": ""}, {"profile": "x" * 65}):
            self.assertEqual(self.admin("GET", "/admin/api/signin", query=bad)[0].status, 400, bad)

    def test_needs_the_admin_login(self):
        for headers in ({}, APP, {"Authorization": "Bearer " + WORKER_KEY},
                        {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}):
            self.assertEqual(self.admin("GET", "/admin/api/signin", headers=headers)[0].status, 401)
            self.assertEqual(self.admin("POST", "/admin/api/signin", {"deleteAll": True}, headers)[0].status, 401)
        self.assertEqual(self.gw.signin.page(1)["total"], 2)

    def test_delete_needs_header_and_same_origin_and_is_logged(self):
        for headers in (self.auth, {**self.auth, "X-Cztvhub-Admin": "1", "Host": "pi:8098", "Origin": "http://evil"}):
            self.assertEqual(self.admin("POST", "/admin/api/signin", {"deleteAll": True}, headers)[0].status, 403)
        self.assertEqual(self.gw.signin.page(1)["total"], 2)
        self.assertEqual(self.admin("POST", "/admin/api/signin", {"deleteAll": "yes"})[0].status, 400)
        self.assertEqual(self.admin("POST", "/admin/api/signin", {})[0].status, 400)
        resp, body = self.admin("POST", "/admin/api/signin", {"deleteAll": True},
                                {**self.auth, "X-Cztvhub-Admin": "1", "Host": "pi:8098", "Origin": "http://pi:8098"})
        self.assertEqual((resp.status, body), (200, {"deleted": 2}))
        self.assertIn("admin: signin log deleted (2 entries)", self.logs)
        self.assertEqual(self.gw.signin.page(1)["total"], 0)
        self.assertEqual(self.admin("PUT", "/admin/api/signin", {})[0].status, 405)

    def test_page_has_the_tab_and_no_download(self):
        page = self.admin("GET", "/admin")[1].decode("utf-8")
        self.assertIn('id="tab-signin"', page)
        self.assertIn(">Přihlášení<", page)
        self.assertIn("/admin/api/signin", page)
        self.assertIn('timeZone: "Europe/Prague"', page)
        self.assertIn("Smazat záznamy", page)
        self.assertIn('case "loginFailedLimit": return "příliš mnoho neúspěšných přihlášení', page)
        self.assertIn('e.network === "other" ? "warn"', page)  # „jiná" highlighted
        for word in ("download", ".jsonl", "Blob(", "createObjectURL", "/dvb/"):
            self.assertNotIn(word, page.split('<section id="signin"')[1].split("</section>")[0], word)
            self.assertNotIn(word, page.split("function loadSignin")[1].split("function tvs")[0], word)


class _StubGateway:
    """Answers every /dvb/* request with ``status`` (a playback route that worked or failed); /dvb/boom raises."""

    def __init__(self):
        self.status = 200

    def handle(self, path, query, headers, method, body, client):
        if path == "/dvb/boom":
            raise RuntimeError("boom")
        return Response(self.status, b"x", "text/plain")


class JournalTest(unittest.TestCase):
    """O019: the HTTP server writes nothing for a successful request, errors without the query string."""

    def setUp(self):
        self.gateway = _StubGateway()
        server = Server(("127.0.0.1", 0),
                        make_handler(StreamResolver(fetch=lambda c: f"https://cdn.example/{c}.m3u8"), self.gateway))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers={sl.CLIENT_HEADER: "tvos/1.4 (57)"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status

    def captured(self, action):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            action()
        return out.getvalue() + err.getvalue()

    PLAYBACK = ("/ct/CH_1.m3u8", "/dvb/live/ch1.m3u8?start=1", "/dvb/hls/" + "a" * 32 + "/seg00001.m4s",
                "/dvb/recording/rec1.m3u8", "/dvb/details?idec=123456789012", "/dvb/v/p/ch1/1/2/index.m3u8",
                "/dvb/timeline/t1.m3u8", "/dvb/status")

    def test_success_writes_nothing(self):
        def run():
            for path in self.PLAYBACK:
                self.assertIn(self.get(path), (200, 302), path)
        self.assertEqual(self.captured(run), "")

    def test_error_writes_the_path_without_query_or_address(self):
        self.gateway.status = 404
        text = self.captured(lambda: self.get("/dvb/details?idec=123456789012&secret=x"))
        self.assertEqual(text, "http: 404 GET /dvb/details\n")
        text = self.captured(lambda: self.get("/ct/CH_999.m3u8?x=1"))
        self.assertEqual(text, "http: 404 GET /ct/*.m3u8\n")

    def test_error_names_no_channel_or_recording(self):
        self.gateway.status = 502
        cases = {"/dvb/live/0123456789abcdef0123456789abcdef.m3u8": "/dvb/live/*.m3u8",
                 "/dvb/recording/ch1-1792300800-1792304400.m3u8": "/dvb/recording/*.m3u8",
                 "/dvb/v/" + "b" * 32 + "/m.m3u8": "/dvb/v/*/m.m3u8",
                 "/dvb/hls/" + "a" * 32 + "/seg_00001.m4s": "/dvb/hls/*/*.m4s",
                 "/dvb/timeline/t1.m3u8": "/dvb/timeline/*.m3u8",
                 "/dvb/details/image/w342/abc.jpg": "/dvb/details/image/w342/*.jpg",
                 "/dvb/ct/vod/123456789012": "/dvb/ct/vod/*", "/dvb/recording/r1/thumb.jpg": "/dvb/recording/*/thumb.jpg"}
        for path, shown in cases.items():
            self.assertEqual(self.captured(lambda: self.get(path)), f"http: 502 GET {shown}\n", path)

    def test_exception_writes_no_address(self):
        def run():
            try:
                self.get("/dvb/boom?idec=1")
            except (http.client.HTTPException, OSError):
                pass  # the connection closes without an answer
        text = self.captured(run)
        self.assertIn("http: exception while handling a request", text)
        self.assertIn("RuntimeError: boom", text)
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn("idec", text)

    def test_malformed_request_line_has_no_query(self):
        def run():
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
                sock.sendall(b"GET /dvb/live/x.m3u8?secret=1 HTTP/1.1 extra\r\n\r\n")
                sock.recv(1024)
        text = self.captured(run)
        self.assertEqual(text, "http: 400 - -\n")


if __name__ == "__main__":
    unittest.main()
