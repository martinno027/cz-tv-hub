"""The apps' Archiv (spec/dvb-gateway.md "Archive"): iVysílání shows from ČT's public GraphQL API and the
household's own recordings, in ČT's categories, one card per normalised title (iVysílání first).

- ČT is asked like ``api.py`` does (the same endpoint and honest User-Agent, never the website), one request at a
  time, answers cached 6 h (rows, sub-genres), 1 h (a show, its episodes), 10 min (a search); at most 3 pages of
  20 shows per row. Nothing of ČT's is stored on disk.
- An episode list of more than 100 gets year/month periods (T32, ``EpisodePeriods``): ČT has no date filter, so a
  year is found by bisection over its date-ordered list and read in chunks of 100, all kept for the show TTL. A
  year of at most 100 episodes is shown whole, without month chips (T51: ``wholeYear``, one month for older app builds).
- Our recordings are ended programmes of the continuous recording (the gateway passes them in); the same cleaned
  title is one show. Their categories come from the stored TMDb match or the DVB genre, computed per answer and
  never stored (golden ``spec/fixtures/archive_cases.json``).
- Card ids: ``ct:<ČT show id>``, ``rec:<normalised title>``.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .. import api as ct_api
from ..channels import CHANNELS as CT_CHANNELS
from . import details as dt
from .epg import PRAGUE, genre_text
from .tmdbstore import Entry, TmdbStore

ROW_TTL = 6 * 3600.0
SHOW_TTL = 3600.0
SEARCH_TTL = 600.0
CT_CACHE_MAX = 400
CT_WAIT = 15.0  # for the one ČT request slot
CT_TIMEOUT = 10.0
CT_MAX_BYTES = 4 * 1024 * 1024  # a page of 20 shows is a few kB; anything this large is not an answer
PAGE = 20  # ČT shows per page
MAX_PAGES = 3  # ≤ 60 shows of a row or search, no crawling
HOME_REC = 10  # our shows mixed into a home row (and per category page)
EPISODE_PAGE = 20
MAX_EPISODE_PAGES = 10
PERIODS_OVER = 100  # more playable episodes than this: year and month chips (T32)
YEAR_MONTHS_MIN = 101  # a year with fewer episodes is shown whole, without month chips (T51: ≤ 5 pages)
CT_LIMIT = 100  # ČT's largest `limit` (2026-11-01: 100 works, 200 is a validation error)
OLDEST_PROBE = 10  # the oldest episodes asked for the oldest year
YEAR_MAX_CHUNKS = 30  # ≤ 3000 episodes of one year read (Události: ~475 a year)
PERIODS_MAX = 32  # episode lists with periods kept in memory
RECORDINGS_PAGE = 20
MAX_RECORDINGS_PAGES = 50
LISTED_SECONDS = 7 * 86400  # a ČT title counts as "listed by the archive" this long after an answer showed it
LISTED_MAX = 5000
KNOWN_MAX = 2000  # ČT cards the archive showed, so a "both" show page works while ČT is down
RECORDINGS_TTL = 60.0
REC_GROUP = "Nahráno z antény"
CT_GROUP = "Díly"
CT_IMAGE_RE = re.compile(r"^https://[A-Za-z0-9.-]+\.ceskatelevize\.cz/[^\s\"<>]{1,500}$")
CT_ID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
IDEC_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")  # an episode's id is its IDEC (the VOD path plays it)
SEASON_RE = re.compile(r"^[A-Za-z0-9:_-]{1,80}$")
# ČT answers an error when offset + limit is over 10 000 (measured 2026-11-01 on Události, 9970 episodes: offset
# 9900 limit 100 gives 70, offset 9909 limit 100 an error): a search window. Episodes past it cannot be read in
# the newest-first list.
CT_WINDOW = 10000
GROUP_PREFIX = "group:"  # ČT's groups of other versions (sign language, subtitles), not seasons
_SPACES = re.compile(r"[ \t\r\n\f\v]+")
SORTS = {"popular": "popular_previous_seven_days", "newest": "catchup_broadcast", "az": "alphabet"}

# ČT's 14 categories (ids and slugs as iVysílání has them, 2026-10-25), in the apps' row order.
CATEGORIES: list[tuple[str, str, str]] = [
    ("3947", "3947-filmy", "Filmy"),
    ("3976", "3976-serialy", "Seriály"),
    ("4003", "4003-dokumenty", "Dokumenty"),
    ("4124", "4124-zpravodajstvi", "Zpravodajství"),
    ("4142", "4142-sport", "Sport"),
    ("4118", "4118-pro-deti", "Pro děti"),
    ("4068", "4068-zabava", "Zábava"),
    ("4079", "4079-historie", "Historie"),
    ("4106", "4106-priroda", "Příroda"),
    ("4029", "4029-kultura", "Kultura"),
    ("4093", "4093-spolecnost", "Společnost"),
    ("4055", "4055-rady-a-recepty", "Rady a recepty"),
    ("4191", "4191-spiritualita", "Spiritualita"),
    ("4732", "4732-archiv", "Archiv"),
]
BY_SLUG = {slug: (cid, title) for cid, slug, title in CATEGORIES}
TOP_IDS = {cid for cid, _, _ in CATEGORIES}
RECORDINGS = "recordings"
RECORDINGS_TITLE = "Naše nahrávky"
FILMY, SERIALY, DOKUMENTY, KULTURA, RADY, ZABAVA, HISTORIE, SPOLECNOST, DETI, ZPRAVY, SPORT = (
    "3947-filmy", "3976-serialy", "4003-dokumenty", "4029-kultura", "4055-rady-a-recepty", "4068-zabava",
    "4079-historie", "4093-spolecnost", "4118-pro-deti", "4124-zpravodajstvi", "4142-sport")
# TMDb genre ids (stable across languages)
DOCUMENTARY, NEWS, TALK, REALITY, KIDS, ANIMATION, FAMILY, HISTORY, MUSIC = 99, 10763, 10767, 10764, 10762, 16, \
    10751, 36, 10402
DVB_CATEGORIES = {"Zprávy": ZPRAVY, "Zábava": ZABAVA, "Sport": SPORT, "Pro děti": DETI, "Hudba": KULTURA,
                  "Kultura": KULTURA, "Společnost": SPOLECNOST, "Vzdělávání / věda": DOKUMENTY, "Volný čas": RADY}
FILM_GENRE = "Film / drama"
# ČT sub-genre titles (normalised) → TMDb genre ids; "filmy"/"seriály" go by the TMDb kind (golden "subgenres").
# Any other sub-genre (České, Zahraniční, Pohádky, Klasika, the decades of Archiv …) has no TMDb counterpart.
SUBGENRE_TMDB = {"krimi": {80}, "komedie": {35}, "drama": {18}, "romanticke": {10749}, "thriller": {53},
                 "thrillery": {53}, "animovane": {16}, "valecne": {10752}, "historicke": {36}, "historie": {36},
                 "rodinne": {10751}, "hudba": {10402}, "hudebni": {10402}}
SUBGENRE_KIND = {"filmy": "movie", "serialy": "tv"}

SHOW_FIELDS = "id title year showType playableEpisodeCount durationText images { card } flatGenres { id title }"
SUBGENRES_QUERY = "query($id: String!) { category(categoryId: $id) { title categoryBlocks { title categoryId } } }"
ROW_QUERY = ("query($id: String!) { showFindByGenre(categoryId: $id, limit: %d, offset: %d, orderBy: %s%s) "
             "{ totalCount items { " + SHOW_FIELDS + " } } }")
SEARCH_QUERY = ("query($q: String!) { searchShows(keyword: $q, limit: %d, offset: %d) "
                "{ items { id title images { card } } } }")
SHOW_QUERY = ("query($id: String!) { programme(legacySidpOrIdp: $id) { ... on Show { " + SHOW_FIELDS +
              " description shortDescription creators countriesOfOrigin { title } seasons { id title } } } }")
EPISODES_QUERY = ("query($id: String!) { programme(legacySidpOrIdp: $id) { ... on Show { episodes(limit: %d, "
                  "offset: %d, onlyPlayable: true, orderBy: %s%s) { totalCount items { id title description "
                  "duration playable date { datetime channelName } } } } } }")


class CtError(Exception):
    user_message = "iVysílání teď neodpovídá"


class NotFound(Exception):
    """No such show (ours or ČT's)."""


class CtNotFound(CtError, NotFound):
    user_message = "Pořad nenalezen"


class BadInput(Exception):
    """A sub-genre or season that is not the category's or show's."""


class BadPeriod(BadInput):
    """A year or month the show's episode list does not have (or a list without periods)."""


# -- rules (golden spec/fixtures/archive_cases.json) -----------------------------------------------
def title_key(title: str) -> str:
    """The key that makes an iVysílání show and our recordings one card ("normalise")."""
    return dt.normalise(dt.clean_title(title, "tv"))


def card_key(key: str) -> str:
    """``title_key`` as ASCII for ``rec:<id>``: ``normalise`` keeps letters it cannot decompose ("ł", "ø",
    Cyrillic), so those become ``u`` + 4 hex digits and the id is safe unescaped in a path."""
    return "".join(c if c.isascii() else f"u{ord(c):04x}" for c in key)


_CT_CHANNEL_IDS = {" ".join(c.name.split()): c.id for c in CT_CHANNELS}


def ct_channel_id(name: str) -> str | None:
    """ČT's channel name of an airing ("ČT1", "ČT :D") → the API id (CH_1 …) by channels.py; else None."""
    return _CT_CHANNEL_IDS.get(" ".join((name or "").split()))


def _order(slug: str) -> int:
    return int(slug.split("-", 1)[0])


def categories_for(tmdb: dict | None, dvb_genre: str = "", title: str = "") -> list[str]:
    """ČT category slugs of one of our shows: by its TMDb match (kind + genre ids), else its DVB genre."""
    if tmdb is not None:
        genres = set(tmdb.get("genres") or ())
        if NEWS in genres:
            return [ZPRAVY]
        if DOCUMENTARY in genres:
            base = DOKUMENTY
        elif genres & {TALK, REALITY}:
            base = ZABAVA
        else:
            base = FILMY if tmdb.get("kind") == "movie" else SERIALY
        extra = set()
        if KIDS in genres or {ANIMATION, FAMILY} <= genres:
            extra.add(DETI)
        if HISTORY in genres:
            extra.add(HISTORIE)
        if MUSIC in genres:
            extra.add(KULTURA)
        return [base] + sorted(extra - {base}, key=_order)
    if dvb_genre == FILM_GENRE:
        return [SERIALY if dt.guess_kind(dt.Programme(title)) == "tv" else FILMY]
    found = DVB_CATEGORIES.get(dvb_genre)
    return [found] if found else []


def in_subgenre(subgenre_title: str, tmdb: dict | None) -> bool:
    """Whether one of our shows (already in the sub-genre's category) is also listed in that ČT sub-genre: by
    its stored TMDb match only (``{"kind", "genres"}``); no match, or a title without a TMDb counterpart: no."""
    if tmdb is None:
        return False
    key = dt.normalise(subgenre_title)
    if key in SUBGENRE_KIND:
        return tmdb.get("kind") == SUBGENRE_KIND[key]
    return bool(SUBGENRE_TMDB.get(key, set()) & set(tmdb.get("genres") or ()))


# -- ČT ------------------------------------------------------------------------------------------
def post_graphql(body: dict, timeout: float = CT_TIMEOUT,
                 urlopen: Callable[..., Any] = urllib.request.urlopen) -> Any:
    """ČT's GraphQL as ``api.py`` asks it (same URL and User-Agent), with the answer's size capped."""
    request = urllib.request.Request(ct_api.GRAPHQL_URL, data=json.dumps(body).encode("utf-8"),
                                     headers={"User-Agent": ct_api.USER_AGENT, "Accept": "application/json",
                                              "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(CT_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise CtError(f"ČT GraphQL: HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CtError(f"ČT GraphQL: {type(exc).__name__}: {exc}") from None
    if len(data) > CT_MAX_BYTES:
        raise CtError(f"ČT GraphQL: larger than {CT_MAX_BYTES} bytes")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise CtError("ČT GraphQL: not JSON") from None


class CtArchiveClient:
    """The few GraphQL queries the archive needs (shapes found 2026-10-25, see the spec), cached."""

    def __init__(self, post: Callable[[dict], Any] | None = None, clock: Callable[[], float] = time.monotonic,
                 timeout: float = CT_TIMEOUT, image_re: re.Pattern = CT_IMAGE_RE):
        self._post = post or (lambda body: post_graphql(body, timeout))
        self.image_re = image_re  # card images only from ČT's own https hosts (tests: an invented host)
        self.clock = clock
        self._lock = threading.Lock()  # one ČT request at a time
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, float, Any]] = OrderedDict()
        self._periods: OrderedDict[str, EpisodePeriods] = OrderedDict()

    def _cached(self, key: str) -> tuple[bool, Any]:
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and self.clock() - hit[0] < hit[1]:
                return True, hit[2]
        return False, None

    def _query(self, key: str, ttl: float, query: str, variables: dict) -> dict:
        found, value = self._cached(key)
        if found:
            return value
        fresh, payload = self._fetch(query, variables, key)
        if not fresh:  # another thread fetched it while this one waited for the slot
            return payload
        with self._cache_lock:
            now = self.clock()
            self._cache[key] = (now, ttl, payload)
            self._cache.move_to_end(key)
            for k in [k for k, v in self._cache.items() if now - v[0] >= v[1]]:
                del self._cache[k]
            while len(self._cache) > CT_CACHE_MAX:
                self._cache.popitem(last=False)
        return payload

    def _fetch(self, query: str, variables: dict, key: str | None = None) -> tuple[bool, dict]:
        """One ČT request through the one request slot (the archive's only pacing) → (fresh, payload); with
        ``key`` answered from the cache instead (fresh False) when another thread fetched it meanwhile."""
        if not self._lock.acquire(timeout=CT_WAIT):
            raise CtError("another ČT request is still running")
        try:
            if key is not None:
                found, value = self._cached(key)
                if found:
                    return False, value
            try:
                data = self._post({"query": query, "variables": variables})
            except ct_api.StreamError as exc:  # a post built on api.py's helpers
                raise CtError(f"ČT GraphQL: {exc}") from None
        finally:
            self._lock.release()
        if not isinstance(data, dict):
            raise CtError("ČT GraphQL: unexpected answer")
        errors = data.get("errors") if isinstance(data.get("errors"), list) else []
        codes = {(e.get("extensions") or {}).get("code") for e in errors if isinstance(e, dict)}
        if "NOT_FOUND" in codes:
            raise CtNotFound("ČT: not found")
        payload = data.get("data")
        if not isinstance(payload, dict) or errors:
            raise CtError(f"ČT GraphQL: {sorted(str(c) for c in codes) or 'no data'}")
        return True, payload

    def subgenres(self, category_id: str) -> list[dict]:
        data = self._query(f"sub:{category_id}", ROW_TTL, SUBGENRES_QUERY, {"id": category_id})
        category = data.get("category") if isinstance(data.get("category"), dict) else {}
        out, seen = [], set()
        for block in category.get("categoryBlocks") or [] if isinstance(category.get("categoryBlocks"), list) else []:
            cid = block.get("categoryId") if isinstance(block, dict) else None
            title = block.get("title") if isinstance(block, dict) else None
            if isinstance(cid, str) and CT_ID_RE.match(cid) and isinstance(title, str) and title and cid not in seen:
                seen.add(cid)
                out.append({"id": cid, "title": title})
        return out

    def row(self, category_id: str, sort: str, page: int) -> tuple[list[dict], int]:
        """One page of a category or sub-genre: (shows, ČT's total)."""
        order = SORTS[sort]
        query = ROW_QUERY % (PAGE, (page - 1) * PAGE, order, ", order: asc" if sort == "az" else "")
        data = self._query(f"row:{category_id}:{sort}:{page}", ROW_TTL, query, {"id": category_id})
        found = data.get("showFindByGenre") if isinstance(data.get("showFindByGenre"), dict) else {}
        total = found.get("totalCount")
        shows = [s for s in (parse_show(i, self.image_re) for i in _items(found)) if s is not None]
        return shows, total if isinstance(total, int) and not isinstance(total, bool) else len(shows)

    def search(self, keyword: str, page: int) -> list[dict]:
        query = SEARCH_QUERY % (PAGE, (page - 1) * PAGE)
        data = self._query("search:" + json.dumps([keyword, page], ensure_ascii=False), SEARCH_TTL, query,
                           {"q": keyword})
        found = data.get("searchShows") if isinstance(data.get("searchShows"), dict) else {}
        return [s for s in (parse_show(i, self.image_re) for i in _items(found)) if s is not None]

    def show(self, show_id: str) -> dict:
        data = self._query(f"show:{show_id}", SHOW_TTL, SHOW_QUERY, {"id": show_id})
        raw = data.get("programme")
        show = parse_show(raw, self.image_re)
        if show is None:
            raise CtNotFound("ČT: not a show")
        show["description"] = _text(raw.get("description")) or _text(raw.get("shortDescription"))
        show["creators"] = _text(raw.get("creators"))
        countries = raw.get("countriesOfOrigin") if isinstance(raw.get("countriesOfOrigin"), list) else []
        show["countries"] = [c["title"] for c in countries if isinstance(c, dict) and _text(c.get("title"))]
        seasons = raw.get("seasons") if isinstance(raw.get("seasons"), list) else []
        # Only real seasons ("season:…", "I. řada"). ČT also lists groups of other versions of the same episodes
        # ("group:…", e.g. Události's "Znakový jazyk a skryté titulky", 2026-11-01): taken as a season, the page
        # showed only those versions (since 2021) instead of the whole list (since 2005).
        show["seasons"] = [{"id": s["id"], "title": s["title"]} for s in seasons if isinstance(s, dict)
                           and isinstance(s.get("id"), str) and SEASON_RE.match(s["id"])
                           and not s["id"].startswith(GROUP_PREFIX) and _text(s.get("title"))]
        return show

    def episode_chunk(self, show_id: str, season: str | None, offset: int, limit: int,
                      oldest: bool = False) -> tuple[list[dict | None], int | None]:
        """``limit`` episodes from ``offset``, uncached (``EpisodePeriods`` keeps what it learns): one entry per
        position, None where ČT's item is not an episode we can show; ČT's total or None."""
        season_arg = f", seasonId: {json.dumps(season)}" if season else ""
        limit = min(limit, CT_WINDOW - offset)  # ČT refuses offset + limit over its window (an error, not fewer)
        if limit <= 0:
            return [], None
        query = EPISODES_QUERY % (limit, offset, "oldest" if oldest else "newest", season_arg)
        _, data = self._fetch(query, {"id": show_id})
        raw = data.get("programme") if isinstance(data.get("programme"), dict) else {}
        found = raw.get("episodes") if isinstance(raw.get("episodes"), dict) else {}
        total = found.get("totalCount")
        return [parse_episode(i) for i in _items(found)], \
            total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None

    def periods(self, show_id: str, season: str | None) -> "EpisodePeriods":
        """The year/month index of one episode list, kept for the show TTL (all of it expires together)."""
        key = f"{show_id}:{season}"
        with self._cache_lock:
            now = self.clock()
            for k in [k for k, v in self._periods.items() if now - v.created >= SHOW_TTL]:
                del self._periods[k]
            found = self._periods.get(key)
            if found is None:
                found = self._periods[key] = EpisodePeriods(self, show_id, season, now)
                while len(self._periods) > PERIODS_MAX:
                    self._periods.popitem(last=False)
            self._periods.move_to_end(key)
            return found

    def episodes(self, show_id: str, season: str | None, page: int, oldest: bool) -> tuple[list[dict], int]:
        season_arg = f", seasonId: {json.dumps(season)}" if season else ""
        query = EPISODES_QUERY % (EPISODE_PAGE, (page - 1) * EPISODE_PAGE, "oldest" if oldest else "newest",
                                  season_arg)
        data = self._query(f"episodes:{show_id}:{season}:{page}:{oldest}", SHOW_TTL, query, {"id": show_id})
        raw = data.get("programme") if isinstance(data.get("programme"), dict) else {}
        found = raw.get("episodes") if isinstance(raw.get("episodes"), dict) else {}
        total = found.get("totalCount")
        items = [e for e in map(parse_episode, _items(found)) if e is not None]
        return items, total if isinstance(total, int) and not isinstance(total, bool) else len(items)


def local_month(episode: dict | None) -> tuple[int, int] | None:
    """(year, month) of an episode's airing in Prague's calendar (ČT's own: a programme at 00:30 on 1 January
    belongs to the new year, as iVysílání lists it), None without a date."""
    aired = episode.get("aired") if episode else None
    if not aired:
        return None
    local = datetime.strptime(aired, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(PRAGUE)
    return local.year, local.month


class EpisodePeriods:
    """Years and months of a long ČT episode list (spec "Year and month (T32)"). ČT has no date filter, but its
    ``newest`` list is ordered by airing, so a year starts at the first offset whose episode is that year or older
    (bisection with ``limit: 1``), and is read in chunks of 100 from there until an older episode shows up. Every
    episode seen is kept by its offset, so later bisections start narrower and the year below the one just read
    starts where that read stopped (no requests). Episodes without a date are left out of every month."""

    def __init__(self, ct: CtArchiveClient, show_id: str, season: str | None, created: float):
        self.ct, self.show_id, self.season, self.created = ct, show_id, season, created
        self.lock = threading.Lock()
        self.total: int | None = None
        self.known: dict[int, dict | None] = {}  # offset in the newest-first list → episode (None: not one)
        self.newest_year: int | None = None
        self.oldest_year: int | None = None
        self.read: dict[int, OrderedDict[int, list[dict]]] = {}  # year → month → episodes, newest first
        self.empty: set[int] = set()  # years of the range found to have no episode (left out of ``years``)

    def _chunk(self, offset: int, limit: int) -> None:
        items, total = self.ct.episode_chunk(self.show_id, self.season, offset, limit)
        if total is not None:
            self.total = total
        for i, item in enumerate(items):
            self.known[offset + i] = item
        if len(items) < limit and self.total is not None:  # the list ended earlier than ČT's total said
            self.total = min(self.total, offset + len(items))

    def _year_at(self, offset: int) -> int | None:
        found = local_month(self.known.get(offset))
        return found[0] if found else None

    def years(self) -> list[int]:
        """Newest first, every year from the newest to the oldest episode's; [] = no periods (≤ 100 episodes or
        no dates). Years found empty are left out (``months`` finds them)."""
        with self.lock:
            if self.total is None:
                self._chunk(0, CT_LIMIT)  # the newest year's first chunk, and ČT's total
            if self.total is None or self.total <= PERIODS_OVER:
                return []
            if self.newest_year is None:
                dated = [y for y in (self._year_at(i) for i in range(min(CT_LIMIT, self.total))) if y]
                self.newest_year = max(dated) if dated else None
            if self.newest_year is None:
                return []
            if self.oldest_year is None:
                # a few, not one: the oldest may have no date (the same single request either way)
                # (only for the range: offsets are always those of the newest-first list)
                items, _ = self.ct.episode_chunk(self.show_id, self.season, 0, OLDEST_PROBE, oldest=True)
                oldest = next((m for m in map(local_month, items) if m), None)
                known = [y for y in (self._year_at(i) for i in self.known) if y]
                self.oldest_year = min([oldest[0]] if oldest else known)
            return [y for y in range(self.newest_year, min(self.oldest_year, self.newest_year) - 1, -1)
                    if y not in self.empty]

    def in_range(self, year: int) -> bool:
        return self.newest_year is not None and self.oldest_year is not None and \
            min(self.oldest_year, self.newest_year) <= year <= self.newest_year

    def _start(self, year: int) -> int:
        """The first offset whose episode is of ``year`` or older (bisection over what is not known yet)."""
        if year >= (self.newest_year or year):
            return 0
        lo, hi = 0, self.total or 0
        for offset in self.known:
            y = self._year_at(offset)
            if y is None:
                continue
            if y > year:
                lo = max(lo, offset + 1)
            else:
                hi = min(hi, offset)
        while lo < hi:
            mid = (lo + hi) // 2
            if mid not in self.known:
                self._chunk(mid, 1)
                if mid not in self.known:  # past the list's real end
                    hi = mid
                    continue
            y = self._year_at(mid)
            if y is not None and y > year:
                lo = mid + 1
            else:  # this year or older; an episode without a date starts the read earlier, never later
                hi = mid
        return lo

    def months(self, year: int) -> "OrderedDict[int, list[dict]]":
        """The year's months that have an episode, newest first, each with its episodes newest first."""
        with self.lock:
            if year in self.read:
                return self.read[year]
            start = self._start(year)
            offset, chunks, found = start, 0, []
            while self.total is None or offset < self.total:
                if offset not in self.known:
                    if chunks >= YEAR_MAX_CHUNKS:
                        break
                    self._chunk(offset, CT_LIMIT)
                    chunks += 1
                    if offset not in self.known:
                        break
                item = self.known[offset]
                y = self._year_at(offset)
                if y is not None and y < year:
                    break
                if y == year:
                    found.append(item)
                offset += 1
            if not found:
                # Empty, and so is every newer year up to the episode just before its start. The older ones down
                # to the next episode cost nothing when the caller tries them: their start and chunk are known.
                newer = self._year_at(start - 1) if start > 0 else None
                self.empty.update(range(year, newer if newer is not None and newer > year else year + 1))
            by_month: dict[int, list[dict]] = {}
            for item in found:
                by_month.setdefault(local_month(item)[1], []).append(item)
            ordered: OrderedDict[int, list[dict]] = OrderedDict()
            for month in sorted(by_month, reverse=True):
                ordered[month] = sorted(by_month[month], key=lambda e: e["aired"], reverse=True)
            self.read[year] = ordered
            return ordered


def _nearest(year: int, years: list[int]) -> int:
    """The next older year of ``years`` (newest first), else the next newer one."""
    return next((y for y in years if y < year), None) or min(y for y in years if y > year)


def _items(found: dict) -> list:
    items = found.get("items")
    return items if isinstance(items, list) else []


def _text(value: Any) -> str:
    """ČT's text with runs of ASCII white space as one space; its no-break spaces (Czech typography) stay."""
    return _SPACES.sub(" ", value).strip(" ") if isinstance(value, str) else ""


def parse_show(item: Any, image_re: re.Pattern = CT_IMAGE_RE) -> dict | None:
    """A ČT Show (or search ShowCard) in a neutral shape; what ČT did not send stays None."""
    if not isinstance(item, dict):
        return None
    sid, title = item.get("id"), _text(item.get("title"))
    if not isinstance(sid, str) or not CT_ID_RE.match(sid) or not title:
        return None
    year = item.get("year")
    show_type = item.get("showType")
    images = item.get("images") if isinstance(item.get("images"), dict) else {}
    image = images.get("card")
    genres = item.get("flatGenres") if isinstance(item.get("flatGenres"), list) else []
    count = item.get("playableEpisodeCount")
    duration = re.match(r"^(\d{1,4})\s*min", _text(item.get("durationText")))
    return {
        "id": sid,
        "title": title,
        "year": int(year) if isinstance(year, str) and re.match(r"^\d{4}$", year) else None,
        "kind": {"movie": "movie", "series": "series"}.get(show_type) if isinstance(show_type, str) else None,
        "image": image if isinstance(image, str) and image_re.match(image) else None,
        "genres": [(g["id"], _text(g.get("title"))) for g in genres if isinstance(g, dict)
                   and isinstance(g.get("id"), str) and _text(g.get("title"))],
        "episodeCount": count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None,
        "durationMinutes": int(duration.group(1)) if duration else None,
    }


def parse_episode(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    idec, title = item.get("id"), _text(item.get("title"))
    if not isinstance(idec, str) or not IDEC_RE.match(idec) or not title:
        return None
    date = item.get("date") if isinstance(item.get("date"), dict) else {}
    duration = item.get("duration")
    return {
        "source": "ct",
        "idec": idec,
        "title": title,
        "description": _text(item.get("description")),
        "durationSeconds": int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        and duration > 0 else 0,
        "aired": _iso(date.get("datetime")),
        "channel": _text(date.get("channelName")),
        "channelId": ct_channel_id(_text(date.get("channelName"))),
        "playable": item.get("playable") is True,
    }


def _iso(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- our recordings ------------------------------------------------------------------------------
@dataclass(frozen=True)
class RecProgramme:
    """An ended, completely recorded programme (the gateway's EPG + continuous recording)."""

    rid: str
    channel: str  # channel name, as the apps show it
    title: str
    description: str
    genre: tuple[int, ...]
    start: int
    end: int


@dataclass
class RecShow:
    key: str
    title: str
    episodes: list[RecProgramme] = field(default_factory=list)  # newest first

    @property
    def ident(self) -> str:
        return card_key(self.key)

    @property
    def newest(self) -> RecProgramme:
        return self.episodes[0]

    @property
    def dvb_genre(self) -> str:
        return genre_text(list(self.newest.genre))

    def query(self, this_year: int) -> dt.Query | None:
        newest = self.newest
        return dt.make_query(dt.Programme(newest.title, description=newest.description, genres=newest.genre),
                             this_year)


def group_shows(programmes: Iterable[RecProgramme]) -> list[RecShow]:
    """Same cleaned title = one show, episodes newest first; shows by their newest episode."""
    shows: dict[str, RecShow] = {}
    for p in sorted(programmes, key=lambda p: (p.start, p.rid), reverse=True):
        key = title_key(p.title)
        if not key:
            continue
        show = shows.get(key)
        if show is None:
            shows[key] = show = RecShow(key, dt.clean_title(p.title, "tv"))
        show.episodes.append(p)
    return list(shows.values())


def rec_episode(p: RecProgramme) -> dict:
    return {"source": "rec", "recordingId": p.rid, "title": p.title, "description": p.description,
            "durationSeconds": p.end - p.start, "aired": epoch_iso(p.start), "channel": p.channel,
            "channelId": None, "playable": True}


# -- merging -------------------------------------------------------------------------------------
@dataclass
class _Card:
    key: str
    data: dict


def merge(ct_cards: list[_Card], rec_cards: list[_Card], az: bool = False) -> list[dict]:
    """One card per key, iVysílání first: a ČT card with our show of the same key becomes ``both``; our other
    shows go into every third place (A–Z: into title order), the rest at the end."""
    recs = {c.key: c for c in rec_cards}
    used: set[str] = set()
    ct_out: list[_Card] = []
    for card in ct_cards:
        rec = recs.get(card.key)
        if rec is not None and card.key not in used:
            used.add(card.key)
            card = _Card(card.key, both(card.data, rec.data))
        ct_out.append(card)
    rest = [c for c in rec_cards if c.key not in used]
    if az:
        out = list(ct_out)
        for card in sorted(rest, key=lambda c: c.key):
            at = next((i for i, c in enumerate(out) if c.key > card.key), len(out))
            out.insert(at, card)
        return [c.data for c in out]
    out, pending = [], list(rest)
    for i, card in enumerate(ct_out):
        out.append(card)
        if i % 2 == 1 and pending:
            out.append(pending.pop(0))
    return [c.data for c in out + pending]


def both(ct: dict, rec: dict) -> dict:
    card = dict(ct)
    card["source"] = "both"
    if card.get("kind") is None and rec.get("kind"):  # a search card: ČT did not say, our recording does
        card["kind"] = rec["kind"]
    if card.get("rating") is None and rec.get("rating") is not None:
        card["rating"] = rec["rating"]
        card["tmdb"] = True
    return card


# -- the catalogue -------------------------------------------------------------------------------
class ArchiveCatalog:
    def __init__(self, ct: CtArchiveClient, recordings: Callable[[], list[RecProgramme]],
                 store: Callable[[], TmdbStore | None] = lambda: None, thumbs: Callable[[], bool] = lambda: False,
                 now: Callable[[], float] = time.time, clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = print, on_listed: Callable[[], None] = lambda: None):
        self.ct, self._recordings, self.store, self.thumbs = ct, recordings, store, thumbs
        self.now, self.clock, self.log, self.on_listed = now, clock, log, on_listed
        self._rec_lock = threading.Lock()
        self._rec: tuple[float, list[RecShow]] | None = None
        self._listed_lock = threading.Lock()
        self._listed: OrderedDict[str, tuple[float, dt.Query]] = OrderedDict()
        self._known: OrderedDict[str, _Card] = OrderedDict()  # ČT show id → its last card (memory only)

    # -- inputs ----------------------------------------------------------------------------
    def rec_shows(self) -> list[RecShow]:
        with self._rec_lock:
            if self._rec is None or self.clock() - self._rec[0] >= RECORDINGS_TTL:
                self._rec = (self.clock(), group_shows(self._recordings()))
            return self._rec[1]

    def _year(self) -> int:
        return datetime.fromtimestamp(self.now(), timezone.utc).year

    def _ct_query(self, show: dict) -> dt.Query | None:
        title = dt.clean_title(show["title"])
        if not dt.normalise(title):
            return None
        if show["kind"] is None:  # a search card: ČT did not say
            return dt.make_query(dt.Programme(show["title"]), self._year())
        return dt.Query(title, show["year"], "movie" if show["kind"] == "movie" else "tv")

    def _entries(self, queries: Iterable[dt.Query | None]) -> dict[str, Entry]:
        store = self.store()
        keys = [q.key for q in queries if q is not None]
        return store.get_many(keys) if store is not None and keys else {}

    def _remember(self, queries: Iterable[dt.Query | None]) -> None:
        """ČT titles an answer listed: the worker may look them up (never anything a client typed)."""
        now, new = self.clock(), False
        with self._listed_lock:
            for q in queries:
                if q is None:
                    continue
                new = new or q.key not in self._listed
                self._listed[q.key] = (now, q)
                self._listed.move_to_end(q.key)
            while len(self._listed) > LISTED_MAX:
                self._listed.popitem(last=False)
        if new:
            self.on_listed()

    def wanted(self) -> list[dt.Query]:
        """For the TMDb worker: our shows (all within retention), then ČT shows listed in the last 7 days."""
        year = self._year()
        out = [q for q in (s.query(year) for s in self.rec_shows()) if q is not None]
        now = self.clock()
        with self._listed_lock:
            for key in [k for k, (at, _) in self._listed.items() if now - at >= LISTED_SECONDS]:
                del self._listed[key]
            out += [q for _, q in self._listed.values()]
        return out

    # -- cards -----------------------------------------------------------------------------
    def ct_cards(self, shows: list[dict]) -> list[_Card]:
        queries = [self._ct_query(s) for s in shows]
        self._remember(queries)
        entries = self._entries(queries)
        out = []
        for show, query in zip(shows, queries):
            entry = entries.get(query.key) if query else None
            rating = dict(entry.rating) if entry and entry.matched and entry.rating else None
            card: dict[str, Any] = {"id": "ct:" + show["id"], "source": "ct", "title": show["title"],
                                    "kind": show["kind"]}
            if show["year"]:
                card["year"] = show["year"]
            card["image"] = show["image"]
            card["rating"] = rating
            card["genres"] = [title for gid, title in show["genres"] if gid not in TOP_IDS]
            if show["kind"] == "series" and show["episodeCount"] is not None:
                card["episodeCount"] = show["episodeCount"]
            if show["kind"] == "movie" and show["durationMinutes"]:
                card["durationMinutes"] = show["durationMinutes"]
            card["tmdb"] = rating is not None
            out.append(_Card(title_key(show["title"]), card))
        with self._listed_lock:
            for show, item in zip(shows, out):
                self._known[show["id"]] = item
                self._known.move_to_end(show["id"])
            while len(self._known) > KNOWN_MAX:
                self._known.popitem(last=False)
        return out

    def rec_cards(self, shows: list[RecShow]) -> list[_Card]:
        return [_Card(s.key, card) for s, card, _ in self._rec_info(shows)]

    def _rec_info(self, shows: list[RecShow]) -> list[tuple[RecShow, dict, list[str]]]:
        """Card and categories of each of our shows."""
        return [(show, card, categories) for show, card, categories, _ in self._rec_details(shows)]

    def _rec_details(self, shows: list[RecShow]) -> list[tuple[RecShow, dict, list[str], dict | None]]:
        """Card, categories and the stored TMDb match (``{"kind", "genres"}`` or None) of each of our shows."""
        year = self._year()
        queries = [s.query(year) for s in shows]
        entries = self._entries(queries)
        thumbs = self.thumbs()
        out = []
        for show, query in zip(shows, queries):
            entry = entries.get(query.key) if query else None
            matched = entry is not None and entry.matched and entry.match_kind in ("movie", "tv")
            newest = show.newest
            if matched:
                kind = "movie" if entry.match_kind == "movie" and len(show.episodes) == 1 else "series"
            else:
                film = any((code >> 4) == 0x1 for code in newest.genre[:1])
                single = len(show.episodes) == 1 and query is not None and query.kind == "movie"
                kind = "movie" if single and film else "series"
            card: dict[str, Any] = {"id": "rec:" + show.ident, "source": "rec", "title": show.title, "kind": kind}
            if matched and entry.match_year:
                card["year"] = entry.match_year
            poster = dt.POSTER_PREFIX + entry.poster if matched and entry.poster else None
            image = f"/dvb/recording/{newest.rid}/thumb.jpg" if thumbs else poster
            card["image"] = image
            card["rating"] = dict(entry.rating) if matched and entry.rating else None
            genre = show.dvb_genre
            card["genres"] = list(entry.genres) if matched and entry.genres else ([genre] if genre else [])
            if kind == "series":
                card["episodeCount"] = len(show.episodes)
            else:
                card["durationMinutes"] = round((newest.end - newest.start) / 60)
            card["tmdb"] = matched and bool(card["rating"] or (image and image == poster) or entry.genres)
            tmdb = {"kind": entry.match_kind, "genres": list(entry.genre_ids)} if matched else None
            out.append((show, card, categories_for(tmdb, genre, newest.title), tmdb))
        return out

    def _ours_in(self, slug: str, subgenre_title: str | None = None) -> list[tuple[RecShow, dict, list[str]]]:
        """Our shows in a category; with a sub-genre's title only those whose TMDb match fits it."""
        return [(show, card, categories) for show, card, categories, tmdb in self._rec_details(self.rec_shows())
                if slug in categories and (subgenre_title is None or in_subgenre(subgenre_title, tmdb))]

    # -- answers ---------------------------------------------------------------------------
    def home(self) -> dict:
        ours = self._rec_info(self.rec_shows())
        rows = []
        if ours:
            rows.append({"id": RECORDINGS, "title": RECORDINGS_TITLE,
                         "cards": [card for _, card, _ in ours[:PAGE]], "more": len(ours) > PAGE})
        failed = False
        for cid, slug, title in CATEGORIES:
            mine = [_Card(s.key, card) for s, card, cats in ours if slug in cats]
            try:
                shows, total = self.ct.row(cid, "popular", 1)
            except CtError as exc:
                if not failed:
                    self.log(f"dvb: archive: {exc}")
                failed, shows, total = True, [], 0
            cards = merge(self.ct_cards(shows), mine[:HOME_REC])
            if cards:
                rows.append({"id": slug, "title": title, "cards": cards,
                             "more": total > len(shows) or len(mine) > HOME_REC})
        return self._finish({"rows": rows}, failed)

    def category(self, slug: str, sort: str, genre: str | None, page: int) -> dict:
        if slug == RECORDINGS:
            ours = self._rec_info(self.rec_shows())
            if sort == "az":
                ours = sorted(ours, key=lambda info: info[0].key)
            start = (page - 1) * RECORDINGS_PAGE
            cards = [card for _, card, _ in ours[start:start + RECORDINGS_PAGE]]
            more = len(ours) > start + RECORDINGS_PAGE and page < MAX_RECORDINGS_PAGES
            return self._finish({"title": RECORDINGS_TITLE, "genres": [], "cards": cards,
                                 "nextPage": page + 1 if more else None}, False)
        cid, title = BY_SLUG[slug]
        failed = False
        try:
            genres = self.ct.subgenres(cid)
        except CtError as exc:
            self.log(f"dvb: archive: {exc}")
            failed, genres = True, []
        if genre is not None and not failed and genre not in {g["id"] for g in genres}:
            raise BadInput("unknown sub-genre")
        if genre is None:
            mine = self._ours_in(slug)
        else:  # ČT down: the sub-genre's title is unknown, so none of ours
            subgenre_title = next((g["title"] for g in genres if g["id"] == genre), None)
            mine = self._ours_in(slug, subgenre_title) if subgenre_title is not None else []
        if sort == "az":
            mine.sort(key=lambda info: info[0].key)
        mine_page = [_Card(s.key, card) for s, card, _ in mine[(page - 1) * HOME_REC:page * HOME_REC]]
        shows, total = [], 0
        if not failed:
            try:
                shows, total = self.ct.row(genre or cid, sort, page)
            except CtError as exc:
                self.log(f"dvb: archive: {exc}")
                failed = True
        cards = merge(self.ct_cards(shows), mine_page, az=sort == "az")
        more = page < MAX_PAGES and ((page - 1) * PAGE + len(shows) < total or len(mine) > page * HOME_REC)
        return self._finish({"title": title, "genres": genres, "cards": cards,
                             "nextPage": page + 1 if more else None}, failed)

    @staticmethod
    def categories() -> dict:
        """The 14 categories in the row order, from our own list (no ČT request): the apps' menu."""
        return {"categories": [{"id": slug, "title": title} for _, slug, title in CATEGORIES]}

    def search(self, text: str, page: int) -> dict:
        needle = dt.normalise(text)
        mine = [_Card(s.key, card) for s, card, _ in self._rec_info(self.rec_shows())
                if needle and needle in dt.normalise(s.title)]
        mine_page = mine[(page - 1) * HOME_REC:page * HOME_REC]
        failed = False
        try:
            shows = self.ct.search(text, page)
        except CtError as exc:
            self.log(f"dvb: archive: {exc}")
            failed, shows = True, []
        cards = merge(self.ct_cards(shows), mine_page)
        more = page < MAX_PAGES and (len(shows) >= PAGE or len(mine) > page * HOME_REC)
        return self._finish({"cards": cards, "nextPage": page + 1 if more else None}, failed)

    def show(self, card_id: str, season: str | None, page: int, year: int | None = None,
             month: int | None = None) -> dict:
        """``ct:<id>`` or ``rec:<id>``; NotFound → 404, BadInput → 400, CtError → 502. The same answer for
        ``?season=&page=``: the ČT group then holds that page (the apps replace it on page 1, append later).
        More than 100 episodes: the group has ``periods`` and holds a month's episodes (``year``/``month``), or a
        whole year's when the year has at most 100 (T51)."""
        kind, _, ident = card_id.partition(":")
        if kind == "rec":
            if year is not None or month is not None:
                raise BadPeriod("our recordings have no periods")
            show = next((s for s in self.rec_shows() if s.ident == ident), None)
            if show is None:
                raise NotFound(card_id)
            _, card, _ = self._rec_info([show])[0]
            return self._finish({"card": card, "description": show.newest.description, "seasons": [],
                                 "groups": [self._rec_group(show)]}, False)
        try:
            info = self.ct.show(ident)
            seasons = info["seasons"]
            if season is not None and season not in {s["id"] for s in seasons}:
                raise BadInput("unknown season")
            chosen = season or (seasons[0]["id"] if seasons else None)
            periods = None
            if year is None and month is None:
                episodes, total = self.ct.episodes(ident, chosen, page, oldest=bool(seasons))
                more = page < MAX_EPISODE_PAGES and (page - 1) * EPISODE_PAGE + len(episodes) < total
                if total > PERIODS_OVER:
                    found = self._periods(ident, chosen, None, None, page)
                    if found is not None:  # else: no dated episodes, the list as up to 100
                        episodes, more, periods = found
            else:
                found = self._periods(ident, chosen, year, month, page)
                if found is None:
                    raise BadPeriod("no periods")
                episodes, more, periods = found
        except CtNotFound:
            raise
        except CtError as exc:
            return self._show_without_ct(ident, exc)
        ct_card = self.ct_cards([info])[0]  # kind and year from the show itself, also after a search card
        mine = next((s for s in self.rec_shows() if s.key == ct_card.key), None)
        card = both(ct_card.data, self._rec_info([mine])[0][1]) if mine else ct_card.data
        group_title = next((s["title"] for s in seasons if s["id"] == chosen), CT_GROUP)
        out: dict[str, Any] = {"card": card, "description": info["description"]}
        if info["creators"]:
            out["creators"] = info["creators"]
        if info["countries"]:
            out["countries"] = info["countries"]
        out["seasons"] = seasons
        group: dict[str, Any] = {"title": group_title, "season": chosen}
        if periods is not None:
            group["periods"] = periods
        group["episodes"] = episodes
        group["nextPage"] = page + 1 if more else None
        groups = [group]
        if mine:
            groups.append(self._rec_group(mine))
        out["groups"] = groups
        return self._finish(out, False)

    def _periods(self, ident: str, season: str | None, year: int | None, month: int | None,
                 page: int) -> tuple[list[dict], bool, dict] | None:
        """(the month's page of episodes, more, ``periods``) or None when the list has none; BadPeriod for a year
        or month it does not have. A year of at most 100 episodes: its page of the whole year, ``wholeYear`` True with
        ``months`` [M] and ``month`` M (M = its newest month, for app builds from before T51), any ``month``
        ignored (T51). Default: the newest year, a year's newest month; a year found
        empty (also one asked for again from a stale chip): the next older year with episodes, else the next newer."""
        index = self.ct.periods(ident, season)
        years = index.years()
        if not years:
            return None
        if year is None:
            if month is not None:
                raise BadPeriod("a month needs its year")
            year = years[0]
        elif year not in years:
            if not (year in index.empty and index.in_range(year)):
                raise BadPeriod("unknown year")
            year, month = _nearest(year, years), None  # a stale chip: the nearest year with episodes
        for _ in range(len(years) + 1):  # each empty year leaves the list, so this ends; the bound is a guard
            by_month = index.months(year)
            if by_month:
                break
            years = index.years()  # now without it (and any empty year it showed up)
            if not years or year in years:
                return None
            year, month = _nearest(year, years), None
        else:
            return None
        if sum(map(len, by_month.values())) < YEAR_MONTHS_MIN:
            # T51: few episodes a year (a weekly show) is one list, newest first; a month from a stale chip is
            # ignored. Months are newest first and each newest first, so joined they are the year newest first.
            # One month (the newest), not []: builds from before T51 drop periods with months [] and then show
            # no years at all; with [M] they show the year chips, and any month they send gets the whole year.
            newest = next(iter(by_month))
            episodes = [e for eps in by_month.values() for e in eps]
            start = (page - 1) * EPISODE_PAGE
            more = page < MAX_EPISODE_PAGES and len(episodes) > start + EPISODE_PAGE
            return episodes[start:start + EPISODE_PAGE], more, {"years": index.years(), "year": year,
                                                                "months": [newest], "month": newest,
                                                                "wholeYear": True}
        else:
            if month is None:
                month = next(iter(by_month))
            elif month not in by_month:
                raise BadPeriod("unknown month")
            episodes, months = by_month[month], list(by_month)
        start = (page - 1) * EPISODE_PAGE
        more = page < MAX_EPISODE_PAGES and len(episodes) > start + EPISODE_PAGE
        return episodes[start:start + EPISODE_PAGE], more, {"years": index.years(), "year": year,
                                                            "months": months, "month": month}

    def _show_without_ct(self, ident: str, exc: CtError) -> dict:
        """ČT is down: a show the archive listed that we also recorded still shows our episodes."""
        with self._listed_lock:
            known = self._known.get(ident)
        mine = next((s for s in self.rec_shows() if known and s.key == known.key), None)
        if mine is None:
            raise exc
        self.log(f"dvb: archive: {exc}")
        card = both(known.data, self._rec_info([mine])[0][1])
        return self._finish({"card": card, "description": mine.newest.description, "seasons": [],
                             "groups": [self._rec_group(mine)]}, True)

    @staticmethod
    def _rec_group(show: RecShow) -> dict:
        return {"title": REC_GROUP, "episodes": [rec_episode(p) for p in show.episodes]}

    @staticmethod
    def _finish(answer: dict, failed: bool) -> dict:
        """``error`` when ČT failed; TMDb's attribution whenever a card shows TMDb data."""
        if failed:
            answer["error"] = CtError.user_message
        cards = [c for row in answer.get("rows", []) for c in row["cards"]] + answer.get("cards", []) + \
            ([answer["card"]] if "card" in answer else [])
        if any(c.get("tmdb") for c in cards):
            answer["attribution"] = dt.ATTRIBUTION
        return answer
