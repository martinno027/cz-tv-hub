"""Programme details from TMDb (spec/dvb-gateway.md "Programme details", approved by the user on 2026-10-24).

Only the gateway talks to TMDb, with the household's own key (``[tmdb] key``; none = off), and only for a programme
the gateway found in its own EPG or the CT schedule: TMDb gets the cleaned title, nothing else (the year and the
kind only decide which result is accepted). A result counts only when its title equals ours and its year is within
±1 of the one the description states; anything less is "no match" (no rating is better than a wrong one).
Results are kept in ``<state dir>/tmdb-cache.json`` (0600) for 30 days, at most 5000; posters only in memory.
The key never appears in a log line, an exception or an answer.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

API_BASE = "https://api.themoviedb.org/3/"
IMAGE_BASE = "https://image.tmdb.org/t/p/w342/"  # the only host and size the poster proxy fetches from
POSTER_PREFIX = "/dvb/details/image/w342/"
LANGUAGE = "cs-CZ"
CACHE_FILE = "tmdb-cache.json"
CACHE_SECONDS = 30 * 86400  # TMDb terms 1.C allow at most 6 months (checked 2026-10-24)
CACHE_MAX = 5000
PER_MINUTE = 30
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
    """At most one TMDb request at a time and ``per_minute`` per minute; over that, TmdbBusy at once."""

    def __init__(self, per_minute: int = PER_MINUTE, wait: float = WAIT_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.per_minute, self.wait, self.clock = per_minute, wait, clock
        self._lock = threading.Lock()
        self._sent: list[float] = []

    def __enter__(self) -> "Limiter":
        if not self._lock.acquire(timeout=self.wait):
            raise TmdbBusy("another TMDb request is still running")
        now = self.clock()
        self._sent = [t for t in self._sent if now - t < 60.0]
        if len(self._sent) >= self.per_minute:
            self._lock.release()
            raise TmdbBusy(f"{self.per_minute} TMDb requests in the last minute")
        self._sent.append(now)
        return self

    def __exit__(self, *exc: Any) -> None:
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

    def get(self, path: str, params: dict[str, str]) -> dict:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        query = dict(params)
        if self.bearer:
            headers["Authorization"] = "Bearer " + self._key
        else:
            query["api_key"] = self._key
        request = urllib.request.Request(API_BASE + path + "?" + urllib.parse.urlencode(query), headers=headers)
        body = self._fetch(request, path, MAX_JSON)[0]
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

    def _fetch(self, request: urllib.request.Request, what: str, limit: int) -> tuple[bytes, str]:
        with self.limiter:
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
                exc.close()
                hint = " (the [tmdb] key was refused)" if exc.code == 401 else ""
                raise TmdbError(f"TMDb {what}: HTTP {exc.code}{hint}") from None
            except urllib.error.URLError as exc:
                raise TmdbError(self._scrub(f"TMDb {what}: {exc.reason}")) from None
            except (OSError, ValueError) as exc:
                raise TmdbError(self._scrub(f"TMDb {what}: {type(exc).__name__}: {exc}")) from None
        if len(body) > limit:
            raise TmdbError(f"TMDb {what}: larger than {limit} bytes")
        return body, content_type


# -- cache -------------------------------------------------------------------------------------
class DetailsCache:
    """Programme key → match or ``None`` (no match), 30 days, at most 5000; ``<state dir>/tmdb-cache.json``."""

    def __init__(self, state_dir: Path, now: Callable[[], float] = time.time, log: Callable[[str], None] = print,
                 max_entries: int = CACHE_MAX, max_age: float = CACHE_SECONDS):
        self.path = Path(state_dir) / CACHE_FILE
        self.now, self.log = now, log
        self.max_entries, self.max_age = max_entries, max_age
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self.log(f"dvb: details: {self.path.name} unreadable ({type(exc).__name__}); starting empty")
            return {}
        entries = raw.get("entries") if isinstance(raw, dict) else None
        out: dict[str, dict] = {}
        for key, item in (entries or {}).items() if isinstance(entries, dict) else []:
            at = item.get("at") if isinstance(item, dict) else None
            match = item.get("match") if isinstance(item, dict) else None
            if isinstance(key, str) and isinstance(at, (int, float)) and not isinstance(at, bool) \
                    and (match is None or isinstance(match, dict)):
                out[key] = {"at": float(at), "match": match}
        return self._trim(out)

    def _trim(self, entries: dict[str, dict]) -> dict[str, dict]:
        now = self.now()
        fresh = {k: v for k, v in entries.items() if 0 <= now - v["at"] < self.max_age}
        if len(fresh) > self.max_entries:
            newest = sorted(fresh.items(), key=lambda kv: kv[1]["at"], reverse=True)[:self.max_entries]
            fresh = dict(newest)
        return fresh

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: str) -> tuple[bool, dict | None]:
        with self._lock:
            item = self._entries.get(key)
            if item is None or not 0 <= self.now() - item["at"] < self.max_age:
                return False, None
            return True, json.loads(json.dumps(item["match"]))

    def put(self, key: str, match: dict | None) -> None:
        with self._lock:
            self._entries[key] = {"at": self.now(), "match": match}
            self._entries = self._trim(self._entries)
            payload = json.dumps({"v": 1, "entries": self._entries}, ensure_ascii=False, separators=(",", ":"))
        try:
            _write_private(self.path, payload)
        except OSError as exc:
            self.log(f"dvb: details: cannot save {self.path.name}: {exc}")

    def posters(self) -> set[str]:
        """Poster files of fresh matches: the only ones the proxy fetches."""
        with self._lock:
            now = self.now()
            out = set()
            for item in self._entries.values():
                poster = (item["match"] or {}).get("poster")
                if isinstance(poster, str) and poster.startswith(POSTER_PREFIX) and 0 <= now - item["at"] < self.max_age:
                    out.add(poster[len(POSTER_PREFIX):])
            return out


def _write_private(path: Path, payload: str) -> None:
    """Atomically, mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # an older tmp file may have had other bits
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def remove_cache(state_dir: Path, log: Callable[[str], None] = print) -> None:
    """``[tmdb] key`` is gone: nothing from TMDb stays on the Pi."""
    path = Path(state_dir) / CACHE_FILE
    for item in (path, path.with_name(path.name + ".tmp")):
        try:
            item.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            log(f"dvb: details: cannot delete {item.name}: {exc}")
            continue
        log(f"dvb: details: no [tmdb] key, deleted {item.name}")


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
                 cache: DetailsCache | None = None):
        self.now, self.log = now, log
        self.client = client or TmdbClient(key)
        self.cache = cache or DetailsCache(state_dir, now=now, log=log)
        self._lookup = threading.Lock()  # one lookup at a time; the second asker then finds it cached
        self._posters = _Posters()
        self._posters_lock = threading.Lock()

    @staticmethod
    def answer(match: dict | None, error: str | None = None) -> dict:
        out: dict[str, Any] = {"match": match}
        if error:
            out["error"] = error
        out["attribution"] = ATTRIBUTION
        return out

    def lookup(self, programme: Programme) -> dict:
        this_year = datetime.fromtimestamp(self.now(), timezone.utc).year
        query = make_query(programme, this_year)
        if query is None:
            return self.answer(None)
        found, match = self.cache.get(query.key)
        if found:
            return self.answer(match)
        if not self._lookup.acquire(timeout=WAIT_SECONDS):
            return self.answer(None, TmdbBusy.user_message)
        try:
            found, match = self.cache.get(query.key)
            if found:
                return self.answer(match)
            try:
                match = self._search(query)
            except TmdbError as exc:
                self.log(f"dvb: details: {exc}")
                return self.answer(None, exc.user_message)
            self.cache.put(query.key, match)
            return self.answer(match)
        finally:
            self._lookup.release()

    def _search(self, query: Query) -> dict | None:
        for kind in (query.kind, "movie" if query.kind == "tv" else "tv"):
            title = clean_title(query.title, kind)
            if not normalise(title):
                continue
            data = self.client.get(f"search/{kind}", {"query": title, "language": LANGUAGE,
                                                      "include_adult": "false"})
            result = pick(data.get("results"), title, query.year, kind)
            if result is not None:
                return to_match(kind, self.client.get(f"{kind}/{result['id']}", {"language": LANGUAGE}))
        return None

    def allowed(self, file: str) -> bool:
        return bool(POSTER_FILE_RE.match(file)) and file in self.cache.posters()

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
