import unittest
from datetime import datetime, timedelta, timezone

from cztvhub.api import LiveStreams, Programme, StillLive, StreamError
from cztvhub.channels import BY_ID
from cztvhub.plugin import Directory, Message, Play, Plugin, day_label

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)


def programme(idec, start_h, end_h, playable=True, title=None):
    start = NOW.replace(hour=0) + timedelta(hours=start_h)
    end = NOW.replace(hour=0) + timedelta(hours=end_h)
    return Programme(idec, title or f"P{idec}", start, end, playable, False)


class FakeApi:
    def __init__(self):
        self.schedules = {}
        self.vod = {}
        self.timeshift = "https://cdn/ts"
        self.schedule_error = None

    def fetch_live(self, channel_id):
        return LiveStreams(f"https://cdn/{channel_id}", self.timeshift)

    def fetch_vod(self, idec):
        value = self.vod.get(idec)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise StreamError("403", "ČT nemá práva pro internet")
        return value

    def fetch_schedules(self, slugs, day):
        if self.schedule_error:
            raise self.schedule_error
        return {s: self.schedules.get(s, []) for s in slugs}


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.plugin = Plugin(self.api.fetch_live, self.api.fetch_vod, self.api.fetch_schedules, lambda: NOW)

    def labels(self, result):
        self.assertIsInstance(result, Directory)
        return [i.label for i in result.items]

    def test_root(self):
        result = self.plugin.route("/")
        self.assertEqual([i.path for i in result.items], ["/live", "/archive", "/guide/refresh"])

    def test_live_channels_show_current_programme(self):
        self.api.schedules["ct1"] = [programme("1", 17, 19, title="Večerníček")]
        result = self.plugin.route("/live")
        self.assertIn("Večerníček", result.items[0].label)
        self.assertEqual(result.items[0].path, "/play/live/CH_1")
        self.assertTrue(result.items[0].playable)

    def test_live_channels_survive_schedule_failure(self):
        self.api.schedule_error = StreamError("down")
        result = self.plugin.route("/live")
        self.assertEqual(result.items[0].label, "ČT1")

    def test_play_live(self):
        result = self.plugin.route("/play/live/CH_24")
        self.assertEqual(result, Play("https://cdn/CH_24", "ČT24", live=True))

    def test_archive_days(self):
        result = self.plugin.route("/archive/CH_1")
        self.assertEqual(result.items[0].label, "Dnes")
        self.assertEqual(result.items[1].label, "Včera")
        self.assertEqual(result.items[0].path, "/archive/CH_1/2026-10-17")

    def test_archive_day_classifies_programmes(self):
        self.api.schedules["ct1"] = [
            programme("old", 8, 9, playable=True),        # in archive
            programme("gone", 9, 10, playable=False),     # no rights, outside buffer
            programme("recent", 16, 17, playable=False),  # not archived yet, in buffer
            programme("now", 17, 19),                     # airing
            programme("later", 20, 21),                   # future
        ]
        items = self.plugin.route("/archive/CH_1/2026-10-17").items
        old, gone, recent, now, later = items
        self.assertTrue(old.path.startswith("/play/vod/old?"))
        self.assertIn("ch=CH_1", old.path)
        self.assertIn("nedostupné", gone.label)
        self.assertTrue(gone.path.startswith("/play/vod/gone"))
        self.assertTrue(recent.path.startswith("/play/startover/CH_1/"))
        self.assertIn("právě běží", now.label)
        self.assertTrue(now.path.startswith("/play/startover/CH_1/"))
        self.assertFalse(later.playable)
        self.assertTrue(later.path.startswith("/info?"))

    def test_play_vod(self):
        self.api.vod["123"] = "https://cdn/vod"
        self.assertEqual(self.plugin.route("/play/vod/123"), Play("https://cdn/vod"))

    def test_play_vod_shows_ct_reason(self):
        self.assertEqual(self.plugin.route("/play/vod/999"), Message("ČT nemá práva pro internet"))

    def test_play_vod_still_live_falls_back_to_startover(self):
        self.api.vod["9"] = StillLive("9", "CH_24")
        start = int((NOW - timedelta(minutes=20)).timestamp())
        result = self.plugin.route("/play/vod/9", {"start": str(start)})
        self.assertEqual(result.url, "https://cdn/ts")
        self.assertTrue(result.from_buffer_start)

    def test_startover_offset_into_buffer(self):
        start = int((NOW - timedelta(minutes=30)).timestamp())
        result = self.plugin.route(f"/play/startover/CH_1/{start}")
        # buffer begins 3 h ago; programme began 30 min ago, minus 30 s margin
        self.assertEqual(result.start_offset, 2.5 * 3600 - 30)

    def test_startover_older_than_buffer_starts_at_zero(self):
        start = int((NOW - timedelta(hours=5)).timestamp())
        self.assertEqual(self.plugin.route(f"/play/startover/CH_1/{start}").start_offset, 0.0)

    def test_startover_without_timeshift(self):
        self.api.timeshift = None
        start = int(NOW.timestamp())
        self.assertIsInstance(self.plugin.route(f"/play/startover/CH_1/{start}"), Message)

    def test_unknown_routes(self):
        for path in ("/nope", "/play/live/CH_999", "/archive/CH_1/not-a-date", "/play/startover/CH_1/x"):
            self.assertEqual(self.plugin.route(path), Message("Neznámá položka"), path)

    def test_info(self):
        self.assertEqual(self.plugin.route("/info", {"text": "Pořad ještě nezačal"}), Message("Pořad ještě nezačal"))


class DayLabelTest(unittest.TestCase):
    def test_labels(self):
        today = NOW.date()
        self.assertEqual(day_label(today, today), "Dnes")
        self.assertEqual(day_label(today - timedelta(days=1), today), "Včera")
        self.assertEqual(day_label(today - timedelta(days=2), today), "čt 15. 10.")


class CatchupTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.plugin = Plugin(self.api.fetch_live, self.api.fetch_vod, self.api.fetch_schedules, lambda: NOW)

    def path(self, start, end, idec="{catchup-id}"):
        return f"/play/catchup/CH_1/{int(start.timestamp())}/{int(end.timestamp())}", {"idec": idec}

    def test_archived_programme_plays_vod(self):
        self.api.vod["42"] = "https://cdn/vod42"
        result = self.plugin.route(*self.path(NOW - timedelta(days=1), NOW - timedelta(days=1, minutes=-50), "42"))
        self.assertEqual(result, Play("https://cdn/vod42"))

    def test_airing_programme_starts_over(self):
        result = self.plugin.route(*self.path(NOW - timedelta(minutes=10), NOW + timedelta(minutes=40), "42"))
        self.assertTrue(result.from_buffer_start)

    def test_recent_programme_not_archived_uses_buffer(self):
        result = self.plugin.route(*self.path(NOW - timedelta(hours=1), NOW - timedelta(minutes=10), "missing"))
        self.assertEqual(result.url, "https://cdn/ts")

    def test_old_unavailable_programme_shows_ct_reason(self):
        result = self.plugin.route(*self.path(NOW - timedelta(days=2), NOW - timedelta(days=2, minutes=-30), "missing"))
        self.assertEqual(result, Message("ČT nemá práva pro internet"))

    def test_unfilled_placeholder_outside_buffer(self):
        result = self.plugin.route(*self.path(NOW - timedelta(days=2), NOW - timedelta(days=2, minutes=-30)))
        self.assertEqual(result, Message("Pořad není v archivu"))

    def test_refresh_route(self):
        from cztvhub.plugin import RefreshGuide
        self.assertEqual(self.plugin.route("/guide/refresh"), RefreshGuide())


class SharedDecisionCasesTest(unittest.TestCase):
    """The add-on must agree with the native apps on spec/fixtures/decisions.json."""

    def test_golden_cases(self):
        import json
        import pathlib
        from cztvhub.api import Programme

        root = pathlib.Path(__file__).resolve().parents[2]  # repo root
        cases = json.loads((root / "spec/fixtures/decisions.json").read_text(encoding="utf-8"))
        parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
        now = parse(cases["now"])
        api = FakeApi()
        api.vod = {}
        plugin = Plugin(api.fetch_live, api.fetch_vod, api.fetch_schedules, lambda: now)
        for case in cases["programmes"]:
            with self.subTest(case["name"]):
                p = Programme(case["name"], case["name"], parse(case["start"]), parse(case["end"]),
                              case["isPlayableNow"], False)
                item = plugin._programme_item(BY_ID["CH_1"], p, now)
                action = case["expect"]["action"]
                if action == "future":
                    self.assertFalse(item.playable)
                elif action == "startover":
                    self.assertTrue(item.path.startswith("/play/startover/"))
                    played = plugin.route(item.path)
                    self.assertEqual(played.start_offset, case["expect"]["offsetSeconds"])
                elif action == "vod":
                    self.assertTrue(item.path.startswith("/play/vod/"))
                    self.assertNotIn("nedostupné", item.label)
                else:
                    self.assertTrue(item.path.startswith("/play/vod/"))
                    self.assertIn("nedostupné", item.label)


class DrmTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.plugin = Plugin(self.api.fetch_live, self.api.fetch_vod, self.api.fetch_schedules, lambda: NOW)

    def test_drm_only_vod_shows_ct_text_and_official_app_hint(self):
        self.api.vod["7"] = StreamError("HTTP 403 UNSUPPORTED_DRM", "Video vyžaduje prohlížeč s ochranou DRM",
                                        "UNSUPPORTED_DRM")
        result = self.plugin.route("/play/vod/7")
        self.assertEqual(result.text, "Video vyžaduje prohlížeč s ochranou DRM. "
                                      "Pořad můžete pustit v oficiální aplikaci iVysílání.")

    def test_recent_archived_catchup_uses_buffer_without_calling_vod(self):
        self.api.vod["7"] = AssertionError("VOD must not be called within the buffer window")
        start, end = NOW - timedelta(hours=2), NOW - timedelta(minutes=50)
        result = self.plugin.route(f"/play/catchup/CH_1/{int(start.timestamp())}/{int(end.timestamp())}",
                                   {"idec": "7"})
        self.assertEqual(result.url, "https://cdn/ts")
