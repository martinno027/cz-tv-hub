"""A synthetic world for the archive tests: ČT's GraphQL (invented shows, ČT's shapes as found 2026-10-25), TMDb
(invented titles, TMDb's shapes) and a gateway with a real continuous recording of a few programmes on Nova.
Nothing here touches the network."""
import io
import json
import re
import tempfile
import urllib.error
import urllib.parse
from pathlib import Path

from cztvhub import api
from cztvhub.config import AdsConfig, Config, DvbConfig, TmdbConfig
from cztvhub.dvb import details as dt
from cztvhub.dvb.archive import Archive
from cztvhub.dvb.archive_catalog import CtArchiveClient
from cztvhub.dvb.buffer import BufferStore
from cztvhub.dvb.gateway import DvbGateway
from cztvhub.dvb.hls import HlsManager
from cztvhub.dvb.recorder import RecorderManager
from cztvhub.dvb.thumbs import Settings, Thumbnails
from cztvhub.dvb.tvheadend import EpgEvent
from dvb_fixtures import NOVA, NOW, FakeClient

KEY = "archive-app-key-" * 3
WORKER_KEY = "archive-worker-" * 3
TMDB_KEY = "0123456789abcdef0123456789abcdef"
APP = {"Authorization": "Bearer " + KEY}
WORKER = {"Authorization": "Bearer " + WORKER_KEY}
RUN = "1792280000-cd34"
IMG = "https://img.example.invalid/archive/card/"  # invented: nothing checking the samples asks ČT's CDN
IMAGE_RE = re.compile(r"^https://img\.example\.invalid/[^\s\"<>]{1,500}$")


def ct_client(post, clock=None):
    """The gateway's ČT client, accepting the invented image host instead of ČT's."""
    return CtArchiveClient(post=post, clock=clock or Clock(), image_re=IMAGE_RE)


def show(sid, title, kind, count, genres, year=None, duration=None, **extra):
    return {"id": sid, "title": title, "year": year, "showType": kind, "playableEpisodeCount": count,
            "durationText": duration, "images": {"card": IMG + sid + ".jpg"},
            "flatGenres": [{"id": i, "title": t} for i, t in genres], **extra}


SERIES_ID, MOVIE_ID, KITCHEN_ID, RIVER_ID = "10000000001", "10000000002", "10000000003", "10000000004"
CT_SHOWS = {
    SERIES_ID: show(SERIES_ID, "Zahradní seriál", "series", 52, [("3976", "Seriál"), ("3986", "Komedie")],
                    description=None, shortDescription="Smyšlený seriál o jedné zahradě.",
                    creators="Režie: Jan Příklad. Hrají: Eva Vzorová, Petr Ukázka a další.", countriesOfOrigin=None,
                    seasons=[{"id": "season:00000000000000000000aa01", "title": "I. řada"},
                             {"id": "season:00000000000000000000aa02", "title": "II. řada"}]),
    MOVIE_ID: show(MOVIE_ID, "Noční vlak", "movie", 1, [("3947", "Film"), ("3951", "Drama")], year="2019",
                   duration="98 min", description="Smyšlený film o jedné noci ve vlaku.",
                   shortDescription="Film.", creators="Režie: Eva Vzorová.",
                   countriesOfOrigin=[{"title": "Česko"}, {"title": "Slovensko"}], seasons=[]),
    KITCHEN_ID: show(KITCHEN_ID, "Kuchyně u sousedů", "series", 12, [("3976", "Seriál")]),
    RIVER_ID: show(RIVER_ID, "Příběh řeky", "series", 6, [("4003", "Dokument"), ("4106", "Příroda")]),
}
ROWS = {"3947": [MOVIE_ID], "3976": [SERIES_ID, KITCHEN_ID], "4003": [RIVER_ID], "4106": [RIVER_ID],
        "3986": [SERIES_ID]}
SUBGENRES = {
    "3947": [{"title": "Nejsledovanější za 7 dní", "categoryId": None}, {"title": "Drama", "categoryId": "3951"}],
    "3976": [{"title": "Nejsledovanější za 7 dní", "categoryId": None}, {"title": "Komedie", "categoryId": "3986"},
             {"title": "Naposledy vysílané", "categoryId": None}],
}


def episode(idec, title, start, channel="ČT1", duration=3113, description="Smyšlený popis dílu."):
    return {"id": idec, "title": title, "description": description, "duration": duration, "playable": True,
            "date": {"datetime": start, "channelName": channel}}


EPISODES = {
    (SERIES_ID, "season:00000000000000000000aa01"): [
        episode(f"2085221614{n:05d}", f"{n}/26 Smyšlený díl {n}", f"2026-04-{n:02d}T18:10:00.000Z")
        for n in range(1, 27)],
    (SERIES_ID, "season:00000000000000000000aa02"): [
        episode(f"2085221615{n:05d}", f"{n}/26 Druhá řada {n}", f"2026-05-{n:02d}T18:10:00.000Z")
        for n in range(1, 27)],
    (MOVIE_ID, None): [episode("22638149399", "Noční vlak", "2026-10-05T20:25:00.000Z", "ČT2", 5880, None)],
}


class FakeCt:
    """ČT's GraphQL endpoint: answers the archive's five query shapes from the tables above."""

    def __init__(self):
        self.bodies = []
        self.error = None

    def __call__(self, body):
        self.bodies.append(body)
        if self.error is not None:
            raise self.error
        query, variables = body["query"], body["variables"]
        number = lambda name, default=0: int((re.search(name + r": (\d+)", query) or [0, default])[1])  # noqa: E731
        limit, offset = number("limit", 20), number("offset")
        if "showFindByGenre" in query:
            ids = ROWS.get(variables["id"], [])
            if "orderBy: alphabet" in query:
                ids = sorted(ids, key=lambda i: dt.normalise(CT_SHOWS[i]["title"]))
            items = [_show_only(CT_SHOWS[i]) for i in ids[offset:offset + limit]]
            return {"data": {"showFindByGenre": {"totalCount": len(ids), "items": items}}}
        if "categoryBlocks" in query:
            blocks = SUBGENRES.get(variables["id"], [])
            return {"data": {"category": {"title": "Kategorie", "categoryBlocks": blocks}}}
        if "searchShows" in query:
            needle = dt.normalise(variables["q"])
            found = [{"id": s["id"], "title": s["title"], "images": s["images"]} for s in CT_SHOWS.values()
                     if needle and needle in dt.normalise(s["title"])]
            return {"data": {"searchShows": {"items": found[offset:offset + limit]}}}
        found = CT_SHOWS.get(variables["id"])
        if found is None:
            return {"data": None, "errors": [{"message": "Obsah stránky nebyl nalezen.",
                                              "extensions": {"code": "NOT_FOUND"}}]}
        if "episodes(" in query:
            season = (re.search(r'seasonId: "([^"]+)"', query) or [None, None])[1]
            items = list(EPISODES.get((found["id"], season), []))
            if "orderBy: newest" in query:
                items.reverse()
            return {"data": {"programme": {"episodes": {"totalCount": len(items),
                                                        "items": items[offset:offset + limit]}}}}
        return {"data": {"programme": found}}


def _show_only(item):
    return {k: v for k, v in item.items()
            if k not in ("description", "shortDescription", "creators", "countriesOfOrigin", "seasons")}


# -- TMDb ---------------------------------------------------------------------------------------
TMDB_SEARCH = {
    ("movie", "Tichá hora"): [{"id": 9001, "title": "Tichá hora", "original_title": "Silent Mountain",
                               "release_date": "2015-05-01"}],
    ("movie", "Noční vlak"): [{"id": 9002, "title": "Noční vlak", "original_title": "Night Train",
                               "release_date": "2019-03-01"}],
    ("tv", "Příběh řeky"): [{"id": 9003, "name": "Příběh řeky", "original_name": "River Story",
                             "first_air_date": "2020-01-01"}],
}
TMDB_DETAILS = {
    "/3/movie/9001": {"id": 9001, "title": "Tichá hora", "original_title": "Silent Mountain",
                      "release_date": "2015-05-01", "overview": "Smyšlený popis.", "runtime": 88,
                      "genres": [{"id": 18, "name": "Drama"}, {"id": 36, "name": "Historický"}],
                      "vote_average": 7.1, "vote_count": 250, "poster_path": "/tichaHora01.jpg"},
    "/3/movie/9002": {"id": 9002, "title": "Noční vlak", "original_title": "Night Train",
                      "release_date": "2019-03-01", "overview": "", "runtime": 98,
                      "genres": [{"id": 18, "name": "Drama"}], "vote_average": 6.4, "vote_count": 80,
                      "poster_path": "/nocniVlak02.jpg"},
    "/3/tv/9003": {"id": 9003, "name": "Příběh řeky", "original_name": "River Story",
                   "first_air_date": "2020-01-01", "genres": [{"id": 99, "name": "Dokumentární"}],
                   "episode_run_time": [52], "vote_average": 8.0, "vote_count": 12, "poster_path": None},
}


class FakeTmdb:
    """TMDb's API as urlopen: searches answered by (kind, query), details by path; records every request."""

    def __init__(self):
        self.requests = []
        self.error = None

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        url = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(url.query))
        if url.path.startswith("/3/search/"):
            payload = {"results": TMDB_SEARCH.get((url.path.rsplit("/", 1)[1], query.get("query")), [])}
        elif url.path in TMDB_DETAILS:
            payload = TMDB_DETAILS[url.path]
        else:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return _Response(json.dumps(payload).encode())

    def queries(self):
        return [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.full_url).query)).get("query")
                for r in self.requests if "/search/" in r.full_url]

    def urls(self):
        return [urllib.parse.unquote_plus(r.full_url) for r in self.requests]


class _Response:
    status = 200

    def __init__(self, body):
        self.headers = {"Content-Type": "application/json"}
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


# -- our recordings ------------------------------------------------------------------------------
HOUR = 3600
PROGRAMMES = [  # (event id, title, description, genre, start, minutes): all on Nova, ended before NOW
    ("7001", "Dílna pana Nováka (12)", "Rady do dílny.", [0xA0], NOW - 5 * HOUR, 30),
    ("7002", "Dílna pana Nováka (13)", "Další rady do dílny.", [0xA0], NOW - 5 * HOUR + 1800, 30),
    ("7003", "Film: Tichá hora", "Smyšlený film (ČR, 2015)", [0x10], NOW - 4 * HOUR, 90),
    ("7004", "Zahradní seriál (3)", "Třetí díl.", [0x15], NOW - 2 * HOUR, 45),
]


class HealthyRecorder:
    state = "nahrává"
    run = RUN

    def healthy(self, now=None, within=20.0):
        return True

    def stop(self, wait=0):
        pass

    def start(self):
        pass


class World:
    """A gateway with TMDb on, ČT and TMDb faked, Nova recorded for the last 5 hours."""

    def __init__(self, root: Path, tmdb=True, segments=True):
        self.root = Path(root)
        self.now = {"t": NOW}
        self.logs = []
        cfg = Config(dvb=DvbConfig(enabled=True, key=KEY, hls_dir=self.root / "hls", state_dir=self.root / "state",
                                   buffer_dir=self.root / "buffer"),
                     ads=AdsConfig(enabled=True, worker_key=WORKER_KEY, breaks_dir=self.root / "breaks"),
                     tmdb=TmdbConfig(key=TMDB_KEY if tmdb else ""))
        self.client = FakeClient()
        self.store = BufferStore(self.root / "buffer")
        self.settings = Settings(self.root / "state")
        self.settings.update(recordChannels=[NOVA])
        now = lambda: self.now["t"]  # noqa: E731
        recorders = RecorderManager(self.store, lambda ch: None, log=lambda m: None,
                                    factory=lambda ch: HealthyRecorder())
        self.gw = None
        archive = Archive(self.store, recorders, self.settings, Thumbnails(self.client, self.settings), 3, now=now,
                          log=lambda m: None, offered=lambda: self.gw._offered_ids(),
                          is_programme=lambda c, s, e: self.gw._is_programme(c, s, e), allow_system_disk=True)
        self.hls = HlsManager(cfg.dvb.hls_dir, idle_seconds=30, log=lambda m: None)
        self.gw = DvbGateway(cfg, client=self.client, hls=self.hls, now=now, log=self.logs.append, archive=archive)
        self.ct = FakeCt()
        self.gw.catalog.ct = ct_client(self.ct)
        self.tmdb = FakeTmdb()
        self.limiter_clock = Clock()
        if self.gw.details is not None:
            client = dt.TmdbClient(TMDB_KEY, limiter=dt.Limiter(wait=0.05, clock=self.limiter_clock),
                                   urlopen=self.tmdb)
            self.gw.details.client = client
        self.gw.history.merge([EpgEvent(eid, NOVA, title, "", desc, genre, start, start + minutes * 60)
                               for eid, title, desc, genre, start, minutes in PROGRAMMES], NOW, (0, 0))
        if segments:
            self.record(PROGRAMMES[0][4] - 60, NOW)
        archive.sync()

    def record(self, start, end, step=300.0):
        self.store.save_run(NOVA, RUN, b"not an mp4", {"audio": [{"label": "Čeština", "language": "ces"}]})
        work = self.root / "work"
        work.mkdir(exist_ok=True)
        t, n = float(start), 0
        while t < end:
            path = work / f"s{n}.m4s"
            path.write_bytes(b"x")
            self.store.add(NOVA, RUN, n, t, step, path)
            t += step
            n += 1

    def get(self, path, query=None, headers=APP, method="GET"):
        resp = self.gw.handle(path, query or {}, headers, method, b"", "192.168.1.10")
        body = json.loads(resp.body) if resp.content_type.startswith("application/json") and resp.body else resp.body
        return resp, body

    def close(self):
        self.hls.shutdown()


SHARED = Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "dvb"
SHARED_FILES = {
    "home": ("dvb_archive_home.json", "/dvb/archive", {}),
    "category": ("dvb_archive_category.json", "/dvb/archive/category/3947-filmy", {"sort": "popular"}),
    "search": ("dvb_archive_search.json", "/dvb/archive/search", {"q": "zahrad"}),
    "show_series": ("dvb_archive_show_series.json", "/dvb/archive/show/ct:" + SERIES_ID, {}),
    "show_movie": ("dvb_archive_show_movie.json", "/dvb/archive/show/ct:" + MOVIE_ID, {}),
    "show_season_page": ("dvb_archive_show_season_page.json", "/dvb/archive/show/ct:" + SERIES_ID,
                         {"season": "season:00000000000000000000aa02", "page": "2"}),
    # last: ČT down (nothing of it cached), a show the archive listed and we recorded
    "show_ctdown": ("dvb_archive_show_ctdown.json", "/dvb/archive/show/ct:" + SERIES_ID, {}),
}
CT_DOWN = {"show_ctdown"}


def shared_answers():
    """The shared samples as the gateway sends them: the archive opened once, the TMDb worker's round, then
    every answer again (now with the stored ratings); the ČT-down ones last, after ČT failed."""
    with tempfile.TemporaryDirectory() as tmp:
        world = World(Path(tmp))
        try:
            for name, (_, path, query) in SHARED_FILES.items():
                if name not in CT_DOWN:
                    world.get(path, query)
            world.gw.tmdb_worker.run_once()
            out = {}
            for name, (_, path, query) in SHARED_FILES.items():
                if name in CT_DOWN and world.ct.error is None:
                    world.gw.catalog.ct = ct_client(world.ct)
                    world.ct.error = api.StreamError("ČT GraphQL: timed out")
                resp, body = world.get(path, query)
                out[name] = {"status": resp.status, "body": body}
            return out
        finally:
            world.close()


def write_shared_fixtures():
    """Regenerate the samples: PYTHONPATH=src:tests python3 -c 'import archive_world as w;
    w.write_shared_fixtures()' (run from server/)."""
    for name, sample in shared_answers().items():
        text = json.dumps(sample, ensure_ascii=False, indent=1) + "\n"
        (SHARED / SHARED_FILES[name][0]).write_text(text, encoding="utf-8")
