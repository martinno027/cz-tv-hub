import io
import json
import unittest
import urllib.error
import urllib.parse

from cztvhub.dvb import tvheadend as tvh
from dvb_fixtures import CT1, NOVA, REC_COMPLETED, REC_RECORDING, load


class ParseTest(unittest.TestCase):
    def test_channels_sorted_enabled_only(self):
        channels = tvh.parse_channels(load("channel_grid.json"))
        self.assertEqual([c.name for c in channels], ["ČT1 HD", "Nova HD", "Prima HD"])
        self.assertEqual(channels[0].uuid, CT1)
        self.assertEqual(channels[0].icon, "imagecache/3")
        self.assertEqual(channels[2].icon, "")

    def test_events(self):
        events = tvh.parse_events(load("epg_events_grid.json"))
        self.assertEqual(len(events), 3)  # zero-length event dropped
        first = events[0]
        self.assertEqual((first.event_id, first.channel, first.title), ("4711", CT1, "Události"))
        self.assertEqual(first.description, "Hlavní zpravodajská relace.")
        self.assertEqual((first.dvr_uuid, first.dvr_state), (REC_RECORDING, "recording"))
        self.assertEqual(events[1].description, "Jen summary.")
        self.assertEqual(events[1].genre, [16, 20])
        self.assertEqual((events[2].dvr_uuid, events[2].channel), ("", NOVA))

    def test_dvr_entries(self):
        entries = {e.uuid: e for e in tvh.parse_dvr_entries(load("dvr_entry_grid.json"))}
        rec = entries[REC_RECORDING]
        self.assertEqual((rec.channel, rec.title, rec.broadcast, rec.sched_status), (CT1, "Události", "4711", "recording"))
        done = entries[REC_COMPLETED]
        self.assertEqual((done.broadcast, done.description), ("", "Starý popis."))

    def test_language_maps(self):
        entries = tvh.parse_dvr_entries({"entries": [{"uuid": "x", "title": {"eng": "Title"}, "start": 1, "stop": 2}]})
        self.assertEqual(entries[0].title, "Title")

    def test_inputs(self):
        tuners = tvh.parse_inputs(load("status_inputs.json"))
        self.assertEqual([t.in_use for t in tuners], [True, False])
        self.assertIn("DVB-T #0", tuners[0].name)

    def test_garbage_is_empty(self):
        for data in (None, [], {"entries": "x"}, {"entries": [1, "a"]}):
            self.assertEqual(tvh.parse_channels(data), [])
            self.assertEqual(tvh.parse_events(data), [])


class Recorder:
    def __init__(self, body=b"{}", error=None):
        self.requests, self.body, self.error = [], body, error

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


class ClientTest(unittest.TestCase):
    def test_posts_form_with_json_values_and_basic_auth(self):
        opener = Recorder(json.dumps(load("epg_events_grid.json")).encode())
        client = tvh.TvheadendClient("http://tvh:9981/", "cztvhub", "pw", opener=opener)
        events = client.epg_events(1792300000, 1792303000)
        request = opener.requests[0]
        self.assertEqual(request.full_url, "http://tvh:9981/api/epg/events/grid")
        self.assertEqual(request.get_header("Authorization"), "Basic Y3p0dmh1Yjpwdw==")
        form = dict(urllib.parse.parse_qsl(request.data.decode()))
        filters = json.loads(form["filter"])
        self.assertEqual(filters[0], {"field": "stop", "type": "numeric", "value": 1792300000, "comparison": "gt"})
        self.assertEqual({e.event_id for e in events}, {"4711", "9001"})

    def test_remove_sends_uuid_list(self):
        opener = Recorder()
        tvh.TvheadendClient("http://tvh:9981", opener=opener).remove_dvr_entries(["a", "b"])
        form = dict(urllib.parse.parse_qsl(opener.requests[0].data.decode()))
        self.assertEqual(json.loads(form["uuid"]), ["a", "b"])
        self.assertIsNone(opener.requests[0].get_header("Authorization"))

    def test_status_codes(self):
        def error(code):
            return urllib.error.HTTPError("http://tvh/x", code, "x", {}, io.BytesIO(b""))

        cases = ((503, tvh.TunerBusy), (404, tvh.TvhNotFound), (500, tvh.TvhError))
        for code, exc in cases:
            client = tvh.TvheadendClient("http://tvh", opener=Recorder(error=error(code)))
            with self.assertRaises(exc):
                client.open_live("abc")
        client = tvh.TvheadendClient("http://tvh", opener=Recorder(error=urllib.error.URLError("refused")))
        with self.assertRaises(tvh.TvhError):
            client.channels()

    def test_media_urls(self):
        opener = Recorder(b"")
        client = tvh.TvheadendClient("http://tvh:9981", opener=opener)
        client.open_live(CT1)
        client.open_recording(REC_COMPLETED, 1000)
        self.assertEqual(opener.requests[0].full_url, f"http://tvh:9981/stream/channel/{CT1}?profile=pass")
        self.assertIsNone(opener.requests[0].data)
        self.assertEqual(opener.requests[1].full_url, f"http://tvh:9981/dvrfile/{REC_COMPLETED}")
        self.assertEqual(opener.requests[1].get_header("Range"), "bytes=1000-")

    def test_invalid_json(self):
        with self.assertRaises(tvh.TvhError):
            tvh.TvheadendClient("http://tvh", opener=Recorder(b"<html>")).channels()

    def test_external_icon_refused(self):
        client = tvh.TvheadendClient("http://tvh:9981", opener=Recorder(b""))
        with self.assertRaises(tvh.TvhNotFound):
            client.fetch_icon("https://example.com/logo.png")
