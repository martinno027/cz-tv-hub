"""The channel timeline of the continuous recording: a programme's window of its channel's recording
(spec/dvb-gateway.md "Continuous recording", "Channel timeline" and "Views")."""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cztvhub.config import AdsConfig, Config, DvbConfig
from cztvhub.dvb import archive as archive_mod
from cztvhub.dvb.archive import Archive, position_of, recording_id, timeline_window
from cztvhub.dvb.buffer import BufferStore, Segment
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.recorder import RecorderManager
from cztvhub.dvb.thumbs import Settings, Thumbnails
from cztvhub.dvb.tvheadend import EpgEvent
from dvb_fixtures import CT1, NOVA, REC_COMPLETED, FakeClient

KEY = "s3cret-" * 6
WORKER_KEY = "w0rker-" * 6
AUTH = {"Authorization": "Bearer " + KEY}
WORKER = {"Authorization": "Bearer " + WORKER_KEY}
H = 3600
M = 60
B = 1792821600  # 2026-10-24 06:00 UTC
STEP = 60.0  # segment length of the synthetic recording
RUN_A = "1792818000-aaaa"  # two audio tracks
RUN_B = "1792832700-bbbb"  # one audio track, starts right where run A ends (a run change without a gap)
HIDDEN = "hidden0channel0not0offered00000"
FIXTURE = Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "dvb" / "dvb_timeline.json"


def box(typ: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), typ) + body


def full(typ: bytes, body: bytes) -> bytes:
    return box(typ, bytes(4) + body)


def trak(track_id: int, handler: bytes) -> bytes:
    tkhd = full(b"tkhd", struct.pack(">III", 0, 0, track_id) + bytes(68))
    mdhd = full(b"mdhd", struct.pack(">IIII", 0, 0, 90000, 0) + bytes(4))
    hdlr = full(b"hdlr", bytes(4) + handler + bytes(12))
    return box(b"trak", tkhd + box(b"mdia", mdhd + hdlr))


def init(audio: int) -> bytes:
    mvhd = full(b"mvhd", struct.pack(">IIII", 0, 0, 1000, 0) + bytes(80))
    traks = trak(1, b"vide") + b"".join(trak(2 + i, b"soun") for i in range(audio))
    return box(b"ftyp", b"iso5\0\0\0\0") + box(b"moov", mvhd + traks)


def event(start, stop, title, subtitle="", channel=NOVA):
    return EpgEvent(f"e{start}-{stop}", channel, title, subtitle, "", [], start, stop)


P0 = event(B - 3 * H, B, "Noc")
P1 = event(B, B + 3 * H, "Ranní show")
P2 = event(B + 3 * H, B + 3 * H + 30 * M, "Zprávy", "Ranní vydání")  # the anchor of most tests
P3 = event(B + 3 * H + 30 * M, B + 4 * H, "Teleshopping")
TRAILER = event(B + 4 * H, B + 4 * H + 5 * M, "Upoutávka")  # lies in the gap: not recorded
P4 = event(B + 4 * H + 5 * M, B + 6 * H + 30 * M, "Film")
P5 = event(B + 6 * H + 30 * M, B + 8 * H, "Večer")
ZERO = event(B + 5 * H, B + 5 * H, "zero length")
OLD = event(B - 10 * H, B - 9 * H, "Před nahráváním")  # before anything was recorded
NEWS = recording_id(NOVA, P2.start, P2.stop)


def rid(e):
    return recording_id(NOVA, e.start, e.stop)


class EpgClient(FakeClient):
    """TVHeadend with a synthetic EPG (the gateway's EPG history stays empty)."""

    def __init__(self, events):
        super().__init__()
        self.events = events

    def epg_events(self, start, stop):
        self._check()
        return [e for e in self.events if e.stop > start and e.start < stop]


class HealthyRecorder:
    state = "nahrává"
    run = RUN_B

    def healthy(self, now=None, within=20.0):
        return True

    def stop(self, wait=0):
        pass

    def start(self):
        pass


def parse(text):
    """(segment URIs, EXTINF durations, positions where a discontinuity starts a new piece, EXT-X-START)."""
    uris, durations, starts, start_offset = [], [], [0.0], None
    position, pending = 0.0, False
    for line in text.splitlines():
        if line.startswith("#EXT-X-START:TIME-OFFSET="):
            start_offset = float(line.split("=")[1].split(",")[0])
        elif line == "#EXT-X-DISCONTINUITY":
            pending = True
        elif line.startswith("#EXTINF:"):
            if pending:
                starts.append(round(position, 3))
                pending = False
            durations.append(float(line[8:].rstrip(",")))
            position += durations[-1]
        elif line.startswith("/dvb/v/"):
            uris.append(line)
    return uris, durations, starts, start_offset


class TimelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = {"now": B + 5 * H}
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.root / "hls", state_dir=self.root / "state",
                                   buffer_dir=self.root / "buffer"),
                     ads=AdsConfig(enabled=True, channels=["Nova HD"], worker_key=WORKER_KEY,
                                   breaks_dir=self.root / "breaks"))
        self.client = EpgClient([P0, P1, P2, P3, TRAILER, P4, P5, ZERO, OLD,
                                 event(B, B + H, "Skrytý", channel=HIDDEN)])
        self.store = BufferStore(self.root / "buffer")
        self.settings = Settings(self.root / "state")
        self.settings.update(recordChannels=[NOVA])
        recorders = RecorderManager(self.store, lambda ch: None, log=lambda m: None,
                                    factory=lambda ch: HealthyRecorder())
        self.gw = None
        self.archive = Archive(self.store, recorders, self.settings, Thumbnails(self.client, self.settings), 3,
                               now=lambda: self.clock["now"], log=lambda m: None,
                               offered=lambda: self.gw._offered_ids(),
                               is_programme=lambda c, s, e: self.gw._is_programme(c, s, e),
                               programmes=lambda c, s, e: self.gw._programmes(c, s, e), allow_system_disk=True)
        self.gw = DvbGateway(cfg, client=self.client, now=lambda: self.clock["now"], log=lambda m: None,
                             archive=self.archive)
        self.store.save_run(NOVA, RUN_A, init(2), {"audio": [{"label": "Čeština", "language": "ces"},
                                                              {"label": "Angličtina", "language": "eng"}]})
        self.store.save_run(NOVA, RUN_B, init(1), {"audio": [{"label": "Čeština", "language": "ces"}]})
        self.store.save_run(HIDDEN, RUN_A, init(1), {"audio": [{"label": "Čeština", "language": "ces"}]})
        self.n = 0
        self.record(NOVA, RUN_A, B - H, B + 3 * H + 5 * M)
        self.record(NOVA, RUN_B, B + 3 * H + 5 * M, B + 4 * H)
        self.record(NOVA, RUN_B, B + 4 * H + 5 * M, self.clock["now"])  # a gap of 5 min within run B
        self.record(HIDDEN, RUN_A, B, B + H)
        self.recorded_until = self.clock["now"]
        self.archive.sync()

    def record(self, channel, run, start, end):
        work = self.root / "work"
        work.mkdir(exist_ok=True)
        t = float(start)
        while t < end:
            path = work / f"s{self.n}.m4s"
            path.write_bytes(bytes([self.n % 256]) * 50)
            self.store.add(channel, run, self.n, t, STEP, path)
            t += STEP
            self.n += 1

    def later(self, now):
        """Time passes; the recording goes on until ``now``."""
        self.record(NOVA, RUN_B, self.recorded_until, now)
        self.recorded_until = self.clock["now"] = now

    def get(self, path, headers=AUTH):
        resp = self.gw.handle(path, {}, headers, "GET", b"", "192.168.1.10")
        body = json.loads(resp.body) if resp.content_type.startswith("application/json") and resp.body else resp.body
        return resp, body

    def playlists(self, rid_):
        """Master playlist → {role: media playlist text}."""
        resp, body = self.get(f"/dvb/timeline/{rid_}.m3u8")
        self.assertEqual(resp.status, 200, body)
        master = body.decode()
        out = {}
        for line in master.splitlines():
            uri = line.split('URI="')[1].rstrip('"') if 'URI="' in line else line if line.startswith("/dvb/v/") else ""
            if uri:
                resp, text = self.get(uri)
                self.assertEqual(resp.status, 200, uri)
                out[uri.rsplit("/", 1)[1].split(".")[0]] = text.decode()
        return out

    # -- window -------------------------------------------------------------------------------
    def test_window_is_programme_plus_margins_at_most_12_hours(self):
        self.assertEqual(timeline_window(B, B + 30 * M), (B - 3 * H, B + 30 * M + 3 * H))
        self.assertEqual(timeline_window(B, B + 6 * H), (B - 3 * H, B + 9 * H))  # exactly 12 h
        self.assertEqual(timeline_window(B, B + 10 * H), (B - H, B + 11 * H))  # equal smaller margins
        self.assertEqual(timeline_window(B, B + 12 * H), (B, B + 12 * H))  # never below 0
        self.assertEqual(timeline_window(B, B + 12 * H - 1), (B, B + 12 * H - 1))
        for length in (1, 30 * M, 6 * H + 1, 11 * H + 7, 12 * H):
            lo, hi = timeline_window(B, B + length)
            self.assertLessEqual(hi - lo, 12 * H, length)
            self.assertEqual(B - lo, hi - B - length, length)

    def test_position_of_maps_wall_clock_through_gaps(self):
        segs = [Segment(NOVA, RUN_A, i, i, B + i * 10.0, 10.0, "") for i in range(3)] + \
               [Segment(NOVA, RUN_A, 3, 3, B + 100.0, 10.0, "")]
        self.assertEqual(position_of(segs, B - 50), 0)
        self.assertEqual(position_of(segs, B + 15), 15)
        self.assertEqual(position_of(segs, B + 50), 30)  # in the gap: the next segment's start
        self.assertEqual(position_of(segs, B + 105), 35)
        self.assertEqual(position_of(segs, B + 500), 40)

    # -- the JSON -------------------------------------------------------------------------------
    def test_growing_window(self):
        resp, info = self.get(f"/dvb/timeline/{NEWS}")
        self.assertEqual(resp.status, 200, info)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertEqual(info["id"], NEWS)
        self.assertEqual(info["channel"], NOVA)
        self.assertEqual(info["playlist"], f"/dvb/timeline/{NEWS}.m3u8")
        self.assertFalse(info["complete"])
        self.assertEqual(info["programme"], {"start": "2026-10-24T09:00:00Z", "end": "2026-10-24T09:30:00Z"})
        # window 06:00..12:30 clipped to now (11:00): a run change at 09:05, a gap 10:00..10:05
        self.assertEqual(info["pieces"], [
            {"position": 0.0, "time": "2026-10-24T06:00:00.000Z", "duration": 11100.0},
            {"position": 11100.0, "time": "2026-10-24T09:05:00.000Z", "duration": 3300.0},
            {"position": 14400.0, "time": "2026-10-24T10:05:00.000Z", "duration": 3300.0},
        ])
        self.assertEqual([(p["id"], p["title"]) for p in info["programmes"]], [
            (rid(P1), "Ranní show"), (NEWS, "Zprávy"), (rid(P3), "Teleshopping"), (None, "Upoutávka"),
            (rid(P4), "Film")])  # the zero-length event is no programme; P5 has not started
        news = info["programmes"][1]
        self.assertEqual(news, {"id": NEWS, "title": "Zprávy", "subtitle": "Ranní vydání",
                                "start": "2026-10-24T09:00:00Z", "end": "2026-10-24T09:30:00Z", "breaks": False})
        self.assertEqual(info["previous"], rid(P0))  # airing 1 s before 06:00
        self.assertIsNone(info["next"])  # only once the window is complete

    def test_shape_matches_the_shared_fixture(self):
        sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
        sample.pop("unknownFieldIsIgnored")
        info = self.get(f"/dvb/timeline/{NEWS}")[1]
        self.assertEqual(set(info), set(sample))
        self.assertEqual(set(info["programme"]), set(sample["programme"]))
        self.assertEqual(set(info["pieces"][0]), set(sample["pieces"][0]))
        self.assertEqual(set(info["programmes"][0]), set(sample["programmes"][0]))

    def test_complete_window_gets_next_and_vod(self):
        self.later(B + 9 * H)
        info = self.get(f"/dvb/timeline/{NEWS}")[1]
        self.assertTrue(info["complete"])
        self.assertEqual(info["pieces"][-1], {"position": 14400.0, "time": "2026-10-24T10:05:00.000Z",
                                              "duration": 8700.0})  # up to the window's end, 12:30
        self.assertEqual(info["next"], rid(P5))  # airing 1 s after 12:30
        self.assertEqual(info["previous"], rid(P0))
        text = self.playlists(NEWS)["v"]
        self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", text)
        self.assertTrue(text.endswith("#EXT-X-ENDLIST\n"))

    def test_previous_null_at_the_recording_start_next_null_at_its_end(self):
        info = self.get(f"/dvb/timeline/{rid(P1)}")[1]  # window 03:00..12:00, recorded from 05:00
        self.assertEqual(info["pieces"][0]["time"], "2026-10-24T05:00:00.000Z")
        self.assertIsNone(info["previous"])
        self.later(B + 9 * H)
        self.clock["now"] = B + 15 * H  # the recording stopped at 15:00; P5's window ended at 17:00
        info = self.get(f"/dvb/timeline/{rid(P5)}")[1]
        self.assertTrue(info["complete"])
        self.assertIsNone(info["next"])
        self.assertEqual(info["previous"], NEWS)  # P5's window starts 09:30; the news air at 09:29:59

    def test_previous_falls_back_to_the_latest_programme_starting_before(self):
        early = event(B - 3 * H, B - 10 * M, "Noc (kratší)")  # nothing in the EPG airs at 05:59:59
        self.client.events = [early if e is P0 else e for e in self.client.events]
        self.assertEqual(self.get(f"/dvb/timeline/{NEWS}")[1]["previous"], rid(early))

    def test_breaks_marked_per_programme(self):
        self.gw.breaks.put(NEWS, "logo-v1", [{"start": 100.0, "end": 200.0, "confidence": 1.0}])
        info = self.get(f"/dvb/timeline/{NEWS}")[1]
        self.assertEqual([p["breaks"] for p in info["programmes"]], [False, True, False, False, False])

    # -- the playlist ---------------------------------------------------------------------------
    def test_playlist_positions_match_the_pieces_in_every_rendition(self):
        lists = self.playlists(NEWS)
        self.assertEqual(sorted(lists), ["a0", "a1", "v"])
        info = self.get(f"/dvb/timeline/{NEWS}")[1]
        parsed = {role: parse(text) for role, text in lists.items()}
        for role, (uris, durations, starts, offset) in parsed.items():
            self.assertEqual(starts, [p["position"] for p in info["pieces"]], role)
            self.assertAlmostEqual(sum(durations), sum(p["duration"] for p in info["pieces"]), 3, role)
            # a player without its own seek starts 30 s before the programme: 09:00 − 30 s is 3 h − 30 s in
            self.assertEqual(offset, 3 * H - 30, role)
            self.assertIn("#EXT-X-PLAYLIST-TYPE:EVENT", lists[role])
            self.assertNotIn("#EXT-X-ENDLIST", lists[role])
            self.assertNotIn("TIME-OFFSET=0,", lists[role])
        # the second audio track exists only in run A; run B plays the first one there, at the same positions
        self.assertEqual(len(parsed["a1"][0]), len(parsed["v"][0]))
        self.assertEqual(lists["a1"].count("#EXT-X-MAP"), 2)
        self.assertEqual(lists["v"].count("#EXT-X-DISCONTINUITY"), 2)
        self.assertEqual(self.get(parsed["a1"][0][-1])[0].status, 200)

    def test_playlist_lists_only_the_window(self):
        uris, durations, _, _ = parse(self.playlists(NEWS)["v"])
        g_first = int(uris[0].rsplit("/", 1)[1].split(".")[0])
        before = self.store.between(NOVA, B - STEP, B)[0]  # recorded, but before the window
        self.assertEqual(g_first, before.g + 1)
        view = uris[0].split("/")[3]
        self.assertEqual(self.get(f"/dvb/v/{view}/v/{before.g}.m4s")[0].status, 404)
        self.assertEqual(self.get(uris[0])[0].status, 200)
        self.assertEqual(self.get(uris[0], headers={})[0].status, 401)
        # a long programme: equal smaller margins (12 h in total)
        long_ = event(B - H, B + 9 * H, "Maraton")
        self.client.events.append(long_)
        uris = parse(self.playlists(rid(long_))["v"])[0]
        first = self.store.by_g(NOVA, int(uris[0].rsplit("/", 1)[1].split(".")[0]))
        self.assertEqual(first.start, B - H)  # margin 1 h, clipped to what is recorded (from 05:00)

    # -- access ---------------------------------------------------------------------------------
    def test_refused_ids(self):
        for path in (
            "/dvb/timeline/garbage",
            f"/dvb/timeline/{REC_COMPLETED}",  # a TVHeadend DVR recording has no timeline
            f"/dvb/timeline/{REC_COMPLETED}.m3u8",
            f"/dvb/timeline/{recording_id(NOVA, P2.start + 1, P2.stop)}",  # not an EPG programme
            f"/dvb/timeline/{recording_id(NOVA, B, B + 13 * H)}",  # longer than 12 h
            f"/dvb/timeline/{rid(OLD)}",  # before anything was recorded
            f"/dvb/timeline/{rid(P5)}.m3u8",  # not started yet
            f"/dvb/timeline/{recording_id(CT1, B, B + H)}",  # channel not recorded
            f"/dvb/timeline/{recording_id(HIDDEN, B, B + H)}",  # recorded but not offered
            f"/dvb/timeline/{recording_id(HIDDEN, B, B + H)}.m3u8",
        ):
            resp, body = self.get(path)
            self.assertEqual(resp.status, 404, path)
        self.assertEqual(self.get(f"/dvb/timeline/{NEWS}", headers={})[0].status, 401)

    def test_worker_key_cannot_use_timelines(self):
        self.later(B + 9 * H)  # ended, analysable programmes
        self.assertEqual(self.get(f"/dvb/timeline/{NEWS}", headers=WORKER)[0].status, 403)
        self.assertEqual(self.get(f"/dvb/timeline/{NEWS}.m3u8", headers=WORKER)[0].status, 403)
        uri = parse(self.playlists(NEWS)["v"])[0][0]
        self.assertEqual(self.get(uri, headers=WORKER)[0].status, 404)

    def test_at_most_four_timeline_views_per_channel(self):
        uris = {e.title: parse(self.playlists(rid(e))["v"])[0][0] for e in (P0, P1, P2, P3)}
        programme = self.get(f"/dvb/recording/{rid(P3)}.m3u8")[1].decode()  # programme views do not count
        self.assertEqual(self.get(f"/dvb/timeline/{NEWS}")[0].status, 200)  # the same window again: no new view
        self.assertEqual(self.get(uris[P0.title])[0].status, 200)  # now P1's window is the one used longest ago
        fifth = parse(self.playlists(rid(P4))["v"])[0][0]  # a fifth window drops it
        self.assertEqual(self.get(uris[P1.title])[0].status, 404)
        for e in (P0, P2, P3):
            self.assertEqual(self.get(uris[e.title])[0].status, 200, e.title)
        self.assertEqual(self.get(fifth)[0].status, 200)
        uri = next(line for line in programme.splitlines() if line.startswith("/dvb/v/"))
        self.assertEqual(self.get(uri)[0].status, 200)
        self.assertEqual(len(self.archive._views), 5)
        self.assertEqual(sorted(self.archive._view_keys.values()), sorted(self.archive._views))

    def test_expired_views_lose_their_keys(self):
        for e in (P1, P2, P3):
            self.get(f"/dvb/recording/{rid(e)}.m3u8")
        self.get(f"/dvb/timeline/{NEWS}")
        self.assertEqual(len(self.archive._view_keys), 4)
        later = archive_mod.time.monotonic() + archive_mod.VIEW_IDLE + 1
        with mock.patch.object(archive_mod.time, "monotonic", return_value=later):
            self.get(f"/dvb/timeline/{rid(P1)}")
        self.assertEqual(list(self.archive._view_keys), [f"t::{rid(P1)}"])
        self.assertEqual(list(self.archive._view_keys.values()), list(self.archive._views))


if __name__ == "__main__":
    unittest.main()
