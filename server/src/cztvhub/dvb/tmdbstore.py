"""TMDb results on the Pi's disk (spec/dvb-gateway.md "Archive" and "Programme details"; the TMDb rule in
CLAUDE.md, approved by the user on 2026-10-24 and extended for the archive on 2026-10-25).

``<state dir>/tmdb.sqlite`` (0600) keeps per programme key (kind + year + cleaned title) only what the rule lists:
match or no match, TMDb id (with the kind it belongs to), rating, genres (ids and names), year, poster path and
when it was fetched. Nothing else from TMDb goes to disk. An entry is refreshed (by the worker, while the archive
lists its title) once it is nearly N months old and deleted at N months (N = ``/admin`` setting, default 5),
checked on every read and at start. Without ``[tmdb] key`` the file is deleted. It replaces ``tmdb-cache.json``
(T15), migrated once and then deleted.

The worker looks up titles the archive lists that have no entry or a stale one, one request at a time through the
shared limiter (``details.Limiter``: page lookups first, ≤ 30 per minute in total). It never retries in a loop: a
title that failed waits an hour, a refusal (limit, Retry-After) ends the round.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol

FILE = "tmdb.sqlite"
OLD_FILE = "tmdb-cache.json"  # T15's JSON cache, migrated once
SIDE_FILES = ("-journal", "-wal", "-shm")
MONTH = 30 * 86400  # a "month" of the refresh setting; 6 of them stay inside TMDb terms 1.C (6 months)
DEFAULT_MONTHS = 5
LICENCE_MONTHS = 6  # TMDb terms 1.C (checked 2026-10-24); /admin warns above this
MAX_MONTHS = 999_999_999  # "any positive number" (owner, 2026-10-25); only keeps the arithmetic finite
SETTING = "tmdbRefreshMonths"
REFRESH_LEAD = 7 * 86400  # refreshed this long before the deletion date, so a listed title never goes blank
FAILED_WAIT = 3600.0  # a title whose lookup failed is not asked again for this long
WORKER_IDLE = 300.0  # between worker rounds
WORKER_BUSY = 20.0  # after the limiter refused (other requests first, or TMDb asked to wait)
PAGE_SIZE = 50  # /admin
WARNING = ("Licence TMDb povoluje uchovávat údaje nejvýš 6 měsíců. Delší doba je vaše rozhodnutí a TMDb může klíč "
           "zrušit.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  year INTEGER,
  title TEXT NOT NULL,
  matched INTEGER NOT NULL,
  match_kind TEXT,
  tmdb_id INTEGER,
  rating_value REAL,
  rating_votes INTEGER,
  genre_ids TEXT NOT NULL DEFAULT '[]',
  genre_names TEXT NOT NULL DEFAULT '[]',
  match_year INTEGER,
  poster TEXT,
  fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_fetched ON entries (fetched_at);
"""
COLUMNS = ("key, kind, year, title, matched, match_kind, tmdb_id, rating_value, rating_votes, genre_ids, "
           "genre_names, match_year, poster, fetched_at")


def clamp_months(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return min(value, MAX_MONTHS)
    return DEFAULT_MONTHS


def store_key(kind: str, year: int | None, title: str) -> str:
    """The same key as ``details.Query.key``."""
    return json.dumps([kind, year, title], ensure_ascii=False)


@dataclass(frozen=True)
class Entry:
    key: str
    kind: str  # the guessed kind of the key
    year: int | None  # the year we knew (from the description or ČT)
    title: str  # our cleaned title, the only thing TMDb got
    matched: bool
    match_kind: str | None = None  # "movie" | "tv": TMDb ids are per kind
    tmdb_id: int | None = None
    rating: dict | None = None  # {"value", "votes"}, only with enough votes
    genre_ids: tuple[int, ...] = ()
    genres: tuple[str, ...] = ()
    match_year: int | None = None
    poster: str | None = None  # TMDb's file name ("abc.jpg"), served through /dvb/details/image/w342/
    fetched_at: float = 0.0


class Query(Protocol):
    key: str


class TmdbStore:
    def __init__(self, state_dir: Path, months: Callable[[], int] = lambda: DEFAULT_MONTHS,
                 now: Callable[[], float] = time.time, log: Callable[[str], None] = print):
        self.path = Path(state_dir) / FILE
        self.months, self.now, self.log = months, now, log
        self._lock = threading.Lock()
        with self._lock:
            self._open()
        self.migrate()
        self.purge()

    # -- file ------------------------------------------------------------------------------
    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._conn() as db:
                db.executescript(SCHEMA)
                db.execute("SELECT count(*) FROM entries").fetchone()
        except sqlite3.DatabaseError as exc:
            self.log(f"dvb: tmdb: {self.path.name} unreadable ({exc}); starting empty")
            _unlink_all(self.path, self.log)
            with self._conn() as db:
                db.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """One connection per use (callers hold ``_lock``): the file is (re)created 0600 before SQLite opens
        it, and SQLite gives its journal the same mode."""
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        db = sqlite3.connect(str(self.path), timeout=10.0)
        try:
            with db:
                yield db
        finally:
            db.close()

    # -- ages ------------------------------------------------------------------------------
    @property
    def max_age(self) -> float:
        """Seconds after fetching when an entry is deleted (N months)."""
        return float(clamp_months(self.months()) * MONTH)

    @property
    def refresh_age(self) -> float:
        """Seconds after fetching when the worker refreshes an entry still listed."""
        age = self.max_age
        return age - min(REFRESH_LEAD, age / 4)

    def _alive(self, fetched_at: float) -> bool:
        return 0 <= self.now() - fetched_at < self.max_age

    # -- reading ---------------------------------------------------------------------------
    def get(self, key: str) -> Entry | None:
        """The entry, or None (also for one N months old, which is deleted right here)."""
        with self._lock, self._conn() as db:
            row = db.execute(f"SELECT {COLUMNS} FROM entries WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            entry = _entry(row)
            if not self._alive(entry.fetched_at):
                db.execute("DELETE FROM entries WHERE key = ?", (key,))
                return None
            return entry

    def get_many(self, keys: Iterable[str]) -> dict[str, Entry]:
        keys = list(dict.fromkeys(keys))
        out: dict[str, Entry] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            with self._lock, self._conn() as db:
                rows = db.execute(f"SELECT {COLUMNS} FROM entries WHERE key IN ({','.join('?' * len(chunk))})",
                                        chunk).fetchall()
            for row in rows:
                entry = _entry(row)
                if self._alive(entry.fetched_at):
                    out[entry.key] = entry
        return out

    def due(self, key: str) -> bool:
        """No entry, or one old enough to be refreshed."""
        entry = self.get(key)
        return entry is None or not 0 <= self.now() - entry.fetched_at < self.refresh_age

    def __len__(self) -> int:
        with self._lock, self._conn() as db:
            return db.execute("SELECT count(*) FROM entries").fetchone()[0]

    def page(self, number: int, size: int = PAGE_SIZE) -> list[Entry]:
        """Entries by title for /admin (read-only); expired ones are purged first."""
        self.purge()
        with self._lock, self._conn() as db:
            rows = db.execute(f"SELECT {COLUMNS} FROM entries ORDER BY title COLLATE NOCASE, key "
                                    "LIMIT ? OFFSET ?", (size, max(0, number - 1) * size)).fetchall()
        return [_entry(r) for r in rows]

    def posters(self) -> set[str]:
        """Poster files of live matches: the only ones the proxy fetches."""
        cutoff = self.now() - self.max_age
        with self._lock, self._conn() as db:
            rows = db.execute("SELECT poster, fetched_at FROM entries WHERE matched = 1 AND poster IS NOT NULL "
                                    "AND fetched_at > ?", (cutoff,)).fetchall()
        return {p for p, at in rows if self._alive(at)}

    # -- writing ---------------------------------------------------------------------------
    def put(self, key: str, kind: str, year: int | None, title: str, result: dict | None) -> Entry:
        """Store a lookup: ``result`` None = no match, else ``{"kind", "tmdbId", "rating", "genreIds", "genres",
        "year", "poster"}`` (poster = TMDb's file name or None)."""
        entry = entry_from(key, kind, year, title, result, self.now())
        with self._lock, self._conn() as db:
            db.execute(f"INSERT OR REPLACE INTO entries ({COLUMNS}) VALUES ({','.join('?' * 14)})",
                             _row(entry))
        return entry

    def delete(self, key: str) -> Entry | None:
        with self._lock, self._conn() as db:
            row = db.execute(f"SELECT {COLUMNS} FROM entries WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM entries WHERE key = ?", (key,))
        return _entry(row)

    def delete_all(self) -> int:
        with self._lock, self._conn() as db:
            return db.execute("DELETE FROM entries").rowcount

    def purge(self) -> int:
        """Delete every entry N months old (or dated in the future: a clock that jumped back)."""
        now = self.now()
        with self._lock, self._conn() as db:
            removed = db.execute("DELETE FROM entries WHERE fetched_at <= ? OR fetched_at > ?",
                                       (now - self.max_age, now + 86400)).rowcount
        if removed:
            self.log(f"dvb: tmdb: deleted {removed} entr{'y' if removed == 1 else 'ies'} older than "
                     f"{clamp_months(self.months())} month(s)")
        return removed

    def migrate(self) -> int:
        """T15's ``tmdb-cache.json`` → this store, once; the JSON file is deleted afterwards."""
        old = self.path.with_name(OLD_FILE)
        if not old.exists():
            return 0
        moved = 0
        try:
            raw = json.loads(old.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.log(f"dvb: tmdb: {OLD_FILE} unreadable ({type(exc).__name__}); not migrated")
            raw = None
        entries = raw.get("entries") if isinstance(raw, dict) else None
        rows = []
        for key, item in entries.items() if isinstance(entries, dict) else []:
            parsed = _old_entry(key, item)
            if parsed is not None and self._alive(parsed.fetched_at):
                rows.append(_row(parsed))
        with self._lock, self._conn() as db:
            for row in rows:
                moved += db.execute(f"INSERT OR IGNORE INTO entries ({COLUMNS}) VALUES "
                                          f"({','.join('?' * 14)})", row).rowcount
        for item in (old, old.with_name(old.name + ".tmp")):
            try:
                item.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.log(f"dvb: tmdb: cannot delete {item.name}: {exc}")
        self.log(f"dvb: tmdb: moved {moved} entr{'y' if moved == 1 else 'ies'} from {OLD_FILE} to {FILE}")
        return moved


def entry_from(key: str, kind: str, year: int | None, title: str, result: dict | None, now: float) -> Entry:
    if result is None:
        return Entry(key, kind, year, title, False, fetched_at=now)
    rating = result.get("rating")
    return Entry(key, kind, year, title, True, result.get("kind"), result.get("tmdbId"),
                 {"value": float(rating["value"]), "votes": int(rating["votes"])} if rating else None,
                 tuple(result.get("genreIds") or ()), tuple(result.get("genres") or ()), result.get("year"),
                 result.get("poster"), now)


def _row(e: Entry) -> tuple:
    return (e.key, e.kind, e.year, e.title, 1 if e.matched else 0, e.match_kind, e.tmdb_id,
            e.rating["value"] if e.rating else None, e.rating["votes"] if e.rating else None,
            json.dumps(list(e.genre_ids)), json.dumps(list(e.genres), ensure_ascii=False), e.match_year, e.poster,
            float(e.fetched_at))


def _entry(row: tuple) -> Entry:
    (key, kind, year, title, matched, match_kind, tmdb_id, value, votes, genre_ids, genre_names, match_year,
     poster, fetched_at) = row
    return Entry(key, kind, year, title, bool(matched), match_kind, tmdb_id,
                 {"value": value, "votes": votes} if value is not None and votes is not None else None,
                 tuple(i for i in _json_list(genre_ids) if isinstance(i, int) and not isinstance(i, bool)),
                 tuple(n for n in _json_list(genre_names) if isinstance(n, str)), match_year, poster, fetched_at)


def _json_list(text: Any) -> list:
    try:
        value = json.loads(text) if isinstance(text, str) else []
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _old_entry(key: Any, item: Any) -> Entry | None:
    """One ``tmdb-cache.json`` entry: key ``[kind, year, title]``, ``{"at", "match"}``; the match had genre names
    only (no ids), and its title/overview/runtime are not carried over (the rule stores none of them)."""
    try:
        kind, year, title = json.loads(key)
    except (TypeError, ValueError):
        return None
    if kind not in ("movie", "tv") or not isinstance(title, str) or not title or \
            not (year is None or (isinstance(year, int) and not isinstance(year, bool))):
        return None
    at = item.get("at") if isinstance(item, dict) else None
    match = item.get("match") if isinstance(item, dict) else None
    if not isinstance(at, (int, float)) or isinstance(at, bool) or not (match is None or isinstance(match, dict)):
        return None
    if match is None:
        return Entry(key, kind, year, title, False, fetched_at=float(at))
    tmdb_id = match.get("tmdbId")
    if match.get("kind") not in ("movie", "tv") or not isinstance(tmdb_id, int) or isinstance(tmdb_id, bool):
        return None
    rating = match.get("rating")
    good = isinstance(rating, dict) and isinstance(rating.get("value"), (int, float)) \
        and isinstance(rating.get("votes"), int)
    poster = match.get("poster")
    prefix = "/dvb/details/image/w342/"
    match_year = match.get("year")
    return Entry(key, kind, year, title, True, match["kind"], tmdb_id,
                 {"value": float(rating["value"]), "votes": rating["votes"]} if good else None, (),
                 tuple(g for g in match.get("genres") or [] if isinstance(g, str)),
                 match_year if isinstance(match_year, int) and not isinstance(match_year, bool) else None,
                 poster[len(prefix):] if isinstance(poster, str) and poster.startswith(prefix) else None, float(at))


def _unlink_all(path: Path, log: Callable[[str], None]) -> list[str]:
    removed = []
    for item in [path] + [path.with_name(path.name + s) for s in SIDE_FILES]:
        try:
            item.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            log(f"dvb: tmdb: cannot delete {item.name}: {exc}")
            continue
        removed.append(item.name)
    return removed


def remove_store(state_dir: Path, log: Callable[[str], None] = print) -> None:
    """``[tmdb] key`` is gone: nothing from TMDb stays on the Pi (the store and T15's old cache)."""
    state = Path(state_dir)
    removed = _unlink_all(state / FILE, log)
    for name in (OLD_FILE, OLD_FILE + ".tmp"):
        try:
            (state / name).unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            log(f"dvb: tmdb: cannot delete {name}: {exc}")
            continue
        removed.append(name)
    if removed:
        log(f"dvb: tmdb: no [tmdb] key, deleted {', '.join(removed)}")


class TmdbWorker:
    """Background lookups for titles the archive lists (``wanted``): no entry, or one due for refresh.
    ``lookup(query)`` asks TMDb at background priority and stores the result; it raises ``busy`` when the limiter
    refuses (page lookups first, the per-minute share, Retry-After) and ``failed`` when TMDb failed."""

    def __init__(self, store: TmdbStore, wanted: Callable[[], list], lookup: Callable[[Any], Any],
                 busy: type[Exception], failed: type[Exception], clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = print):
        self.store, self.wanted, self.lookup = store, wanted, lookup
        self.busy, self.failed = busy, failed
        self.clock, self.log = clock, log
        self._failed: dict[str, float] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()

    def run_once(self) -> tuple[int, bool]:
        """One round: (lookups done, refused by the limiter)."""
        self.store.purge()
        now = self.clock()
        self._failed = {k: t for k, t in self._failed.items() if now - t < FAILED_WAIT}
        done = 0
        seen: set[str] = set()
        for query in self.wanted():
            if query.key in seen or query.key in self._failed:
                continue
            seen.add(query.key)
            if not self.store.due(query.key):
                continue
            try:
                self.lookup(query)
            except self.busy:
                return done, True
            except self.failed as exc:
                self._failed[query.key] = self.clock()
                self.log(f"dvb: tmdb: background lookup failed: {exc}")
                continue
            done += 1
        return done, False

    def poke(self) -> None:
        """The archive listed titles: look at them soon."""
        self._wake.set()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="dvb-tmdb", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                _, refused = self.run_once()
            except Exception as exc:  # never let the worker die
                self.log(f"dvb: tmdb: worker round failed: {exc!r}")
                refused = False
            self._wake.clear()
            self._wake.wait(WORKER_BUSY if refused else WORKER_IDLE)
