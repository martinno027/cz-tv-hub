"""Programme details from TMDb (spec/dvb-gateway.md "Programme details"): title cleaning, the year, the kind,
strict matching, the TMDb client and its limits, the cache, the poster proxy and the /dvb/details routes.
TMDb is a fake ``urlopen``; nothing here touches the network."""
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path

from cztvhub import api
from cztvhub.config import AdsConfig, Config, ConfigError, DvbConfig, TmdbConfig, parse_config
from cztvhub.dvb import details as dt
from cztvhub.dvb import tmdbstore
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from cztvhub.dvb.tvheadend import EpgEvent
from dvb_fixtures import CT1, NOW, FakeClient

KEY = "app-key-" * 5
WORKER_KEY = "worker-key-" * 4
TMDB_V3 = "0123456789abcdef0123456789abcdef"
TMDB_V4 = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ0ZXN0In0.c2lnbmF0dXJlLXNlY3JldA"
APP = {"Authorization": "Bearer " + KEY}
WORKER = {"Authorization": "Bearer " + WORKER_KEY}
TEMPLATE = Path(__file__).resolve().parents[1] / "deploy" / "cztvhub.conf.example"
SHARED = Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "dvb"
SHARED_FILES = {"match": "dvb_details_match.json", "nomatch": "dvb_details_nomatch.json"}

# Synthetic TMDb answers in TMDb's shape (no real TMDb data).
TITANS = {"id": 4401, "title": "Hněv Titánů", "original_title": "Wrath of the Titans", "release_date": "2012-03-27"}
TITANS_DETAILS = {**TITANS, "overview": "Smyšlený popis filmu.", "genres": [{"id": 28, "name": "Akční"}],
                  "runtime": 99, "vote_average": 5.8, "vote_count": 7311, "poster_path": "/titanyPoster01.jpg"}
PAWN = {"id": 5501, "name": "Mistři zastavárny", "original_name": "Pawn Stars", "first_air_date": "2009-07-19"}
PAWN_DETAILS = {**PAWN, "overview": "", "genres": [{"id": 99, "name": "Dokumentární"}], "episode_run_time": [22],
                "vote_average": 6.9, "vote_count": 19, "poster_path": "/pawnPoster02.png"}


class FakeHttp:
    """Stands in for urllib's opener: answers by URL path, records every request."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        self.error = None

    def add(self, path, payload, status=200, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.routes[path] = (status, body, content_type)

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        url = urllib.parse.urlsplit(request.full_url)
        found = self.routes.get(url.path)
        if found is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        status, body, content_type = found
        return FakeHttpResponse(status, body, content_type)

    def paths(self):
        return [urllib.parse.urlsplit(r.full_url).path for r in self.requests]

    def query(self, index):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.requests[index].full_url).query))


class FakeHttpResponse:
    def __init__(self, status, body, content_type):
        self.status, self.headers = status, {"Content-Type": content_type}
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def details_for(state_dir, http, key=TMDB_V3, now=lambda: NOW, logs=None, per_minute=dt.PER_MINUTE, clock=None):
    limiter = dt.Limiter(per_minute=per_minute, wait=0.05, clock=clock or Clock())
    client = dt.TmdbClient(key, limiter=limiter, urlopen=http)
    log = logs.append if logs is not None else (lambda m: None)
    return dt.Details(key, state_dir, now=now, log=log, client=client)


def put_poster(details, file):
    """A stored match with this poster file, as a lookup would leave it."""
    details.store.put(tmdbstore.store_key("movie", None, file), "movie", None, file,
                      {"kind": "movie", "tmdbId": 1, "rating": None, "genreIds": [], "genres": [], "year": None,
                       "poster": file})


def shared_answers(state_dir):
    """The two shared samples (spec/fixtures/dvb/dvb_details_*.json) as the gateway sends them, TMDb mocked:
    a film with a rating, a poster and an original title, and one whose year is 2 off (no match)."""
    http = FakeHttp()
    http.add("/3/search/movie", {"results": [TITANS]})
    http.add("/3/movie/4401", TITANS_DETAILS)
    http.add("/3/search/tv", {"results": []})
    cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=Path(state_dir) / "hls", state_dir=Path(state_dir)),
                 tmdb=TmdbConfig(key=TMDB_V3))
    hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
    try:
        gw = DvbGateway(cfg, client=FakeClient(), hls=hls, now=lambda: NOW, log=lambda m: None)
        gw.details = details_for(state_dir, http)
        gw.history.merge([EpgEvent("5001", CT1, "Film: Hněv Titánů", "", "Americký film (USA, 2012)", [0x10],
                                   NOW - 7200, NOW - 3600),
                          EpgEvent("5002", CT1, "Film: Hněv Titánů", "", "Americký film (USA, 2010)", [0x10],
                                   NOW - 3600, NOW)], NOW, (0, 0))
        out = {}
        for name, event_id in (("match", "5001"), ("nomatch", "5002")):
            resp = gw.handle("/dvb/details", {"source": "dvb", "id": event_id}, APP)
            out[name] = {"status": resp.status, "body": json.loads(resp.body)}
        return out
    finally:
        hls.shutdown()


def write_shared_fixtures():
    """Regenerate the shared samples: PYTHONPATH=src:tests python3 -c 'import test_dvb_details as t;
    t.write_shared_fixtures()' (run from server/)."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, sample in shared_answers(tmp).items():
            text = json.dumps(sample, ensure_ascii=False, indent=1) + "\n"
            (SHARED / SHARED_FILES[name]).write_text(text, encoding="utf-8")


class SharedFixtureTest(unittest.TestCase):
    def test_gateway_answers_equal_the_shared_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = shared_answers(tmp)
        for name, file in SHARED_FILES.items():
            with self.subTest(file=file):
                self.assertEqual(answers[name], json.loads((SHARED / file).read_text(encoding="utf-8")))
        match = answers["match"]["body"]["match"]
        self.assertTrue(match["rating"] and match["poster"] and match["originalTitle"] != match["title"])
        self.assertIsNone(answers["nomatch"]["body"]["match"])
        self.assertNotEqual(answers["match"], answers["nomatch"])


class CleanTitleTest(unittest.TestCase):
    CASES = [  # EPG title, cleaned for a film, cleaned for a series, the guessed kind
        ("Mistři zastavárny VI (14)", "Mistři zastavárny VI", "Mistři zastavárny", "tv"),
        ("Film: Hněv Titánů", "Hněv Titánů", "Hněv Titánů", "movie"),
        ("Kobra 11 IV (13)", "Kobra 11 IV", "Kobra 11", "tv"),
        ("Ordinace v růžové zahradě 2", "Ordinace v růžové zahradě 2", "Ordinace v růžové zahradě 2", "movie"),
        ("Pražská 5 - díl 3", "Pražská 5", "Pražská 5", "tv"),
        ("Rocky II", "Rocky II", "Rocky", "movie"),
        ("Columbo (R)", "Columbo", "Columbo", "movie"),
        ("Návrat domů HD", "Návrat domů", "Návrat domů", "movie"),
        ("Hercule Poirot X (3/12) HD", "Hercule Poirot X", "Hercule Poirot", "tv"),
        ("  Film:  Pelíšky ", "Pelíšky", "Pelíšky", "movie"),
        ("Vinnetou", "Vinnetou", "Vinnetou", "movie"),  # a word ending in roman letters stays
    ]

    def test_cases(self):
        for title, movie, tv, kind in self.CASES:
            with self.subTest(title=title):
                self.assertEqual(dt.clean_title(title, "movie"), movie)
                self.assertEqual(dt.clean_title(title, "tv"), tv)
                self.assertEqual(dt.guess_kind(dt.Programme(title)), kind)

    def test_kind_from_episode_line_and_genre(self):
        self.assertEqual(dt.guess_kind(dt.Programme("Ordinace v růžové zahradě 2", episode="Návrat")), "tv")
        self.assertEqual(dt.guess_kind(dt.Programme("Ordinace v růžové zahradě 2", genre="Seriály")), "tv")
        self.assertEqual(dt.guess_kind(dt.Programme("Ulice", genres=(0x15,))), "tv")  # soap / melodrama
        self.assertEqual(dt.guess_kind(dt.Programme("Pelíšky", genres=(0x10, 0x14), genre="Film / drama")), "movie")

    def test_year_from_description(self):
        cases = [
            ("Americký akční film (USA, 2012). Hrají: …", 2012),
            ("Režie: J. Hřebejk. Hrají: M. Donutil, J. Kodet (Česko, 1999)", 1999),
            ("Smyšlený popis … 2010)", 2010),
            ("Natočeno v roce 2010 v Praze.", None),  # not stated the EPG way
            ("Sci-fi (USA, 2099)", None),  # in the future: not a production year
            ("", None),
        ]
        for text, year in cases:
            with self.subTest(text=text):
                self.assertEqual(dt.description_year(text, 2026), year)

    def test_normalise(self):
        self.assertEqual(dt.normalise("Hněv Titánů!"), "hnevtitanu")
        self.assertEqual(dt.normalise("HNEV  titanu"), dt.normalise("Hněv Titánů"))
        self.assertNotEqual(dt.normalise("Kobra 11"), dt.normalise("Kobra 1"))
        self.assertEqual(dt.normalise(None), "")

    def test_query(self):
        q = dt.make_query(dt.Programme("Film: Hněv Titánů", description="(USA, 2012)"), 2026)
        self.assertEqual((q.title, q.year, q.kind), ("Hněv Titánů", 2012, "movie"))
        self.assertIsNone(dt.make_query(dt.Programme(" (14) "), 2026))  # nothing left to ask about
        # the key ignores the episode number, so every episode shares one lookup
        a = dt.make_query(dt.Programme("Mistři zastavárny VI (14)"), 2026)
        b = dt.make_query(dt.Programme("Mistři zastavárny VI (15)"), 2026)
        self.assertEqual(a.key, b.key)


class PickTest(unittest.TestCase):
    def test_match(self):
        self.assertEqual(dt.pick([TITANS], "Hněv Titánů", 2012, "movie"), TITANS)
        self.assertEqual(dt.pick([TITANS], "hnev titanu", None, "movie"), TITANS)
        self.assertEqual(dt.pick([TITANS], "Wrath of the Titans", 2012, "movie"), TITANS)  # original title
        self.assertEqual(dt.pick([PAWN], "Pawn Stars", None, "tv"), PAWN)

    def test_wrong_title_is_no_match(self):
        self.assertIsNone(dt.pick([TITANS], "Souboj Titánů", 2012, "movie"))
        self.assertIsNone(dt.pick([TITANS], "Hněv Titánů 2", 2012, "movie"))

    def test_year_within_one(self):
        self.assertEqual(dt.pick([TITANS], "Hněv Titánů", 2011, "movie"), TITANS)
        self.assertEqual(dt.pick([TITANS], "Hněv Titánů", 2013, "movie"), TITANS)
        self.assertIsNone(dt.pick([TITANS], "Hněv Titánů", 2010, "movie"))
        self.assertIsNone(dt.pick([TITANS], "Hněv Titánů", 2014, "movie"))
        self.assertIsNone(dt.pick([{**TITANS, "release_date": ""}], "Hněv Titánů", 2012, "movie"))

    def test_ambiguous_is_no_match(self):
        remake = {**TITANS, "id": 4402, "release_date": "1981-06-12"}
        self.assertIsNone(dt.pick([TITANS, remake], "Hněv Titánů", None, "movie"))  # which one?
        self.assertEqual(dt.pick([TITANS, remake], "Hněv Titánů", 2012, "movie"), TITANS)  # the year decides
        near = {**TITANS, "id": 4403, "release_date": "2013-01-01"}
        self.assertEqual(dt.pick([near, TITANS], "Hněv Titánů", 2012, "movie"), TITANS)  # the closer year
        twin = {**TITANS, "id": 4404}
        self.assertIsNone(dt.pick([TITANS, twin], "Hněv Titánů", 2012, "movie"))

    def test_kind_fields_and_odd_results(self):
        self.assertIsNone(dt.pick([TITANS], "Hněv Titánů", 2012, "tv"))  # a tv result has "name"
        self.assertIsNone(dt.pick([{**TITANS, "id": "4401"}, None, "x"], "Hněv Titánů", 2012, "movie"))
        self.assertIsNone(dt.pick(None, "Hněv Titánů", 2012, "movie"))


class ToMatchTest(unittest.TestCase):
    def test_movie(self):
        self.assertEqual(dt.to_match("movie", TITANS_DETAILS), {
            "kind": "movie", "title": "Hněv Titánů", "originalTitle": "Wrath of the Titans", "year": 2012,
            "overview": "Smyšlený popis filmu.", "genres": ["Akční"], "runtimeMinutes": 99,
            "rating": {"value": 5.8, "votes": 7311}, "poster": "/dvb/details/image/w342/titanyPoster01.jpg",
            "tmdbId": 4401})

    def test_tv_and_rating_threshold(self):
        match = dt.to_match("tv", PAWN_DETAILS)
        self.assertIsNone(match["rating"])  # 19 votes
        self.assertNotIn("overview", match)
        self.assertEqual((match["runtimeMinutes"], match["year"], match["poster"]),
                         (22, 2009, "/dvb/details/image/w342/pawnPoster02.png"))
        self.assertEqual(dt.to_match("tv", {**PAWN_DETAILS, "vote_count": 20})["rating"],
                         {"value": 6.9, "votes": 20})

    def test_odd_fields_stay_out(self):
        for path in ("../etc/passwd.jpg", "https://evil.example/x.jpg", "/a/b.jpg", "/x.gif", 7, None):
            self.assertIsNone(dt.to_match("movie", {**TITANS_DETAILS, "poster_path": path})["poster"], path)
        odd = dt.to_match("movie", {"id": 1, "title": 5, "runtime": "99", "genres": "Akční", "vote_count": True,
                                    "vote_average": 9})
        self.assertEqual((odd["title"], odd["genres"], odd["rating"], odd["year"]), ("", [], None, None))
        self.assertNotIn("runtimeMinutes", odd)
        self.assertIsNone(dt.to_match("movie", {"title": "bez id"}))


class ClientTest(unittest.TestCase):
    def test_v3_key_as_parameter(self):
        http = FakeHttp()
        http.add("/3/search/movie", {"results": []})
        dt.TmdbClient(TMDB_V3, urlopen=http).get("search/movie", {"query": "Pelíšky", "language": "cs-CZ"})
        self.assertEqual(http.query(0), {"query": "Pelíšky", "language": "cs-CZ", "api_key": TMDB_V3})
        self.assertNotIn("Authorization", http.requests[0].headers)
        self.assertTrue(http.requests[0].full_url.startswith("https://api.themoviedb.org/3/search/movie?"))

    def test_v4_token_as_bearer(self):
        http = FakeHttp()
        http.add("/3/movie/4401", TITANS_DETAILS)
        dt.TmdbClient(TMDB_V4, urlopen=http).get("movie/4401", {"language": "cs-CZ"})
        self.assertEqual(http.requests[0].get_header("Authorization"), "Bearer " + TMDB_V4)
        self.assertNotIn("api_key", http.query(0))
        self.assertNotIn(TMDB_V4, http.requests[0].full_url)

    def test_key_never_in_errors(self):
        for key in (TMDB_V3, TMDB_V4):
            errors = [
                urllib.error.HTTPError(f"https://api.themoviedb.org/3/x?api_key={key}", 401, "Unauthorized", {},
                                       None),
                urllib.error.URLError(f"cannot reach host for api_key={key}"),
                OSError(f"reset while sending {key}"),
            ]
            for exc in errors:
                http = FakeHttp()
                http.error = exc
                with self.assertRaises(dt.TmdbError) as ctx:
                    dt.TmdbClient(key, urlopen=http).get("search/movie", {"query": "x"})
                self.assertNotIn(key, str(ctx.exception))
                self.assertIsNone(ctx.exception.__cause__)  # no chained urllib error carrying the URL
                self.assertTrue(ctx.exception.__suppress_context__)
        http = FakeHttp()
        http.error = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        with self.assertRaises(dt.TmdbError) as ctx:
            dt.TmdbClient(TMDB_V3, urlopen=http).get("search/movie", {})
        self.assertIn("key was refused", str(ctx.exception))

    def test_bad_answers(self):
        for body in (b"not json", b"[1, 2]", b"\xff"):
            http = FakeHttp()
            http.add("/3/search/movie", body)
            with self.assertRaises(dt.TmdbError):
                dt.TmdbClient(TMDB_V3, urlopen=http).get("search/movie", {})

    def test_redirects_are_not_followed(self):
        handler = dt._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))
        self.assertTrue(any(isinstance(h, dt._NoRedirect) for h in dt._OPENER.handlers))

    def test_image_has_fixed_host_size_and_no_key(self):
        http = FakeHttp()
        http.add("/t/p/w342/titanyPoster01.jpg", b"JPEG", content_type="image/jpeg")
        self.assertEqual(dt.TmdbClient(TMDB_V3, urlopen=http).image("titanyPoster01.jpg"), (b"JPEG", "image/jpeg"))
        self.assertEqual(http.requests[0].full_url, "https://image.tmdb.org/t/p/w342/titanyPoster01.jpg")
        self.assertNotIn(TMDB_V3, http.requests[0].full_url)
        self.assertNotIn("Authorization", http.requests[0].headers)
        for file in ("../x.jpg", "https://evil.example/x.jpg", "x.gif", "a/b.jpg"):
            with self.assertRaises(dt.TmdbError):
                dt.TmdbClient(TMDB_V3, urlopen=http).image(file)
        self.assertEqual(len(http.requests), 1)


class LimiterTest(unittest.TestCase):
    def test_thirty_per_minute(self):
        clock = Clock()
        limiter = dt.Limiter(clock=clock)
        for _ in range(30):
            with limiter.slot():
                pass
        with self.assertRaises(dt.TmdbBusy):
            with limiter.slot():
                pass
        clock.t += 59.9
        with self.assertRaises(dt.TmdbBusy):
            with limiter.slot():
                pass
        clock.t += 0.2  # the first ones are a minute old
        with limiter.slot():
            pass

    def test_one_at_a_time(self):
        limiter = dt.Limiter(wait=0.05)
        inside, release = threading.Event(), threading.Event()

        def hold():
            with limiter.slot():
                inside.set()
                release.wait(5)

        worker = threading.Thread(target=hold)
        worker.start()
        self.assertTrue(inside.wait(5))
        try:
            with self.assertRaises(dt.TmdbBusy):
                with limiter.slot():
                    pass
        finally:
            release.set()
            worker.join()
        with limiter.slot():  # free again
            pass

    def test_refused_request_is_not_sent(self):
        http = FakeHttp()
        http.add("/3/search/movie", {"results": []})
        client = dt.TmdbClient(TMDB_V3, limiter=dt.Limiter(per_minute=2, clock=Clock()), urlopen=http)
        client.get("search/movie", {})
        client.get("search/movie", {})
        with self.assertRaises(dt.TmdbBusy):
            client.get("search/movie", {})
        self.assertEqual(len(http.requests), 2)


class LookupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.http = FakeHttp()
        self.logs = []
        self.details = details_for(self.dir, self.http, logs=self.logs)

    def test_movie_match(self):
        self.http.add("/3/search/movie", {"results": [TITANS]})
        self.http.add("/3/movie/4401", TITANS_DETAILS)
        answer = self.details.lookup(dt.Programme("Film: Hněv Titánů", description="Akční (USA, 2012)"))
        self.assertEqual(answer["match"]["tmdbId"], 4401)
        self.assertEqual(answer["attribution"], dt.ATTRIBUTION)
        self.assertNotIn("error", answer)
        self.assertEqual(self.http.paths(), ["/3/search/movie", "/3/movie/4401"])
        # only the cleaned title (and the fixed language) went to TMDb, never the description
        self.assertEqual(self.http.query(0), {"query": "Hněv Titánů", "language": "cs-CZ", "include_adult": "false",
                                              "api_key": TMDB_V3})
        self.assertEqual(self.details.lookup(dt.Programme("Film: Hněv Titánů", description="(USA, 2012)")), answer)
        self.assertEqual(len(self.http.requests), 2)  # cached

    def test_series_first_then_film(self):
        self.http.add("/3/search/tv", {"results": [PAWN]})
        self.http.add("/3/tv/5501", PAWN_DETAILS)
        answer = self.details.lookup(dt.Programme("Mistři zastavárny VI (14)"))
        self.assertEqual((answer["match"]["kind"], answer["match"]["rating"]), ("tv", None))
        self.assertEqual(self.http.paths(), ["/3/search/tv", "/3/tv/5501"])
        self.assertEqual(self.http.query(0)["query"], "Mistři zastavárny")

    def test_fallback_to_the_other_kind(self):
        self.http.add("/3/search/movie", {"results": []})
        self.http.add("/3/search/tv", {"results": [PAWN]})
        self.http.add("/3/tv/5501", PAWN_DETAILS)
        answer = self.details.lookup(dt.Programme("Mistři zastavárny"))
        self.assertEqual(answer["match"]["tmdbId"], 5501)
        self.assertEqual(self.http.paths(), ["/3/search/movie", "/3/search/tv", "/3/tv/5501"])

    def test_no_match_is_cached(self):
        self.http.add("/3/search/movie", {"results": [TITANS]})
        self.http.add("/3/search/tv", {"results": []})
        programme = dt.Programme("Hněv Titánů", description="(USA, 2010)")  # 2 years off
        self.assertEqual(self.details.lookup(programme), {"match": None, "attribution": dt.ATTRIBUTION})
        self.assertEqual(self.details.lookup(programme)["match"], None)
        self.assertEqual(self.http.paths(), ["/3/search/movie", "/3/search/tv"])

    def test_error_is_not_cached(self):
        self.http.error = urllib.error.HTTPError("u", 503, "Unavailable", {}, None)
        answer = self.details.lookup(dt.Programme("Pelíšky"))
        self.assertEqual(answer, {"match": None, "error": dt.TmdbError.user_message, "attribution": dt.ATTRIBUTION})
        self.http.error = None
        self.http.add("/3/search/movie", {"results": []})
        self.http.add("/3/search/tv", {"results": []})
        self.assertNotIn("error", self.details.lookup(dt.Programme("Pelíšky")))
        self.assertEqual(len(self.http.requests), 3)  # asked again after the error
        self.assertTrue(any("HTTP 503" in line for line in self.logs))

    def test_over_the_limit(self):
        details = details_for(self.dir, self.http, per_minute=1)
        self.http.add("/3/search/movie", {"results": []})
        answer = details.lookup(dt.Programme("Pelíšky"))  # the movie search fits, the tv one does not
        self.assertEqual(answer["error"], dt.TmdbBusy.user_message)
        self.assertEqual(len(details.store), 0)

    def test_nothing_to_ask(self):
        self.assertEqual(self.details.lookup(dt.Programme("(14)"))["match"], None)
        self.assertEqual(self.http.requests, [])


class ConfigTest(unittest.TestCase):
    def test_key(self):
        cfg = parse_config("[tmdb]\nkey = " + TMDB_V3 + "\n")
        self.assertTrue(cfg.tmdb.enabled)
        self.assertEqual(cfg.tmdb.key, TMDB_V3)
        self.assertNotIn(TMDB_V3, repr(cfg))
        self.assertFalse(parse_config("[tmdb]\nkey =\n").tmdb.enabled)
        self.assertFalse(parse_config("").tmdb.enabled)

    def test_template_leaves_it_off(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        for placeholder, value in (("@KEY@", KEY), ("@DVB_ENABLED@", "true"), ("@TVH_PASSWORD@", "x"),
                                   ("@ADMIN_PASSWORD@", "")):
            text = text.replace(placeholder, value)
        cfg = parse_config(text)
        self.assertFalse(cfg.tmdb.enabled)
        self.assertIn("[tmdb]", text)


class GatewayDetailsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.logs = []
        self.http = FakeHttp()
        self.client = FakeClient()
        self.schedule_calls = []
        self.ct_error = None
        self.gw = self.make(TMDB_V3)

    def make(self, tmdb_key):
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.dir / "hls", state_dir=self.dir),
                     ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=self.dir / "breaks"),
                     tmdb=TmdbConfig(key=tmdb_key))
        hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.addCleanup(hls.shutdown)
        gw = DvbGateway(cfg, client=self.client, hls=hls, now=lambda: NOW, log=self.logs.append)
        if gw.details is not None:
            gw.details = details_for(self.dir, self.http, key=tmdb_key, logs=self.logs)
        gw.fetch_schedules = self.fetch_schedules
        return gw

    def fetch_schedules(self, slugs, day):
        self.schedule_calls.append((tuple(slugs), day))
        if self.ct_error:
            raise self.ct_error
        start = api._parse_time("2026-10-17T18:00:00Z")
        return {"ct1": [api.Programme("6236181", "Film: Hněv Titánů", start, start, True, False,
                                      description="Akční film (USA, 2012)"),
                        api.Programme("6236182", "Ordinace v růžové zahradě 2", start, start, True, False,
                                      episode="Návrat", genre="Seriály")]}

    def get(self, path, query=None, headers=APP):
        resp = self.gw.handle(path, query or {}, headers)
        body = json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body
        return resp, body

    def remember(self, event_id, title, description="", channel=CT1):
        event = EpgEvent(event_id, channel, title, "", description, [0x10], NOW - 7200, NOW - 3600)
        self.gw.history.merge([event], NOW, (0, 0))

    def titans(self):
        self.http.add("/3/search/movie", {"results": [TITANS]})
        self.http.add("/3/movie/4401", TITANS_DETAILS)
        self.http.add("/3/search/tv", {"results": []})

    # -- off -----------------------------------------------------------------------------------
    def test_off_without_key(self):
        gw = self.make("")
        self.assertIsNone(gw.details)
        for path, query in (("/dvb/details", {"source": "dvb", "id": "4711"}),
                            ("/dvb/details/image/w342/titanyPoster01.jpg", {})):
            resp = gw.handle(path, query, APP)
            self.assertEqual((resp.status, json.loads(resp.body)), (404, {"error": "Podrobnosti nejsou zapnuté"}))
        self.assertEqual(self.http.requests, [])

    def test_removing_the_key_deletes_the_cache(self):
        self.titans()
        self.remember("5001", "Film: Hněv Titánů", "(USA, 2012)")
        self.assertEqual(self.get("/dvb/details", {"source": "dvb", "id": "5001"})[0].status, 200)
        store = self.dir / tmdbstore.FILE
        self.assertTrue(store.exists())
        self.make(TMDB_V3)
        self.assertTrue(store.exists())  # a restart with the key keeps it
        self.make("")
        self.assertFalse(store.exists())
        self.assertIn("dvb: tmdb: no [tmdb] key, deleted tmdb.sqlite", self.logs)

    # -- access --------------------------------------------------------------------------------
    def test_needs_the_app_key(self):
        self.remember("5001", "Film: Hněv Titánů")
        for path, query in (("/dvb/details", {"source": "dvb", "id": "5001"}),
                            ("/dvb/details", {"source": "ct", "channel": "ct1", "date": "2026-10-17",
                                              "idec": "6236181"}),
                            ("/dvb/details/image/w342/titanyPoster01.jpg", {})):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, query, headers={})[0].status, 401)
                self.assertEqual(self.get(path, query, headers=WORKER)[0].status, 403)
        self.assertEqual(self.http.requests, [])
        self.assertEqual(self.schedule_calls, [])
        self.assertEqual(self.gw.handle("/dvb/details", {"source": "dvb", "id": "5001"}, APP, "POST").status, 405)

    def test_cookie_key(self):
        self.titans()
        self.remember("5001", "Film: Hněv Titánů", "(USA, 2012)")
        resp, _ = self.get("/dvb/details", {"source": "dvb", "id": "5001"}, headers={"Cookie": "cztvhub_key=" + KEY})
        self.assertEqual(resp.status, 200)

    # -- source=dvb ----------------------------------------------------------------------------
    def test_dvb_from_history(self):
        self.titans()
        self.remember("5001", "Film: Hněv Titánů", "Americký film (USA, 2012)")
        resp, body = self.get("/dvb/details", {"source": "dvb", "id": "5001"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(body["match"]["poster"], "/dvb/details/image/w342/titanyPoster01.jpg")
        self.assertEqual(body["match"]["rating"], {"value": 5.8, "votes": 7311})
        self.assertEqual(body["attribution"], dt.ATTRIBUTION)

    def test_dvb_from_tvheadend_and_dvr(self):
        self.http.add("/3/search/movie", {"results": []})
        self.http.add("/3/search/tv", {"results": []})
        resp, body = self.get("/dvb/details", {"source": "dvb", "id": "4713"})  # TVHeadend's EPG: "Film"
        self.assertEqual((resp.status, body["match"]), (200, None))
        self.assertEqual(self.http.query(0)["query"], "Film")
        resp, _ = self.get("/dvb/details", {"source": "dvb", "id": "dvr:c1c2c3c4c5c6c7c8c9c0b1b2b3b4b5b6"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.http.query(2)["query"], "Včerejší film")

    def test_dvb_unknown_bad_and_hidden(self):
        self.assertEqual(self.get("/dvb/details", {"source": "dvb", "id": "777"})[0].status, 404)
        for bad in ("", "a b", "x" * 129, "../4711"):
            self.assertEqual(self.get("/dvb/details", {"source": "dvb", "id": bad})[0].status, 400, bad)
        # a programme of a channel hidden on /admin (disabled in TVHeadend) is unknown
        self.remember("5002", "Film: Hněv Titánů", channel="1234567890abcdef1234567890abcdef")
        self.assertEqual(self.get("/dvb/details", {"source": "dvb", "id": "5002"})[0].status, 404)
        self.assertEqual(self.http.requests, [])

    def test_free_text_is_never_looked_up(self):
        for query in ({"source": "dvb", "title": "Pelíšky"}, {"source": "dvb", "id": "Pelíšky"},
                      {"title": "Pelíšky"}, {"source": "tmdb", "query": "Pelíšky"}):
            self.assertIn(self.get("/dvb/details", query)[0].status, (400, 404), query)
        self.assertEqual(self.http.requests, [])

    def test_tvheadend_down(self):
        self.client.unreachable = True
        self.assertEqual(self.get("/dvb/details", {"source": "dvb", "id": "5001"})[0].status, 502)

    # -- source=ct -----------------------------------------------------------------------------
    def test_ct(self):
        self.titans()
        query = {"source": "ct", "channel": "ct1", "date": "2026-10-17", "idec": "6236181"}
        resp, body = self.get("/dvb/details", query)
        self.assertEqual((resp.status, body["match"]["tmdbId"]), (200, 4401))
        self.assertEqual(self.schedule_calls, [(("ct1",), date(2026, 10, 17))])
        self.get("/dvb/details", {**query, "idec": "6236182"})
        self.assertEqual(len(self.schedule_calls), 1)  # the day is cached for 10 minutes
        # the episode line and "Seriály" make it a series: tv is searched first
        self.assertEqual(self.http.paths()[2], "/3/search/tv")

    def test_ct_schedule_expires(self):
        self.titans()
        query = {"source": "ct", "channel": "ct1", "date": "2026-10-17", "idec": "6236181"}
        self.get("/dvb/details", query)
        self.gw._ct_schedules = {k: (v[0] - 601, v[1]) for k, v in self.gw._ct_schedules.items()}
        self.get("/dvb/details", query)
        self.assertEqual(len(self.schedule_calls), 2)

    def test_ct_errors(self):
        base = {"source": "ct", "channel": "ct1", "date": "2026-10-17", "idec": "6236181"}
        cases = [({"channel": "nova"}, 404), ({"channel": ""}, 404), ({"date": "17.10.2026"}, 400),
                 ({"date": "2026-10-10"}, 400), ({"date": "2026-10-21"}, 400), ({"idec": ""}, 400),
                 ({"idec": "x/y"}, 400), ({"idec": "999"}, 404)]
        for change, status in cases:
            with self.subTest(change=change):
                self.assertEqual(self.get("/dvb/details", {**base, **change})[0].status, status)
        for day in ("2026-10-11", "2026-10-20"):  # 7 days back, 2 ahead: asked (that idec is not in the day)
            self.assertEqual(self.get("/dvb/details", {**base, "date": day, "idec": "999"})[0].status, 404)
        self.gw._ct_schedules.clear()
        self.ct_error = api.StreamError("HTTP 500")
        resp, body = self.get("/dvb/details", base)
        self.assertEqual((resp.status, body), (502, {"error": "Program ČT se nepodařilo načíst"}))
        self.assertEqual(self.http.requests, [])

    def test_unknown_source(self):
        self.assertEqual(self.get("/dvb/details", {"source": "x"})[0].status, 400)
        self.assertEqual(self.get("/dvb/details", {})[0].status, 400)

    # -- TMDb trouble ------------------------------------------------------------------------
    def test_tmdb_error_is_200_without_match_and_key_stays_secret(self):
        self.remember("5001", "Film: Hněv Titánů")
        self.http.error = urllib.error.HTTPError(f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_V3}",
                                                 401, "Unauthorized", {}, None)
        resp, body = self.get("/dvb/details", {"source": "dvb", "id": "5001"})
        self.assertEqual((resp.status, body["match"], body["error"]), (200, None, dt.TmdbError.user_message))
        self.http.error = urllib.error.URLError(f"refused api_key={TMDB_V3}")
        self.get("/dvb/details", {"source": "dvb", "id": "5001"})
        self.http.error = None
        self.http.add("/t/p/w342/titanyPoster01.jpg", b"x", content_type="text/html")
        put_poster(self.gw.details, "titanyPoster01.jpg")
        self.get("/dvb/details/image/w342/titanyPoster01.jpg")
        self.assertTrue(any("key was refused" in line for line in self.logs))
        self.assertGreaterEqual(len([line for line in self.logs if "details" in line]), 3)
        for line in self.logs:
            self.assertNotIn(TMDB_V3, line)
        self.assertNotIn(TMDB_V3.encode(), resp.body)
        self.assertEqual(len(self.gw.details.store), 1)  # only the poster entry put by hand

    # -- posters -------------------------------------------------------------------------------
    def test_poster_only_from_own_lookups(self):
        self.titans()
        self.http.add("/t/p/w342/titanyPoster01.jpg", b"\xff\xd8JPEG", content_type="image/jpeg")
        self.http.add("/t/p/w342/other.jpg", b"\xff\xd8JPEG", content_type="image/jpeg")
        poster = "/dvb/details/image/w342/titanyPoster01.jpg"
        self.assertEqual(self.get(poster)[0].status, 404)  # not seen yet
        self.remember("5001", "Film: Hněv Titánů", "(USA, 2012)")
        self.get("/dvb/details", {"source": "dvb", "id": "5001"})
        resp, body = self.get(poster)
        self.assertEqual((resp.status, body, resp.content_type), (200, b"\xff\xd8JPEG", "image/jpeg"))
        self.assertEqual(resp.headers["Cache-Control"], "private, no-store")
        self.assertEqual(self.get(poster)[0].status, 200)
        self.assertEqual(self.http.paths().count("/t/p/w342/titanyPoster01.jpg"), 1)  # kept in memory
        for path, query in (("/dvb/details/image/w342/other.jpg", {}),
                            ("/dvb/details/image/original/titanyPoster01.jpg", {}),
                            ("/dvb/details/image/w342/../titanyPoster01.jpg", {}),
                            ("/dvb/details/image/w342/titanyPoster01.gif", {}),
                            ("/dvb/details/image", {"url": "https://image.tmdb.org/t/p/w342/other.jpg"}),
                            ("/dvb/details/image/w342/https://evil.example/x.jpg", {})):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, query)[0].status, 404)
        self.assertNotIn("/t/p/w342/other.jpg", self.http.paths())

    def test_poster_limits(self):
        for name in ("big.jpg", "html.jpg"):
            put_poster(self.gw.details, name)
        self.http.add("/t/p/w342/big.jpg", b"x" * (dt.POSTER_MAX + 1), content_type="image/jpeg")
        self.http.add("/t/p/w342/html.jpg", b"<html>", content_type="text/html")
        for name in ("big.jpg", "html.jpg"):
            resp, body = self.get("/dvb/details/image/w342/" + name)
            self.assertEqual((resp.status, body), (502, {"error": "Plakát se nepodařilo načíst"}), name)
        self.http.add("/t/p/w342/big.jpg", b"x" * dt.POSTER_MAX, content_type="image/jpeg")
        self.assertEqual(self.get("/dvb/details/image/w342/big.jpg")[0].status, 200)  # exactly 1 MB is fine

    def test_poster_busy(self):
        self.gw.details = details_for(self.dir, self.http, per_minute=0)
        put_poster(self.gw.details, "a.jpg")
        resp, _ = self.get("/dvb/details/image/w342/a.jpg")
        self.assertEqual((resp.status, resp.headers["Retry-After"]), (503, "5"))

    def test_poster_memory_is_bounded(self):
        posters = dt._Posters(limit=10)
        posters.put("a", (b"1234", "image/jpeg"))
        posters.put("b", (b"1234", "image/jpeg"))
        posters.get("a")  # used: b is now the oldest
        posters.put("c", (b"1234", "image/jpeg"))
        self.assertEqual((list(posters.items), posters.size), (["a", "c"], 8))
        posters.put("d", (b"x" * 11, "image/jpeg"))  # larger than everything: kept out
        self.assertEqual((list(posters.items), posters.size), ([], 0))
        self.assertEqual(dt.POSTER_MEMORY, 40 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
