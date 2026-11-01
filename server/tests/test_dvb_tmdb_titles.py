"""T44: which titles the gateway asks TMDb for and which result counts (spec/dvb-gateway.md "Programme details",
golden cases spec/fixtures/tmdb_title_cases.json): a "series: episode" title falls back to the series, a series'
start year against the episode's year, ČT's original title first (behind gateway.CT_ORIGINAL_TITLE), at most four
searches. TMDb is a fake ``urlopen`` and ČT a fake schedule; nothing here touches the network."""
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path
from unittest import mock

from cztvhub import api
from cztvhub.config import Config, DvbConfig, TmdbConfig
from cztvhub.dvb import details as dt
from cztvhub.dvb import gateway as gateway_module
from cztvhub.dvb import tmdbstore
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from dvb_fixtures import NOW, FakeClient

CASES_FILE = Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "tmdb_title_cases.json"
GOLDEN = json.loads(CASES_FILE.read_text(encoding="utf-8"))
CASES = GOLDEN["cases"]
TMDB_V3 = "0123456789abcdef0123456789abcdef"
KEY = "app-key-" * 5
APP = {"Authorization": "Bearer " + KEY}
GRACE = next(c for c in CASES if c["name"].startswith("series: episode (owner's example)"))


def programme(case):
    p = case["programme"]
    return dt.Programme(p["title"], episode=p.get("episode", ""), description=p.get("description", ""),
                        genres=tuple(p.get("dvbGenres", ())), genre=p.get("genre", ""),
                        original_title=p.get("originalTitle", ""))


class FakeTmdb:
    """TMDb's search/{kind} answering from a golden case's ``results`` by (kind, query), and {kind}/{id} with the
    result itself; records every request."""

    def __init__(self, results):
        self.results = results
        self.requests = []

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(url.query))
        self.requests.append((url.path, query))
        parts = url.path.split("/")  # ['', '3', 'search', 'tv'] or ['', '3', 'tv', '90001']
        if parts[2] == "search":
            body = {"results": self.results.get(f"{parts[3]}|{query['query']}", [])}
        else:
            found = [r for k, items in self.results.items() if k.startswith(parts[2] + "|") for r in items
                     if str(r["id"]) == parts[3]]
            if not found:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            body = dict(found[0], vote_count=100, vote_average=7.5)
        return _Response(json.dumps(body).encode())

    def searches(self):
        return [[path.split("/")[3], q["query"]] for path, q in self.requests if path.startswith("/3/search/")]


class _Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, body):
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def details_for(state_dir, http, per_minute=dt.PER_MINUTE):
    limiter = dt.Limiter(per_minute=per_minute, wait=0.05, clock=lambda: 1000.0)
    return dt.Details(TMDB_V3, Path(state_dir), now=lambda: NOW, log=lambda m: None,
                      client=dt.TmdbClient(TMDB_V3, limiter=limiter, urlopen=http))


class GoldenCasesTest(unittest.TestCase):
    def test_fixture_covers_the_brief(self):
        names = " / ".join(c["name"] for c in CASES)
        for needle in ("owner's example", "+-1", "started 2021", "same year: no match", "plain film",
                       "original title"):
            self.assertIn(needle, names)
        self.assertTrue(all(len(c["searches"]) <= dt.SEARCHES_MAX for c in CASES))
        self.assertEqual(max(len(c["searches"]) for c in CASES), dt.SEARCHES_MAX)  # the budget is reached

    def test_query(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                q = dt.make_query(programme(case), GOLDEN["thisYear"])
                self.assertEqual({"title": q.title, "year": q.year, "kind": q.kind, "original": q.original},
                                 case["query"])

    def test_searches_and_pick(self):
        """The order of searches and the picked result, without HTTP: searches() and pick() as _search uses them."""
        for case in CASES:
            with self.subTest(case=case["name"]):
                q = dt.make_query(programme(case), GOLDEN["thisYear"])
                asked, match = [], None
                for kind, text in dt.searches(q):
                    asked.append([kind, text])
                    found = dt.pick(case["results"].get(f"{kind}|{text}", []), text, q.year, kind)
                    if found is not None:
                        match = {"kind": kind, "tmdbId": found["id"]}
                        break
                self.assertEqual(asked, case["searches"])
                self.assertEqual(match, case["match"])

    def test_end_to_end_with_fake_tmdb(self):
        """Details.lookup against a fake TMDb: the same searches in order, one details request on a match,
        never more than four searches."""
        for case in CASES:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as tmp:
                http = FakeTmdb(case["results"])
                answer = details_for(tmp, http).lookup(programme(case))
                self.assertNotIn("error", answer)
                self.assertEqual(http.searches(), case["searches"])
                self.assertLessEqual(len(http.searches()), dt.SEARCHES_MAX)
                expected = case["match"]
                got = {"kind": answer["match"]["kind"], "tmdbId": answer["match"]["tmdbId"]} \
                    if answer["match"] else None
                self.assertEqual(got, expected)
                self.assertEqual(len(http.requests), len(case["searches"]) + (1 if expected else 0))
                # only the title or a part of it (or ČT's original title) went to TMDb, never the description
                p = case["programme"]
                for _, query in http.requests:
                    if "query" in query:
                        self.assertTrue(query["query"] in p["title"] or query["query"] in p.get("originalTitle", "")
                                        or query["query"] == dt.clean_title(p["title"], "tv")
                                        or query["query"] == dt.clean_title(dt.series_part(
                                            dt.clean_title(p["title"])), "tv"), query["query"])


class RulesTest(unittest.TestCase):
    """The single rules behind the golden cases, so a mutation names what broke."""

    SERIES_2021 = {"id": 1, "name": "Detektiv Grace", "original_name": "Grace", "first_air_date": "2021-03-14"}

    def test_series_year_is_the_start(self):
        self.assertEqual(dt.pick([self.SERIES_2021], "Detektiv Grace", 2024, "tv"), self.SERIES_2021)
        self.assertEqual(dt.pick([self.SERIES_2021], "Grace", 2020, "tv"), self.SERIES_2021)  # 2021 <= 2020 + 1
        self.assertIsNone(dt.pick([self.SERIES_2021], "Grace", 2019, "tv"))
        self.assertIsNone(dt.pick([{**self.SERIES_2021, "first_air_date": ""}], "Grace", 2024, "tv"))

    def test_film_year_stays_within_one(self):
        film = {"id": 2, "title": "Grace", "release_date": "2021-03-14"}
        self.assertIsNone(dt.pick([film], "Grace", 2024, "movie"))
        self.assertEqual(dt.pick([film], "Grace", 2022, "movie"), film)
        self.assertIsNone(dt.pick([film], "Grace", 2019, "movie"))

    def test_series_part(self):
        self.assertEqual(dt.series_part("Detektiv Grace: Hodinky smrti"), "Detektiv Grace")
        self.assertEqual(dt.series_part("Kobra 11 – Pomsta: Návrat"), "Kobra 11")  # the first separator
        self.assertEqual(dt.series_part("Doktor Martin - Nový začátek"), "Doktor Martin")
        self.assertEqual(dt.series_part("Spider-Man"), "")
        self.assertEqual(dt.series_part("Pelíšky"), "")

    def test_original_is_not_in_the_key(self):
        with_original = dt.make_query(dt.Programme("Neviditelný host", original_title="The Unseen Guest"), 2026)
        without = dt.make_query(dt.Programme("Neviditelný host"), 2026)
        self.assertNotEqual(with_original.original, without.original)
        self.assertEqual(with_original.key, without.key)  # admin's refresh rebuilds the query from the stored title

    def test_every_search_counts_in_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            http = FakeTmdb(GRACE["results"])
            details = details_for(tmp, http, per_minute=2)
            answer = details.lookup(programme(GRACE))
            self.assertEqual(answer["error"], dt.TmdbBusy.user_message)
            self.assertEqual(len(http.requests), 2)  # the third search was refused, not sent
            self.assertEqual(len(details.store), 0)

    def test_background_lookup_uses_the_same_searches(self):
        """The archive's worker calls refresh(query, background=True) with the catalogue's query."""
        with tempfile.TemporaryDirectory() as tmp:
            http = FakeTmdb(GRACE["results"])
            details = details_for(tmp, http)
            match = details.refresh(dt.Query("Detektiv Grace: Hodinky smrti", 2024, "movie"), background=True)
            self.assertEqual((match["kind"], match["tmdbId"]), ("tv", 90001))
            self.assertEqual(http.searches(), GRACE["searches"])


PRE_T44_SCHEMA = """
CREATE TABLE entries (key TEXT PRIMARY KEY, kind TEXT NOT NULL, year INTEGER, title TEXT NOT NULL,
  matched INTEGER NOT NULL, match_kind TEXT, tmdb_id INTEGER, rating_value REAL, rating_votes INTEGER,
  genre_ids TEXT NOT NULL DEFAULT '[]', genre_names TEXT NOT NULL DEFAULT '[]', match_year INTEGER, poster TEXT,
  fetched_at REAL NOT NULL);
CREATE INDEX entries_fetched ON entries (fetched_at);
"""


class RulesVersionTest(unittest.TestCase):
    """A no match stored under the matching rules before T44 is looked up again at once (page and worker), then
    stored with the current version and not asked again; a stored match stays."""

    GRACE_KEY = dt.Query("Detektiv Grace: Hodinky smrti", 2024, "movie").key
    TITANS_KEY = dt.Query("Hněv Titánů", 2012, "movie").key

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        with sqlite3.connect(self.dir / tmdbstore.FILE) as db:  # a store written before T44
            db.executescript(PRE_T44_SCHEMA)
            db.execute("INSERT INTO entries (key, kind, year, title, matched, fetched_at) VALUES (?, ?, ?, ?, 0, ?)",
                       (self.GRACE_KEY, "movie", 2024, "Detektiv Grace: Hodinky smrti", NOW - 86400))
            db.execute("INSERT INTO entries (key, kind, year, title, matched, match_kind, tmdb_id, fetched_at) "
                       "VALUES (?, ?, ?, ?, 1, 'movie', 4401, ?)", (self.TITANS_KEY, "movie", 2012, "Hněv Titánů",
                                                                     NOW - 86400))
        db.close()

    def test_migration_keeps_rows_as_version_one(self):
        store = tmdbstore.TmdbStore(self.dir, now=lambda: NOW, log=lambda m: None)
        with sqlite3.connect(self.dir / tmdbstore.FILE) as db:
            self.assertEqual(db.execute("SELECT key, rules FROM entries ORDER BY key").fetchall(),
                             sorted([(self.GRACE_KEY, 1), (self.TITANS_KEY, 1)]))
        db.close()
        grace, titans = store.get(self.GRACE_KEY), store.get(self.TITANS_KEY)
        self.assertTrue(grace.outdated and store.due(self.GRACE_KEY))  # an old no match: ask again
        self.assertFalse(titans.outdated or store.due(self.TITANS_KEY))  # an old match stays
        tmdbstore.TmdbStore(self.dir, now=lambda: NOW, log=lambda m: None)  # opening again changes nothing
        self.assertEqual(len(store), 2)

    def test_page_lookup_asks_an_old_no_match_once(self):
        http = FakeTmdb(GRACE["results"])
        details = details_for(self.dir, http)
        answer = details.lookup(programme(GRACE))
        self.assertEqual((answer["match"]["kind"], answer["match"]["tmdbId"]), ("tv", 90001))
        self.assertEqual(http.searches(), GRACE["searches"])
        entry = details.store.get(self.GRACE_KEY)
        self.assertEqual((entry.matched, entry.rules), (True, tmdbstore.RULES))
        sent = len(http.requests)
        details.lookup(programme(GRACE))
        self.assertEqual(len(http.requests), sent)  # stored now

    def test_still_nothing_is_stored_with_the_new_version(self):
        http = FakeTmdb({})
        details = details_for(self.dir, http)
        self.assertIsNone(details.lookup(programme(GRACE))["match"])
        self.assertEqual(len(http.searches()), dt.SEARCHES_MAX)
        entry = details.store.get(self.GRACE_KEY)
        self.assertEqual((entry.matched, entry.rules, entry.outdated), (False, tmdbstore.RULES, False))
        details.lookup(programme(GRACE))
        self.assertEqual(len(http.searches()), dt.SEARCHES_MAX)  # not asked again

    def test_worker_asks_an_old_no_match_once(self):
        http = FakeTmdb(GRACE["results"])
        details = details_for(self.dir, http)
        grace = dt.Query("Detektiv Grace: Hodinky smrti", 2024, "movie")
        titans = dt.Query("Hněv Titánů", 2012, "movie")
        worker = tmdbstore.TmdbWorker(details.store, lambda: [grace, titans],
                                      lambda q: details.refresh(q, background=True), dt.TmdbBusy, dt.TmdbError,
                                      log=lambda m: None)
        self.assertEqual(worker.run_once(), (1, False))  # Grace only; the old Titans match is not due
        self.assertEqual(http.searches(), GRACE["searches"])
        self.assertEqual(worker.run_once(), (0, False))
        self.assertEqual(details.store.get(grace.key).rules, tmdbstore.RULES)


class CtOriginalTitleTest(unittest.TestCase):
    """The gateway's ČT path: originalTitle is asked for and used only while CT_ORIGINAL_TITLE is on."""

    CASE = next(c for c in CASES if c["name"].startswith("ČT original title of a foreign programme"))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.dir / "hls", state_dir=self.dir),
                     tmdb=TmdbConfig(key=TMDB_V3))
        hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        self.gw = DvbGateway(cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=lambda m: None)
        self.http = FakeTmdb({**self.CASE["results"], "movie|Neviditelný host": []})
        self.gw.details = details_for(self.dir, self.http)
        self.calls = []
        self.gw.fetch_schedules = self.fetch_schedules

    def fetch_schedules(self, slugs, day, **kwargs):
        self.calls.append(kwargs)
        start = api._parse_time("2026-10-17T18:00:00Z")
        p = self.CASE["programme"]
        original = p["originalTitle"] if kwargs.get("original_title") else ""
        return {"ct1": [api.Programme("6236190", p["title"], start, start, True, False, description=p["description"],
                                      original_title=original)]}

    def lookup(self):
        resp = self.gw.handle("/dvb/details", {"source": "ct", "channel": "ct1", "date": "2026-10-17",
                                               "idec": "6236190"}, APP)
        return resp.status, json.loads(resp.body)

    def test_off_by_default(self):
        self.assertFalse(gateway_module.CT_ORIGINAL_TITLE)  # until checked on ČT's real schedule
        status, body = self.lookup()
        self.assertEqual((status, body["match"]), (200, None))
        self.assertEqual(self.calls, [{}])  # the plain schedule query, no originalTitle
        self.assertEqual(self.http.searches(), [["movie", "Neviditelný host"], ["tv", "Neviditelný host"]])

    def test_on_searches_the_original_first(self):
        with mock.patch.object(gateway_module, "CT_ORIGINAL_TITLE", True):
            status, body = self.lookup()
        self.assertEqual((status, body["match"]["tmdbId"]), (200, 90070))
        self.assertEqual(self.calls, [{"original_title": True}])
        self.assertEqual(self.http.searches(), self.CASE["searches"])


class CtScheduleOriginalTitleTest(unittest.TestCase):
    def test_query_has_the_field_only_when_asked(self):
        self.assertNotIn("originalTitle", api.SCHEDULE_QUERY)  # the add-on and /dvb/ct/schedule stay unchanged
        self.assertNotIn("originalTitle", api.schedule_request(["ct1"], date(2026, 11, 1))["query"])
        self.assertIn("originalTitle", api.schedule_request(["ct1"], date(2026, 11, 1), original_title=True)["query"])
        self.assertEqual(api.SCHEDULE_QUERY_ORIGINAL.replace(" originalTitle", ""), api.SCHEDULE_QUERY)

    def test_parsed_defensively(self):
        base = {"idec": "1", "title": "Host", "start": "2026-11-01T09:00:00.000Z", "end": "2026-11-01T10:00:00.000Z"}
        payload = {"data": {"TVProgramDailyChannelsPlanV2": [{"channel": "ct2", "program": [
            dict(base, originalTitle=" The Guest "), dict(base, idec="2", originalTitle=None),
            dict(base, idec="3", originalTitle=7), dict(base, idec="4", originalTitle={"cs": "x"}), dict(base, idec="5"),
        ]}]}}
        body = io.BytesIO(json.dumps(payload).encode())
        response = mock.MagicMock()
        response.__enter__.return_value = body
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            result = api.fetch_schedules(["ct2"], date(2026, 11, 1), original_title=True)
        self.assertIn("originalTitle", json.loads(urlopen.call_args.args[0].data)["query"])
        self.assertEqual([p.original_title for p in result["ct2"]], ["The Guest", "", "", "", ""])


if __name__ == "__main__":
    unittest.main()
