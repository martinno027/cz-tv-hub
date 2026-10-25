"""The TMDb store (spec/dvb-gateway.md "Archive"): SQLite 0600, refresh and deletion after N months, migration from
T15's JSON cache, deletion without the key; the background worker and the shared TMDb limits (page lookups first,
≤ 30 per minute, Retry-After, no retry loops). TMDb is faked; nothing here touches the network."""
import json
import os
import sqlite3
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path

from archive_world import TMDB_KEY, Clock, FakeTmdb, World
from cztvhub.dvb import details as dt
from cztvhub.dvb import tmdbstore as ts

DAY = 86400
NOW = 1_790_000_000.0
MATCH = {"kind": "movie", "tmdbId": 9001, "rating": {"value": 7.1, "votes": 250}, "genreIds": [18, 36],
         "genres": ["Drama", "Historický"], "year": 2015, "poster": "tichaHora01.jpg"}


def key(title, kind="movie", year=None):
    return ts.store_key(kind, year, title)


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.now = Clock(NOW)
        self.months = {"n": ts.DEFAULT_MONTHS}
        self.logs = []

    def make(self):
        return ts.TmdbStore(self.dir, months=lambda: self.months["n"], now=self.now, log=self.logs.append)

    def test_keeps_only_the_rule_s_fields_private(self):
        store = self.make()
        store.put(key("Tichá hora", year=2015), "movie", 2015, "Tichá hora", MATCH)
        store.put(key("Nic"), "movie", None, "Nic", None)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / ts.FILE).st_mode), 0o600)
        for item in self.dir.iterdir():  # a journal left behind would have the same mode
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600, item.name)
        again = self.make().get(key("Tichá hora", year=2015))
        self.assertEqual((again.matched, again.match_kind, again.tmdb_id, again.rating, again.genre_ids,
                          again.genres, again.match_year, again.poster, again.fetched_at),
                         (True, "movie", 9001, {"value": 7.1, "votes": 250}, (18, 36), ("Drama", "Historický"),
                          2015, "tichaHora01.jpg", NOW))
        self.assertFalse(self.make().get(key("Nic")).matched)  # "no match" is stored too
        with sqlite3.connect(self.dir / ts.FILE) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(entries)")]
        self.assertEqual(columns, ["key", "kind", "year", "title", "matched", "match_kind", "tmdb_id", "rating_value",
                                   "rating_votes", "genre_ids", "genre_names", "match_year", "poster", "fetched_at"])

    def test_mode_is_restored(self):
        store = self.make()
        os.chmod(self.dir / ts.FILE, 0o644)
        store.put(key("A"), "movie", None, "A", None)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / ts.FILE).st_mode), 0o600)

    def test_refresh_due_before_deletion_after_n_months(self):
        store = self.make()
        k = key("Tichá hora")
        store.put(k, "movie", None, "Tichá hora", MATCH)
        age = 5 * ts.MONTH
        self.assertEqual(store.max_age, age)
        self.now.t = NOW + age - ts.REFRESH_LEAD - 1
        self.assertFalse(store.due(k))
        self.now.t += 1
        self.assertTrue(store.due(k))  # a week before deletion the worker refreshes it
        self.assertIsNotNone(store.get(k))
        self.assertEqual(store.posters(), {"tichaHora01.jpg"})
        self.now.t = NOW + age - 1
        self.assertIsNotNone(store.get(k))
        self.now.t = NOW + age
        self.assertEqual(store.posters(), set())  # nothing older is served
        self.assertIsNone(store.get(k))  # deleted on read
        self.assertEqual(len(store), 0)

    def test_short_period_has_a_short_lead(self):
        self.months["n"] = 1
        store = self.make()
        self.assertEqual(store.refresh_age, ts.MONTH - ts.REFRESH_LEAD)
        self.assertEqual(store.max_age, ts.MONTH)

    def test_any_positive_number_of_months(self):
        for value, expected in ((1, 1), (6, 6), (7, 7), (9999999, 9999999), (10 ** 12, ts.MAX_MONTHS),
                                (0, ts.DEFAULT_MONTHS), (-3, ts.DEFAULT_MONTHS), (True, ts.DEFAULT_MONTHS),
                                ("7", ts.DEFAULT_MONTHS), (None, ts.DEFAULT_MONTHS)):
            self.assertEqual(ts.clamp_months(value), expected, value)
        self.months["n"] = 9999999
        store = self.make()
        store.put(key("A"), "movie", None, "A", None)
        self.now.t += 400 * 365 * DAY
        self.assertIsNotNone(store.get(key("A")))

    def test_purged_at_start_and_when_n_shrinks(self):
        store = self.make()
        store.put(key("old"), "movie", None, "old", None)
        self.now.t += 2 * ts.MONTH
        store.put(key("new"), "movie", None, "new", None)
        self.months["n"] = 2
        self.make()  # at start
        self.assertEqual(len(store), 1)
        self.assertIn("dvb: tmdb: deleted 1 entry older than 2 month(s)", self.logs)
        self.months["n"] = 1
        self.now.t += ts.MONTH
        self.assertEqual(store.purge(), 1)
        self.assertEqual(len(store), 0)

    def test_future_dates_are_dropped(self):
        store = self.make()
        store.put(key("A"), "movie", None, "A", None)
        self.now.t = NOW - 2 * DAY  # the clock went back: a fetch "in the future" proves nothing
        self.assertIsNone(store.get(key("A")))

    def test_page_delete_and_delete_all(self):
        store = self.make()
        for title in ("b", "A", "c"):
            store.put(key(title), "movie", None, title, None)
        self.assertEqual([e.title for e in store.page(1, size=2)], ["A", "b"])
        self.assertEqual([e.title for e in store.page(2, size=2)], ["c"])
        self.assertEqual(store.delete(key("b")).title, "b")
        self.assertIsNone(store.delete(key("b")))
        self.assertEqual(store.delete_all(), 2)
        self.assertEqual(len(store), 0)

    def test_get_many(self):
        store = self.make()
        store.put(key("A"), "movie", None, "A", None)
        store.put(key("B"), "movie", None, "B", MATCH)
        self.now.t += 4 * ts.MONTH
        store.put(key("C"), "movie", None, "C", None)
        self.now.t += ts.MONTH  # A and B are 5 months old now
        self.assertEqual(set(store.get_many([key("A"), key("B"), key("C"), key("D")])), {key("C")})

    def test_broken_file_starts_empty(self):
        (self.dir / ts.FILE).write_bytes(b"not a database at all" * 100)
        store = self.make()
        self.assertEqual(len(store), 0)
        store.put(key("A"), "movie", None, "A", None)
        self.assertEqual(len(self.make()), 1)
        self.assertTrue(any("unreadable" in line for line in self.logs))

    def test_migration_from_the_json_cache(self):
        old = {"v": 1, "entries": {
            key("Hněv Titánů", year=2012): {"at": NOW - DAY, "match": {
                "kind": "movie", "title": "Hněv Titánů", "originalTitle": "Wrath of the Titans", "year": 2012,
                "overview": "Smyšlený popis.", "genres": ["Akční"], "runtimeMinutes": 99,
                "rating": {"value": 5.8, "votes": 7311}, "poster": "/dvb/details/image/w342/titanyPoster01.jpg",
                "tmdbId": 4401}},
            key("Mistři zastavárny", "tv"): {"at": NOW - 2 * DAY, "match": None},
            key("Staré", "tv"): {"at": NOW - 6 * ts.MONTH, "match": None},  # past N months: not carried over
            "not json": {"at": NOW, "match": None},
            key("bad", "tv"): {"at": "x", "match": None},
        }}
        (self.dir / ts.OLD_FILE).write_text(json.dumps(old), encoding="utf-8")
        (self.dir / (ts.OLD_FILE + ".tmp")).write_text("{")
        store = self.make()
        self.assertFalse((self.dir / ts.OLD_FILE).exists())
        self.assertFalse((self.dir / (ts.OLD_FILE + ".tmp")).exists())
        self.assertEqual(len(store), 2)
        titans = store.get(key("Hněv Titánů", year=2012))
        self.assertEqual((titans.tmdb_id, titans.match_kind, titans.rating, titans.genres, titans.genre_ids,
                          titans.poster, titans.fetched_at),
                         (4401, "movie", {"value": 5.8, "votes": 7311}, ("Akční",), (), "titanyPoster01.jpg",
                          NOW - DAY))
        self.assertFalse(store.get(key("Mistři zastavárny", "tv")).matched)
        self.assertIn("dvb: tmdb: moved 2 entries from tmdb-cache.json to tmdb.sqlite", self.logs)
        with sqlite3.connect(self.dir / ts.FILE) as db:  # no overview, no runtime: the rule keeps none of them
            self.assertNotIn("Smyšlený popis", json.dumps(db.execute("SELECT * FROM entries").fetchall(),
                                                          ensure_ascii=False))
        self.make()
        self.assertEqual(len(store), 2)  # once

    def test_unreadable_json_is_deleted_not_migrated(self):
        (self.dir / ts.OLD_FILE).write_text("{nope")
        self.assertEqual(len(self.make()), 0)
        self.assertFalse((self.dir / ts.OLD_FILE).exists())

    def test_remove_store(self):
        store = self.make()
        store.put(key("A"), "movie", None, "A", None)
        (self.dir / (ts.FILE + "-journal")).write_text("x")
        (self.dir / ts.OLD_FILE).write_text("{}")
        (self.dir / "settings.json").write_text("{}")
        ts.remove_store(self.dir, self.logs.append)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["settings.json"])
        self.assertIn("dvb: tmdb: no [tmdb] key, deleted tmdb.sqlite, tmdb.sqlite-journal, tmdb-cache.json",
                      self.logs)


class GatewayStoreTest(unittest.TestCase):
    def test_no_key_deletes_the_store_and_looks_nothing_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            world.gw.details.store.put(key("A"), "movie", None, "A", None)
            state = Path(tmp) / "state"
            self.assertTrue((state / ts.FILE).exists())
            world.close()
            world = World(Path(tmp), tmdb=False)
            try:
                self.assertFalse((state / ts.FILE).exists())
                self.assertIsNone(world.gw.details)
                self.assertIsNone(world.gw.tmdb_worker)
                resp, body = world.get("/dvb/archive")
                self.assertEqual(resp.status, 200)
                self.assertTrue(all(c["rating"] is None and not c["tmdb"] for r in body["rows"] for c in r["cards"]))
                self.assertFalse((state / ts.FILE).exists())
                self.assertEqual(world.tmdb.requests, [])
            finally:
                world.close()

    def test_months_come_from_the_admin_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            try:
                self.assertEqual(world.gw.tmdb_months(), 5)
                self.assertEqual(world.gw.details.store.max_age, 5 * ts.MONTH)
                world.settings.update(tmdbRefreshMonths=9)
                world.gw.settings.update(tmdbRefreshMonths=9)
                self.assertEqual(world.gw.details.store.max_age, 9 * ts.MONTH)
            finally:
                world.close()


class LimiterTest(unittest.TestCase):
    def test_background_gives_way_to_a_waiting_page_lookup(self):
        """The slot is free, but a page lookup is about to take it (it was waiting): the worker steps back."""
        limiter = dt.Limiter(wait=5)
        limiter._waiting = 1
        with self.assertRaises(dt.TmdbBusy):
            with limiter.slot(background=True):
                pass
        limiter._waiting = 0
        with limiter.slot(background=True):
            pass

    def test_waiting_is_counted_while_a_page_lookup_waits(self):
        limiter = dt.Limiter(wait=5)
        inside, release, page_done = threading.Event(), threading.Event(), threading.Event()
        seen = []

        def hold():
            with limiter.slot():
                inside.set()
                release.wait(5)

        def page():
            with limiter.slot():
                page_done.set()

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(inside.wait(5))
        waiter = threading.Thread(target=page)
        waiter.start()
        for _ in range(500):
            if limiter._waiting:
                seen.append(limiter._waiting)
                break
            threading.Event().wait(0.01)
        release.set()
        holder.join()
        waiter.join()
        self.assertEqual((seen, page_done.is_set(), limiter._waiting), ([1], True, 0))

    def test_background_never_waits_for_the_slot(self):
        limiter = dt.Limiter(wait=5)
        inside, release = threading.Event(), threading.Event()

        def hold():
            with limiter.slot():
                inside.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(inside.wait(5))
        try:
            with self.assertRaises(dt.TmdbBusy):
                with limiter.slot(background=True):
                    pass
        finally:
            release.set()
            holder.join()

    def test_shared_thirty_per_minute_background_twenty(self):
        clock = Clock()
        limiter = dt.Limiter(clock=clock)
        self.assertEqual((dt.PER_MINUTE, dt.BACKGROUND_PER_MINUTE), (30, 20))
        for _ in range(20):
            with limiter.slot(background=True):
                pass
        with self.assertRaises(dt.TmdbBusy):
            with limiter.slot(background=True):
                pass
        for _ in range(10):  # the rest of the minute is the pages'
            with limiter.slot():
                pass
        with self.assertRaises(dt.TmdbBusy):
            with limiter.slot():
                pass
        clock.t += 60
        with limiter.slot(background=True):
            pass

    def test_retry_after_pauses_everything_once(self):
        clock = Clock()
        http = FakeTmdb()
        http.error = urllib.error.HTTPError("https://api.themoviedb.org/3/search/movie", 429, "Too Many",
                                            {"Retry-After": "40"}, None)
        client = dt.TmdbClient(TMDB_KEY, limiter=dt.Limiter(clock=clock), urlopen=http)
        with self.assertRaises(dt.TmdbBusy):
            client.get("search/movie", {"query": "A"}, background=True)
        self.assertEqual(len(http.requests), 1)  # not retried
        http.error = None
        clock.t += 39
        for background in (True, False):
            with self.assertRaises(dt.TmdbBusy):
                client.get("search/movie", {"query": "A"}, background)
        self.assertEqual(len(http.requests), 1)  # nothing went out while TMDb asked to wait
        clock.t += 1
        client.get("search/movie", {"query": "A"})
        self.assertEqual(len(http.requests), 2)

    def test_retry_after_values(self):
        self.assertEqual(dt.retry_after("12"), 12)
        self.assertEqual(dt.retry_after(None), dt.RETRY_AFTER_DEFAULT)
        self.assertEqual(dt.retry_after("Wed, 21 Oct 2026 07:28:00 GMT"), dt.RETRY_AFTER_DEFAULT)
        self.assertEqual(dt.retry_after("-5"), dt.RETRY_AFTER_DEFAULT)
        self.assertEqual(dt.retry_after("nan"), dt.RETRY_AFTER_DEFAULT)
        self.assertEqual(dt.retry_after("999999"), dt.RETRY_AFTER_MAX)


class Q:
    def __init__(self, title):
        self.title = title
        self.key = key(title)


class WorkerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.now = Clock(NOW)
        self.clock = Clock()
        self.store = ts.TmdbStore(Path(tmp.name), now=self.now, log=lambda m: None)
        self.wanted = []
        self.looked = []
        self.fail = {}
        self.logs = []
        self.worker = ts.TmdbWorker(self.store, lambda: list(self.wanted), self.lookup, dt.TmdbBusy, dt.TmdbError,
                                    clock=self.clock, log=self.logs.append)

    def lookup(self, query):
        self.looked.append(query.title)
        error = self.fail.get(query.title)
        if error is not None:
            raise error
        self.store.put(query.key, "movie", None, query.title, None)

    def test_missing_and_stale_are_looked_up_fresh_ones_not(self):
        self.store.put(key("fresh"), "movie", None, "fresh", None)
        self.now.t += 5 * ts.MONTH - ts.REFRESH_LEAD
        self.store.put(key("new"), "movie", None, "new", None)
        self.wanted = [Q("fresh"), Q("missing"), Q("new"), Q("missing")]
        self.assertEqual(self.worker.run_once(), (2, False))
        self.assertEqual(self.looked, ["fresh", "missing"])  # "fresh" is now due, "missing" once
        self.assertEqual(self.worker.run_once(), (0, False))
        self.assertEqual(self.store.get(key("fresh")).fetched_at, self.now.t)  # refreshed in place

    def test_busy_ends_the_round(self):
        self.wanted = [Q("a"), Q("b"), Q("c")]
        self.fail["b"] = dt.TmdbBusy("limit")
        self.assertEqual(self.worker.run_once(), (1, True))
        self.assertEqual(self.looked, ["a", "b"])
        del self.fail["b"]
        self.assertEqual(self.worker.run_once(), (2, False))  # b is asked again in the next round

    def test_a_failed_title_waits_an_hour(self):
        self.wanted = [Q("a"), Q("b")]
        self.fail["a"] = dt.TmdbError("HTTP 500")
        self.assertEqual(self.worker.run_once(), (1, False))
        del self.fail["a"]
        self.assertEqual(self.worker.run_once(), (0, False))
        self.assertEqual(self.looked, ["a", "b"])  # no retry loop
        self.clock.t += ts.FAILED_WAIT
        self.assertEqual(self.worker.run_once(), (1, False))
        self.assertTrue(any("background lookup failed" in line for line in self.logs))

    def test_round_purges_expired_entries(self):
        self.store.put(key("old"), "movie", None, "old", None)
        self.now.t += 5 * ts.MONTH
        self.worker.run_once()
        self.assertEqual(len(self.store), 0)

    def test_thread_stops(self):
        self.worker.start()
        self.worker.poke()
        self.worker.stop()
        self.assertFalse(self.worker._thread.is_alive())

    def test_stop_during_a_round_is_not_lost(self):
        # stop() arriving while a round runs must end the thread, not leave it asleep for WORKER_IDLE
        inside, release = threading.Event(), threading.Event()

        def slow_lookup(query):
            inside.set()
            release.wait(5)

        self.worker.lookup = slow_lookup
        self.wanted = [Q("slow")]
        self.worker.start()
        self.assertTrue(inside.wait(5))
        threading.Timer(0.2, release.set).start()
        started = time.monotonic()
        self.worker.stop()
        self.assertFalse(self.worker._thread.is_alive())
        self.assertLess(time.monotonic() - started, 4)


class RefreshTest(unittest.TestCase):
    """Details.refresh: what the worker and /admin call."""

    def test_background_refresh_stores_ids_and_keeps_texts_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp), segments=False)
            try:
                details = world.gw.details
                match = details.refresh(dt.Query("Tichá hora", 2015, "movie"), background=True)
                self.assertEqual(match["overview"], "Smyšlený popis.")
                entry = details.store.get(ts.store_key("movie", 2015, "Tichá hora"))
                self.assertEqual((entry.genre_ids, entry.poster), ((18, 36), "tichaHora01.jpg"))
                with sqlite3.connect(Path(tmp) / "state" / ts.FILE) as db:
                    rows = json.dumps(db.execute("SELECT * FROM entries").fetchall(), ensure_ascii=False)
                for text in ("Smyšlený popis.", "Silent Mountain"):
                    self.assertNotIn(text, rows)
                # the page answer still has TMDb's texts (memory), without asking TMDb again
                before = len(world.tmdb.requests)
                answer = details.lookup(dt.Programme("Film: Tichá hora", description="(ČR, 2015)"))
                self.assertEqual((answer["match"]["originalTitle"], answer["match"]["runtimeMinutes"]),
                                 ("Silent Mountain", 88))
                self.assertEqual(len(world.tmdb.requests), before)
                # after a restart the texts are fetched once more, on the page's request
                details._extras.clear()
                again = details.lookup(dt.Programme("Film: Tichá hora", description="(ČR, 2015)"))
                self.assertEqual(again, answer)
                self.assertEqual(world.tmdb.urls()[-1].split("?")[0], "https://api.themoviedb.org/3/movie/9001")
                # and when TMDb is down then, the stored facts are still shown
                details._extras.clear()
                world.tmdb.error = urllib.error.URLError("down")
                fallback = details.lookup(dt.Programme("Film: Tichá hora", description="(ČR, 2015)"))
                self.assertEqual((fallback["match"]["rating"], fallback["match"]["title"],
                                  fallback["match"]["originalTitle"]),
                                 ({"value": 7.1, "votes": 250}, "Tichá hora", ""))
                self.assertNotIn("overview", fallback["match"])
            finally:
                world.close()


if __name__ == "__main__":
    unittest.main()
