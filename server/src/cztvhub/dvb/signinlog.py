"""Sign-in and connection log for ``/admin`` (spec/dvb-gateway.md "Sign-in log", docs/legal-notes.md).

What is written, and only that: connections (``GET /dvb/status`` with the app key, at most once per 12 hours per
address and platform, at most 20 per address per 24 hours plus one "too many") and the profile events sign-in
(success, wrong password, unknown name, too many attempts), profile created, sign-out and sessions ended on
``/admin``. Never what is watched (that is the separate, switchable watch log, ``watchlog.py``), never a password,
token, typed name, request body or full User-Agent.

``<state_dir>/signin-log.jsonl``: one JSON object per line, mode 0600, the newest 1 000 entries of at most 90 days
(trimmed when written, daily while running and at every start of the service, also without ``[dvb]``). The dedup
and flood state is in memory only, bounded, and lost on restart. Nothing here is served on ``/dvb/*``.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from . import ctweb

FILE = "signin-log.jsonl"
MAX_ENTRIES = 1000
MAX_DAYS = 90
MAX_AGE = MAX_DAYS * 86400
SLACK = 100  # lines over MAX_ENTRIES allowed on disk before a rewrite (a flood must not rewrite the file per entry)
CONNECTION_SECONDS = 12 * 3600  # one "connect" per address and platform
ADDRESS_WINDOW = 24 * 3600
ADDRESS_LIMIT = 20  # "connect" entries per address per window, then one "connectLimit"
FAILED_LIMIT = 20  # failed sign-in entries (wrong password, unknown name, too many) per address per window, then
# one "loginFailedLimit" (coordinator 2026-11-01: a flood of made-up names must not push real entries out)
LOCKED_SECONDS = 60  # "loginLocked" at most once a minute per name
MAX_TRACKED = 1000  # addresses, address+platform pairs and names kept in memory
PAGE_SIZE = 50
CLIENT_HEADER = "X-Cztvhub-Client"
CLIENT_RE = re.compile(r"^[A-Za-z0-9 ./()+_-]{1,64}$")
PLATFORMS = ("tvos", "androidtv", "tizen", "web")
MAX_USER_AGENT = 512  # longer values are not looked at

# Stored event codes (neutral); /admin shows them in Czech.
CONNECT = "connect"
CONNECT_LIMIT = "connectLimit"
LOGIN_FAILED_LIMIT = "loginFailedLimit"  # „příliš mnoho neúspěšných přihlášení": only "address" and "network"
LOGIN = "login"
LOGIN_FAILED = "loginFailed"  # with "profile": wrong password; without: unknown name (the text is not kept)
LOGIN_LOCKED = "loginLocked"  # "count": refused attempts since the previous such entry for the name
PROFILE_CREATED = "profileCreated"
LOGOUT = "logout"
SESSIONS_ENDED = "sessionsEnded"  # "reason": newPassword | deleted, "count": sessions ended
EVENTS = (CONNECT, CONNECT_LIMIT, LOGIN_FAILED_LIMIT, LOGIN, LOGIN_FAILED, LOGIN_LOCKED, PROFILE_CREATED, LOGOUT, SESSIONS_ENDED)
NETWORKS = ("home", "tailscale", "local", "other")
REASONS = ("newPassword", "deleted")
FIELDS = ("time", "event", "profile", "address", "network", "client", "platform", "browser", "system", "count",
          "reason")


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(stamp: Any) -> float | None:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def network(peer: str) -> str:
    """"local" for loopback, "tailscale" for the tailnet ranges, "home" for the home LAN ranges of ``ctweb.py``,
    else "other" (a misconfiguration: the gateway is LAN/tailnet only)."""
    try:
        addr = ipaddress.ip_address(peer.split("%", 1)[0])
    except ValueError:
        return "other"
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return "local"
    if addr in (ctweb.TAILNET_V4 if addr.version == 4 else ctweb.TAILNET_V6):
        return "tailscale"
    return "home" if ctweb.home_lan(str(addr)) else "other"


def client_info(headers: Mapping[str, str]) -> dict[str, str | None]:
    """``client`` (the validated header), ``platform`` and, for the web or an unknown client, a coarse browser
    and system from ``User-Agent``. An invalid header is ignored as if it were missing."""
    raw = headers.get(CLIENT_HEADER) or ""
    client = raw if CLIENT_RE.match(raw) else None
    head = client.split("/", 1)[0] if client else ""
    platform = head if head in PLATFORMS else "unknown"
    browser = system = None
    if platform in ("web", "unknown"):
        browser, system = coarse_browser(headers.get("User-Agent") or "")
    return {"client": client, "platform": platform, "browser": browser, "system": system}


def coarse_browser(agent: str) -> tuple[str | None, str | None]:
    """(Chrome | Safari | Edge | Firefox | None, macOS | Windows | iPhone | iPad | Android | Linux | None)."""
    if not agent or len(agent) > MAX_USER_AGENT:
        return None, None
    if re.search(r"\b(Edg|EdgA|EdgiOS)/", agent):
        browser = "Edge"
    elif re.search(r"\b(Firefox|FxiOS)/", agent):
        browser = "Firefox"
    elif re.search(r"\b(Chrome|CriOS)/", agent):
        browser = "Chrome"
    elif "Safari/" in agent and "Version/" in agent:
        browser = "Safari"
    else:
        browser = None
    for token, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                        ("Macintosh", "macOS"), ("Mac OS X", "macOS"), ("Linux", "Linux"), ("X11", "Linux")):
        if token in agent:
            return browser, name
    return browser, None


def _valid(entry: Any) -> bool:
    """A line the page can show; anything else in the file is dropped, not guessed at."""
    return isinstance(entry, dict) and entry.get("event") in EVENTS and epoch(entry.get("time")) is not None


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
            entries.append(entry)
    return entries


def keep(entries: list[dict], now: float) -> list[dict]:
    """The newest MAX_ENTRIES of at most MAX_DAYS, in file order (oldest first)."""
    young = [e for e in entries if now - (epoch(e["time"]) or 0) <= MAX_AGE]
    return young[-MAX_ENTRIES:]


def write_entries(path: Path, entries: list[dict]) -> None:
    """Atomic rewrite with mode 0600 (also when an older file had other bits)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(_line(e) for e in entries))
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _line(entry: dict) -> str:
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"


def append_entry(path: Path, entry: dict) -> None:
    """One line appended, the file created (and kept) with mode 0600. Also the watch log's (watchlog.py)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        if os.fstat(fh.fileno()).st_mode & 0o777 != 0o600:
            os.fchmod(fh.fileno(), 0o600)
        fh.write(_line(entry))


def trim_file(state_dir: Path, now: float | None = None, log: Callable[[str], None] = print) -> None:
    """At every start of the service (``cli serve``, also without ``[dvb]``): drop old lines, fix the mode.
    No file = nothing to do (nothing is created); an error is logged, the service still starts."""
    path = Path(state_dir) / FILE
    try:
        if not path.exists():
            return
        entries = read_entries(path)
        kept = keep(entries, time.time() if now is None else now)
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if len(kept) != lines or (path.stat().st_mode & 0o777) != 0o600:
            write_entries(path, kept)
    except (OSError, UnicodeDecodeError) as exc:
        log(f"signin log: cannot trim {path}: {exc}")


class SigninLog:
    """The log of one gateway. ``now`` is wall time (entry times), ``clock`` monotonic (dedup windows)."""

    def __init__(self, state_dir: Path, now: Callable[[], float] = time.time,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print):
        self.path = Path(state_dir) / FILE
        self.now, self.clock, self.log = now, clock, log
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, str], float] = {}  # (address, platform) -> last "connect"
        self._per_address: dict[str, deque[float]] = {}  # address -> "connect" times in the window
        self._limited: set[str] = set()  # addresses whose "connectLimit" is written for the current window
        self._failed: dict[str, deque[float]] = {}  # address -> failed sign-in entry times in the window
        self._failed_limited: set[str] = set()
        self._locked: dict[str, list] = {}  # name key -> [last entry (clock), pending count, newest refused entry]
        trim_file(Path(state_dir), now(), log)
        try:
            self._entries = keep(read_entries(self.path), now())
        except (OSError, UnicodeDecodeError) as exc:
            log(f"signin log: cannot read {self.path}: {exc}")
            self._entries = []
        self._lines = len(self._entries)

    # -- events ----------------------------------------------------------------------------
    def connection(self, address: str, headers: Mapping[str, str]) -> None:
        """``GET /dvb/status`` with the app key."""
        info = client_info(headers)
        with self._lock:
            now = self.clock()
            pair = (address, info["platform"] or "unknown")
            last = self._seen.get(pair)
            if last is not None and now - last < CONNECTION_SECONDS:
                return
            if not self._under_cap(self._per_address, self._limited, ADDRESS_LIMIT, CONNECT_LIMIT, address, now):
                return
            self._seen.pop(pair, None)
            self._seen[pair] = now
            _bound(self._seen)
            self._append(self._request(CONNECT, None, address, info))

    def login(self, profile: str, address: str, headers: Mapping[str, str]) -> None:
        self.event(LOGIN, profile, address, headers)

    def login_failed(self, profile: str | None, address: str, headers: Mapping[str, str]) -> None:
        """``profile`` None = an unknown name; what was typed is not written. Counts to the address's cap."""
        entry = self._request(LOGIN_FAILED, profile, address, client_info(headers))
        with self._lock:
            if self._under_cap(self._failed, self._failed_limited, FAILED_LIMIT, LOGIN_FAILED_LIMIT, address,
                               self.clock()):
                self._append(entry)

    def login_locked(self, key: str, profile: str | None, address: str, headers: Mapping[str, str]) -> None:
        """A refused attempt (429) for the name ``key`` (in memory only). Written at once when the last entry
        for the name is a minute old; otherwise counted, and written with the count (timed at the newest of
        them) once the minute is over: with the next entry of any kind or when ``/admin`` reads the log."""
        entry = self._request(LOGIN_LOCKED, profile, address, client_info(headers))
        with self._lock:
            now = self.clock()
            entry = {"time": iso(self.now()), **entry}
            state = self._locked.pop(key, None)
            if state is not None and now - state[0] < LOCKED_SECONDS:
                state[1] += 1
                state[2] = entry  # the newest attempt is the one shown
                self._locked[key] = state
                return
            pending = state[1] if state is not None else 0
            self._locked[key] = [now, 0, entry]
            _bound(self._locked)
            # each name's own minute, but the address's failed sign-ins together are capped (many made-up names)
            if not self._under_cap(self._failed, self._failed_limited, FAILED_LIMIT, LOGIN_FAILED_LIMIT, address,
                                   now):
                return
            self._flush_locked(force=False)
            self._write({**entry, "count": pending + 1})

    def profile_created(self, profile: str, address: str, headers: Mapping[str, str]) -> None:
        self.event(PROFILE_CREATED, profile, address, headers)

    def logout(self, profile: str, address: str, headers: Mapping[str, str]) -> None:
        self.event(LOGOUT, profile, address, headers)

    def sessions_ended(self, profile: str, reason: str, count: int, address: str,
                       headers: Mapping[str, str]) -> None:
        """``/admin`` ended ``count`` sessions (new password, deleted profile); nothing when there were none.
        The address and browser are the admin's."""
        if count <= 0 or reason not in REASONS:
            return
        info = client_info({"User-Agent": headers.get("User-Agent") or ""})  # the admin page: no client header
        entry = self._request(SESSIONS_ENDED, profile, address, info)
        with self._lock:
            self._append({**entry, "reason": reason, "count": count})

    def event(self, event: str, profile: str | None, address: str, headers: Mapping[str, str]) -> None:
        entry = self._request(event, profile, address, client_info(headers))
        with self._lock:
            self._append(entry)

    # -- /admin ----------------------------------------------------------------------------
    def page(self, page: int, profile: str | None = None) -> dict:
        """Newest first; ``profile`` filters by the profile's name. Pending "too many" counts are written first."""
        with self._lock:
            self._flush_locked(force=True)
            if self._entries and self.now() - (epoch(self._entries[0]["time"]) or 0) > MAX_AGE:
                self._rewrite(keep(self._entries, self.now()))
            entries = sorted(reversed(self._entries), key=lambda e: e["time"], reverse=True)  # stable: newest first
        names = sorted({e["profile"] for e in entries if isinstance(e.get("profile"), str)}, key=str.casefold)
        if profile is not None:
            entries = [e for e in entries if e.get("profile") == profile]
        pages = max(1, -(-len(entries) // PAGE_SIZE))
        rows = entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        return {"total": len(entries), "page": page, "pages": pages, "pageSize": PAGE_SIZE, "profiles": names,
                "entries": [{k: e.get(k) for k in FIELDS} for e in rows]}

    def delete_all(self) -> int:
        """"Smazat záznamy": the file and the memory state go; returns how many entries there were."""
        with self._lock:
            count = len(self._entries)
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._entries, self._lines = [], 0
            self._seen.clear()
            self._per_address.clear()
            self._limited.clear()
            self._failed.clear()
            self._failed_limited.clear()
            self._locked.clear()
            return count

    def trim(self) -> None:
        """Daily while the service runs, so nothing older than 90 days waits for the next entry or start."""
        with self._lock:
            kept = keep(self._entries, self.now())
            if len(kept) != len(self._entries) or self._lines != len(kept):
                self._rewrite(kept)

    # -- helpers ---------------------------------------------------------------------------
    def _under_cap(self, table: dict[str, deque[float]], limited: set[str], limit: int, limit_event: str,
                   address: str, now: float) -> bool:
        """Under the lock: True (and counted) while the address has fewer than ``limit`` entries of this kind in
        the last 24 hours; at the limit one ``limit_event`` entry is written for the window, then nothing."""
        times = table.pop(address, None) or deque()
        while times and times[0] <= now - ADDRESS_WINDOW:
            times.popleft()
        table[address] = times  # re-inserted: the oldest address is dropped first
        _bound(table)
        if len(times) < limit:
            limited.discard(address)
            times.append(now)
            return True
        if address not in limited:
            limited.add(address)
            while len(limited) > MAX_TRACKED:
                limited.pop()
            self._write({"time": iso(self.now()), "event": limit_event, "address": address,
                         "network": network(address)})  # _write, not _append: also called from _flush_locked
        return False

    def _request(self, event: str, profile: str | None, address: str, info: Mapping[str, Any]) -> dict:
        entry: dict[str, Any] = {"event": event}
        if profile is not None:
            entry["profile"] = profile
        entry.update(address=address, network=network(address))
        entry.update({k: v for k, v in info.items() if v is not None})
        return entry

    def _append(self, entry: dict) -> None:
        """Under the lock: stamp, append one line; rewrite when over MAX_ENTRIES + SLACK or the oldest is too old."""
        self._flush_locked(force=False)
        self._write({"time": iso(self.now()), **entry})

    def _write(self, entry: dict) -> None:
        self._entries.append(entry)
        now = self.now()
        try:
            if self._lines + 1 > MAX_ENTRIES + SLACK or now - (epoch(self._entries[0]["time"]) or 0) > MAX_AGE:
                self._rewrite(keep(self._entries, now))
                return
            append_entry(self.path, entry)
            self._lines += 1
            if len(self._entries) > MAX_ENTRIES:
                del self._entries[:len(self._entries) - MAX_ENTRIES]
        except OSError as exc:
            self.log(f"signin log: cannot write {self.path}: {exc}")

    def _rewrite(self, entries: list[dict]) -> None:
        try:
            write_entries(self.path, entries)
        except OSError as exc:
            self.log(f"signin log: cannot write {self.path}: {exc}")
            return
        self._entries, self._lines = entries, len(entries)

    def _flush_locked(self, force: bool) -> None:
        """Pending "too many" counts whose minute is over (or all, ``force``) become entries."""
        now = self.clock()
        for state in self._locked.values():
            if state[1] and (force or now - state[0] >= LOCKED_SECONDS):
                count, entry = state[1], state[2]
                state[0], state[1] = now, 0
                if self._under_cap(self._failed, self._failed_limited, FAILED_LIMIT, LOGIN_FAILED_LIMIT,
                                   entry["address"], now):
                    self._write({**entry, "count": count})


def _bound(table: dict) -> None:
    while len(table) > MAX_TRACKED:
        del table[next(iter(table))]
