"""Watch log for ``/admin`` "Sledování" (spec/dvb-gateway.md "Watch log", T53; docs/legal-notes.md).

Off by default (``settings.json`` key ``watchLog``); switching it off ends the sessions in progress without writing
them and deletes the store. While on, the apps and the web report playback with ``POST /dvb/watch`` (start, a beat
every 60 s, stop); sessions in progress live in memory only (at most 4 per address, 200 in total, 60 reports per
address and minute) and end at ``stop`` or after 3 minutes without a beat. A session of at least 5 s is then
written as one line of ``<state_dir>/watch-log.jsonl`` (mode 0600): start, end, seconds, profile, device as the
sign-in log (``signinlog.py``), source, mode, channel and programme. Never a session id, position, idec or
recording id; never in the journal, an export or any ``/dvb/*`` answer. The newest 5 000 entries of at most 30 days
(trimmed when written, every 30 minutes, when ``/admin`` reads it and at every start of the service, also without
``[dvb]``; with the switch off a leftover file is deleted at start).
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping

from . import signinlog as sl

FILE = "watch-log.jsonl"
SETTING = "watchLog"  # settings.json (thumbs.Settings), default off
MAX_ENTRIES = 5000
SLACK = 500  # lines over MAX_ENTRIES allowed on disk before a rewrite
MAX_DAYS = 30
MAX_AGE = MAX_DAYS * 86400
MAX_BODY = 2048
PER_ADDRESS = 4  # sessions in progress per address
MAX_SESSIONS = 200  # sessions in progress in total
EXPIRY_SECONDS = 180.0  # a session without a beat for this long has ended (the apps beat every 60 s)
MIN_SECONDS = 5  # shorter sessions are not written
RATE_LIMIT = 60  # reports per address and RATE_WINDOW
RATE_WINDOW = 60.0
LIMIT_LOG_SECONDS = 60.0  # "watch: limit" at most once a minute
MAX_TRACKED = 1000  # addresses kept for the rate limit
MAX_TITLE = 200
PAGE_SIZE = 50
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
EVENTS = ("start", "beat", "stop")
SOURCES = ("ct", "psp", "dvb")
MODES = ("live", "startover", "archive", "recording", "timeline")  # startover: anything from ČT's 3 h buffer
UNKNOWN = "unknown"
FIELDS = ("start", "end", "seconds", "profile", "address", "network", "platform", "client", "browser", "system",
          "source", "mode", "channel", "title")
TEXT_FIELDS = ("profile", "client", "browser", "system", "title")


class BadReport(ValueError):
    """A body that is not a report (400): not JSON, too large, or ``event``/``session`` wrong."""


def parse_report(body: bytes) -> dict:
    """The report as sent; only ``event`` and ``session`` are checked here (the rest is made neutral later)."""
    if len(body) > MAX_BODY:
        raise BadReport("too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BadReport("not JSON")
    if not isinstance(payload, dict) or payload.get("event") not in EVENTS \
            or not isinstance(payload.get("session"), str) or not SESSION_RE.match(payload["session"]):
        raise BadReport("event or session")
    return payload


def clean_title(value: Any) -> str | None:
    """A programme title as shown: control and format characters removed, whitespace collapsed, at most 200
    characters; anything that is not a non-empty string is no title."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in value[:4 * MAX_TITLE])
    text = " ".join(text.split())[:MAX_TITLE].strip()
    return text or None


def neutral(value: Any, allowed: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in allowed else UNKNOWN


def enabled_in(state_dir: Path) -> bool:
    """The switch as stored in ``settings.json`` (what ``thumbs.Settings`` writes); unreadable = off."""
    try:
        data = json.loads((Path(state_dir) / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get(SETTING) is True


def _valid(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    start, end, seconds = sl.epoch(entry.get("start")), sl.epoch(entry.get("end")), entry.get("seconds")
    if start is None or end is None or not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
        return False
    if not all(isinstance(entry.get(k), str) for k in ("address", "network", "platform", "source", "mode",
                                                       "channel")):
        return False
    return all(entry.get(k) is None or isinstance(entry[k], str) for k in TEXT_FIELDS)


def read_entries(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    entries = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if _valid(entry):
            entries.append({k: entry[k] for k in FIELDS if entry.get(k) is not None})
    return entries


def keep(entries: list[dict], now: float) -> list[dict]:
    """The newest MAX_ENTRIES that ended at most MAX_DAYS ago, in file order (oldest first)."""
    young = [e for e in entries if now - (sl.epoch(e["end"]) or 0) <= MAX_AGE]
    return young[-MAX_ENTRIES:]


def trim_file(state_dir: Path, now: float | None = None, log: Callable[[str], None] = print) -> None:
    """At every start of the service (``cli serve``, also without ``[dvb]``): with the switch off a leftover file
    is deleted; otherwise old lines go and the mode is fixed. No file = nothing is created; an error is logged and
    the service still starts."""
    path = Path(state_dir) / FILE
    try:
        if not path.exists():
            return
        if not enabled_in(state_dir):
            count = len(read_entries(path))
            path.unlink()
            log(f"watch log: off, deleted the leftover log ({count} entr{'y' if count == 1 else 'ies'})")
            return
        kept = keep(read_entries(path), time.time() if now is None else now)
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if len(kept) != lines or (path.stat().st_mode & 0o777) != 0o600:
            sl.write_entries(path, kept)
    except (OSError, UnicodeDecodeError) as exc:
        log(f"watch log: cannot trim {path}: {exc}")


class _Session:
    __slots__ = ("address", "started", "seen", "start", "entry")

    def __init__(self, address: str, started: float, start: float, entry: dict):
        self.address, self.started, self.seen, self.start, self.entry = address, started, started, start, entry


class WatchLog:
    """The watch log of one gateway. ``settings`` is the admin settings (``thumbs.Settings``), ``now`` wall time
    (entry times), ``clock`` monotonic (durations, expiry, limits)."""

    def __init__(self, state_dir: Path, settings: Any, now: Callable[[], float] = time.time,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print):
        self.path = Path(state_dir) / FILE
        self.settings, self.now, self.clock, self.log = settings, now, clock, log
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], _Session] = {}  # (address, session id) -> session
        self._rates: dict[str, deque[float]] = {}
        self._dropped = 0  # starts over the session limits since the last "watch: limit" line
        self._limit_logged = float("-inf")
        trim_file(Path(state_dir), now(), log)
        try:
            self._entries = keep(read_entries(self.path), now())
        except (OSError, UnicodeDecodeError) as exc:
            log(f"watch log: cannot read {self.path}: {exc}")
            self._entries = []
        self._lines = len(self._entries)

    @property
    def enabled(self) -> bool:
        return self.settings.get(SETTING, False) is True

    def set_enabled(self, on: bool) -> int:
        """The switch. Off ends the sessions in progress without writing them and deletes the store; returns how
        many entries were deleted (0 when switched on)."""
        with self._lock:
            self.settings.update(**{SETTING: bool(on)})
            if on:
                return 0
            self._sessions.clear()
            self._rates.clear()
            return self._delete()

    # -- reports (POST /dvb/watch) ---------------------------------------------------------
    def report(self, report: Mapping[str, Any], address: str, describe: Callable[[], dict]) -> None:
        """One parsed report (``parse_report``). ``describe()`` is asked only for an accepted ``start`` and gives
        the session's fixed fields (profile, device, source, mode, channel, title). Nothing is answered: off,
        an unknown session or a limit all just drop the report."""
        if not self.enabled:
            return
        key = (address, report["session"])
        with self._lock:
            now = self.clock()
            self._expire(now)
            if not self._rate_ok(address, now):
                return
            session = self._sessions.get(key)
            if report["event"] == "beat":
                if session is not None:
                    session.seen = now
                return
            if report["event"] == "stop":
                if session is not None:
                    del self._sessions[key]
                    self._finish(session, now)
                return
            if session is not None:  # a start of a known session restarts nothing
                session.seen = now
                return
            if not self._room(address, now):
                return
        entry = describe()  # may ask TVHeadend: not under the lock
        with self._lock:
            now = self.clock()
            if key in self._sessions or not self.enabled:
                return
            if not self._room(address, now):
                return
            self._sessions[key] = _Session(address, now, self.now(), entry)

    def _room(self, address: str, now: float) -> bool:
        """Under the lock: a new session fits (4 per address, 200 in total); else counted for "watch: limit"."""
        mine = sum(1 for s in self._sessions.values() if s.address == address)
        if mine < PER_ADDRESS and len(self._sessions) < MAX_SESSIONS:
            return True
        self._dropped += 1
        if now - self._limit_logged >= LIMIT_LOG_SECONDS:
            self.log(f"watch: limit ({self._dropped} start{'' if self._dropped == 1 else 's'} dropped)")
            self._limit_logged, self._dropped = now, 0
        return False

    def _rate_ok(self, address: str, now: float) -> bool:
        times = self._rates.pop(address, None) or deque()
        while times and times[0] <= now - RATE_WINDOW:
            times.popleft()
        self._rates[address] = times  # re-inserted: the oldest address is dropped first
        while len(self._rates) > MAX_TRACKED:
            del self._rates[next(iter(self._rates))]
        if len(times) >= RATE_LIMIT:
            return False
        times.append(now)
        return True

    def _expire(self, now: float) -> None:
        """Under the lock: sessions without a beat for 3 minutes end at their last report."""
        for key, session in list(self._sessions.items()):
            if now - session.seen > EXPIRY_SECONDS:
                del self._sessions[key]
                self._finish(session, session.seen)

    def _finish(self, session: _Session, ended: float) -> None:
        seconds = int(round(ended - session.started))
        if seconds < MIN_SECONDS:
            return
        start = int(session.start)
        entry = {"start": sl.iso(start), "end": sl.iso(start + seconds), "seconds": seconds, **session.entry}
        self._write({k: entry[k] for k in FIELDS if entry.get(k) is not None})

    # -- /admin ----------------------------------------------------------------------------
    def sweep(self) -> None:
        """Ended sessions written and old entries dropped: every 30 minutes and before ``/admin`` reads."""
        with self._lock:
            self._expire(self.clock())
            kept = keep(self._entries, self.now())
            if len(kept) != len(self._entries) or self._lines != len(kept):
                self._rewrite(kept)

    trim = sweep

    def now_playing(self) -> list[dict]:
        """The sessions in progress, longest first: start (UTC) and ``seconds`` so far; no session id."""
        self.sweep()
        with self._lock:
            now = self.clock()
            rows = [{"start": sl.iso(s.start), "seconds": max(0, int(now - s.started)), **s.entry}
                    for s in self._sessions.values()]
        rows.sort(key=lambda r: -r["seconds"])
        return [{k: r.get(k) for k in FIELDS if k != "end"} for r in rows]

    def page(self, page: int, profile: str | None = None, device: str | None = None,
             channel: str | None = None) -> dict:
        """History newest first, 50 a page. ``device`` = ``"<platform> <address>"``, ``channel`` = ``"<source>
        <channel>"`` (the values of ``devices`` / ``channels``); ``profile`` the profile's name."""
        self.sweep()
        with self._lock:
            entries = sorted(reversed(self._entries), key=lambda e: e["end"], reverse=True)
        profiles = sorted({e["profile"] for e in entries if "profile" in e}, key=str.casefold)
        devices = sorted({device_key(e) for e in entries}, key=str.casefold)
        channels = sorted({channel_key(e) for e in entries}, key=str.casefold)
        if profile is not None:
            entries = [e for e in entries if e.get("profile") == profile]
        if device is not None:
            entries = [e for e in entries if device_key(e) == device]
        if channel is not None:
            entries = [e for e in entries if channel_key(e) == channel]
        pages = max(1, -(-len(entries) // PAGE_SIZE))
        rows = entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        return {"enabled": self.enabled, "total": len(entries), "page": page, "pages": pages, "pageSize": PAGE_SIZE,
                "profiles": profiles, "devices": devices, "channels": channels,
                "entries": [{k: e.get(k) for k in FIELDS} for e in rows]}

    def delete_all(self) -> int:
        """"Smazat záznamy": the stored entries go (sessions in progress stay and are written when they end)."""
        with self._lock:
            return self._delete()

    # -- helpers ---------------------------------------------------------------------------
    def _delete(self) -> int:
        count = len(self._entries)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.log(f"watch log: cannot delete {self.path}: {exc}")
        self._entries, self._lines = [], 0
        return count

    def _write(self, entry: dict) -> None:
        self._entries.append(entry)
        now = self.now()
        try:
            if self._lines + 1 > MAX_ENTRIES + SLACK or now - (sl.epoch(self._entries[0]["end"]) or 0) > MAX_AGE:
                self._rewrite(keep(self._entries, now))
                return
            sl.append_entry(self.path, entry)
            self._lines += 1
            if len(self._entries) > MAX_ENTRIES:
                del self._entries[:len(self._entries) - MAX_ENTRIES]
        except OSError as exc:
            self.log(f"watch log: cannot write {self.path}: {exc}")

    def _rewrite(self, entries: list[dict]) -> None:
        try:
            sl.write_entries(self.path, entries)
        except OSError as exc:
            self.log(f"watch log: cannot write {self.path}: {exc}")
            return
        self._entries, self._lines = entries, len(entries)


def device_key(entry: Mapping[str, Any]) -> str:
    return f"{entry.get('platform') or UNKNOWN} {entry.get('address') or ''}"


def channel_key(entry: Mapping[str, Any]) -> str:
    return f"{entry.get('source') or UNKNOWN} {entry.get('channel') or UNKNOWN}"
