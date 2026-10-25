"""Programme details from TMDb (spec/dvb-gateway.md "Programme details", approved by the user on 2026-10-24).

Only the gateway talks to TMDb, with the household's own key (``[tmdb] key``; none = off), and only for a programme
the gateway found in its own EPG or the CT schedule: TMDb gets the cleaned title, nothing else (the year and the
kind only decide which result is accepted). A result counts only when its title equals ours and its year is within
±1 of the one the description states; anything less is "no match" (no rating is better than a wrong one).
Results are kept in the TMDb store (``tmdbstore.py``, ``<state dir>/tmdb.sqlite``, 0600) with only what the rule
lists; TMDb's titles, overview and runtime stay in memory, like the posters. Page lookups go before the archive's
background lookups (``Limiter``). The key never appears in a log line, an exception or an answer.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from . import tmdbstore
from .tmdbstore import Entry, TmdbStore

API_BASE = "https://api.themoviedb.org/3/"
IMAGE_BASE = "https://image.tmdb.org/t/p/w342/"  # the only host and size the poster proxy fetches from
POSTER_PREFIX = "/dvb/details/image/w342/"
LANGUAGE = "cs-CZ"
PER_MINUTE = 30
BACKGROUND_PER_MINUTE = 20  # the archive's worker leaves the rest of the minute to page lookups
RETRY_AFTER_DEFAULT = 60.0  # TMDb said 429 without a usable Retry-After
RETRY_AFTER_MAX = 3600.0
EXTRAS_MAX = 2000  # TMDb's titles/overview/runtime of matches, in memory only
WAIT_SECONDS = 10.0  # for the one TMDb request at a time; a viewer waiting longer gets the page without details
TIMEOUT = 10.0
MIN_VOTES = 20
MAX_JSON = 2 * 1024 * 1024
POSTER_MAX = 1024 * 1024
POSTER_MEMORY = 40 * 1024 * 1024
POSTER_TYPES = ("image/jpeg", "image/png")
USER_AGENT = "cz-tv-hub (household gateway)"
ATTRIBUTION = ("This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved "
               "by TMDB.")
POSTER_FILE = r"[A-Za-z0-9_-]{1,64}\.(?:jpg|png)"
POSTER_PATH_RE = re.compile(rf"^/({POSTER_FILE})$")  # TMDb's poster_path
POSTER_FILE_RE = re.compile(rf"^{POSTER_FILE}$")

# What EPGs append to a title; removed from the end, repeatedly ("Mistři zastavárny VI (14) HD").
_TAIL = [
    re.compile(r"\s*\((?:\d{1,4}(?:/\d{1,4})?|R)\)$", re.IGNORECASE),  # (14), (3/12), (R)
    re.compile(r"\s*[-–—,:]?\s*\b(?:díl|část|epizoda)\s+\d{1,4}$", re.IGNORECASE),  # - díl 3
    re.compile(r"\s+HD$"),
]
_LEAD = re.compile(r"^film\s*:\s*", re.IGNORECASE)
_ROMAN = re.compile(r"\s+(?=[IVX])X{0,3}(?:IX|IV|V?I{0,3})$")  # a series number: "Kobra 11 IV"
_EPISODE_MARK = re.compile(r"\(\d{1,3}(?:/\d{1,3})?\)|\b(?:díl|část|epizoda)\s+\d{1,4}\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\s*\)")  # "(USA, 2010)", "… 2010)"
SOAP = 0x15  # ETSI EN 300 468 content nibble: soap / melodrama / folklore (series)


class TmdbError(Exception):
    """TMDb failed or refused; the message is ours (never the URL, never the key)."""

    user_message = "TMDB teď neodpovídá"


class TmdbBusy(TmdbError):
    user_message = "Příliš mnoho dotazů na TMDB, zkuste to za chvíli"


@dataclass(frozen=True)
class Programme:
    """What the gateway knows of a programme from its own EPG or the CT schedule."""

    title: str
    episode: str = ""  # the CT schedule's episode title
    description: str = ""
    genres: tuple[int, ...] = ()  # DVB content codes
    genre: str = ""  # a genre as text (CT)


@dataclass(frozen=True)
class Query:
    title: str  # cleaned, series number kept (a film's "Rocky II")
    year: int | None
    kind: str  # the guess, tried first

    @property
    def key(self) -> str:
        return json.dumps([self.kind, self.year, self.title], ensure_ascii=False)


# -- title, year, kind -------------------------------------------------------------------------
def clean_title(title: str, kind: str = "movie") -> str:
    """The title without what EPGs add to it. A trailing roman number goes only for ``tv`` (the season of
    "Mistři zastavárny VI"); for a film it is part of the title ("Rocky II")."""
    text = " ".join(unicodedata.normalize("NFC", title or "").split())
    text = _LEAD.sub("", text)
    while True:
        before = text
        for pattern in _TAIL:
            text = pattern.sub("", text).strip()
        if kind == "tv":
            text = _ROMAN.sub("", text).strip()
        text = text.rstrip(" -–—,:")
        if text == before:
            return text


def description_year(description: str, this_year: int) -> int | None:
    """The first year the description states in the EPG way, "(USA, 2010)" or "… 2010)"."""
    for m in _YEAR.finditer(description or ""):
        year = int(m.group(1))
        if 1890 <= year <= this_year + 1:
            return year
    return None


def guess_kind(programme: Programme) -> str:
    """``tv`` for an episode line, an episode number in the title or a series genre, else ``movie``."""
    title = " ".join((programme.title or "").split())
    if programme.episode.strip() or _EPISODE_MARK.search(title) or SOAP in programme.genres:
        return "tv"
    if "serial" in normalise(programme.genre):
        return "tv"
    return "movie"


def normalise(text: Any) -> str:
    """Casefold, no diacritics, letters and digits only: how two titles are compared."""
    if not isinstance(text, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def make_query(programme: Programme, this_year: int) -> Query | None:
    title = clean_title(programme.title)
    if not normalise(title):
        return None
    return Query(title, description_year(programme.description, this_year), guess_kind(programme))


def _year(value: Any) -> int | None:
    if isinstance(value, str) and re.match(r"^\d{4}-", value):
        return int(value[:4])
    return None


def pick(results: Any, title: str, year: int | None, kind: str) -> dict | None:
    """The one search result whose Czech or original title equals ours (and year within ±1 when we have one).
    Several equally good ones are no match: without a year a common title is a guess."""
    target = normalise(title)
    names, date_field = (("title", "original_title"), "release_date") if kind == "movie" \
        else (("name", "original_name"), "first_air_date")
    found: list[tuple[int, dict]] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool):
            continue
        if target not in {normalise(item.get(n)) for n in names}:
            continue
        if year is None:
            found.append((0, item))
            continue
        theirs = _year(item.get(date_field))
        if theirs is not None and abs(theirs - year) <= 1:
            found.append((abs(theirs - year), item))
    if not found:
        return None
    best = min(diff for diff, _ in found)
    closest = [item for diff, item in found if diff == best]
    return closest[0] if len(closest) == 1 else None


def to_match(kind: str, data: Any) -> dict | None:
    """TMDb's details in the gateway's shape; fields TMDb did not send (or sent oddly) stay out or null."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), int) or isinstance(data.get("id"), bool):
        return None
    movie = kind == "movie"
    title = data.get("title" if movie else "name")
    original = data.get("original_title" if movie else "original_name")
    match: dict[str, Any] = {
        "kind": kind,
        "title": title if isinstance(title, str) else "",
        "originalTitle": original if isinstance(original, str) else "",
        "year": _year(data.get("release_date" if movie else "first_air_date")),
    }
    overview = data.get("overview")
    if isinstance(overview, str) and overview.strip():
        match["overview"] = overview.strip()
    genres = data.get("genres") if isinstance(data.get("genres"), list) else []
    match["genres"] = [g["name"] for g in genres if isinstance(g, dict) and isinstance(g.get("name"), str) and g["name"]]
    if movie:
        runtime = data.get("runtime")
    else:  # tv: the usual episode length
        lengths = data.get("episode_run_time")
        runtime = lengths[0] if isinstance(lengths, list) and lengths else None
    if isinstance(runtime, int) and not isinstance(runtime, bool) and runtime > 0:
        match["runtimeMinutes"] = runtime
    votes, value = data.get("vote_count"), data.get("vote_average")
    good = isinstance(votes, int) and not isinstance(votes, bool) and votes >= MIN_VOTES \
        and isinstance(value, (int, float)) and not isinstance(value, bool)
    match["rating"] = {"value": float(value), "votes": votes} if good else None
    path = data.get("poster_path")
    poster = POSTER_PATH_RE.match(path) if isinstance(path, str) else None
    match["poster"] = POSTER_PREFIX + poster.group(1) if poster else None
    match["tmdbId"] = data["id"]
    return match


# -- TMDb --------------------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Fixed hosts: a redirect elsewhere is an error, not followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class Limiter:
    """At most one TMDb request at a time and ``per_minute`` per minute; over that, TmdbBusy at once.
    Background requests (the archive's worker) never wait: they give way while a page lookup waits, keep to
    ``background_per_minute`` and stop while TMDb asked us to wait (Retry-After)."""

    def __init__(self, per_minute: int = PER_MINUTE, wait: float = WAIT_SECONDS,
                 clock: Callable[[], float] = time.monotonic, background_per_minute: int = BACKGROUND_PER_MINUTE):
        self.per_minute, self.wait, self.clock = per_minute, wait, clock
        self.background_per_minute = min(background_per_minute, per_minute)
        self._lock = threading.Lock()
        self._state = threading.Lock()
        self._sent: list[float] = []
        self._waiting = 0  # page lookups waiting for the one request slot
        self._paused_until = float("-inf")

    def pause(self, seconds: float) -> None:
        """TMDb answered 429: nothing goes out for that long."""
        with self._state:
            self._paused_until = max(self._paused_until, self.clock() + max(0.0, seconds))

    def paused(self) -> bool:
        with self._state:
            return self.clock() < self._paused_until

    @contextmanager
    def slot(self, background: bool = False) -> Iterator["Limiter"]:
        if background:
            with self._state:
                waiting = self._waiting
            if waiting or not self._lock.acquire(blocking=False):
                raise TmdbBusy("a page lookup goes first")
        else:
            with self._state:
                self._waiting += 1
            try:
                got = self._lock.acquire(timeout=self.wait)
            finally:
                with self._state:
                    self._waiting -= 1
            if not got:
                raise TmdbBusy("another TMDb request is still running")
        try:
            now = self.clock()
            with self._state:
                if now < self._paused_until:
                    raise TmdbBusy(f"TMDb asked to wait {self._paused_until - now:.0f} s")
                self._sent = [t for t in self._sent if now - t < 60.0]
                limit = self.background_per_minute if background else self.per_minute
                if len(self._sent) >= limit:
                    raise TmdbBusy(f"{len(self._sent)} TMDb requests in the last minute")
                self._sent.append(now)
            yield self
        finally:
            self._lock.release()


class TmdbClient:
    """``search/…`` and ``movie|tv/{id}`` of TMDb API v3. A v4 read token (a JWT) goes as the bearer header,
    a v3 API key as ``api_key``; either way only in the request, never in a message."""

    def __init__(self, key: str, limiter: Limiter | None = None,
                 urlopen: Callable[..., Any] | None = None, timeout: float = TIMEOUT):
        self._key = key
        self.limiter = limiter or Limiter()
        self._urlopen = urlopen or _OPENER.open
        self.timeout = timeout

    @property
    def bearer(self) -> bool:
        return self._key.startswith("eyJ") and self._key.count(".") == 2

    def _scrub(self, text: str) -> str:
        if self._key:
            text = text.replace(self._key, "***").replace(urllib.parse.quote(self._key, safe=""), "***")
        return text

    def get(self, path: str, params: dict[str, str], background: bool = False) -> dict:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        query = dict(params)
        if self.bearer:
            headers["Authorization"] = "Bearer " + self._key
        else:
            query["api_key"] = self._key
        request = urllib.request.Request(API_BASE + path + "?" + urllib.parse.urlencode(query), headers=headers)
        body = self._fetch(request, path, MAX_JSON, background)[0]
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise TmdbError(f"TMDb {path}: not JSON") from None
        if not isinstance(data, dict):
            raise TmdbError(f"TMDb {path}: unexpected answer")
        return data

    def image(self, file: str) -> tuple[bytes, str]:
        """A w342 poster from image.tmdb.org; no key is sent there."""
        if not POSTER_FILE_RE.match(file):
            raise TmdbError("not a TMDb poster file")
        request = urllib.request.Request(IMAGE_BASE + file, headers={"User-Agent": USER_AGENT})
        body, content_type = self._fetch(request, "poster", POSTER_MAX)
        if content_type not in POSTER_TYPES:
            raise TmdbError(f"TMDb poster: unexpected type {content_type[:40]!r}")
        return body, content_type

    def _fetch(self, request: urllib.request.Request, what: str, limit: int,
               background: bool = False) -> tuple[bytes, str]:
        with self.limiter.slot(background):
            try:
                with self._urlopen(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status != 200:
                        raise TmdbError(f"TMDb {what}: HTTP {status}")
                    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    body = response.read(limit + 1)
            except TmdbError:
                raise
            except urllib.error.HTTPError as exc:
                retry = exc.headers.get("Retry-After") if exc.code == 429 and exc.headers else None
                exc.close()
                if exc.code == 429:
                    wait = retry_after(retry)
                    self.limiter.pause(wait)
                    raise TmdbBusy(f"TMDb {what}: HTTP 429, waiting {wait:.0f} s") from None
                hint = " (the [tmdb] key was refused)" if exc.code == 401 else ""
                raise TmdbError(f"TMDb {what}: HTTP {exc.code}{hint}") from None
            except urllib.error.URLError as exc:
                raise TmdbError(self._scrub(f"TMDb {what}: {exc.reason}")) from None
            except (OSError, ValueError) as exc:
                raise TmdbError(self._scrub(f"TMDb {what}: {type(exc).__name__}: {exc}")) from None
        if len(body) > limit:
            raise TmdbError(f"TMDb {what}: larger than {limit} bytes")
        return body, content_type


def retry_after(value: Any) -> float:
    """TMDb's Retry-After in seconds (only the number form; anything else waits the default)."""
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return RETRY_AFTER_DEFAULT
    if seconds != seconds or seconds < 0:
        return RETRY_AFTER_DEFAULT
    return min(seconds, RETRY_AFTER_MAX)


def genre_ids(data: Any) -> list[int]:
    genres = data.get("genres") if isinstance(data, dict) and isinstance(data.get("genres"), list) else []
    return [g["id"] for g in genres if isinstance(g, dict) and isinstance(g.get("id"), int)
            and not isinstance(g.get("id"), bool)]


def stored_result(match: dict, data: Any) -> dict:
    """What the store keeps of a match (the rule's list): kind, id, rating, genres, year, poster file."""
    poster = match.get("poster")
    return {"kind": match["kind"], "tmdbId": match["tmdbId"], "rating": match.get("rating"),
            "genreIds": genre_ids(data), "genres": list(match.get("genres") or []), "year": match.get("year"),
            "poster": poster[len(POSTER_PREFIX):] if isinstance(poster, str) and poster.startswith(POSTER_PREFIX)
            else None}


def remove_cache(state_dir: Path, log: Callable[[str], None] = print) -> None:
    """``[tmdb] key`` is gone: nothing from TMDb stays on the Pi."""
    tmdbstore.remove_store(state_dir, log)


# -- posters -----------------------------------------------------------------------------------
@dataclass
class _Posters:
    limit: int = POSTER_MEMORY
    size: int = 0
    items: OrderedDict = field(default_factory=OrderedDict)

    def get(self, file: str) -> tuple[bytes, str] | None:
        hit = self.items.get(file)
        if hit is not None:
            self.items.move_to_end(file)
        return hit

    def put(self, file: str, value: tuple[bytes, str]) -> None:
        if file in self.items:
            self.size -= len(self.items.pop(file)[0])
        self.items[file] = value
        self.size += len(value[0])
        while self.size > self.limit and self.items:
            self.size -= len(self.items.popitem(last=False)[1][0])


# -- the service -------------------------------------------------------------------------------
class Details:
    def __init__(self, key: str, state_dir: Path, now: Callable[[], float] = time.time,
                 log: Callable[[str], None] = print, client: TmdbClient | None = None,
                 store: TmdbStore | None = None, months: Callable[[], int] = lambda: tmdbstore.DEFAULT_MONTHS):
        self.now, self.log = now, log
        self.client = client or TmdbClient(key)
        self.store = store or TmdbStore(state_dir, months=months, now=now, log=log)
        self._lookup = threading.Lock()  # one page lookup at a time; the second asker then finds it stored
        self._posters = _Posters()
        self._posters_lock = threading.Lock()
        self._extras: OrderedDict[tuple[str, int], dict] = OrderedDict()  # memory only (the rule stores none)
        self._extras_lock = threading.Lock()

    @staticmethod
    def answer(match: dict | None, error: str | None = None) -> dict:
        out: dict[str, Any] = {"match": match}
        if error:
            out["error"] = error
        out["attribution"] = ATTRIBUTION
        return out

    def query(self, programme: Programme) -> Query | None:
        return make_query(programme, datetime.fromtimestamp(self.now(), timezone.utc).year)

    def lookup(self, programme: Programme) -> dict:
        """The page lookup (a viewer opened the programme): stored result, else TMDb at once."""
        query = self.query(programme)
        if query is None:
            return self.answer(None)
        entry = self.store.get(query.key)
        if entry is not None:
            return self.answer(self._match(entry))
        if not self._lookup.acquire(timeout=WAIT_SECONDS):
            return self.answer(None, TmdbBusy.user_message)
        try:
            entry = self.store.get(query.key)
            if entry is not None:
                return self.answer(self._match(entry))
            try:
                match = self.refresh(query)
            except TmdbError as exc:
                self.log(f"dvb: details: {exc}")
                return self.answer(None, exc.user_message)
            return self.answer(match)
        finally:
            self._lookup.release()

    def refresh(self, query: Query, background: bool = False) -> dict | None:
        """Ask TMDb for ``query`` and store the result (also a no match); TmdbError when TMDb failed or the
        limiter refused, and then nothing is stored."""
        match, data = self._search(query, background)
        self.store.put(query.key, query.kind, query.year, query.title,
                       stored_result(match, data) if match else None)
        if match:
            self._remember(match)
        return match

    def _search(self, query: Query, background: bool) -> tuple[dict | None, Any]:
        for kind in (query.kind, "movie" if query.kind == "tv" else "tv"):
            title = clean_title(query.title, kind)
            if not normalise(title):
                continue
            data = self.client.get(f"search/{kind}", {"query": title, "language": LANGUAGE,
                                                      "include_adult": "false"}, background)
            result = pick(data.get("results"), title, query.year, kind)
            if result is not None:
                details = self.client.get(f"{kind}/{result['id']}", {"language": LANGUAGE}, background)
                return to_match(kind, details), details
        return None, None

    # -- a stored entry as the page's match -----------------------------------------------
    def _remember(self, match: dict) -> None:
        extras = {k: match[k] for k in ("title", "originalTitle", "overview", "runtimeMinutes") if k in match}
        with self._extras_lock:
            self._extras[(match["kind"], match["tmdbId"])] = extras
            self._extras.move_to_end((match["kind"], match["tmdbId"]))
            while len(self._extras) > EXTRAS_MAX:
                self._extras.popitem(last=False)

    def _match(self, entry: Entry) -> dict | None:
        """A stored match with TMDb's texts: from memory, else one details request (the page asked); without
        them the stored facts and our own title."""
        if not entry.matched or entry.match_kind not in ("movie", "tv") or entry.tmdb_id is None:
            return None
        ident = (entry.match_kind, entry.tmdb_id)
        with self._extras_lock:
            extras = self._extras.get(ident)
        if extras is None:
            try:
                data = self.client.get(f"{entry.match_kind}/{entry.tmdb_id}", {"language": LANGUAGE})
                fresh = to_match(entry.match_kind, data)
            except TmdbError as exc:
                self.log(f"dvb: details: {exc}")
                fresh = None
            if fresh is not None:
                self._remember(fresh)
                extras = {k: fresh[k] for k in ("title", "originalTitle", "overview", "runtimeMinutes")
                          if k in fresh}
        extras = extras or {"title": entry.title, "originalTitle": ""}
        match: dict[str, Any] = {"kind": entry.match_kind, "title": extras.get("title", entry.title),
                                 "originalTitle": extras.get("originalTitle", ""), "year": entry.match_year}
        if extras.get("overview"):
            match["overview"] = extras["overview"]
        match["genres"] = list(entry.genres)
        if extras.get("runtimeMinutes"):
            match["runtimeMinutes"] = extras["runtimeMinutes"]
        match["rating"] = dict(entry.rating) if entry.rating else None
        match["poster"] = POSTER_PREFIX + entry.poster if entry.poster else None
        match["tmdbId"] = entry.tmdb_id
        return match

    # -- posters ---------------------------------------------------------------------------
    def allowed(self, file: str) -> bool:
        return bool(POSTER_FILE_RE.match(file)) and file in self.store.posters()

    def poster(self, file: str) -> tuple[bytes, str] | None:
        """The poster of one of our own matches (None for any other file); TmdbError when TMDb fails."""
        if not self.allowed(file):
            return None
        with self._posters_lock:
            hit = self._posters.get(file)
        if hit is not None:
            return hit
        fetched = self.client.image(file)
        with self._posters_lock:
            self._posters.put(file, fetched)
        return fetched
