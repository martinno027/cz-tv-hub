"""The archive (spec/dvb-gateway.md "Archive"): the golden rules of spec/fixtures/archive_cases.json, ČT's GraphQL
client (shapes, caching, one request at a time, errors), the /dvb/archive routes with their errors, the shared
samples and the /admin tab "TMDb". ČT and TMDb are faked; nothing here touches the network."""
import base64
import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

import archive_world as w
from archive_world import APP, MOVIE_ID, SERIES_ID, WORKER, World
from cztvhub import api
from cztvhub.api import StreamResolver
from cztvhub.server import Server, make_handler
from cztvhub.config import AdminConfig
from cztvhub.dvb import archive_catalog as ac
from cztvhub.dvb import details as dt
from cztvhub.dvb import tmdbstore as ts
from cztvhub.dvb.admin import AdminSite
from dvb_fixtures import NOVA, NOW

GOLDEN = json.loads((Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "archive_cases.json")
                    .read_text(encoding="utf-8"))
ARCHIVE_ROUTES = [("/dvb/archive", {}), ("/dvb/archive/category/3947-filmy", {}), ("/dvb/archive/search", {"q": "a"}),
                  ("/dvb/archive/show/ct:" + SERIES_ID, {})]


def rec(title, start=0, rid=None):
    return ac.RecProgramme(rid or f"b{NOVA}-{start}-{start + 60}", "Nova HD", title, "", (), start, start + 60)


class GoldenTest(unittest.TestCase):
    def test_categories(self):
        for case in GOLDEN["categories"]:
            with self.subTest(case=case["name"]):
                if "tmdb" in case:
                    got = ac.categories_for(case["tmdb"])
                else:
                    got = ac.categories_for(None, case["dvb"]["genre"], case["dvb"]["title"])
                self.assertEqual(got, case["categories"])

    def test_every_category_slug_is_one_of_ct_s(self):
        for case in GOLDEN["categories"]:
            for slug in case["categories"]:
                self.assertIn(slug, ac.BY_SLUG)
        self.assertEqual(len(ac.CATEGORIES), 14)
        self.assertEqual(ac.CATEGORIES[-1][:2], ("4732", "4732-archiv"))  # ČT's "Archiv" too (owner, 2026-10-25)

    def test_normalise(self):
        for case in GOLDEN["normalise"]:
            self.assertEqual(ac.title_key(case["title"]), case["key"], case["title"])

    def test_merge(self):
        for case in GOLDEN["merge"]:
            with self.subTest(case=case["name"]):
                ct_cards = [ac._Card(ac.title_key(c["title"]), {"id": c["id"], "source": "ct"}) for c in case["ct"]]
                shows = ac.group_shows(rec(r["title"], start=1000 * i) for i, r in enumerate(case["rec"]))
                rec_cards = [ac._Card(s.key, {"id": "rec:" + s.key, "source": "rec"}) for s in shows]
                self.assertEqual(ac.merge(ct_cards, rec_cards), case["cards"])

    def test_more_of_ours_go_into_every_third_place(self):
        ct_cards = [ac._Card(k, {"id": k}) for k in ("a", "b", "c", "d")]
        rec_cards = [ac._Card(k, {"id": k}) for k in ("x", "y", "z")]
        self.assertEqual([c["id"] for c in ac.merge(ct_cards, rec_cards)], ["a", "b", "x", "c", "d", "y", "z"])
        self.assertEqual([c["id"] for c in ac.merge(ct_cards, [ac._Card("bb", {"id": "bb"})], az=True)],
                         ["a", "b", "bb", "c", "d"])

    def test_rec_ids_are_ascii(self):
        self.assertEqual(ac.title_key("Łódź v noci"), "łodzvnoci")  # normalise keeps "ł"
        self.assertEqual(ac.card_key("łodzvnoci"), "u0142odzvnoci")
        self.assertEqual(ac.card_key("mistrizastavarny"), "mistrizastavarny")
        for title in ("Sněhová královna", "Øresund", "Страна", "Straße"):
            self.assertRegex(ac.card_key(ac.title_key(title)), r"^[a-z0-9]+$", title)

    def test_grouping_newest_first(self):
        shows = ac.group_shows([rec("Mistři zastavárny VI (14)", 100), rec("Mistři zastavárny VI (15)", 200),
                                rec("Jiný pořad", 150), rec("(14)", 300)])
        self.assertEqual([(s.key, s.title, [p.start for p in s.episodes]) for s in shows],
                         [("mistrizastavarny", "Mistři zastavárny", [200, 100]), ("jinyporad", "Jiný pořad", [150])])


class CtClientTest(unittest.TestCase):
    def setUp(self):
        self.ct = w.FakeCt()
        self.clock = w.Clock()
        self.client = ac.CtArchiveClient(post=self.ct, clock=self.clock)

    def test_query_shapes(self):
        self.client.row("3976", "popular", 2)
        self.client.row("3976", "az", 1)
        self.client.row("3976", "newest", 3)
        queries = [b["query"] for b in self.ct.bodies]
        self.assertIn("showFindByGenre(categoryId: $id, limit: 20, offset: 20, orderBy: popular_previous_seven_days)",
                      queries[0])
        self.assertIn("orderBy: alphabet, order: asc", queries[1])
        self.assertIn("offset: 40, orderBy: catchup_broadcast)", queries[2])
        self.assertEqual(self.ct.bodies[0]["variables"], {"id": "3976"})
        self.client.search('a"b', 1)
        self.assertEqual(self.ct.bodies[-1]["variables"], {"q": 'a"b'})  # the text only ever as a variable
        self.assertNotIn('a"b', self.ct.bodies[-1]["query"])
        self.client.episodes(SERIES_ID, 'season:"x', 1, oldest=True)
        self.assertIn('seasonId: "season:\\"x"', self.ct.bodies[-1]["query"])
        self.assertIn("onlyPlayable: true, orderBy: oldest", self.ct.bodies[-1]["query"])

    def test_cached_six_hours_one_hour_ten_minutes(self):
        for call, ttl in ((lambda: self.client.row("3947", "popular", 1), ac.ROW_TTL),
                          (lambda: self.client.subgenres("3947"), ac.ROW_TTL),
                          (lambda: self.client.show(MOVIE_ID), ac.SHOW_TTL),
                          (lambda: self.client.episodes(MOVIE_ID, None, 1, False), ac.SHOW_TTL),
                          (lambda: self.client.search("vlak", 1), ac.SEARCH_TTL)):
            before = len(self.ct.bodies)
            call()
            self.clock.t += ttl - 1
            call()
            self.assertEqual(len(self.ct.bodies), before + 1)
            self.clock.t += 1
            call()
            self.assertEqual(len(self.ct.bodies), before + 2)
        self.assertEqual((ac.ROW_TTL, ac.SHOW_TTL, ac.SEARCH_TTL), (6 * 3600, 3600, 600))

    def test_one_request_at_a_time(self):
        inside, release = threading.Event(), threading.Event()
        active, most = [0], [0]

        def slow(body):
            active[0] += 1
            most[0] = max(most[0], active[0])
            inside.set()
            release.wait(5)
            active[0] -= 1
            return self.ct(body)

        client = ac.CtArchiveClient(post=slow, clock=self.clock)
        threads = [threading.Thread(target=client.row, args=(cid, "popular", 1)) for cid in ("3947", "3976", "4003")]
        for t in threads:
            t.start()
        self.assertTrue(inside.wait(5))
        release.set()
        for t in threads:
            t.join()
        self.assertEqual((most[0], len(self.ct.bodies)), (1, 3))

    def test_errors(self):
        self.ct.error = api.StreamError("request to https://api.ceskatelevize.cz/graphql/ failed: timed out")
        with self.assertRaises(ac.CtError):
            self.client.row("3947", "popular", 1)
        self.ct.error = None
        with self.assertRaises(ac.CtNotFound):
            self.client.show("99999")
        invalid = {"errors": [{"extensions": {"code": "GRAPHQL_VALIDATION_FAILED"}}]}
        client = ac.CtArchiveClient(post=lambda body: invalid)
        with self.assertRaises(ac.CtError) as caught:
            client.row("3947", "popular", 1)
        self.assertNotIsInstance(caught.exception, ac.CtNotFound)
        for answer in (None, [], {"data": None}, {"data": {"showFindByGenre": None}}):
            with self.subTest(answer=answer):
                client = ac.CtArchiveClient(post=lambda body, a=answer: a)
                try:
                    self.assertEqual(client.row("3947", "popular", 1), ([], 0))
                except ac.CtError:
                    pass
        self.assertEqual(self.ct.bodies[-1]["variables"], {"id": "99999"})

    def test_defensive_parsing(self):
        good = w.CT_SHOWS[MOVIE_ID]
        items = [good, None, {"id": "../x", "title": "A"}, {"id": "1", "title": ""}, {"id": 5, "title": "A"},
                 {**good, "id": "2", "year": "2019a", "showType": "podcast", "playableEpisodeCount": True,
                  "images": {"card": "https://evil.example/x.jpg"}, "flatGenres": "x", "durationText": 98}]
        answer = {"data": {"showFindByGenre": {"totalCount": "6", "items": items}}}
        client = ac.CtArchiveClient(post=lambda body: answer)
        shows, total = client.row("3947", "popular", 1)
        self.assertEqual([s["id"] for s in shows], [MOVIE_ID, "2"])
        self.assertEqual(total, 2)
        odd = shows[1]
        self.assertEqual((odd["year"], odd["kind"], odd["image"], odd["genres"], odd["episodeCount"],
                          odd["durationMinutes"]), (None, None, None, [], None, None))  # unknown stays unknown
        self.assertEqual((shows[0]["year"], shows[0]["kind"], shows[0]["durationMinutes"]), (2019, "movie", 98))
        episode = ac.parse_episode({"id": "123", "title": "1/26 A", "duration": "x", "playable": 1,
                                    "date": {"datetime": "2026-04-08T08:35:00.000Z"}})
        self.assertEqual((episode["durationSeconds"], episode["playable"], episode["aired"], episode["channel"]),
                         (0, False, "2026-04-08T08:35:00Z", ""))
        self.assertIsNone(ac.parse_episode({"id": "1 2", "title": "x"}))
        self.assertIsNone(ac._iso("2026-04-08T08:35:00"))  # no zone: not guessed

    def test_post_as_api_py_with_a_size_cap(self):
        sent = []

        class Answer:
            def __init__(self, body):
                self.body = body

            def read(self, size=-1):
                return self.body[:size]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(body):
            def urlopen(request, timeout=None):
                sent.append(request)
                return Answer(body)
            return urlopen

        self.assertEqual(ac.post_graphql({"query": "q"}, urlopen=opener(b'{"data": {}}')), {"data": {}})
        request = sent[0]
        self.assertEqual((request.full_url, request.get_header("User-agent")), (api.GRAPHQL_URL, api.USER_AGENT))
        self.assertEqual(json.loads(request.data), {"query": "q"})
        with self.assertRaises(ac.CtError):
            ac.post_graphql({}, urlopen=opener(b" " * (ac.CT_MAX_BYTES + 1)))
        self.assertEqual(ac.post_graphql({}, urlopen=opener(b"{}" + b" " * (ac.CT_MAX_BYTES - 2))), {})
        with self.assertRaises(ac.CtError):
            ac.post_graphql({}, urlopen=opener(b"<html>"))

        def refused(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)

        with self.assertRaises(ac.CtError):
            ac.post_graphql({}, urlopen=refused)

    def test_production_images_only_from_ct_hosts(self):
        client = ac.CtArchiveClient(post=lambda body: {"data": {"showFindByGenre": {"items": [
            {"id": "1", "title": "A", "images": {"card": "https://img.example.invalid/a.jpg"}},
            {"id": "2", "title": "B", "images": {"card": "https://ctfs.ceskatelevize.cz/porady-s3/b.jpg"}},
            {"id": "3", "title": "C", "images": {"card": "http://ctfs.ceskatelevize.cz/c.jpg"}}]}}})
        self.assertEqual([s["image"] for s in client.row("3947", "popular", 1)[0]],
                         [None, "https://ctfs.ceskatelevize.cz/porady-s3/b.jpg", None])

    def test_channel_ids_by_channels_py(self):
        for name, expected in (("ČT1", "CH_1"), ("ČT2", "CH_2"), ("ČT24", "CH_24"), ("ČT sport", "CH_4"),
                               ("ČT :D", "CH_5"), ("ČT\u00a0:D", "CH_5"), ("ČT art", "CH_6"), ("ČT 1", None),
                               ("ČT3", None), ("Nova", None), ("", None), ("čt1", None)):
            self.assertEqual(ac.ct_channel_id(name), expected, name)

    def test_text_keeps_no_break_spaces(self):
        self.assertEqual(ac._text(" Film o vlaku\n\t a  noci "), "Film o vlaku a noci")

    def test_subgenres_are_blocks_with_an_id(self):
        self.assertEqual(self.client.subgenres("3976"), [{"id": "3986", "title": "Komedie"}])


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.world = World(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.world.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.world.ct.error = None
        self.world.gw.catalog.ct = w.ct_client(self.world.ct)
        self.world.gw.catalog._rec = None
        self.world.gw.catalog._known.clear()

    def get(self, path, query=None, headers=APP, method="GET"):
        return self.world.get(path, query, headers, method)

    def test_needs_the_app_key_and_refuses_the_worker(self):
        for path, query in ARCHIVE_ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self.get(path, query, headers={})[0].status, 401)
                self.assertEqual(self.get(path, query, headers=WORKER)[0].status, 403)
                self.assertEqual(self.get(path, query, headers={"Cookie": "cztvhub_key=" + w.KEY})[0].status, 200)
                self.assertEqual(self.get(path, query, method="POST")[0].status, 405)

    def test_home(self):
        resp, body = self.get("/dvb/archive")
        self.assertEqual((resp.status, resp.headers["Cache-Control"]), (200, "no-store"))
        ids = [r["id"] for r in body["rows"]]
        self.assertEqual(ids[0], "recordings")
        self.assertEqual(ids[1:], [s for _, s, _ in ac.CATEGORIES if s in ids])  # ČT's order, empty rows left out
        self.assertNotIn("4142-sport", ids)
        self.assertNotIn("error", body)

    def test_ct_down_still_lists_our_recordings(self):
        self.world.ct.error = api.StreamError("down")
        resp, body = self.get("/dvb/archive")
        self.assertEqual((resp.status, body["error"]), (200, "iVysílání teď neodpovídá"))
        self.assertTrue(all(c["source"] == "rec" for r in body["rows"] for c in r["cards"]))
        self.assertIn("4055-rady-a-recepty", [r["id"] for r in body["rows"]])  # Dílna pana Nováka: "Volný čas"
        resp, body = self.get("/dvb/archive/search", {"q": "dílna"})
        self.assertEqual((resp.status, body["error"], [c["id"] for c in body["cards"]]),
                         (200, "iVysílání teď neodpovídá", ["rec:dilnapananovaka"]))
        resp, body = self.get("/dvb/archive/category/3947-filmy")
        self.assertEqual((resp.status, body["error"], [c["id"] for c in body["cards"]]),
                         (200, "iVysílání teď neodpovídá", ["rec:tichahora"]))
        self.assertEqual(self.get("/dvb/archive/show/ct:" + SERIES_ID)[0].status, 502)
        self.assertEqual(self.get("/dvb/archive/show/rec:tichahora")[0].status, 200)

    def test_search_input(self):
        for query, status in (({}, 400), ({"q": ""}, 400), ({"q": "   "}, 400), ({"q": "a" * 101}, 400),
                              ({"q": "a\x00b"}, 400), ({"q": "a" * 100}, 200), ({"q": "vlak", "page": "0"}, 400),
                              ({"q": "vlak", "page": "4"}, 400), ({"q": "vlak", "page": "x"}, 400),
                              ({"q": "vlak", "page": "-1"}, 400), ({"q": "vlak", "page": "3"}, 200)):
            with self.subTest(query=query):
                self.assertEqual(self.get("/dvb/archive/search", query)[0].status, status)

    def test_search_finds_ours_by_text_without_diacritics(self):
        _, body = self.get("/dvb/archive/search", {"q": "TICHA"})
        self.assertEqual([c["id"] for c in body["cards"]], ["rec:tichahora"])
        self.assertEqual(self.world.ct.bodies[-1]["variables"], {"q": "TICHA"})

    def test_category_input(self):
        cases = [("/dvb/archive/category/9999-nic", {}, 404), ("/dvb/archive/category/3947", {}, 404),
                 ("/dvb/archive/category/../3947-filmy", {}, 404), ("/dvb/archive/category/3947-filmy/x", {}, 404),
                 ("/dvb/archive/category/3947-filmy", {"sort": "random"}, 400),
                 ("/dvb/archive/category/3947-filmy", {"genre": "x"}, 400),
                 ("/dvb/archive/category/3947-filmy", {"genre": "4003"}, 400),  # not one of the category's
                 ("/dvb/archive/category/3947-filmy", {"page": "4"}, 400),
                 ("/dvb/archive/category/recordings", {"genre": "3951"}, 400),
                 ("/dvb/archive/category/recordings", {"page": "51"}, 400),
                 ("/dvb/archive/category/recordings", {"page": "50"}, 200),
                 ("/dvb/archive/category/3947-filmy", {"genre": "3951", "sort": "az", "page": "3"}, 200)]
        for path, query, status in cases:
            with self.subTest(path=path, query=query):
                self.assertEqual(self.get(path, query)[0].status, status)

    def test_category_sub_genre_and_sorts(self):
        _, body = self.get("/dvb/archive/category/3976-serialy", {"genre": "3986", "sort": "az"})
        self.assertEqual([c["id"] for c in body["cards"]], ["ct:" + SERIES_ID])  # no recordings in a sub-genre
        self.assertEqual(self.world.ct.bodies[-1]["variables"], {"id": "3986"})
        self.assertIn("orderBy: alphabet, order: asc", self.world.ct.bodies[-1]["query"])
        _, body = self.get("/dvb/archive/category/recordings", {"sort": "az"})
        self.assertEqual([c["title"] for c in body["cards"]], ["Dílna pana Nováka", "Tichá hora", "Zahradní seriál"])
        _, body = self.get("/dvb/archive/category/recordings")
        self.assertEqual([c["title"] for c in body["cards"]], ["Zahradní seriál", "Tichá hora", "Dílna pana Nováka"])
        self.assertEqual((body["title"], body["genres"], body["nextPage"]), ("Naše nahrávky", [], None))

    def test_show_input(self):
        cases = [("ct:", 400), ("ct:1/2", 404), ("x:1", 400), ("ct:..", 400), ("rec:", 400), ("rec:a b", 400),
                 ("rec:a_b", 400), ("ct:99999", 404), ("rec:nicneni", 404), ("rec:dilnapananovaka", 200),
                 ("rec:d%C3%ADlna", 400), ("ct:" + MOVIE_ID, 200)]
        for ident, status in cases:
            with self.subTest(ident=ident):
                self.assertEqual(self.get("/dvb/archive/show/" + ident)[0].status, status)
        path = "/dvb/archive/show/ct:" + SERIES_ID
        for query, status in (({"season": "season:00000000000000000000aa02"}, 200), ({"season": "season:nope"}, 400),
                              ({"season": "a b"}, 400), ({"page": "11"}, 400), ({"page": "2"}, 200)):
            with self.subTest(query=query):
                self.assertEqual(self.get(path, query)[0].status, status)
        self.assertEqual(self.get("/dvb/archive/show/rec:tichahora", {"season": "x"})[0].status, 400)

    def test_show_seasons_and_pages(self):
        path = "/dvb/archive/show/ct:" + SERIES_ID
        _, first = self.get(path)
        _, second = self.get(path, {"page": "2"})
        _, other = self.get(path, {"season": "season:00000000000000000000aa02"})
        group = first["groups"][0]
        self.assertEqual((group["title"], len(group["episodes"]), group["nextPage"]), ("I. řada", 20, 2))
        self.assertEqual(group["episodes"][0]["title"], "1/26 Smyšlený díl 1")  # a series from its first episode
        self.assertEqual([e["title"] for e in second["groups"][0]["episodes"]][:1], ["21/26 Smyšlený díl 21"])
        self.assertIsNone(second["groups"][0]["nextPage"])
        self.assertEqual(other["groups"][0]["title"], "II. řada")
        self.assertNotEqual(first["groups"][0]["episodes"], other["groups"][0]["episodes"])
        self.assertEqual(first["groups"][1]["title"], "Nahráno z antény")
        self.assertEqual(first["card"]["source"], "both")

    def test_show_id_with_a_plain_or_an_encoded_colon(self):
        for ident in ("ct:" + SERIES_ID, "ct%3A" + SERIES_ID, "ct%3a" + SERIES_ID, "rec:tichahora", "rec%3Atichahora"):
            with self.subTest(ident=ident):
                self.assertEqual(self.get("/dvb/archive/show/" + ident)[0].status, 200)

    def test_show_page_has_the_real_kind_and_year_after_a_search_card(self):
        _, found = self.get("/dvb/archive/search", {"q": "Noční"})
        card = next(c for c in found["cards"] if c["id"] == "ct:" + MOVIE_ID)
        self.assertEqual((card["kind"], "year" in card), (None, False))  # ČT's search card does not say
        _, page = self.get("/dvb/archive/show/ct:" + MOVIE_ID)
        self.assertEqual((page["card"]["kind"], page["card"]["year"], page["card"]["durationMinutes"]),
                         ("movie", 2019, 98))

    def test_episodes_carry_the_ct_channel_id(self):
        _, page = self.get("/dvb/archive/show/ct:" + SERIES_ID)
        self.assertEqual({(e["channel"], e["channelId"]) for e in page["groups"][0]["episodes"]}, {("ČT1", "CH_1")})
        self.assertEqual({e["channelId"] for e in page["groups"][1]["episodes"]}, {None})  # ours: Anténa
        _, movie = self.get("/dvb/archive/show/ct:" + MOVIE_ID)
        self.assertEqual(movie["groups"][0]["episodes"][0]["channelId"], "CH_2")

    def test_both_show_page_keeps_our_episodes_while_ct_is_down(self):
        self.get("/dvb/archive")  # the archive listed both shows
        self.world.gw.catalog.ct = w.ct_client(self.world.ct)  # nothing of ČT cached any more
        self.world.ct.error = api.StreamError("down")
        resp, body = self.get("/dvb/archive/show/ct:" + SERIES_ID)
        self.assertEqual((resp.status, body["error"], body["card"]["source"], body["card"]["id"]),
                         (200, "iVysílání teď neodpovídá", "both", "ct:" + SERIES_ID))
        self.assertEqual([g["title"] for g in body["groups"]], ["Nahráno z antény"])
        self.assertEqual(body["seasons"], [])
        self.assertEqual(self.get("/dvb/archive/show/ct:" + MOVIE_ID)[0].status, 502)  # iVysílání only
        self.world.gw.catalog._known.clear()
        self.assertEqual(self.get("/dvb/archive/show/ct:" + SERIES_ID)[0].status, 502)  # never listed: unknown

    def test_rec_show_page(self):
        resp, body = self.get("/dvb/archive/show/rec:dilnapananovaka")
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["seasons"], [])
        episodes = body["groups"][0]["episodes"]
        self.assertEqual([e["title"] for e in episodes], ["Dílna pana Nováka (13)", "Dílna pana Nováka (12)"])
        self.assertEqual(episodes[0], {"source": "rec", "recordingId": f"b{NOVA}-{NOW - 5 * 3600 + 1800}-"
                                                                       f"{NOW - 5 * 3600 + 3600}",
                                       "title": "Dílna pana Nováka (13)", "description": "Další rady do dílny.",
                                       "durationSeconds": 1800, "aired": ac.epoch_iso(NOW - 5 * 3600 + 1800),
                                       "channel": "Nova HD", "channelId": None, "playable": True})


class RecordingsTest(unittest.TestCase):
    """Which programmes are "our recordings": ended, completely recorded, within retention, offered channels."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = World(Path(self.tmp.name), tmdb=False, segments=False)
        self.addCleanup(self.world.close)

    def titles(self):
        self.world.gw.catalog._rec = None
        return sorted(p.title for p in self.world.gw._archive_recordings())

    def test_recorded_as_the_guide_says(self):
        """State "completed" as /dvb/epg annotates it (archive.state: at least 30 s of the programme recorded)."""
        self.assertEqual(self.titles(), [])  # nothing recorded yet
        tila = next(p for p in w.PROGRAMMES if p[0] == "7003")
        self.world.record(tila[4], tila[4] + 20, step=20.0)
        self.assertEqual(self.titles(), [])
        self.world.record(tila[4] + 20, tila[4] + 40, step=20.0)
        self.assertEqual(self.titles(), ["Film: Tichá hora"])

    def test_retention_hidden_channels_and_airing_now(self):
        self.world.record(w.PROGRAMMES[0][4] - 60, NOW)
        self.assertEqual(len(self.titles()), 4)
        self.world.now["t"] = NOW + 3 * 86400 - 5 * 3600 + 60  # the oldest one is past the 3-day retention
        self.assertNotIn("Dílna pana Nováka (12)", self.titles())
        self.world.now["t"] = NOW
        channels = self.world.client.channels
        self.world.client.channels = lambda: [c for c in channels() if c.uuid != NOVA]  # hidden on /admin
        self.world.gw._channels_at = float("-inf")
        self.assertEqual(self.titles(), [])
        self.world.client.channels = channels
        self.world.gw._channels_at = float("-inf")
        self.world.client.unreachable = True
        self.assertEqual(self.titles(), [])  # TVHeadend down: no recordings listed, ČT still works
        resp, _ = self.world.get("/dvb/archive")
        self.assertEqual(resp.status, 200)


class NoClientTextTest(unittest.TestCase):
    """What a client types goes to ČT's search (as a variable) and nowhere else; TMDb gets only titles that ČT
    or our EPG gave the gateway."""

    def test_search_text_never_reaches_tmdb(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            try:
                secret = "vlak tajné heslo"
                for page in ("1", "2"):
                    world.get("/dvb/archive/search", {"q": secret, "page": page})
                world.get("/dvb/archive/search", {"q": "Noční"})
                world.get("/dvb/archive/search", {"q": "Tichá hora"})  # also the title of one of ours
                world.gw.tmdb_worker.run_once()
                world.get("/dvb/archive/show/rec:tichahora")
                self.assertTrue(world.tmdb.requests)
                for url in world.tmdb.urls():
                    self.assertNotIn("tajné", url)
                    self.assertNotIn("heslo", url)
                known = {dt.clean_title(t, k) for t in [s["title"] for s in w.CT_SHOWS.values()] +
                         [p[1] for p in w.PROGRAMMES] for k in ("movie", "tv")}
                self.assertTrue(set(world.tmdb.queries()) <= known, world.tmdb.queries())
                self.assertIn("Noční vlak", world.tmdb.queries())  # a ČT search result is a ČT title
            finally:
                world.close()

    def test_worker_looks_up_what_the_archive_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = World(Path(tmp))
            try:
                worker = world.gw.tmdb_worker
                self.assertEqual(sorted(dt.clean_title(q.title, q.kind) for q in world.gw.catalog.wanted()),
                                 ["Dílna pana Nováka", "Tichá hora", "Zahradní seriál"])  # ours only, until listed
                world.get("/dvb/archive")
                done, refused = worker.run_once()
                self.assertFalse(refused)
                self.assertEqual(done, 6)  # 3 of ours + 3 more ČT shows (Zahradní seriál is one entry)
                self.assertEqual(worker.run_once(), (0, False))  # all fresh now
                store = world.gw.details.store
                self.assertEqual(len(store), 6)
                # nearly 5 months later: what the archive still lists is refreshed, our old recordings are long
                # gone (retention), so their entries are not, and they are deleted at 5 months
                world.now["t"] += 5 * ts.MONTH - ts.REFRESH_LEAD
                world.gw.catalog._rec = None
                self.assertEqual(worker.run_once(), (4, False))
                world.now["t"] += ts.REFRESH_LEAD
                store.purge()
                self.assertEqual(sorted(e.title for e in store.page(1)),
                                 ["Kuchyně u sousedů", "Noční vlak", "Příběh řeky", "Zahradní seriál"])
            finally:
                world.close()


class SharedFixtureTest(unittest.TestCase):
    def test_gateway_answers_equal_the_shared_samples(self):
        answers = w.shared_answers()
        for name, (file, _, _) in w.SHARED_FILES.items():
            with self.subTest(file=file):
                self.assertEqual(answers[name], json.loads((w.SHARED / file).read_text(encoding="utf-8")))
        home = answers["home"]["body"]
        cards = [c for r in home["rows"] for c in r["cards"]]
        self.assertEqual({c["source"] for c in cards}, {"ct", "rec", "both"})
        self.assertTrue(any(c["rating"] for c in cards) and any(c["rating"] is None for c in cards))
        series, movie = answers["show_series"]["body"], answers["show_movie"]["body"]
        self.assertEqual([g["title"] for g in series["groups"]], ["I. řada", "Nahráno z antény"])
        self.assertEqual((movie["card"]["kind"], len(movie["groups"])), ("movie", 1))
        self.assertNotEqual(series, movie)
        self.assertEqual(answers["search"]["body"]["cards"][0]["source"], "both")
        for name, answer in answers.items():  # TMDb's sentence exactly where a card shows TMDb data
            body = answer["body"]
            cards = [c for r in body.get("rows", []) for c in r["cards"]] + body.get("cards", []) + \
                ([body["card"]] if "card" in body else [])
            self.assertEqual("attribution" in body, any(c["tmdb"] for c in cards), name)
        self.assertIn("attribution", home)
        self.assertNotIn("attribution", answers["search"]["body"])
        season = answers["show_season_page"]["body"]
        self.assertEqual((season["groups"][0]["title"], season["groups"][0]["nextPage"],
                          season["groups"][0]["episodes"][0]["title"]), ("II. řada", None, "21/26 Druhá řada 21"))
        self.assertEqual(season["groups"][1]["title"], "Nahráno z antény")
        for answer in answers.values():
            self.assertNotIn("ceskatelevize.cz", json.dumps(answer))
        down = answers["show_ctdown"]["body"]
        self.assertEqual((down["card"]["source"], down["seasons"], [g["title"] for g in down["groups"]],
                          down["error"]), ("both", [], ["Nahráno z antény"], "iVysílání teď neodpovídá"))
        self.assertEqual(down["card"]["id"], series["card"]["id"])
        self.assertTrue(all(a["status"] == 200 for a in answers.values()))


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


PASSWORD = "admin-password-1"
AUTH = {"Authorization": basic("admin", PASSWORD)}
POST = {**AUTH, "X-Cztvhub-Admin": "1", "Host": "pi:8098"}


class AdminTmdbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = World(Path(self.tmp.name), segments=False)
        self.addCleanup(self.world.close)
        self.world.gw.cfg.admin = AdminConfig(password=PASSWORD)
        self.site = AdminSite(self.world.gw.cfg, self.world.gw)
        self.store = self.world.gw.details.store
        self.store.put(ts.store_key("movie", 2015, "Tichá hora"), "movie", 2015, "Tichá hora",
                       {"kind": "movie", "tmdbId": 9001, "rating": {"value": 7.1, "votes": 250}, "genreIds": [18],
                        "genres": ["Drama"], "year": 2015, "poster": "tichaHora01.jpg"})
        self.store.put(ts.store_key("tv", None, "Dílna pana Nováka"), "tv", None, "Dílna pana Nováka", None)

    def request(self, method, path, headers=AUTH, body=None, query=None):
        raw = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
        resp = self.site.handle(method, path, headers, raw, query)
        data = json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body
        return resp, data

    def test_needs_login_and_csrf(self):
        self.assertEqual(self.request("GET", "/admin/api/tmdb", headers={})[0].status, 401)
        self.assertEqual(self.request("POST", "/admin/api/tmdb", headers=AUTH, body={"deleteAll": True})[0].status,
                         403)
        cross = {**POST, "Origin": "http://evil.example"}
        self.assertEqual(self.request("POST", "/admin/api/tmdb", headers=cross, body={"deleteAll": True})[0].status,
                         403)
        self.assertEqual(len(self.store), 2)
        self.assertEqual(self.request("PUT", "/admin/api/tmdb", headers=POST)[0].status, 405)
        self.assertEqual(self.request("GET", "/admin/api/tmdb.json")[0].status, 404)  # no export of any kind

    def test_read_only_paginated_list(self):
        for i in range(ts.PAGE_SIZE):
            self.store.put(ts.store_key("tv", None, f"Pořad {i:02d}"), "tv", None, f"Pořad {i:02d}", None)
        resp, data = self.request("GET", "/admin/api/tmdb")
        self.assertEqual((resp.status, data["total"], data["pages"], len(data["entries"])), (200, 52, 2, 50))
        self.assertEqual((data["months"], data["warning"], data["enabled"]), (5, None, True))
        _, page2 = self.request("GET", "/admin/api/tmdb", query={"page": "2"})
        self.assertEqual([e["title"] for e in page2["entries"]], ["Pořad 49", "Tichá hora"])
        first = data["entries"][0]
        self.assertEqual({k: first[k] for k in ("title", "match", "matchKind", "rating", "genres", "matchYear")},
                         {"title": "Dílna pana Nováka", "match": False, "matchKind": None, "rating": None,
                          "genres": [], "matchYear": None})
        self.assertEqual(self.request("GET", "/admin/api/tmdb", query={"page": "x"})[0].status, 400)

    def test_months_setting_and_warning_above_six(self):
        for value in (0, -1, 1.5, "7", True, None):
            with self.subTest(value=value):
                self.assertEqual(self.request("POST", "/admin/api/tmdb", POST, {"months": value})[0].status, 400)
        resp, data = self.request("POST", "/admin/api/tmdb", POST, {"months": 6})
        self.assertEqual((resp.status, data["months"], data["warning"]), (200, 6, None))
        _, data = self.request("POST", "/admin/api/tmdb", POST, {"months": 9999999})
        self.assertEqual((data["months"], data["warning"]), (9999999, ts.WARNING))
        self.assertEqual(ts.WARNING, "Licence TMDb povoluje uchovávat údaje nejvýš 6 měsíců. Delší doba je vaše "
                                     "rozhodnutí a TMDb může klíč zrušit.")
        self.assertEqual(self.world.gw.settings.get(ts.SETTING), 9999999)
        self.assertIn("admin: tmdb: refresh after 9999999 month(s)", self.world.logs)
        self.world.now["t"] += 2 * ts.MONTH
        _, data = self.request("POST", "/admin/api/tmdb", POST, {"months": 1})
        self.assertEqual(data["total"], 0)  # shorter period: older entries go at once
        self.assertIn("admin: tmdb: refresh after 1 month(s), deleted 2 older entries", self.world.logs)

    def test_delete_one_and_all_logged(self):
        key = ts.store_key("tv", None, "Dílna pana Nováka")
        resp, _ = self.request("POST", "/admin/api/tmdb", POST, {"delete": key})
        self.assertEqual(resp.status, 200)
        self.assertIn("admin: tmdb: delete Dílna pana Nováka", self.world.logs)
        self.assertEqual(self.request("POST", "/admin/api/tmdb", POST, {"delete": key})[0].status, 404)
        self.assertEqual(self.request("POST", "/admin/api/tmdb", POST, {"delete": 5})[0].status, 400)
        resp, data = self.request("POST", "/admin/api/tmdb", POST, {"deleteAll": True})
        self.assertEqual((resp.status, data), (200, {"deleted": 1}))
        self.assertIn("admin: tmdb: delete all (1 entry)", self.world.logs)
        self.assertEqual(len(self.store), 0)

    def test_refresh_sends_only_the_stored_title(self):
        key = ts.store_key("movie", 2015, "Tichá hora")
        resp, data = self.request("POST", "/admin/api/tmdb", POST, {"refresh": key})
        self.assertEqual((resp.status, data["entry"]["rating"]), (200, {"value": 7.1, "votes": 250}))
        self.assertEqual(self.world.tmdb.queries(), ["Tichá hora"])
        self.assertIn("admin: tmdb: refresh Tichá hora: match", self.world.logs)
        self.assertEqual(self.request("POST", "/admin/api/tmdb", POST, {"refresh": "[\"movie\", null, \"x\"]"})[0]
                         .status, 404)
        self.world.tmdb.error = urllib.error.URLError("down")
        resp, data = self.request("POST", "/admin/api/tmdb", POST, {"refresh": key})
        self.assertEqual((resp.status, data["error"]), (502, dt.TmdbError.user_message))
        self.assertTrue(any(line.startswith("admin: tmdb: refresh Tichá hora: failed") for line in self.world.logs))
        self.assertEqual(self.request("POST", "/admin/api/tmdb", POST, {"other": 1})[0].status, 400)
        self.assertEqual(self.request("POST", "/admin/api/tmdb", POST, b"{nope")[0].status, 400)

    def test_off_without_key(self):
        self.world.gw.details = None
        _, data = self.request("GET", "/admin/api/tmdb")
        self.assertEqual((data["enabled"], data["entries"]), (False, []))
        self.assertEqual(self.request("POST", "/admin/api/tmdb", POST, {"deleteAll": True})[0].status, 404)

    def test_server_passes_the_page_parameter(self):
        for i in range(ts.PAGE_SIZE):
            self.store.put(ts.store_key("tv", None, f"Pořad {i:02d}"), "tv", None, f"Pořad {i:02d}", None)
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(), None, self.site))  # an ephemeral port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("GET", "/admin/api/tmdb?page=2", headers=AUTH)
            data = json.loads(conn.getresponse().read())
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual((data["page"], [e["title"] for e in data["entries"]]), (2, ["Pořad 49", "Tichá hora"]))

    def test_page_has_the_tab(self):
        resp, page = self.request("GET", "/admin")
        text = " ".join(page.decode("utf-8").split())
        for snippet in ('id="tab-tmdb"', "Obnovit údaje z TMDb po", "Smazat vše", "/admin/api/tmdb",
                        "nedá stáhnout ani exportovat", "not endorsed, certified, or otherwise approved by TMDB"):
            self.assertIn(snippet, text)


if __name__ == "__main__":
    unittest.main()
