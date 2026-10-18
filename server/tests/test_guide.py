import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

from cztvhub.api import Programme, StreamError
from cztvhub.guide import (
    DAYS_AHEAD,
    DAYS_BACK,
    EPG_NAME,
    M3U_NAME,
    build_m3u,
    build_xmltv,
    collect_schedules,
    write_guide,
)

TODAY = date(2026, 10, 17)


def programme(idec, hour, **extra):
    start = datetime(2026, 10, 17, hour, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 17, hour, 50, tzinfo=timezone.utc)
    return Programme(idec, f"Pořad {idec} & spol.", start, end, True, False, **extra)


class M3uTest(unittest.TestCase):
    def test_live_and_catchup_point_at_addon(self):
        text = build_m3u()
        self.assertIn("plugin://plugin.video.cztvhub/play/live/CH_1\n", text)
        self.assertIn(
            'catchup-source="plugin://plugin.video.cztvhub/play/catchup/CH_1/{utc}/{utcend}?idec={catchup-id}"', text
        )
        self.assertIn('tvg-id="CT1.cz"', text)
        self.assertIn(f'catchup-days="{DAYS_BACK}"', text)


class XmltvTest(unittest.TestCase):
    def test_programme_fields(self):
        p = programme("111", 18, episode="Díl 1", description="Popis", genre="Film", image="https://img/x.jpg")
        root = ET.fromstring(build_xmltv({"ct1": [p]}))
        channel_ids = [c.get("id") for c in root.findall("channel")]
        self.assertIn("CT1.cz", channel_ids)
        [node] = root.findall("programme")
        self.assertEqual(node.get("start"), "20261017180000 +0000")
        self.assertEqual(node.get("stop"), "20261017185000 +0000")
        self.assertEqual(node.get("channel"), "CT1.cz")
        self.assertEqual(node.get("catchup-id"), "111")
        self.assertEqual(node.findtext("title"), "Pořad 111 & spol.")
        self.assertEqual(node.findtext("sub-title"), "Díl 1")
        self.assertEqual(node.findtext("desc"), "Popis")
        self.assertEqual(node.findtext("category"), "Film")
        self.assertEqual(node.find("icon").get("src"), "https://img/x.jpg")

    def test_optional_fields_omitted(self):
        root = ET.fromstring(build_xmltv({"ct1": [programme("1", 8)]}))
        node = root.find("programme")
        self.assertIsNone(node.find("desc"))
        self.assertIsNone(node.find("icon"))


class CollectTest(unittest.TestCase):
    def test_requests_each_day_and_dedupes(self):
        days = []

        def fetch(slugs, day):
            days.append(day)
            return {"ct1": [programme("1", 8)]}  # same item every day

        result = collect_schedules(fetch, TODAY)
        self.assertEqual(len(days), DAYS_BACK + DAYS_AHEAD + 1)
        self.assertEqual(min(days), date(2026, 10, 10))
        self.assertEqual(len(result["ct1"]), 1)

    def test_failing_days_are_skipped(self):
        def fetch(slugs, day):
            if day > TODAY:
                raise StreamError("not published")
            return {"ct1": [programme(day.isoformat(), 8)]}

        self.assertEqual(len(collect_schedules(fetch, TODAY)["ct1"]), DAYS_BACK + 1)

    def test_all_days_failing_raises(self):
        def fetch(slugs, day):
            raise StreamError("down")

        with self.assertRaises(StreamError):
            collect_schedules(fetch, TODAY)


class WriteTest(unittest.TestCase):
    def test_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "addon_data")
            count = write_guide(target, TODAY, lambda slugs, day: {"ct1": [programme("1", 8)]})
            self.assertEqual(count, 1)
            self.assertEqual(sorted(os.listdir(target)), sorted([EPG_NAME, M3U_NAME]))
