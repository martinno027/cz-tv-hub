import io
import json
import unittest
import urllib.error
from datetime import date, datetime, timezone
from unittest import mock

from cztvhub.api import (
    LiveStreams,
    StillLive,
    StreamError,
    StreamResolver,
    fetch_live_streams,
    fetch_schedules,
    fetch_stream_url,
    fetch_vod_url,
)


def fake_response(payload):
    return io.BytesIO(json.dumps(payload).encode())


class FetchTest(unittest.TestCase):
    def test_returns_main_url(self):
        payload = {"streamUrls": {"main": "https://cdn/x.m3u8", "isBlocked": False}}
        with mock.patch("urllib.request.urlopen", return_value=fake_response(payload)):
            self.assertEqual(fetch_stream_url("CH_1"), "https://cdn/x.m3u8")

    def test_blocked_raises(self):
        payload = {"streamUrls": {"main": "https://cdn/x.m3u8", "isBlocked": True}}
        with mock.patch("urllib.request.urlopen", return_value=fake_response(payload)):
            with self.assertRaises(StreamError):
                fetch_stream_url("CH_1")

    def test_missing_url_raises(self):
        with mock.patch("urllib.request.urlopen", return_value=fake_response({})):
            with self.assertRaises(StreamError):
                fetch_stream_url("CH_1")


class ResolverTest(unittest.TestCase):
    def test_caches_within_ttl_and_refreshes_after(self):
        now = [0.0]
        calls = []

        def fetch(channel):
            calls.append(channel)
            return f"url-{len(calls)}"

        resolver = StreamResolver(fetch=fetch, ttl=60, clock=lambda: now[0])
        self.assertEqual(resolver.resolve("CH_1"), "url-1")
        now[0] = 30
        self.assertEqual(resolver.resolve("CH_1"), "url-1")
        now[0] = 61
        self.assertEqual(resolver.resolve("CH_1"), "url-2")


def http_error(status, payload):
    body = io.BytesIO(json.dumps(payload).encode())
    return urllib.error.HTTPError("https://api", status, "error", {}, body)


class LiveStreamsTest(unittest.TestCase):
    def test_main_and_timeshift(self):
        payload = {"streamUrls": {"main": "https://cdn/live", "timeshift": "https://cdn/ts", "isBlocked": False}}
        with mock.patch("urllib.request.urlopen", return_value=fake_response(payload)):
            self.assertEqual(fetch_live_streams("CH_1"), LiveStreams("https://cdn/live", "https://cdn/ts"))

    def test_timeshift_optional(self):
        payload = {"streamUrls": {"main": "https://cdn/live"}}
        with mock.patch("urllib.request.urlopen", return_value=fake_response(payload)):
            self.assertIsNone(fetch_live_streams("CH_1").timeshift)


class VodTest(unittest.TestCase):
    def test_returns_first_stream_url(self):
        payload = {"streams": [{"url": "https://cdn/vod.m3u8"}]}
        with mock.patch("urllib.request.urlopen", return_value=fake_response(payload)):
            self.assertEqual(fetch_vod_url("226411000101016"), "https://cdn/vod.m3u8")

    def test_currently_airing_raises_still_live(self):
        payload = {"platformChannel": "CH_24", "idec": "226411033570003"}
        with mock.patch("urllib.request.urlopen", return_value=fake_response(payload)):
            with self.assertRaises(StillLive) as ctx:
                fetch_vod_url("226411033570003")
        self.assertEqual(ctx.exception.channel_id, "CH_24")

    def test_missing_licence_keeps_ct_message(self):
        payload = {
            "error": {"type": "ResourcePlayabilityError"},
            "message": {"status": {"name": "MISSING_LICENCE_NOW"}, "labels": {"textShort": "ČT nemá práva pro internet"}},
        }
        with mock.patch("urllib.request.urlopen", side_effect=http_error(403, payload)):
            with self.assertRaises(StreamError) as ctx:
                fetch_vod_url("224381492050002")
        self.assertEqual(ctx.exception.user_message, "ČT nemá práva pro internet")
        self.assertEqual(ctx.exception.status, "MISSING_LICENCE_NOW")
        self.assertIn("MISSING_LICENCE_NOW", str(ctx.exception))

    def test_http_error_without_json(self):
        error = urllib.error.HTTPError("https://api", 500, "error", {}, io.BytesIO(b"<html>"))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(StreamError):
                fetch_vod_url("1")


class ScheduleTest(unittest.TestCase):
    PAYLOAD = {"data": {"TVProgramDailyChannelsPlanV2": [{
        "channel": "ct24",
        "program": [
            {"idec": "1", "title": "Zprávy", "start": "2026-10-17T09:00:00.000Z",
             "end": "2026-10-17T09:05:00.000Z", "isPlayableNow": True, "liveOnly": False},
            {"idec": "2", "title": "broken", "start": None, "end": None},
        ],
    }]}}

    def test_parses_programmes_and_skips_malformed(self):
        with mock.patch("urllib.request.urlopen", return_value=fake_response(self.PAYLOAD)) as urlopen:
            result = fetch_schedules(["ct24"], date(2026, 10, 17))
        [programme] = result["ct24"]
        self.assertEqual(programme.title, "Zprávy")
        self.assertEqual(programme.start, datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc))
        self.assertTrue(programme.playable)
        sent = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(sent["variables"], {"channels": ["ct24"], "date": "10.17.2026"})

    def test_graphql_error_raises(self):
        with mock.patch("urllib.request.urlopen", return_value=fake_response({"errors": [{"message": "x"}]})):
            with self.assertRaises(StreamError):
                fetch_schedules(["ct1"], date(2026, 10, 17))
