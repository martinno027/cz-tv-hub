import json
import tempfile
import unittest
from pathlib import Path

from cztvhub.config import Config, DvbConfig
from cztvhub.dvb.epgstore import EpgHistory, merge_past
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from cztvhub.dvb.tvheadend import DvrEntry, EpgEvent
from dvb_fixtures import CT1, NOW, FakeClient

KEY = "epg-history-key-" * 3
AUTH = {"Authorization": "Bearer " + KEY}


def ev(event_id, start, stop, channel=CT1, title="t"):
    return EpgEvent(event_id, channel, title, "", "", [], start, stop)


class EpgHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = EpgHistory(Path(self.tmp.name))

    def test_keeps_the_past_and_persists(self):
        self.assertTrue(self.h.merge([ev("a", 0, 100), ev("b", 100, 200)], now=50, window=(0, 1000)))
        # later TVHeadend no longer lists "a" (it ended): the history still has it
        self.assertFalse(self.h.merge([ev("b", 100, 200)], now=150, window=(150, 1000)))
        self.assertEqual([e.event_id for e in self.h.between(0, 1000)], ["a", "b"])
        self.h.save()
        again = EpgHistory(Path(self.tmp.name))
        self.assertEqual(len(again), 2)
        self.assertEqual(json.loads((Path(self.tmp.name) / "epg-history.json").read_text())[0]["title"], "t")

    def test_future_part_is_authoritative(self):
        self.h.merge([ev("a", 100, 200), ev("b", 200, 300)], now=0, window=(0, 1000))
        self.h.merge([ev("b2", 200, 320)], now=50, window=(50, 1000))  # "a" cancelled, "b" rescheduled
        self.assertEqual(sorted(e.event_id for e in self.h.between(0, 1000)), ["b2"])

    def test_prune_and_garbage(self):
        self.h.merge([ev("old", 0, 10), ev("new", 100, 200)], now=0, window=(0, 1000))
        self.assertTrue(self.h.prune(50))
        self.assertEqual([e.event_id for e in self.h.between(0, 1000)], ["new"])
        (Path(self.tmp.name) / "epg-history.json").write_text("{broken")
        self.assertEqual(len(EpgHistory(Path(self.tmp.name))), 0)

    def test_merge_past_skips_programmes_with_a_recording(self):
        live = [ev("now", 300, 400)]
        history = [ev("now", 300, 400), ev("past", 100, 200), ev("rec", 200, 300)]
        entry = DvrEntry("d1", CT1, "t", "", "", [], 230, 300, "completed")  # recording starts 30 s later
        self.assertEqual([e.event_id for e in merge_past(live, history, [entry])], ["now", "past"])


class GatewayHistoryTest(unittest.TestCase):
    def test_past_programmes_come_back_in_the_guide(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=root / "hls", state_dir=root / "state"))
        client = FakeClient()
        client.dvr_entries = lambda grid="grid": []  # no recordings at all
        hls = HlsManager(root / "hls", idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        clock = [NOW - 7200]
        gw = DvbGateway(cfg, client=client, hls=hls, now=lambda: clock[0], log=lambda m: None)
        morning = ev("m1", NOW - 5400, NOW - 3600, title="Ranní zprávy")
        client.epg_events = lambda start, stop: [e for e in [morning] if e.stop > start and e.start < stop]
        gw.collect_epg()  # seen while it was still listed
        client.epg_events = lambda start, stop: []  # TVHeadend dropped it after it ended
        clock[0] = NOW
        events = json.loads(gw.handle("/dvb/epg", {}, AUTH).body)["events"]
        self.assertEqual([(e["title"], e["recording"]) for e in events], [("Ranní zprávy", None)])
        self.assertTrue((root / "state" / "epg-history.json").exists())


if __name__ == "__main__":
    unittest.main()
