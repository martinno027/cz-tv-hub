import unittest
from datetime import date, datetime, timezone

from cztvhub.dvb import epg
from cztvhub.dvb import tvheadend as tvh
from dvb_fixtures import CT1, NOVA, REC_COMPLETED, REC_FAILED, REC_RECORDING, REC_SCHEDULED, load


class BroadcastDayTest(unittest.TestCase):
    def test_day_starts_at_five_prague(self):
        # 2026-10-18 02:59 UTC = 04:59 CEST -> still the 17th
        self.assertEqual(epg.broadcast_day(datetime(2026, 10, 18, 2, 59, tzinfo=timezone.utc)), date(2026, 10, 17))
        self.assertEqual(epg.broadcast_day(datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)), date(2026, 10, 18))

    def test_bounds_follow_dst(self):
        start, end = epg.day_bounds(date(2026, 10, 18))
        self.assertEqual(epg.iso(start), "2026-10-18T03:00:00Z")
        self.assertEqual(end - start, 86400)
        start, end = epg.day_bounds(date(2026, 10, 24))  # CEST -> CET in the night to the 25th
        self.assertEqual(end - start, 25 * 3600)
        self.assertEqual(epg.iso(end), "2026-10-25T04:00:00Z")

    def test_allowed_range(self):
        self.assertEqual(epg.allowed_days(date(2026, 10, 18), 3), (date(2026, 10, 15), date(2026, 10, 20)))


class MappingTest(unittest.TestCase):
    def test_states(self):
        cases = {"recording": "recording", "recordingError": "recording", "scheduled": "scheduled",
                 "completed": "completed", "completedWarning": "failed", "completedError": "failed",
                 "completedRerecord": "failed", "invalid": "failed", "": "failed"}
        for raw, expected in cases.items():
            self.assertEqual(epg.recording_state(raw), expected, raw)

    def test_genre(self):
        self.assertEqual(epg.genre_text([0x20]), "Zprávy")
        self.assertEqual(epg.genre_text([0x14, 0x40]), "Film / drama")
        self.assertEqual(epg.genre_text([0xF0]), "")
        self.assertEqual(epg.genre_text([]), "")

    def test_build_merges_events_and_dvr_entries(self):
        events = tvh.parse_events(load("epg_events_grid.json"))
        entries = tvh.parse_dvr_entries(load("dvr_entry_grid.json"))
        out = epg.build_epg(events, entries, 1792000000, 1792400000)
        by_id = {e["id"]: e for e in out}
        live = by_id["4711"]
        self.assertEqual(live, {
            "id": "4711", "channel": CT1, "title": "Události", "subtitle": "Zpravodajství",
            "description": "Hlavní zpravodajská relace.", "genre": "Zprávy",
            "start": "2026-10-18T05:20:00Z", "end": "2026-10-18T06:30:00Z",
            "recording": {"id": REC_RECORDING, "state": "recording"},
        })
        self.assertEqual(by_id["4713"]["recording"], {"id": REC_SCHEDULED, "state": "scheduled"})
        self.assertIsNone(by_id["9001"]["recording"])
        past = by_id["dvr:" + REC_COMPLETED]
        self.assertEqual((past["channel"], past["title"], past["recording"]["state"]), (CT1, "Včerejší film", "completed"))
        self.assertEqual(by_id["dvr:" + REC_FAILED]["recording"]["state"], "failed")
        self.assertEqual(len(out), 5)  # every DVR entry appears once
        self.assertEqual([e["start"] for e in out if e["channel"] == CT1],
                         sorted(e["start"] for e in out if e["channel"] == CT1))
        self.assertTrue(all(e["channel"] in (CT1, NOVA) for e in out))

    def test_window_overlap(self):
        events = tvh.parse_events(load("epg_events_grid.json"))
        out = epg.build_epg(events, [], 1792305000, 1792306000)  # starts exactly when 4711 ends
        self.assertEqual([e["id"] for e in out], ["4713"])
