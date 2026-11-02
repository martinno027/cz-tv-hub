"""Watch log (spec/dvb-gateway.md "Watch log", T53): reports, sessions in memory, the store and its limits,
``POST /dvb/watch``, ``watchLog`` in ``/dvb/status``, the ``/admin`` tab "Sledování", trimming at start and the
journal (a report is never a journal line)."""
import argparse
import base64
import contextlib
import http.client
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from cztvhub import api as ct_api
from cztvhub import cli
from cztvhub.api import StreamResolver
from cztvhub.config import AdminConfig, AdsConfig, Config, DvbConfig
from cztvhub.dvb import signinlog as sl
from cztvhub.dvb import watchlog as wl
from cztvhub.dvb.admin import PAGE, AdminSite
from cztvhub.dvb.archive import recording_id
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from cztvhub.dvb.profiles import ProfileStore
from cztvhub.dvb.thumbs import Settings
from cztvhub.server import Server, make_handler
from dvb_fixtures import CT1, NOVA, NOW, REC_COMPLETED, FakeClient

KEY = "app-key-" * 5
WORKER_KEY = "worker-key-" * 4
ADMIN_PASSWORD = "admin-password-1"
APP = {"Authorization": "Bearer " + KEY}
SID = "s" * 16
SID2 = "t" * 16
CHROME_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PROFILE_SETTINGS = {"v": 1, "channelPrefs": {"order": [], "hidden": [], "sourcesOff": []}, "thumbnails": True}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def start(session=SID, **fields):
    return {"event": "start", "session": session, "source": "dvb", "mode": "live", "channel": CT1, **fields}


def entry(**fields):
    return {"address": "192.168.1.20", "network": "home", "platform": "tvos", "source": "dvb", "mode": "live",
            "channel": "ČT1 HD", **fields}


class ParseTest(unittest.TestCase):
    def test_valid_reports(self):
        for event in ("start", "beat", "stop"):
            self.assertEqual(wl.parse_report(json.dumps({"event": event, "session": SID}).encode())["event"], event)
        for session in ("a" * 16, "A-_9" * 16, "x" * 64):
            self.assertEqual(wl.parse_report(json.dumps({"event": "beat", "session": session}).encode())["session"],
                             session)

    def test_not_a_report(self):
        bad = [b"", b"x", b"[]", b'"start"', b"\xff\xfe", json.dumps({"session": SID}).encode(),
               json.dumps({"event": "pause", "session": SID}).encode(),
               json.dumps({"event": "start"}).encode(),
               json.dumps({"event": "start", "session": "a" * 15}).encode(),
               json.dumps({"event": "start", "session": "a" * 65}).encode(),
               json.dumps({"event": "start", "session": "a" * 15 + "!"}).encode(),
               json.dumps({"event": "start", "session": 1234567890123456}).encode(),
               json.dumps({"event": "start", "session": SID, "title": "x" * 2100}).encode()]  # over 2 KB
        for body in bad:
            with self.assertRaises(wl.BadReport, msg=body[:40]):
                wl.parse_report(body)

    def test_two_kilobytes_is_the_limit(self):
        base = {"event": "start", "session": SID, "title": ""}
        pad = wl.MAX_BODY - len(json.dumps(base).encode())
        wl.parse_report(json.dumps({**base, "title": "x" * pad}).encode())
        with self.assertRaises(wl.BadReport):
            wl.parse_report(json.dumps({**base, "title": "x" * (pad + 1)}).encode())

    def test_clean_title(self):
        self.assertEqual(wl.clean_title("  Události\n\tkomentáře \x00\x1b[31m "), "Události komentáře [31m")
        self.assertEqual(wl.clean_title("evil‮gnp.exe​"), "evil gnp.exe")  # bidi/format characters
        self.assertEqual(wl.clean_title("a" * 250), "a" * 200)
        self.assertEqual(len(wl.clean_title("ř" * 201)), 200)
        for value in (None, 12, ["x"], "", " \n "):
            self.assertIsNone(wl.clean_title(value), value)

    def test_neutral(self):
        self.assertEqual(wl.neutral("dvb", wl.SOURCES), "dvb")
        for value in ("kodi", "DVB", None, 1, ""):
            self.assertEqual(wl.neutral(value, wl.SOURCES), "unknown", value)


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.wall = Clock(float(NOW))
        self.clock = Clock()
        self.logs = []
        self.settings = Settings(self.dir)
        self.settings.update(watchLog=True)
        self.log = self.make()

    def make(self):
        return wl.WatchLog(self.dir, self.settings, now=self.wall, clock=self.clock, log=self.logs.append)

    def advance(self, seconds):
        self.clock.t += seconds
        self.wall.t += seconds

    def send(self, report, address="192.168.1.20", fields=None):
        calls = []

        def describe():
            calls.append(1)
            return fields or entry(address=address)
        self.log.report(report, address, describe)
        return calls

    def lines(self):
        path = self.dir / wl.FILE
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_start_and_stop_write_one_line(self):
        self.send(start())
        self.assertEqual(self.lines(), [])  # in memory while it runs
        self.advance(90)
        self.send({"event": "stop", "session": SID})
        self.assertEqual(self.lines(), [{"start": "2026-10-18T05:30:00Z", "end": "2026-10-18T05:31:30Z",
                                         "seconds": 90, **entry()}])
        self.assertEqual(stat.S_IMODE((self.dir / wl.FILE).stat().st_mode), 0o600)
        self.assertNotIn(SID, (self.dir / wl.FILE).read_text(encoding="utf-8"))
        self.assertEqual(self.log.now_playing(), [])

    def test_off_ignores_everything(self):
        self.settings.update(watchLog=False)
        self.assertEqual(self.send(start()), [])  # describe() is never asked
        self.advance(60)
        self.send({"event": "stop", "session": SID})
        self.assertEqual((self.lines(), self.log.now_playing()), ([], []))
        self.assertFalse((self.dir / wl.FILE).exists())

    def test_shorter_than_five_seconds_is_not_written(self):
        self.send(start())
        self.advance(4)
        self.send({"event": "stop", "session": SID})
        self.assertEqual(self.lines(), [])
        self.send(start(SID2))
        self.advance(5)
        self.send({"event": "stop", "session": SID2})
        self.assertEqual([e["seconds"] for e in self.lines()], [5])

    def test_three_minutes_without_a_beat_ends_the_session_at_its_last_beat(self):
        self.send(start())
        self.advance(60)
        self.send({"event": "beat", "session": SID})
        self.advance(180)  # exactly 3 minutes: still in progress
        self.log.sweep()
        self.assertEqual(self.lines(), [])
        self.assertEqual([r["seconds"] for r in self.log.now_playing()], [240])
        self.advance(1)
        self.log.sweep()
        self.assertEqual([(e["seconds"], e["end"]) for e in self.lines()], [(60, "2026-10-18T05:31:00Z")])
        self.assertEqual(self.log.now_playing(), [])
        self.send({"event": "beat", "session": SID})  # too late: unknown now
        self.assertEqual(self.log.now_playing(), [])

    def test_expired_sessions_are_written_by_the_next_report(self):
        self.send(start())
        self.advance(30)
        self.send({"event": "beat", "session": SID})
        self.advance(200)
        self.send({"event": "beat", "session": "u" * 16}, address="10.0.0.9")  # any report sweeps
        self.assertEqual([e["seconds"] for e in self.lines()], [30])

    def test_beats_keep_it_alive_and_a_known_start_restarts_nothing(self):
        self.send(start())
        for _ in range(5):
            self.advance(60)
            self.send({"event": "beat", "session": SID})
        self.assertEqual(self.send(start(title="Jiný")), [])  # no new session, no new description
        self.advance(10)
        self.send({"event": "stop", "session": SID})
        self.assertEqual([e["seconds"] for e in self.lines()], [310])

    def test_unknown_session_and_another_address_are_ignored(self):
        self.send({"event": "beat", "session": SID})
        self.send({"event": "stop", "session": SID})
        self.assertEqual(self.log.now_playing(), [])
        self.send(start())
        self.advance(60)
        self.send({"event": "beat", "session": SID})
        self.advance(10)
        self.send({"event": "stop", "session": SID}, address="192.168.1.99")  # not its session
        self.assertEqual(len(self.log.now_playing()), 1)
        self.advance(30)
        self.send({"event": "beat", "session": SID}, address="192.168.1.99")
        self.advance(141)  # 181 s after its own last beat
        self.log.sweep()  # the other address's beat did not keep it alive
        self.assertEqual([e["seconds"] for e in self.lines()], [60])

    def test_four_sessions_per_address(self):
        for i in range(5):
            self.send(start(f"{i}" * 16))
        self.assertEqual(len(self.log.now_playing()), 4)
        self.assertEqual(self.logs, ["watch: limit (1 start dropped)"])
        self.send(start("z" * 16), address="192.168.1.21")  # another address still fits
        self.assertEqual(len(self.log.now_playing()), 5)

    def test_two_hundred_sessions_in_total_and_the_limit_line_once_a_minute(self):
        for i in range(50):
            for j in range(4):
                self.send(start(f"{j}" * 16), address=f"10.0.{i}.1")
        self.assertEqual(len(self.log.now_playing()), 200)
        for k in range(3):
            self.send(start("n" * 16), address=f"10.1.0.{k}")
        self.assertEqual(len(self.log.now_playing()), 200)
        self.assertEqual(self.logs, ["watch: limit (1 start dropped)"])
        self.advance(60)
        for i in range(50):  # keep them alive
            for j in range(4):
                self.send({"event": "beat", "session": f"{j}" * 16}, address=f"10.0.{i}.1")
        self.send(start("n" * 16), address="10.1.0.9")
        self.assertEqual(self.logs, ["watch: limit (1 start dropped)", "watch: limit (3 starts dropped)"])

    def test_sixty_reports_per_address_and_minute(self):
        self.send(start())
        for _ in range(59):
            self.send({"event": "beat", "session": SID})
        self.assertEqual(self.send(start(SID2)), [])  # the 61st report in the minute is dropped
        self.assertEqual(len(self.log.now_playing()), 1)
        self.send(start(SID2), address="192.168.1.21")  # per address
        self.assertEqual(len(self.log.now_playing()), 2)
        self.advance(60)
        self.send(start(SID2))
        self.assertEqual(len(self.log.now_playing()), 3)

    def test_rate_state_is_bounded(self):
        for i in range(wl.MAX_TRACKED + 50):
            self.send({"event": "beat", "session": SID}, address=f"10.{i // 256}.{i % 256}.1")
        self.assertEqual(len(self.log._rates), wl.MAX_TRACKED)

    def test_switching_off_ends_sessions_without_writing_and_deletes_the_store(self):
        self.send(start())
        self.advance(30)
        self.send({"event": "stop", "session": SID})
        self.send(start(SID2))
        self.advance(30)
        self.assertEqual(self.log.set_enabled(False), 1)
        self.assertFalse((self.dir / wl.FILE).exists())
        self.assertEqual(self.log.now_playing(), [])
        self.assertIs(Settings(self.dir).get(wl.SETTING), False)  # persisted
        self.assertEqual(self.log.set_enabled(True), 0)
        self.send({"event": "stop", "session": SID2})  # the session before "off" is gone
        self.assertEqual(self.lines(), [])
        self.assertIs(Settings(self.dir).get(wl.SETTING), True)

    def test_delete_all(self):
        for sid in (SID, SID2):
            self.send(start(sid))
            self.advance(10)
            self.send({"event": "stop", "session": sid})
        self.assertEqual(self.log.delete_all(), 2)
        self.assertFalse((self.dir / wl.FILE).exists())
        self.assertEqual(self.log.page(1)["total"], 0)

    def test_thirty_days(self):
        self.send(start())
        self.advance(10)
        self.send({"event": "stop", "session": SID})
        self.wall.t += wl.MAX_AGE  # exactly 30 days after its end: kept
        self.log.sweep()
        self.assertEqual(len(self.lines()), 1)
        self.wall.t += 1
        self.log.sweep()
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.log.page(1)["total"], 0)

    def test_five_thousand_entries(self):
        old = [{"start": sl.iso(NOW - 100 - i), "end": sl.iso(NOW - 99 - i), "seconds": 6, **entry(title=str(i))}
               for i in range(wl.MAX_ENTRIES + wl.SLACK)]
        (self.dir / wl.FILE).write_text("".join(json.dumps(e) + "\n" for e in reversed(old)), encoding="utf-8")
        log = self.make()  # the file is within MAX_ENTRIES + SLACK lines but over 5 000: trimmed at start
        self.assertEqual(len(self.lines()), wl.MAX_ENTRIES)
        self.assertEqual(self.lines()[-1]["title"], "0")
        self.assertEqual(log.page(1)["total"], wl.MAX_ENTRIES)

    def test_rewrite_when_over_the_slack(self):
        with mock.patch.object(wl, "MAX_ENTRIES", 3), mock.patch.object(wl, "SLACK", 1):
            counts = []
            for i in range(6):
                sid = f"{i}" * 16
                self.send(start(sid))
                self.advance(6)
                self.send({"event": "stop", "session": sid})
                counts.append(len(self.lines()))
            self.assertEqual(counts, [1, 2, 3, 4, 3, 4])  # the 5th line is over 3 + 1: rewritten to the newest 3
            self.assertEqual(self.log.page(1)["total"], 3)

    def test_unparsable_lines_are_dropped(self):
        good = {"start": sl.iso(NOW - 60), "end": sl.iso(NOW - 30), "seconds": 30, **entry()}
        lines = ["not json", json.dumps({"start": "x", "end": sl.iso(NOW), "seconds": 1, **entry()}),
                 json.dumps({**good, "seconds": "30"}), json.dumps({**good, "seconds": True}),
                 json.dumps({**good, "title": 5}), json.dumps({k: v for k, v in good.items() if k != "channel"}),
                 json.dumps([good]), json.dumps({**good, "session": SID, "idec": "123"}), json.dumps(good)]
        (self.dir / wl.FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
        log = self.make()
        self.assertEqual(log.page(1)["entries"], [{k: good.get(k) for k in wl.FIELDS}] * 2)
        self.assertNotIn(SID, (self.dir / wl.FILE).read_text(encoding="utf-8"))  # unknown keys not kept

    def test_page_filters_and_lists(self):
        rows = [entry(profile="Jana", platform="tvos", channel="ČT1 HD", title="Události"),
                entry(address="100.70.1.2", network="tailscale", platform="web", browser="Chrome", system="macOS",
                      source="ct", channel="ČT24"),
                entry(profile="Petr", source="psp", channel="Schůze PS")]
        for i, fields in enumerate(rows):
            sid = f"{i}" * 16
            self.send(start(sid), address=fields["address"], fields=fields)
            self.advance(10 + i)
            self.send({"event": "stop", "session": sid}, address=fields["address"])
        page = self.log.page(1)
        self.assertEqual((page["total"], page["pages"], page["pageSize"], page["enabled"]), (3, 1, 50, True))
        self.assertEqual([e["channel"] for e in page["entries"]], ["Schůze PS", "ČT24", "ČT1 HD"])  # newest first
        self.assertEqual(page["profiles"], ["Jana", "Petr"])
        self.assertEqual(page["devices"], ["tvos 192.168.1.20", "web 100.70.1.2"])
        self.assertEqual(page["channels"], ["ct ČT24", "dvb ČT1 HD", "psp Schůze PS"])
        self.assertEqual([e["seconds"] for e in self.log.page(1, profile="Jana")["entries"]], [10])
        self.assertEqual([e["channel"] for e in self.log.page(1, device="tvos 192.168.1.20")["entries"]],
                         ["Schůze PS", "ČT1 HD"])
        self.assertEqual([e["seconds"] for e in self.log.page(1, channel="ct ČT24")["entries"]], [11])
        self.assertEqual(self.log.page(1, channel="dvb ČT24")["total"], 0)

    def test_pagination(self):
        with mock.patch.object(wl, "PAGE_SIZE", 2):
            for i in range(5):
                sid = f"{i}" * 16
                self.send(start(sid))
                self.advance(6)
                self.send({"event": "stop", "session": sid})
            self.assertEqual((self.log.page(1)["pages"], len(self.log.page(3)["entries"])), (3, 1))

    def test_now_playing(self):
        self.send(start(), fields=entry(profile="Jana", title="Události"))
        self.advance(42)
        rows = self.log.now_playing()
        self.assertEqual(rows, [{"start": "2026-10-18T05:30:00Z", "seconds": 42, "profile": "Jana",
                                 "address": "192.168.1.20", "network": "home", "platform": "tvos", "client": None,
                                 "browser": None, "system": None, "source": "dvb", "mode": "live",
                                 "channel": "ČT1 HD", "title": "Události"}])
        self.assertNotIn(SID, json.dumps(rows))

    def test_unwritable_store_is_logged_not_raised(self):
        (self.dir / wl.FILE).mkdir()  # a directory where the file should be
        self.send(start())
        self.advance(10)
        self.send({"event": "stop", "session": SID})
        self.assertTrue(any(m.startswith("watch log: cannot write") for m in self.logs), self.logs)


class TrimAtStartTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.logs = []

    def write(self, *entries):
        (self.dir / wl.FILE).write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    def test_off_deletes_a_leftover_file(self):
        self.write({"start": sl.iso(NOW - 60), "end": sl.iso(NOW), "seconds": 60, **entry()})
        wl.trim_file(self.dir, NOW, self.logs.append)
        self.assertFalse((self.dir / wl.FILE).exists())
        self.assertEqual(self.logs, ["watch log: off, deleted the leftover log (1 entry)"])

    def test_on_drops_old_lines_and_fixes_the_mode(self):
        Settings(self.dir).update(watchLog=True)
        young = {"start": sl.iso(NOW - 60), "end": sl.iso(NOW), "seconds": 60, **entry()}
        old = {**young, "end": sl.iso(NOW - wl.MAX_AGE - 1)}
        self.write(old, young)
        os.chmod(self.dir / wl.FILE, 0o644)
        wl.trim_file(self.dir, NOW, self.logs.append)
        self.assertEqual([json.loads(x) for x in (self.dir / wl.FILE).read_text().splitlines()], [young])
        self.assertEqual(stat.S_IMODE((self.dir / wl.FILE).stat().st_mode), 0o600)

    def test_no_file_creates_nothing(self):
        wl.trim_file(self.dir, NOW, self.logs.append)
        Settings(self.dir).update(watchLog=True)
        wl.trim_file(self.dir, NOW, self.logs.append)
        self.assertFalse((self.dir / wl.FILE).exists())
        self.assertEqual(self.logs, [])

    def test_cmd_serve_trims_also_without_dvb(self):
        self.write({"start": sl.iso(NOW - 60), "end": sl.iso(NOW), "seconds": 60, **entry()})
        conf = self.dir / "c.conf"
        conf.write_text(f"[dvb]\nenabled = false\nstate_dir = {self.dir}\n", encoding="utf-8")
        with mock.patch.object(cli, "serve") as serve, mock.patch.object(cli, "_sigterm_as_interrupt"), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_serve(argparse.Namespace(config=str(conf), host=None, port=None))
        self.assertIsNone(serve.call_args.kwargs["gateway"])  # CT-only server
        self.assertFalse((self.dir / wl.FILE).exists())  # the switch is off: the leftover is gone


class GatewayWatchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.dir / "hls", state_dir=self.dir),
                          ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=self.dir / "breaks"),
                          admin=AdminConfig(password=ADMIN_PASSWORD))
        self.clock = Clock()
        self.logs = []
        self.client = FakeClient()
        hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(self.cfg, client=self.client, hls=hls, now=lambda: NOW, log=self.logs.append,
                             profiles=ProfileStore(self.dir, iterations=1, now=lambda: NOW, log=self.logs.append),
                             clock=self.clock)

    def on(self):
        self.gw.watch.set_enabled(True)

    def post(self, body, headers=None, client="192.168.1.20", method="POST"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        return self.gw.handle("/dvb/watch", {}, APP if headers is None else headers, method, data, client)

    def watch(self, report, headers=None, seconds=30, client="192.168.1.20"):
        """start, ``seconds`` later stop; the written entry."""
        self.assertEqual(self.post(report, headers, client).status, 204)
        self.clock.t += seconds
        self.assertEqual(self.post({"event": "stop", "session": report["session"]}, headers, client).status, 204)
        return self.gw.watch.page(1)["entries"][0]

    def test_status_says_whether_the_log_is_on(self):
        self.assertIs(json.loads(self.gw.handle("/dvb/status", {}, APP).body)["watchLog"], False)
        self.on()
        self.assertIs(json.loads(self.gw.handle("/dvb/status", {}, APP).body)["watchLog"], True)

    def test_204_when_off_and_nothing_kept(self):
        resp = self.post(start())
        self.assertEqual((resp.status, resp.body), (204, b""))
        self.assertEqual(self.gw.watch.now_playing(), [])
        self.assertNotIn(("epg", NOW, NOW + 1), self.client.calls)  # nothing was even looked up

    def test_204_for_unknown_sessions_and_over_limits(self):
        self.on()
        self.assertEqual(self.post({"event": "beat", "session": SID}).status, 204)
        self.assertEqual(self.post({"event": "stop", "session": SID}).status, 204)
        for i in range(6):
            self.assertEqual(self.post(start(f"{i}" * 16)).status, 204)
        self.assertEqual(len(self.gw.watch.now_playing()), 4)

    def test_key_errors(self):
        self.on()
        self.assertEqual(self.post(start(), headers={}).status, 401)
        self.assertEqual(self.post(start(), headers={"Authorization": "Bearer wrong"}).status, 401)
        self.assertEqual(self.post(start(), headers={"Authorization": "Bearer " + WORKER_KEY}).status, 403)
        cookie = self.post(start(), headers={"Cookie": "cztvhub_key=" + KEY})
        self.assertEqual(cookie.status, 403)  # the cookie alone never changes anything (spec/web.md, CSRF)
        self.assertEqual(self.gw.watch.now_playing(), [])

    def test_bad_body_and_method(self):
        self.on()
        for body in (b"x", b"{}", {"event": "play", "session": SID}, {"event": "start", "session": "short"},
                     b"{" + b" " * 3000 + b"}"):
            self.assertEqual(self.post(body).status, 400, body)
        for method in ("GET", "PUT", "DELETE"):
            self.assertEqual(self.post(start(), method=method).status, 405, method)
        self.assertEqual(self.gw.watch.now_playing(), [])

    def test_dvb_live_takes_the_programme_from_the_guide(self):
        self.on()
        got = self.watch(start(title="Klientův název"), {**APP, sl.CLIENT_HEADER: "tvos/1.4 (57)"})
        self.assertEqual(got, {"start": "2026-10-18T05:30:00Z", "end": "2026-10-18T05:30:30Z", "seconds": 30,
                               "profile": None, "address": "192.168.1.20", "network": "home", "platform": "tvos",
                               "client": "tvos/1.4 (57)", "browser": None, "system": None, "source": "dvb",
                               "mode": "live", "channel": "ČT1 HD", "title": "Události"})
        got = self.watch(start(SID2, channel=NOVA))
        self.assertEqual((got["channel"], got["title"], got["platform"]), ("Nova HD", "Televizní noviny", "unknown"))

    def test_dvb_recording_and_timeline_take_the_recording_title(self):
        self.on()
        got = self.watch(start(mode="recording", recording=REC_COMPLETED, title="x"))
        self.assertEqual(got["title"], "Včerejší film")
        rid = recording_id(CT1, 1792305000, 1792310400)
        got = self.watch(start(SID2, mode="timeline", recording=rid, title="x"))
        self.assertEqual((got["mode"], got["title"]), ("timeline", "Film"))
        other = recording_id(NOVA, 1792305000, 1792310400)  # another channel's id: not this session's programme
        got = self.watch(start("u" * 16, mode="recording", recording=other, title="Klient"))
        self.assertEqual(got["title"], "Klient")

    def test_dvb_recording_without_a_channel_takes_it_from_the_recording(self):
        self.on()
        got = self.watch(start(channel="", mode="recording", recording=REC_COMPLETED, title="x"))
        self.assertEqual((got["channel"], got["title"]), ("ČT1 HD", "Včerejší film"))  # DVR uuid
        report = start(SID2, mode="timeline", recording=recording_id(CT1, 1792305000, 1792310400))
        del report["channel"]
        got = self.watch(report)
        self.assertEqual((got["channel"], got["title"]), ("ČT1 HD", "Film"))  # EPG block id
        got = self.watch(start("u" * 16, channel="not-a-channel", mode="recording", recording=REC_COMPLETED))
        self.assertEqual((got["channel"], got["title"]), ("ČT1 HD", "Včerejší film"))  # unknown channel: derived
        got = self.watch(start("v" * 16, channel=NOVA, mode="recording", recording=REC_COMPLETED, title="Klient"))
        self.assertEqual((got["channel"], got["title"]), ("Nova HD", "Klient"))  # given and different: as before
        got = self.watch(start("w" * 16, channel="", mode="recording", recording="no-such-recording", title="K"))
        self.assertEqual((got["channel"], got["title"]), ("unknown", "K"))
        got = self.watch(start("y" * 16, channel="", mode="live", recording=REC_COMPLETED, title="K"))
        self.assertEqual(got["channel"], "unknown")  # only recording / timeline look at the recording
        hidden = recording_id("1234567890abcdef1234567890abcdef", 1792305000, 1792310400)  # disabled channel
        got = self.watch(start("z" * 16, channel="", mode="timeline", recording=hidden, title="K"))
        self.assertEqual((got["channel"], got["title"]), ("unknown", "K"))

    def test_dvb_unknown_or_hidden_channel(self):
        self.on()
        got = self.watch(start(channel="1234567890abcdef1234567890abcdef", title="Skrytý"))  # disabled in TVH
        self.assertEqual((got["channel"], got["title"]), ("unknown", "Skrytý"))
        got = self.watch(start(SID2, channel=None, title=None))
        self.assertEqual(got["channel"], "unknown")
        self.assertIsNone(got["title"])

    def test_tvheadend_down_keeps_the_client_title(self):
        self.on()
        self.client.unreachable = True
        got = self.watch(start(title="Události"))
        self.assertEqual((got["channel"], got["title"]), ("unknown", "Události"))

    def test_ct_channels_and_cached_schedule(self):
        self.on()
        got = self.watch(start(source="ct", channel="CH_24", mode="archive", idec="123", title="Archivní pořad"))
        self.assertEqual((got["source"], got["channel"], got["title"]), ("ct", "ČT24", "Archivní pořad"))
        got = self.watch(start(SID2, source="ct", channel="CH_1", title="Z aplikace"))
        self.assertEqual(got["title"], "Z aplikace")  # no schedule cached: the client's title, and no ČT request
        utc = timezone.utc
        cached = [ct_api.Programme("111", "Dobré ráno", datetime.fromtimestamp(NOW - 600, utc),
                                   datetime.fromtimestamp(NOW + 600, utc), True, False),
                  ct_api.Programme("222", "Zprávy", datetime.fromtimestamp(NOW - 7200, utc),
                                   datetime.fromtimestamp(NOW - 600, utc), True, False)]
        self.gw._ct_schedules[("ct1", date(2026, 10, 18))] = (time.monotonic(), cached)
        got = self.watch(start("u" * 16, source="ct", channel="CH_1", title="Z aplikace"))
        self.assertEqual(got["title"], "Dobré ráno")
        got = self.watch(start("v" * 16, source="ct", channel="CH_1", mode="startover", idec="222", title="x"))
        self.assertEqual(got["title"], "Zprávy")
        got = self.watch(start("w" * 16, source="ct", channel="CH_1", mode="startover", title="Bez idec"))
        self.assertEqual(got["title"], "Bez idec")  # not live, no idec: nothing guessed
        got = self.watch(start("y" * 16, source="ct", channel="CH_1", mode="timeshift", idec="222", title="Starý"))
        self.assertEqual((got["mode"], got["title"]), ("unknown", "Starý"))  # "timeshift" left the list (T53)
        got = self.watch(start("x" * 16, source="ct", channel="CH_99", title="?"))
        self.assertEqual(got["channel"], "unknown")

    def test_psp_stream_ids(self):
        self.on()
        self.assertEqual(self.watch(start(source="psp", channel="stream2", mode="live"))["channel"],
                         "Tiskové konference")
        self.assertEqual(self.watch(start(SID2, source="psp", channel="psp:stream2"))["channel"], "unknown")
        self.assertEqual(self.watch(start("u" * 16, source="psp", channel="stream6"))["channel"], "unknown")

    def test_neutral_source_and_mode(self):
        self.on()
        self.assertEqual(wl.MODES, ("live", "startover", "archive", "recording", "timeline"))
        got = self.watch(start(source="kodi", mode="pip", channel="CH_1", title="Něco"))
        self.assertEqual((got["source"], got["mode"], got["channel"], got["title"]),
                         ("unknown", "unknown", "unknown", "Něco"))
        got = self.watch(start(SID2, source=["dvb"], mode=None, channel={"id": CT1}))
        self.assertEqual((got["source"], got["mode"], got["channel"]), ("unknown", "unknown", "unknown"))

    def test_title_cut_and_cleaned(self):
        self.on()
        got = self.watch(start(source="ct", channel="CH_2", mode="archive", title="‮A\x00B\n" + "c" * 300))
        self.assertEqual(got["title"], ("A B " + "c" * 300)[:200])

    def test_profile_token_becomes_the_name(self):
        token = json.loads(self.gw.handle("/dvb/profiles", {}, APP, "POST", json.dumps(
            {"name": "Jana", "password": "1234", "settings": PROFILE_SETTINGS}).encode(), "192.168.1.20").body)["token"]
        self.on()
        self.assertEqual(self.watch(start(), {**APP, "X-Cztvhub-Profile": token})["profile"], "Jana")
        self.assertIsNone(self.watch(start(SID2), {**APP, "X-Cztvhub-Profile": "nope"})["profile"])
        self.assertNotIn(token, (self.dir / wl.FILE).read_text(encoding="utf-8"))

    def test_web_browser_and_tailnet(self):
        self.on()
        got = self.watch(start(), {**APP, sl.CLIENT_HEADER: "web/2026.11.02", "User-Agent": CHROME_MAC},
                         client="100.70.1.2")
        self.assertEqual((got["platform"], got["browser"], got["system"], got["network"]),
                         ("web", "Chrome", "macOS", "tailscale"))
        self.assertNotIn("Mozilla", (self.dir / wl.FILE).read_text(encoding="utf-8"))  # no full User-Agent

    def test_nothing_of_a_report_reaches_the_journal_or_the_file_beyond_its_fields(self):
        self.on()
        self.watch(start(title="Tajný pořad", idec="987654321", recording=REC_COMPLETED))
        self.post(b"not a report")
        self.post(start(SID2), headers={"Cookie": "cztvhub_key=" + KEY})
        self.assertEqual(self.logs, [])
        text = (self.dir / wl.FILE).read_text(encoding="utf-8")
        for secret in (SID, "987654321", REC_COMPLETED, KEY):
            self.assertNotIn(secret, text)

    def test_status_and_other_routes_never_return_the_log(self):
        self.on()
        self.watch(start(title="Tajný pořad"))
        for path in ("/dvb/status", "/dvb/channels", "/dvb/epg", "/dvb/defaults"):
            self.assertNotIn("192.168.1.20", self.gw.handle(path, {}, APP).body.decode("utf-8"), path)
        self.assertNotIn(b"Tajn", self.gw.handle("/dvb/status", {}, APP).body)


class AdminWatchTabTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = self.dir = Path(tmp.name)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=d / "hls", state_dir=d),
                          ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=d / "breaks"),
                          admin=AdminConfig(password=ADMIN_PASSWORD))
        self.logs = []
        self.clock = Clock()
        hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(self.cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=self.logs.append,
                             profiles=ProfileStore(d, iterations=1, log=self.logs.append), clock=self.clock)
        self.site = AdminSite(self.cfg, self.gw, admin=object())
        self.auth = {"Authorization": "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()}
        self.post_headers = {**self.auth, "X-Cztvhub-Admin": "1", "Host": "pi:8098"}

    def admin(self, method, path, body=None, headers=None, query=None):
        sent = self.post_headers if headers is None else headers
        resp = self.site.handle(method, path, sent, json.dumps(body).encode() if body is not None else b"", query)
        return resp, json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body

    def watched(self, sid=SID, seconds=20):
        self.gw.handle("/dvb/watch", {}, APP, "POST", json.dumps(start(sid)).encode(), "192.168.1.20")
        self.clock.t += seconds
        self.gw.handle("/dvb/watch", {}, APP, "POST", json.dumps({"event": "stop", "session": sid}).encode(),
                       "192.168.1.20")

    def test_overview(self):
        resp, body = self.admin("GET", "/admin/api/watch")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertEqual(body, {"enabled": False, "now": [], "total": 0, "page": 1, "pages": 1, "pageSize": 50,
                                "profiles": [], "devices": [], "channels": [], "entries": []})
        self.gw.watch.set_enabled(True)
        self.watched()
        self.gw.handle("/dvb/watch", {}, APP, "POST", json.dumps(start(SID2)).encode(), "192.168.1.20")
        self.clock.t += 7
        _, body = self.admin("GET", "/admin/api/watch", query={"page": "1", "channel": "dvb ČT1 HD",
                                                               "device": "unknown 192.168.1.20"})
        self.assertEqual((body["enabled"], body["total"], body["devices"], body["channels"]),
                         (True, 1, ["unknown 192.168.1.20"], ["dvb ČT1 HD"]))
        self.assertEqual([(r["seconds"], r["title"]) for r in body["now"]], [(7, "Události")])
        for bad in ({"page": "0"}, {"page": "x"}, {"profile": ""}, {"profile": "x" * 65}, {"device": "x" * 129},
                    {"channel": ""}):
            self.assertEqual(self.admin("GET", "/admin/api/watch", query=bad)[0].status, 400, bad)

    def test_needs_the_admin_login(self):
        for headers in ({}, APP, {"Authorization": "Bearer " + WORKER_KEY},
                        {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}):
            self.assertEqual(self.admin("GET", "/admin/api/watch", headers=headers)[0].status, 401)
            self.assertEqual(self.admin("POST", "/admin/api/watch", {"enabled": True}, headers)[0].status, 401)
        self.assertFalse(self.gw.watch.enabled)

    def test_switch_needs_header_and_same_origin_and_is_logged(self):
        for headers in (self.auth, {**self.post_headers, "Origin": "http://evil"}):
            self.assertEqual(self.admin("POST", "/admin/api/watch", {"enabled": True}, headers)[0].status, 403)
        self.assertFalse(self.gw.watch.enabled)
        resp, body = self.admin("POST", "/admin/api/watch", {"enabled": True},
                                {**self.post_headers, "Origin": "http://pi:8098"})
        self.assertEqual((resp.status, body), (200, {"enabled": True, "deleted": 0}))
        self.assertEqual(self.logs, ["admin: watch log on"])
        self.assertIs(Settings(self.dir).get(wl.SETTING), True)  # survives a restart
        self.watched()
        self.watched(SID2)
        resp, body = self.admin("POST", "/admin/api/watch", {"enabled": False})
        self.assertEqual((resp.status, body), (200, {"enabled": False, "deleted": 2}))
        self.assertEqual(self.logs[-1], "admin: watch log off, deleted (2 entries)")
        self.assertFalse((self.dir / wl.FILE).exists())
        self.assertIs(json.loads(self.gw.handle("/dvb/status", {}, APP).body)["watchLog"], False)

    def test_delete_all_is_logged(self):
        self.gw.watch.set_enabled(True)
        self.watched()
        self.assertEqual(self.admin("POST", "/admin/api/watch", {"deleteAll": True}, self.auth)[0].status, 403)
        resp, body = self.admin("POST", "/admin/api/watch", {"deleteAll": True})
        self.assertEqual((resp.status, body), (200, {"enabled": True, "deleted": 1}))
        self.assertEqual(self.logs[-1], "admin: watch log deleted (1 entry)")
        self.assertEqual(self.gw.watch.page(1)["total"], 0)

    def test_bad_bodies_and_methods(self):
        for body in ({}, {"enabled": "yes"}, {"enabled": 1}, {"deleteAll": "yes"}, {"deleteAll": False},
                     {"enabled": True, "deleteAll": True}):
            self.assertEqual(self.admin("POST", "/admin/api/watch", body)[0].status, 400, body)
        self.assertEqual(self.site.handle("POST", "/admin/api/watch", self.post_headers, b"x").status, 400)
        for method in ("PUT", "DELETE"):
            self.assertEqual(self.admin(method, "/admin/api/watch", {})[0].status, 405, method)
        self.assertFalse(self.gw.watch.enabled)
        self.assertEqual(self.logs, [])

    def test_page_has_the_tab_and_no_download(self):
        page = self.admin("GET", "/admin")[1].decode("utf-8")
        self.assertIn('id="tab-watch"', page)
        self.assertIn(">Sledování<", page)
        self.assertIn("Zaznamenávat sledování", page)
        self.assertIn("Právě se sleduje", page)
        self.assertIn("/admin/api/watch", page)
        self.assertIn("}, 15000);", page)  # the list reloads every 15 s
        self.assertIn("watchTick(); }, 1000);", page)  # the durations tick every second
        section = page.split('<section id="watch"')[1].split("</section>")[0]
        script = page.split("// tab \"Sledování\"")[1].split("function tvs")[0]
        for word in ("download", ".jsonl", "Blob(", "createObjectURL", "/dvb/", "innerHTML"):
            self.assertNotIn(word, section, word)
            self.assertNotIn(word, script, word)

    @unittest.skipUnless(shutil.which("node"), "node not on PATH")
    def test_page_script_parses_and_formats_durations(self):
        script = PAGE.split("<script>")[1].split("</script>")[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "admin.js"
            path.write_text(script, encoding="utf-8")
            self.assertEqual(subprocess.run(["node", "--check", str(path)], capture_output=True).returncode, 0)
            fn = script.split("function duration(seconds) {")[1].split("\n}\n")[0]
            out = subprocess.run(["node", "-e", "function duration(seconds) {" + fn + "\n}\nconsole.log("
                                  "[0, 5.9, 65, 3599, 3600, 3725, 90061].map(duration).join(' '))"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "0:00:00 0:00:05 0:01:05 0:59:59 1:00:00 1:02:05 25:01:01")


class JournalTest(unittest.TestCase):
    """A report through the real HTTP server writes nothing to the journal; a bad one the route only."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=d / "hls", state_dir=d))
        self.gw = DvbGateway(cfg, client=FakeClient(), now=lambda: NOW, log=print)
        self.addCleanup(self.gw.hls.shutdown)
        self.gw.watch.set_enabled(True)
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda c: "https://x"), self.gw))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def post(self, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/dvb/watch", body=body, headers={**APP, "Content-Type": "application/json",
                                                                sl.CLIENT_HEADER: "tvos/1.4 (57)"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status

    def captured(self, action):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            action()
        return out.getvalue() + err.getvalue()

    def test_a_204_writes_nothing(self):
        def run():
            self.assertEqual(self.post(json.dumps(start(title="Tajný pořad")).encode()), 204)
            self.assertEqual(self.post(json.dumps({"event": "beat", "session": SID}).encode()), 204)
            self.assertEqual(self.post(json.dumps({"event": "stop", "session": SID}).encode()), 204)
        self.assertEqual(self.captured(run), "")

    def test_a_bad_report_writes_the_route_only(self):
        text = self.captured(lambda: self.assertEqual(self.post(b'{"event": "x", "session": "Tajny_porad_1234"}'),
                                                      400))
        self.assertEqual(text, "http: 400 POST /dvb/watch\n")


if __name__ == "__main__":
    unittest.main()
