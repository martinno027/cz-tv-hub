"""Year and month of long ČT episode lists (spec/dvb-gateway.md "Archive" → "Year and month (T32)"): years from
the newest and the oldest episode, a year's offsets by bisection (limit 1), the year read in chunks of 100 and
grouped by Prague calendar month, the route's inputs, caching, ČT failures and how many requests it all takes.
ČT is faked with thousands of synthetic dated episodes; nothing here touches the network."""
import json
import math
import re
import tempfile
import unittest
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import archive_world as w
from archive_world import NEWS_ID, SERIES_ID, World
from cztvhub import api
from cztvhub.dvb import archive_catalog as ac
from cztvhub.dvb.epg import PRAGUE

GOLDEN = json.loads((Path(__file__).resolve().parents[2] / "spec" / "fixtures" / "archive_cases.json")
                    .read_text(encoding="utf-8"))
SHOW = "20000000001"
UDALOSTI_TOTAL = 9970  # ČT, 2026-11-01: "Události" (1097181328), 2005-01-01 … today


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def spread(total, first, last):
    """``total`` airings evenly from ``first`` to ``last`` (UTC datetimes), oldest first."""
    step = (last - first) / (total - 1)
    return [iso(first + step * i) for i in range(total)]


def udalosti():
    return spread(UDALOSTI_TOTAL, datetime(2005, 1, 1, 18, tzinfo=timezone.utc),
                  datetime(2026, 11, 1, 18, tzinfo=timezone.utc))


class ManyCt:
    """ČT's GraphQL for one invented show: its seasons and playable episodes (dates oldest first; None = no
    date), ``orderBy: newest|oldest``, ``offset``/``limit`` (more than 100 is a validation error, as at ČT).
    ``fail`` = numbers of the requests (1-based) that time out."""

    def __init__(self, dates, seasons=None):
        self.lists = {None: dates} if seasons is None else dict(seasons)
        self.seasons = [] if seasons is None else [{"id": s, "title": f"Řada {s[-1]}"} for s in seasons]
        self.bodies, self.fail = [], set()
        self.oldest_only = []  # airings only the oldest-first list has (ČT inconsistent; not seen, but possible)

    def __call__(self, body):
        self.bodies.append(body)
        if len(self.bodies) in self.fail:
            raise api.StreamError("ČT GraphQL: timed out")
        query = body["query"]
        if "episodes(" not in query:
            return {"data": {"programme": {"id": SHOW, "title": "Dlouhý pořad", "year": None, "showType": "series",
                                           "playableEpisodeCount": 1, "images": {"card": None}, "flatGenres": [],
                                           "description": "Smyšlený pořad.", "seasons": self.seasons}}}
        limit = int(re.search(r"limit: (\d+)", query)[1])
        offset = int(re.search(r"offset: (\d+)", query)[1])
        if limit > 100:
            return {"data": None, "errors": [{"extensions": {"code": "GRAPHQL_VALIDATION_FAILED"}}]}
        if offset + limit > 10000:  # ČT's search window (2026-11-01)
            return {"data": None, "errors": [{"message": "Omlouváme se…", "extensions": {"isExpected": True}}]}
        season = (re.search(r'seasonId: "([^"]+)"', query) or [None, None])[1]
        dates = self.lists[season]
        if "orderBy: oldest" in query:
            dates = self.oldest_only + dates
        order = list(range(len(dates)))
        if "orderBy: newest" in query:
            order.reverse()
        items = [{"id": f"9{season[-1] if season else 0}{n:08d}", "title": f"Díl {n}", "description": "",
                  "duration": 1500, "playable": True,
                  "date": None if dates[n] is None else {"datetime": dates[n], "channelName": "ČT1"}}
                 for n in order[offset:offset + limit]]
        return {"data": {"programme": {"episodes": {"totalCount": len(dates), "items": items}}}}

    def episode_requests(self):
        return [b["query"] for b in self.bodies if "episodes(" in b["query"]]

    def probes(self):
        """Bisection steps."""
        return [q for q in self.episode_requests() if "limit: 1," in q]

    def oldest(self):
        return [q for q in self.episode_requests() if "orderBy: oldest" in q]

    def chunks(self):
        return [q for q in self.episode_requests() if "limit: 100," in q]


def truth(dates):
    """year → OrderedDict month → episode dates newest first (Prague calendar), the way a person would sort."""
    out = {}
    for d in dates:
        if d is None:
            continue
        local = datetime.strptime(d, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc).astimezone(PRAGUE)
        out.setdefault(local.year, {}).setdefault(local.month, []).append(d.replace(".000Z", "Z"))
    return {y: OrderedDict((m, sorted(v, reverse=True)) for m, v in sorted(ms.items(), reverse=True))
            for y, ms in out.items()}


class Base(unittest.TestCase):
    def make(self, dates, seasons=None):
        self.ct = ManyCt(dates, seasons)
        self.clock = w.Clock()
        self.catalog = ac.ArchiveCatalog(ac.CtArchiveClient(post=self.ct, clock=self.clock), lambda: [],
                                         clock=self.clock, log=lambda m: None)
        return self.catalog

    def hydepark(self):
        """Two a month (7th and 21st, 20:00 UTC) from January 2015 to October 2026: 286 episodes, 24 a year."""
        dates = [iso(datetime(y, m, d, 20, tzinfo=timezone.utc)) for y in range(2015, 2027) for m in range(1, 13)
                 for d in (7, 21) if (y, m) <= (2026, 10)]
        self.make(dates)
        return truth(dates)

    def show(self, season=None, page=1, year=None, month=None):
        return self.catalog.show("ct:" + SHOW, season, page, year, month)

    def requests(self, fn):
        before = len(self.ct.bodies)
        result = fn()
        return result, len(self.ct.bodies) - before

    def month_of(self, answer):
        group = answer["groups"][0]
        return group["periods"], [e["aired"] for e in group["episodes"]], group["nextPage"]

    def all_episodes(self, year, month, season=None):
        out, page = [], 1
        while page:
            answer = self.show(season, page, year, month)
            out += [e["aired"] for e in answer["groups"][0]["episodes"]]
            page = answer["groups"][0]["nextPage"]
        return out


class UdalostiTest(Base):
    """A list the size of "Události" (9970 episodes, 2005-01-01 to 2026-11-01)."""

    def setUp(self):
        self.dates = udalosti()
        self.truth = truth(self.dates)
        self.make(self.dates)

    def test_a_chunk_never_crosses_cts_window(self):
        # ČT, 2026-11-01: offset 9909 limit 100 was an error ("Omlouváme se…"), offset 9900 limit 100 gave 70;
        # Události' 2005 is read from such an offset after a bisection
        items, total = ac.CtArchiveClient(post=self.ct).episode_chunk(SHOW, None, 9909, 100)
        self.assertEqual((len(items), total), (61, UDALOSTI_TOTAL))
        self.assertIn("limit: 91, offset: 9909", self.ct.bodies[-1]["query"])
        self.assertEqual(ac.CtArchiveClient(post=self.ct).episode_chunk(SHOW, None, 10000, 1), ([], None))

    def test_default_newest_year_and_month(self):
        answer, count = self.requests(self.show)
        periods, aired, next_page = self.month_of(answer)
        self.assertEqual(periods["years"], list(range(2026, 2004, -1)))
        self.assertEqual((periods["year"], periods["month"]), (2026, 11))
        self.assertEqual(periods["months"], list(self.truth[2026]))
        self.assertEqual(periods["months"], list(range(11, 0, -1)))
        self.assertEqual(aired, self.truth[2026][11][:20])
        self.assertIsNone(next_page)  # 1 November: one or two episodes
        # show + the list's page 1 (it says 9970) + oldest + 2026 read from offset 0 in chunks of 100: no bisection
        chunks_2026 = math.ceil(sum(len(v) for v in self.truth[2026].values()) / 100)
        self.assertEqual(count, 1 + 1 + 1 + chunks_2026)
        self.assertEqual((len(self.ct.probes()), len(self.ct.oldest())), (0, 1))
        self.assertIn("limit: 10, offset: 0, onlyPlayable: true, orderBy: oldest", self.ct.oldest()[0])
        self.assertTrue(all("orderBy: newest" in q for q in self.ct.chunks()))
        _, again = self.requests(self.show)
        self.assertEqual(again, 0)

    def test_a_month_page_by_page_newest_first(self):
        october = self.all_episodes(2026, 10)
        self.assertEqual(october, self.truth[2026][10])
        self.assertGreater(len(october), 20)  # really paged
        first = self.show(page=1, year=2026, month=10)["groups"][0]
        self.assertEqual((len(first["episodes"]), first["nextPage"]), (20, 2))

    def test_every_year_and_month_as_counted_by_hand(self):
        self.show()
        for year in range(2026, 2004, -1):
            with self.subTest(year=year):
                periods, _, _ = self.month_of(self.show(year=year))
                self.assertEqual(periods["months"], list(self.truth[year]))
                self.assertEqual(periods["month"], next(iter(self.truth[year])))  # a year alone: its newest month
                for month in (periods["months"][0], periods["months"][-1]):
                    self.assertEqual(self.all_episodes(year, month), self.truth[year][month])
        seen = sum(len(v) for months in self.truth.values() for v in months.values())
        self.assertEqual(seen, UDALOSTI_TOTAL)
        # going year by year downwards: each year starts where the one above stopped, never a bisection
        self.assertEqual((len(self.ct.probes()), len(self.ct.oldest())), (0, 1))
        self.assertLessEqual(len(self.ct.chunks()), math.ceil(UDALOSTI_TOTAL / 100) + 22)

    def test_jumping_to_a_far_year_bisects_about_log2(self):
        self.show()
        before = len(self.ct.probes())
        answer, count = self.requests(lambda: self.show(year=2005))
        probes = len(self.ct.probes()) - before
        self.assertLessEqual(probes, math.ceil(math.log2(UDALOSTI_TOTAL)))  # 14
        self.assertGreaterEqual(probes, 10)  # it really did bisect
        self.assertEqual(self.month_of(answer)[0]["months"], list(self.truth[2005]))
        self.assertEqual(count, probes + math.ceil(len(sum(self.truth[2005].values(), [])) / 100))
        self.assertLessEqual(count, 20)
        # a second far jump starts narrower: everything seen so far bounds it
        before = len(self.ct.probes())
        self.show(year=2015)
        self.assertLessEqual(len(self.ct.probes()) - before, math.ceil(math.log2(UDALOSTI_TOTAL)))

    def test_one_request_at_a_time_and_never_more_than_100(self):
        client = self.catalog.ct
        held = []
        original = self.ct.__call__

        def post(body):
            held.append(client._lock.locked())
            return original(body)

        client._post = post
        self.show(year=2010)
        self.assertTrue(held and all(held))
        for q in self.ct.episode_requests():
            self.assertLessEqual(int(re.search(r"limit: (\d+)", q)[1]), 100)
            self.assertIn("onlyPlayable: true", q)

    def test_cached_for_the_show_ttl_then_asked_again(self):
        self.show(year=2010)
        _, count = self.requests(lambda: self.show(year=2010, month=3))
        self.assertEqual(count, 0)
        self.clock.t += ac.SHOW_TTL - 1
        _, count = self.requests(lambda: self.show(year=2010))
        self.assertEqual(count, 0)
        self.clock.t += 1
        _, count = self.requests(lambda: self.show(year=2010))
        self.assertGreater(count, 10)  # all of the index expired together

    def test_ct_failing_mid_bisection(self):
        self.show()
        self.ct.fail = {len(self.ct.bodies) + 2 + 5}  # show and page 1 are cached; the 5th probe times out
        with self.assertRaises(ac.CtError):
            self.show(year=2005)
        done = len(self.ct.probes())
        self.ct.fail = set()
        answer, _ = self.requests(lambda: self.show(year=2005))
        self.assertEqual(self.month_of(answer)[0]["months"], list(self.truth[2005]))
        self.assertLessEqual(len(self.ct.probes()) - done, math.ceil(math.log2(UDALOSTI_TOTAL)) - 3)

    def test_ct_failing_while_reading_a_year(self):
        self.ct.fail = {5}  # show, page 1, the first chunk, oldest, then the second chunk of 2026
        with self.assertRaises(ac.CtError):
            self.show()
        self.ct.fail = set()
        periods, aired, _ = self.month_of(self.show())
        self.assertEqual((periods["months"], aired), (list(self.truth[2026]), self.truth[2026][11][:20]))


class PeriodsRulesTest(Base):
    def test_prague_calendar_month_at_year_and_month_boundaries(self):
        dates = spread(150, datetime(2025, 6, 1, 18, tzinfo=timezone.utc), datetime(2025, 12, 30, 18, tzinfo=timezone.utc))
        dates += ["2025-12-31T23:30:00.000Z",  # 00:30 on 1 January 2026 in Prague
                  "2026-03-31T22:30:00.000Z",  # 00:30 on 1 April (summer time, UTC+2)
                  "2026-06-30T21:59:00.000Z"]  # 23:59 on 30 June
        # 2026 needs more than 100 episodes to have months (T51)
        dates += spread(100, datetime(2026, 2, 2, 18, tzinfo=timezone.utc), datetime(2026, 2, 27, 18, tzinfo=timezone.utc))
        self.make(sorted(dates))
        periods, aired, _ = self.month_of(self.show())
        self.assertEqual((periods["years"], periods["year"], periods["months"]), ([2026, 2025], 2026, [6, 4, 2, 1]))
        self.assertEqual(aired, ["2026-06-30T21:59:00Z"])
        self.assertEqual(self.month_of(self.show(year=2026, month=1))[1], ["2025-12-31T23:30:00Z"])
        self.assertEqual(self.month_of(self.show(year=2026, month=4))[1], ["2026-03-31T22:30:00Z"])
        self.assertNotIn("2025-12-31T23:30:00Z", self.all_episodes(2025, 12))

    def gaps(self):
        """Episodes in 2009, 2011 and 2013 (over 100 each: months, T51): 2012 (next to the newest) and 2010 (next to
        the oldest) are empty."""
        dates = []
        for year in (2009, 2011, 2013):
            part = spread(200, datetime(year, 1, 5, 18, tzinfo=timezone.utc), datetime(year, 12, 20, 18, tzinfo=timezone.utc))
            dates += [d for d in part if not d.startswith(f"{year}-04")]
        self.make(dates)
        return truth(dates)

    def test_months_without_episodes(self):
        expected = self.gaps()
        periods = self.show()["groups"][0]["periods"]
        self.assertEqual(periods["years"], [2013, 2012, 2011, 2010, 2009])  # not yet known which are empty
        self.assertEqual(periods["months"], list(expected[2013]))
        self.assertNotIn(4, periods["months"])
        with self.assertRaises(ac.BadPeriod):
            self.show(year=2013, month=4)  # a month of the year without an episode

    def test_an_empty_year_moves_to_the_next_older_one(self):
        expected = self.gaps()
        self.show()
        for empty, landed, years in ((2012, 2011, [2013, 2011, 2010, 2009]), (2010, 2009, [2013, 2011, 2009])):
            with self.subTest(year=empty):
                group = self.show(year=empty)["groups"][0]
                self.assertEqual(group["periods"], {"years": years, "year": landed,
                                                    "months": list(expected[landed]),
                                                    "month": next(iter(expected[landed]))})
                self.assertEqual([e["aired"] for e in group["episodes"]], expected[landed][group["periods"]["month"]][:20])
        # a stale chip of a year known to be empty: the nearest year with episodes (its month dropped), no request
        group, count = self.requests(lambda: self.show(year=2012, month=1)["groups"][0])
        self.assertEqual((group["periods"]["year"], group["periods"]["month"], count), (2011, 12, 0))
        self.assertEqual(self.show(year=2010)["groups"][0]["periods"]["year"], 2009)
        with self.assertRaises(ac.BadPeriod):
            self.show(year=2008)  # outside the range stays unknown

    def test_a_gap_of_several_years_is_known_at_once(self):
        dates = spread(60, datetime(2009, 1, 5, 18, tzinfo=timezone.utc), datetime(2009, 12, 20, 18, tzinfo=timezone.utc))
        dates += spread(60, datetime(2013, 1, 5, 18, tzinfo=timezone.utc), datetime(2013, 12, 20, 18, tzinfo=timezone.utc))
        self.make(dates)
        self.show()
        group = self.show(year=2011)["groups"][0]  # the episode where 2011 would start is of 2009
        self.assertEqual((group["periods"]["year"], group["periods"]["years"]), (2009, [2013, 2009]))
        # 60 a year: shown whole (T51), also after landing from an empty year
        self.assertEqual((group["periods"]["months"], group["periods"]["month"], len(group["episodes"])), ([], None, 20))
        self.assertEqual(group["episodes"][0]["aired"], "2009-12-20T18:00:00Z")

    def test_an_empty_oldest_year_moves_to_the_next_newer_one(self):
        expected = self.gaps()
        self.ct.oldest_only = ["2007-06-01T18:00:00.000Z"]  # the oldest list says 2007, the newest list ends in 2009
        self.assertEqual(self.show()["groups"][0]["periods"]["years"], [2013, 2012, 2011, 2010, 2009, 2008, 2007])
        group = self.show(year=2007)["groups"][0]
        self.assertEqual((group["periods"]["year"], group["periods"]["years"]), (2009, [2013, 2012, 2011, 2010, 2009]))
        self.assertEqual(group["periods"]["months"], list(expected[2009]))
        self.assertTrue(group["episodes"])
        self.assertEqual(self.show(year=2008)["groups"][0]["periods"]["year"], 2009)  # stale chip, no older one

    def test_episodes_without_a_date_are_in_no_month(self):
        dates = spread(600, datetime(2020, 1, 1, 18, tzinfo=timezone.utc), datetime(2022, 12, 31, 18, tzinfo=timezone.utc))
        for i in range(0, 600, 7):
            dates[i] = None
        self.make(dates)
        expected = truth(dates)
        self.show()
        for year in (2022, 2021, 2020):
            for month in expected[year]:
                self.assertEqual(self.all_episodes(year, month), expected[year][month], (year, month))

    def test_up_to_100_episodes_nothing_changes(self):
        dates = spread(101, datetime(2020, 1, 1, 18, tzinfo=timezone.utc), datetime(2022, 12, 31, 18, tzinfo=timezone.utc))
        for total, has in ((100, False), (101, True)):
            with self.subTest(total=total):
                self.make(dates[:total])
                answer, count = self.requests(self.show)
                group = answer["groups"][0]
                self.assertEqual("periods" in group, has)
                if not has:
                    self.assertEqual(count, 2)  # the show and its page, as before T32
                    self.assertEqual([e["aired"] for e in group["episodes"]][:1], [dates[99].replace(".000Z", "Z")])
                    self.assertEqual(group["nextPage"], 2)
                    with self.assertRaises(ac.BadPeriod):
                        self.show(year=2022)
                else:
                    self.assertEqual(group["periods"]["years"], [2022, 2021, 2020])

    def test_a_season_with_periods(self):
        seasons = {"season:a1": spread(50, datetime(2001, 1, 1, 18, tzinfo=timezone.utc),
                                       datetime(2001, 12, 1, 18, tzinfo=timezone.utc)),
                   "season:a2": spread(800, datetime(2010, 1, 1, 18, tzinfo=timezone.utc),
                                       datetime(2012, 6, 30, 18, tzinfo=timezone.utc))}
        self.make(None, seasons)
        first = self.show()["groups"][0]  # the first season: 50 episodes, oldest first as before
        self.assertNotIn("periods", first)
        self.assertEqual(first["episodes"][0]["aired"], seasons["season:a1"][0].replace(".000Z", "Z"))
        second = self.show("season:a2")["groups"][0]
        self.assertEqual((second["season"], second["periods"]["years"], second["periods"]["year"],
                          second["periods"]["month"]), ("season:a2", [2012, 2011, 2010], 2012, 6))
        expected = truth(seasons["season:a2"])
        self.assertEqual(self.all_episodes(2010, 1, "season:a2"), expected[2010][1])
        self.assertTrue(all('seasonId: "season:a2"' in q for q in self.ct.episode_requests()[1:]))

    def test_month_without_year_and_unknown_year(self):
        self.make(udalosti()[-500:])
        for kwargs in ({"month": 3}, {"year": 2004}, {"year": 2027}, {"year": 2026, "month": 12}):
            with self.subTest(**kwargs), self.assertRaises(ac.BadPeriod):
                self.show(**kwargs)

    def test_golden_a_year_alone_selects_its_newest_month(self):
        for case in GOLDEN["periods"]["selectYear"]:
            with self.subTest(case=case["name"]):
                dates = []
                if case["months"]:  # more than 100 episodes in the year: months (T51)
                    per_day = math.ceil(ac.YEAR_MONTHS_MIN / (28 * len(case["months"])))
                    dates += [iso(datetime(2024, month, day, 10 + hour, tzinfo=timezone.utc))
                              for month in case["months"] for day in range(1, 29) for hour in range(per_day)]
                else:  # two a month, as "Hydepark Civilizace": the year whole
                    dates += [iso(datetime(2024, month, day, 20, tzinfo=timezone.utc))
                              for month in range(1, 13) for day in (7, 21)]
                dates += spread(101, datetime(2025, 1, 1, 18, tzinfo=timezone.utc),
                                datetime(2025, 12, 1, 18, tzinfo=timezone.utc))
                self.make(sorted(dates))
                periods = self.show(year=2024)["groups"][0]["periods"]
                self.assertEqual((periods["months"], periods["month"]), (case["months"], case["month"]))

    @staticmethod
    def gateway_shaped(periods):
        """What the gateway sends: these four keys, newest first, the year among the years, and a month among the
        months, or none with no months (a year shown whole, T51)."""
        return isinstance(periods, dict) and list(periods) == ["years", "year", "months", "month"] \
            and all(type(v) is int for v in periods["years"]) and periods["years"] == sorted(periods["years"], reverse=True) \
            and periods["year"] in periods["years"] and isinstance(periods["months"], list) \
            and all(type(m) is int and 1 <= m <= 12 for m in periods["months"]) \
            and periods["months"] == sorted(periods["months"], reverse=True) \
            and (periods["month"] in periods["months"] if periods["months"] else periods["month"] is None)

    def test_golden_chips_have_the_gateway_shape(self):
        self.make(udalosti()[-300:])
        ours = [self.show()["groups"][0]["periods"]]
        self.hydepark()
        ours.append(self.show()["groups"][0]["periods"])
        self.assertEqual((ours[0]["months"][:1], ours[1]["months"]), ([11], []))
        for periods in ours:
            self.assertTrue(self.gateway_shaped(periods), periods)
        # the golden chips also hold what the apps must survive (T51: months missing, not months, a month with
        # empty months); the gateway-shaped ones cover both kinds the gateway sends
        shaped = [c["periods"] for c in GOLDEN["periods"]["chips"] if self.gateway_shaped(c["periods"])]
        self.assertTrue(any(p["months"] for p in shaped))
        self.assertTrue(any(not p["months"] for p in shaped))
        self.assertFalse(self.gateway_shaped({"years": [2026], "year": 2026, "months": [], "month": 3}))


class YearsOnlyTest(Base):
    """T51 (owner 2026-11-02: "Hydepark Civilizace" has two episodes a month): a year of at most 100 episodes is
    shown whole, without months; only a year with more gets month chips. Decided per year."""

    @staticmethod
    def whole(expected, year):
        return [d for eps in expected[year].values() for d in eps]

    def test_a_weekly_show_is_divided_by_years_only(self):
        expected = self.hydepark()
        answer, count = self.requests(self.show)
        periods, aired, next_page = self.month_of(answer)
        self.assertEqual(periods, {"years": list(range(2026, 2014, -1)), "year": 2026, "months": [], "month": None})
        self.assertEqual(aired, self.whole(expected, 2026)[:20])
        self.assertEqual((aired[0], aired[-1], next_page), ("2026-10-21T20:00:00Z", "2026-01-07T20:00:00Z", None))
        self.assertEqual(count, 4)  # show, page 1, oldest, the first chunk (2026 and 2025 in it): as with months
        for year in (2026, 2020, 2015):
            with self.subTest(year=year):
                self.assertEqual(self.all_episodes(year, None), self.whole(expected, year))
                self.assertEqual(len(self.whole(expected, year)), 20 if year == 2026 else 24)

    def test_a_month_for_a_year_shown_whole_is_ignored(self):
        self.hydepark()
        plain = self.show(year=2020)["groups"][0]
        for month in (3, 4, 12):  # one it has, and every month has episodes: still not a month list
            with self.subTest(month=month):
                group, count = self.requests(lambda: self.show(year=2020, month=month)["groups"][0])
                self.assertEqual((group, count), (plain, 0))
        self.assertEqual(self.show(page=2, year=2020, month=5)["groups"][0]["episodes"],
                         self.show(page=2, year=2020)["groups"][0]["episodes"])

    def test_paged_like_a_month(self):
        self.hydepark()
        first = self.show(year=2019)["groups"][0]
        second = self.show(page=2, year=2019)["groups"][0]
        self.assertEqual((len(first["episodes"]), first["nextPage"]), (20, 2))
        self.assertEqual((len(second["episodes"]), second["nextPage"]), (4, None))
        self.assertEqual(second["episodes"][-1]["aired"], "2019-01-07T20:00:00Z")
        self.assertEqual(self.show(page=3, year=2019)["groups"][0]["episodes"], [])

    def test_100_a_year_whole_101_months(self):
        for count, months in ((100, []), (101, [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1])):
            with self.subTest(count=count):
                dates = spread(count, datetime(2024, 1, 3, 18, tzinfo=timezone.utc), datetime(2024, 12, 28, 18, tzinfo=timezone.utc))
                dates += spread(150, datetime(2025, 1, 3, 18, tzinfo=timezone.utc), datetime(2025, 12, 28, 18, tzinfo=timezone.utc))
                self.make(dates)
                periods, aired, _ = self.month_of(self.show(year=2024))
                self.assertEqual(periods["months"], months)
                self.assertEqual(periods["month"], months[0] if months else None)
                if months:
                    self.assertEqual(aired, truth(dates)[2024][12])  # December's few, not the year's 20
                    self.assertEqual(len(self.all_episodes(2024, 12)), len(truth(dates)[2024][12]))
                else:
                    self.assertEqual(len(aired), 20)
                    self.assertEqual(len(self.all_episodes(2024, None)), 100)  # every page, nothing capped

    def test_daily_one_year_weekly_another(self):
        dates = spread(365, datetime(2024, 1, 1, 18, tzinfo=timezone.utc), datetime(2024, 12, 30, 18, tzinfo=timezone.utc))
        dates += [iso(datetime(2025, 1, 6, 19, tzinfo=timezone.utc) + timedelta(weeks=n)) for n in range(52)]
        dates += spread(200, datetime(2026, 1, 1, 18, tzinfo=timezone.utc), datetime(2026, 10, 30, 18, tzinfo=timezone.utc))
        self.make(dates)
        expected = truth(dates)
        default = self.month_of(self.show())[0]
        self.assertEqual((default["year"], default["month"], default["months"]), (2026, 10, list(expected[2026])))
        weekly, aired, next_page = self.month_of(self.show(year=2025, month=10))  # a month chip from 2026: ignored
        self.assertEqual((weekly["year"], weekly["months"], weekly["month"], next_page), (2025, [], None, 2))
        self.assertEqual(aired, self.whole(expected, 2025)[:20])
        self.assertEqual(len(self.all_episodes(2025, None)), 52)
        daily = self.month_of(self.show(year=2024))[0]
        self.assertEqual((daily["months"], daily["month"]), (list(range(12, 0, -1)), 12))
        with self.assertRaises(ac.BadPeriod):  # a daily year still checks its months
            self.show(year=2024, month=13)
        self.assertEqual(default["years"], [2026, 2025, 2024])


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
        self.world.gw.catalog._known.clear()

    def get(self, query, ident="ct:" + NEWS_ID):
        return self.world.get("/dvb/archive/show/" + ident, query)

    def test_inputs(self):
        cases = [({}, 200), ({"year": "2026"}, 200), ({"year": "2026", "month": "10"}, 200),
                 ({"year": "2025", "month": "1"}, 200), ({"year": "2024", "month": "11"}, 200),
                 ({"year": "2024", "month": "12", "page": "2"}, 200),
                 ({"year": "2023"}, 400), ({"year": "2027"}, 400), ({"year": "2026", "month": "11"}, 400),
                 ({"year": "2024", "month": "10"}, 200),  # 2024: 42 episodes, shown whole, the month ignored (T51)
                 ({"year": "2024", "page": "3"}, 200), ({"month": "10"}, 400), ({"year": "26"}, 400),
                 ({"year": "abcd"}, 400), ({"year": "2026", "month": "13"}, 400), ({"year": "2026", "month": "0"}, 400),
                 ({"year": "2026", "month": "010"}, 400), ({"year": "2026", "page": "11"}, 400)]
        for query, status in cases:
            with self.subTest(query=query):
                self.assertEqual(self.get(query)[0].status, status)
        self.assertEqual(self.get({"year": "2026"}, "ct:" + SERIES_ID)[0].status, 400)  # 26 episodes: no periods
        self.assertEqual(self.get({"year": "2026"}, "rec:tichahora")[0].status, 400)
        resp, body = self.get({"year": "2023"})
        self.assertEqual(body["error"], "Pořad nemá díly z tohoto roku nebo měsíce")
        for query in ({"year": "2026", "month": "0"}, {"month": "10"}, {"year": "2026", "month": "13"}):
            self.assertEqual(self.get(query)[1]["error"], "Měsíc musí být 1 až 12 a s rokem", query)
        self.assertEqual(self.get({"year": "0999"})[1]["error"], "Neplatný rok")

    def test_answers(self):
        _, body = self.get({})
        group = body["groups"][0]
        self.assertEqual(group["periods"], {"years": [2026, 2025, 2024], "year": 2026,
                                            "months": list(range(10, 0, -1)), "month": 10})
        self.assertEqual((len(group["episodes"]), group["nextPage"]), (20, 2))
        _, second = self.get({"page": "2"})
        self.assertEqual([e["title"] for e in second["groups"][0]["episodes"]][-1], "Večerní zpravodaj 01. 10. 2026")
        self.assertIsNone(second["groups"][0]["nextPage"])
        # 2024 has 42 episodes (20 November to 31 December): the whole year, newest first, paged (T51)
        _, older = self.get({"year": "2024"})
        self.assertEqual((older["groups"][0]["periods"]["months"], older["groups"][0]["periods"]["month"]), ([], None))
        whole = []
        for page in (1, 2, 3):
            _, part = self.get({"year": "2024", "page": str(page)})
            whole += [e["title"] for e in part["groups"][0]["episodes"]]
            self.assertEqual(part["groups"][0]["nextPage"], page + 1 if page < 3 else None)
        self.assertEqual(len(whole), 42)
        self.assertEqual((whole[0], whole[20], whole[-1]), ("Večerní zpravodaj 31. 12. 2024",
                                                           "Večerní zpravodaj 11. 12. 2024",
                                                           "Večerní zpravodaj 20. 11. 2024"))
        _, november = self.get({"year": "2024", "month": "11"})  # a stale month chip: ignored
        self.assertEqual(november["groups"][0], older["groups"][0])
        self.assertNotEqual(body["groups"][0]["episodes"], older["groups"][0]["episodes"])
        _, daily = self.get({"year": "2025"})  # 365 episodes: months
        self.assertEqual((daily["groups"][0]["periods"]["months"], daily["groups"][0]["periods"]["month"]),
                         (list(range(12, 0, -1)), 12))

    def test_never_an_empty_month(self):
        _, body = self.get({"year": "2025"})
        self.assertTrue(body["groups"][0]["periods"]["months"])
        self.assertIsNotNone(body["groups"][0]["periods"]["month"])

    def test_ct_down_mid_way(self):
        self.get({})
        self.world.gw.catalog.ct._periods.clear()
        self.world.ct.error = api.StreamError("ČT GraphQL: timed out")
        resp, body = self.get({"year": "2025"})
        self.assertEqual((resp.status, body["error"]), (502, "iVysílání teď neodpovídá"))


if __name__ == "__main__":
    unittest.main()
