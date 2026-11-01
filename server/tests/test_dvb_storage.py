"""Tab "Přehled nahrávek" on /admin (T43, spec/dvb-gateway.md "Přehled nahrávek"): loader, average GB per hour,
the free-space reserve setting and the day limit moved into the tab."""
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from cztvhub.dvb import space
from cztvhub.dvb.admin import PAGE
from cztvhub.dvb.archive import Archive
from cztvhub.dvb.buffer import BufferStore, Segment, _Channel, block_name
from cztvhub.dvb.recorder import RecorderManager
from cztvhub.dvb.thumbs import Settings, Thumbnails
from test_dvb_admin import AUTH, CT1, CT2, KEY, NOVA, PASSWORD, POST, InProcess

GB = 1000 ** 3
T0 = 1792300800  # a block boundary
RUN = "1792300800-ab12"


def fill(store, channel, seconds, gb_per_hour, seg=150, start=T0):
    """``seconds`` of recording of ``channel`` at ``gb_per_hour`` (sizes set per block, no big files)."""
    chan = store._channels.setdefault(channel, _Channel())
    n = int(seconds // seg)
    for i in range(n):
        t = start + i * seg
        chan.segments.append(Segment(channel, RUN, i, chan.next_g, t, seg, block_name(t)))
        chan.next_g += 1
        chan.sizes[block_name(t)] = chan.sizes.get(block_name(t), 0) + int(gb_per_hour * GB * seg / 3600)
    chan.reindex()


class FakeRec:
    def __init__(self, ch):
        self.ch, self.run, self.state = ch, None, "nahrává se"

    def start(self):
        pass

    def stop(self, wait=0):
        pass


class StorageAdminTest(unittest.TestCase):
    """Own set-up like test_dvb_admin's AdminTest (subclassing it would run all its tests again here)."""

    def setUp(self):
        from cztvhub.config import AdminConfig, Config, DvbConfig
        from cztvhub.dvb.admin import AdminSite
        from cztvhub.dvb.fake import FakeTvheadend
        from cztvhub.dvb.gateway import DvbGateway
        from cztvhub.dvb.hls import HlsManager
        from cztvhub.dvb.tvheadend import TvheadendClient
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.fake = FakeTvheadend(root, log=lambda m: None)
        self.fake.clips = [root / "clip.ts"]
        self.fake.build_dvr(time.time())  # no tick(): that would start ffmpeg recorders
        self.addCleanup(self.fake.stop)
        self.opener = InProcess(self.fake)
        self.client = TvheadendClient("http://tvh", opener=self.opener)
        self.cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=root / "hls", state_dir=root / "state",
                                        buffer_dir=root / "buffer", buffer_on_system_disk=True),
                          admin=AdminConfig(password=PASSWORD))
        self.logs = []
        hls = HlsManager(self.cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.gateway = DvbGateway(self.cfg, client=self.client, hls=hls, log=self.logs.append)
        self.addCleanup(self.gateway.archive.recorders.stop_all)
        self.site = AdminSite(self.cfg, self.gateway)
        self.store = self.gateway.archive.store
        self.disk = [100 * GB, 128 * GB]  # free, total
        self.store.disk = lambda: tuple(self.disk)

    def request(self, method, path, headers=None, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        resp = self.site.handle(method, path, AUTH if headers is None else headers, raw)
        return resp, json.loads(resp.body)

    def settings(self, body):
        return self.request("POST", "/admin/api/settings", POST, body)

    def test_reserve_setting_range_null_and_log(self):
        # 128 GB disk: 1..64 GB, automatic = 12.8 GB
        for bad in (0, 65, -3, 1.5, "10", True, [], {}):
            resp, data = self.settings({"reserveGb": bad})
            self.assertEqual(resp.status, 400, bad)
            self.assertEqual(data["error"],
                             "Rezervu lze nastavit na 1 až 64 GB (nejvýš polovina disku), nebo automaticky")
        self.assertIsNone(self.gateway.archive.reserve_gb())
        self.assertFalse(any("reserve" in line for line in self.logs), self.logs)
        for good in (1, 64, 20):
            resp, data = self.settings({"reserveGb": good})
            self.assertEqual(resp.status, 200, data)
            self.assertEqual(data["settings"]["reserveGb"], good)
            self.assertIn(f"admin: reserve {good} GB", self.logs)
        stored = json.loads((self.cfg.dvb.state_dir / "settings.json").read_text())
        self.assertEqual(stored["reserveGb"], 20)
        _, data = self.request("GET", "/admin/api/storage")
        self.assertEqual(data["disk"]["reserveBytes"], 20 * GB)
        self.assertEqual(data["disk"]["reserveGb"], 20)
        self.assertEqual(data["disk"]["reserveAutoBytes"], int(12.8 * GB))
        self.assertEqual(data["disk"]["reserveMaxGb"], 64)
        resp, data = self.settings({"reserveGb": None})
        self.assertEqual(resp.status, 200)
        self.assertIsNone(data["settings"]["reserveGb"])
        self.assertEqual(self.logs[-1], "admin: reserve automatic")
        _, data = self.request("GET", "/admin/api/storage")
        self.assertEqual(data["disk"]["reserveBytes"], int(12.8 * GB))
        self.assertIsNone(data["disk"]["reserveGb"])
        # a smaller disk: half of it is the limit
        self.disk = [10 * GB, 20 * GB]
        resp, data = self.settings({"reserveGb": 11})
        self.assertEqual(data["error"],
                         "Rezervu lze nastavit na 1 až 10 GB (nejvýš polovina disku), nebo automaticky")

    def test_max_days_setting(self):
        for bad in (0, 15):
            resp, data = self.settings({"maxDays": bad})
            self.assertEqual(resp.status, 400)
            self.assertEqual(data["error"], "Uchovávat lze 1 až 14 dní")
        resp, data = self.settings({"maxDays": 5})
        self.assertEqual(data["settings"]["maxDays"], 5)
        self.assertIn("admin: recordings kept 5 days", self.logs)
        _, data = self.request("GET", "/admin/api/storage")
        self.assertEqual(data["maxDays"], 5)
        resp, data = self.settings({"other": 1})
        self.assertEqual(resp.status, 400)
        self.assertIn("reserveGb", data["error"])

    def test_averages_count_channels_with_ten_minutes(self):
        _, data = self.request("GET", "/admin/api/storage")
        self.assertIsNone(data["averageGbPerHour"])  # no channels: "—" on the page
        self.assertIsNone(data["totalGbPerHour"])
        fill(self.store, CT1, 3600, 1.0)
        fill(self.store, CT2, 600, 2.0)  # exactly 10 minutes: counts
        fill(self.store, NOVA, 450, 9.0)  # 7.5 minutes: not yet
        _, data = self.request("GET", "/admin/api/storage")
        self.assertEqual(data["averageGbPerHour"], 1.5)
        self.assertEqual(data["totalGbPerHour"], 3.0)
        rows = {c["id"]: c for c in data["channels"]}
        self.assertEqual(rows[CT2]["gbPerHour"], 2.0)
        self.assertEqual(rows[NOVA]["gbPerHour"], 9.0)  # shown per channel, left out of the average
        self.assertEqual(rows[CT1]["hours"], 1.0)

    def test_storage_asks_tvheadend_for_names_only(self):
        before = len(self.opener.calls)
        resp, _ = self.request("GET", "/admin/api/storage")
        self.assertEqual(resp.status, 200)
        paths = sorted(path for path, _ in self.opener.calls[before:])
        self.assertEqual(paths, ["channel/grid", "mpegts/mux/grid", "mpegts/service/grid"])


class PageTest(unittest.TestCase):
    def section(self, name):
        m = re.search(r'<section id="%s"[^>]*>(.*?)</section>' % name, PAGE, re.S)
        self.assertIsNotNone(m, name)
        return m.group(1)

    def test_loader_in_markup_and_before_the_request(self):
        storage = self.section("storage")
        # the table says it is loading before any JS ran
        self.assertRegex(storage,
                         r'<tbody id="storage-rows">\s*<tr><td[^>]*>Načítám přehled nahrávek…</td></tr>')
        body = PAGE[PAGE.index("function loadStorage(quiet) {"):]
        self.assertLess(body.index("Načítám přehled nahrávek…"), body.index('api("GET", "/admin/api/storage")'))
        self.assertIn("Přehled nahrávek se nepodařilo načíst", body)

    def test_settings_live_in_the_storage_tab_only(self):
        self.assertEqual(PAGE.count('id="maxdays"'), 1)
        self.assertEqual(PAGE.count('id="reserve-gb"'), 1)
        storage = self.section("storage")
        self.assertIn('id="maxdays"', storage)
        self.assertIn("Uchovávat nahrávky nejvýše", storage)
        self.assertIn("Rezerva volného místa", storage)
        channels = self.section("channels")
        self.assertNotIn("maxdays", channels)
        self.assertNotIn("Uchovávat nahrávky", channels)
        self.assertIn("Průměrně za hodinu:", PAGE)
        self.assertIn("Všechny kanály dohromady:", PAGE)


class ReserveRetentionTest(unittest.TestCase):
    """The household's reserve is what the retention keeps free, and where a paused recording resumes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = BufferStore(Path(self.tmp.name) / "buffer")
        self.ch = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
        fill(self.store, self.ch, 4 * 600, 6.0)  # four blocks of 1 GB
        self.free = [15 * GB]
        self.store.disk = lambda: (self.free[0], 100 * GB)  # automatic reserve: 10 GB
        settings = Settings(Path(self.tmp.name) / "state")
        settings.update(recordChannels=[self.ch])
        self.manager = RecorderManager(self.store, lambda ch: None, factory=FakeRec, log=lambda m: None)
        self.archive = Archive(self.store, self.manager, settings, Thumbnails(None, settings), 3,
                               log=lambda m: None, offered=lambda: {self.ch}, allow_system_disk=True,
                               now=lambda: T0 + 2400)

    def test_enforce_keeps_the_households_reserve(self):
        self.assertEqual(self.archive.enforce().delete, [])  # 15 GB free, 10 GB automatic: nothing goes
        self.archive.settings.update(reserveGb=17)
        plan = self.archive.enforce()
        self.assertEqual([b.name for b in plan.delete], [block_name(T0), block_name(T0 + 600)])  # 2 GB short
        self.assertEqual(plan.shortfall, 0)

    def test_set_reserve_applies_at_once(self):
        self.archive.set_reserve_gb(16)
        self.assertEqual([b.name for b in self.archive.last_plan.delete], [block_name(T0)])

    def test_paused_resumes_at_the_households_reserve(self):
        self.archive.settings.update(reserveGb=20)
        self.free[0] = 21 * GB + 1  # above 20 GB + the 1 GB resume margin
        self.manager.pause(True)
        self.archive.enforce()
        self.assertFalse(self.manager.paused)
        # 21 GB + 1 GB margin needed (the automatic 10 GB would resume): the disk's free space (deleting here
        # frees nothing, the stub reports the same) stays below it, so recording stays paused
        self.archive.settings.update(reserveGb=21)
        self.manager.pause(True)
        self.archive.enforce()
        self.assertTrue(self.manager.paused)

    def test_hours_estimate_uses_the_reserve(self):
        auto = self.archive.overview({})["hoursPerChannel"]
        self.archive.settings.update(reserveGb=40)
        own = self.archive.overview({})["hoursPerChannel"]
        self.assertNotEqual(auto, own)
        # 15 GB free + 4 GB recorded − 40 GB reserve < 0 → 0 h; automatic: (19 − 10) GB at 6 GB/h = 1.5 h
        self.assertEqual(auto, 1.5)
        self.assertEqual(own, 0.0)


class SummaryTest(unittest.TestCase):
    """``summary``: stats and blocks of a channel in one pass (T43 speed-up), the same numbers as before."""

    def test_one_pass_numbers(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = BufferStore(Path(tmp.name) / "buffer")
        fill(store, "c", 1200, 3.6)  # 20 min: blocks 0 and 1
        fill(store, "c", 300, 3.6, start=T0 + 1500)  # 5 min after a 5-minute hole, block 2
        stats, blocks = store.summary("c")
        self.assertEqual([b.name for b in blocks], [block_name(T0), block_name(T0 + 600), block_name(T0 + 1200)])
        self.assertEqual([(b.start, b.end, b.seconds) for b in blocks],
                         [(T0, T0 + 600, 600), (T0 + 600, T0 + 1200, 600), (T0 + 1500, T0 + 1800, 300)])
        size = int(3.6 * GB * 150 / 3600) * 10
        self.assertEqual(stats, {"seconds": 1500.0, "bytes": size, "oldest": T0,
                                 "newest": T0 + 1800, "bytesPerHour": int(size / 1500 * 3600),
                                 "gaps": 1, "gapSeconds": 300.0, "blocks": 3})
        self.assertEqual(store.stats("c"), stats)
        self.assertEqual(store.blocks("c"), blocks)
        self.assertEqual(store.summary("none"), ({"seconds": 0, "bytes": 0, "oldest": None, "newest": None,
                                                  "bytesPerHour": None, "gaps": 0, "gapSeconds": 0, "blocks": 0}, []))


class ReserveRuleTest(unittest.TestCase):
    def test_reserve_bytes(self):
        self.assertEqual(space.reserve_bytes(118 * GB), int(11.8 * GB))  # automatic as before
        self.assertEqual(space.reserve_bytes(118 * GB, None), int(11.8 * GB))
        self.assertEqual(space.reserve_bytes(118 * GB, 3), 3 * GB)  # below the automatic minimum is allowed
        self.assertEqual(space.reserve_bytes(118 * GB, 100), 59 * GB)  # a smaller disk since: half of it
        self.assertEqual(space.reserve_max_gb(118 * GB), 59)
        self.assertEqual(space.reserve_max_gb(int(119.9 * GB)), 59)  # whole GB, rounded down

    def test_plan_and_estimate_take_the_override(self):
        blocks = {"c": [space.BlockInfo("c", block_name(T0 + i * 600), T0 + i * 600, T0 + i * 600 + 600, 600, GB)
                        for i in range(6)]}  # 6 GB/h
        self.assertEqual(space.plan(blocks, T0 + 3600, 3, free=15 * GB, total=100 * GB).delete, [])
        self.assertEqual(len(space.plan(blocks, T0 + 3600, 3, free=15 * GB, total=100 * GB, reserve_gb=18).delete), 3)
        self.assertEqual(space.hours_per_channel(blocks, 15 * GB, 100 * GB, 14), 1.8)  # (21 − 10) / 6
        self.assertEqual(space.hours_per_channel(blocks, 15 * GB, 100 * GB, 14, reserve_gb=3), 3.0)  # (21 − 3) / 6

    def test_rates_need_ten_minutes(self):
        short = [space.BlockInfo("a", "x", 0, 599, 599, GB)]
        enough = [space.BlockInfo("b", "y", 0, 600, 600, GB)]
        self.assertEqual(space.rates({"a": short, "b": enough}), {"b": GB / 600})


if __name__ == "__main__":
    unittest.main()
