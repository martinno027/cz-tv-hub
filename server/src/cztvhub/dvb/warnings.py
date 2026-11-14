"""ČHMÚ weather warnings for Domů (spec/dvb-gateway.md "Weather", T59, approved by the user 2026-11-14).

Every 15 min, while weather is in use (a household place is set or ``/dvb/weather`` answered within the last hour),
the gateway reads ČHMÚ's public directory listing ``LISTING_URL`` (no location is ever sent there). The listing
holds ``alert_cap_50_DDHHMM.xml`` files whose names repeat month by month, about 1.5–2.3 MB each (seen
2026-11-14), so the newest is chosen by the listing's date column; only a file the gateway has not read yet is
fetched, and it is refused when its ``<sent>`` is older than the last good one. Each file holds the whole current
state (CAP 1.2, cs + en-GB ``info`` blocks, ``area`` per kraj with ``geocode`` ``CISORP`` = ORP code).

Kept: ``cs`` blocks whose ``awareness_level`` parameter ("2; yellow; Moderate") is yellow/orange/red and whose
``responseType`` is present and not ``None`` (ČHMÚ sends "no warning" as real blocks with responseType None,
severity Minor, certainty Unlikely, and the outlook "Výhled jevů" with a level but without any responseType; a
warning may carry several), with an ``expires`` in the
future or none: a block without ``expires`` is open-ended (seen 2026-07-31: forest-fire risk and ozone smog, valid
until a later file ends them) and is answered with ``"to": null`` while the newest file lists it. Since 2026-07-01 ČHMÚ words its events by impact, so nothing here matches event strings.
XML is parsed only without a DTD (a file declaring one is refused: no entity expansion), ≤ 4 MB, no redirects.
A failure keeps the last good file for up to 6 h; after that there are no warnings until a fetch succeeds.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import orp

LISTING_URL = "https://opendata.chmi.cz/meteorology/weather/alerts/cap/"
USER_AGENT = "cz-tv-hub (household gateway; https://github.com/martinno027/cz-tv-hub)"
INTERVAL = 900.0
IN_USE_SECONDS = 3600.0  # a /dvb/weather answer keeps the loop fetching this long
KEEP_SECONDS = 6 * 3600.0
MAX_LISTING = 2 * 1024 * 1024  # 100 KB on 2026-11-14 (783 files)
MAX_FILE = 4 * 1024 * 1024
TIMEOUT = 30.0
LEVELS = ("yellow", "orange", "red")  # weather_cases.json "warningLevels"
MAX_EVENT = 200
NS = "{urn:oasis:names:tc:emergency:cap:1.2}"
MONTHS = {m: i + 1 for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                          "Nov", "Dec"))}
ENTRY_RE = re.compile(r'href="(alert_cap_50_[0-9]{6}\.xml)">[^<]*</a>\s+([0-9]{2})-([A-Z][a-z]{2})-([0-9]{4}) '
                      r'([0-9]{2}):([0-9]{2})')
FILE_RE = re.compile(r"^alert_cap_50_[0-9]{6}\.xml$")


class WarningsError(Exception):
    """ČHMÚ failed or sent something unusable; the text names no URL."""


@dataclass(frozen=True)
class Alert:
    level: str
    event: str
    start: datetime | None
    end: datetime | None
    codes: frozenset[str]

    def answer(self) -> dict:
        return {"level": self.level, "event": self.event, "from": _iso(self.start), "to": _iso(self.end)}


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment is not None else None


def _time(text: str | None) -> datetime | None:
    """A CAP time ("2026-07-31T20:13:21+02:00"); None without a zone or when unparsable."""
    try:
        moment = datetime.fromisoformat((text or "").strip())
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def newest_file(listing: str) -> tuple[str, datetime] | None:
    """(name, listed time) of the ``alert_cap_50_*.xml`` with the latest date in the directory listing (ties: the
    later name). The time tells a file from last month's of the same name."""
    best: tuple[datetime, str] | None = None
    for name, day, month, year, hour, minute in ENTRY_RE.findall(listing):
        if month not in MONTHS:
            continue
        try:
            moment = datetime(int(year), MONTHS[month], int(day), int(hour), int(minute))
        except ValueError:
            continue
        if best is None or (moment, name) > best:
            best = (moment, name)
    return (best[1], best[0]) if best else None


def parse(body: bytes) -> tuple[datetime, list[Alert]]:
    """(sent, kept warnings) of one CAP file. WarningsError for a DTD, bad XML or a file that is not ``Actual``."""
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in body.lower():
        raise WarningsError("CAP file declares a DTD or entities")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise WarningsError(f"CAP file is not XML ({exc.code})") from None
    if root.tag != NS + "alert":
        raise WarningsError("not a CAP 1.2 alert")
    sent = _time(root.findtext(NS + "sent"))
    if sent is None:
        raise WarningsError("CAP file without a valid sent time")
    if (root.findtext(NS + "status") or "").strip() != "Actual":
        raise WarningsError("CAP file is not Actual")
    out: list[Alert] = []
    for info in root.iter(NS + "info"):
        warning = _warning(info)
        if warning is not None and warning not in out:
            out.append(warning)
    return sent, out


def _warning(info: ET.Element) -> Alert | None:
    if (info.findtext(NS + "language") or "").strip() != "cs":
        return None
    responses = {(e.text or "").strip() for e in info.findall(NS + "responseType")}
    if not responses or "None" in responses:  # all-clear (None) and the outlook (no responseType at all)
        return None
    level = None
    for parameter in info.iter(NS + "parameter"):
        if (parameter.findtext(NS + "valueName") or "").strip() == "awareness_level":
            parts = [p.strip().lower() for p in (parameter.findtext(NS + "value") or "").split(";")]
            level = parts[1] if len(parts) > 1 and parts[1] in LEVELS else None
    if level is None:
        return None
    end = _time(info.findtext(NS + "expires"))  # None: until a later file ends it
    event = " ".join((info.findtext(NS + "event") or "").split())[:MAX_EVENT]
    codes = frozenset((geocode.findtext(NS + "value") or "").strip()
                      for area in info.iter(NS + "area") for geocode in area.iter(NS + "geocode")
                      if (geocode.findtext(NS + "valueName") or "").strip() == "CISORP")
    if not event or not codes:
        return None
    start = _time(info.findtext(NS + "onset")) or _time(info.findtext(NS + "effective"))
    return Alert(level, event, start, end, codes - {""})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class Warnings:
    """The last good warnings and the loop that refreshes them."""

    def __init__(self, urlopen: Callable[..., Any] | None = None, now: Callable[[], float] = time.time,
                 log: Callable[[str], None] = print, in_use: Callable[[], bool] = lambda: False):
        self._urlopen = urlopen or _OPENER.open
        self.now, self.log = now, log
        self.in_use = in_use  # a household place is set
        self._lock = threading.Lock()
        self._warnings: list[Alert] = []
        self._sent: datetime | None = None
        self._file: tuple[str, datetime] | None = None
        self._good_at = float("-inf")  # when the data last proved current (a good file, or no newer one listed)
        self._used_at = float("-inf")
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def used(self) -> None:
        """A /dvb/weather answer: keep fetching for the next hour; a use after a pause wakes the loop at once."""
        was = self.wanted()
        self._used_at = self.now()
        if not was:
            self._wake.set()

    def wanted(self) -> bool:
        return self.in_use() or self.now() - self._used_at < IN_USE_SECONDS

    def for_place(self, lat: float, lon: float) -> list[dict]:
        """Warnings for the ORP nearest to the place that have not ended, in the file's order; [] when nothing is
        current."""
        code, _name = orp.nearest(lat, lon)
        now = datetime.fromtimestamp(self.now(), timezone.utc)
        with self._lock:
            if self.now() - self._good_at >= KEEP_SECONDS:
                return []
            found = [w for w in self._warnings if code in w.codes and (w.end is None or w.end > now)]
        return [w.answer() for w in found]

    def refresh(self) -> None:
        """One round: the listing, then the newest file if it is new. Errors are logged, the last data kept."""
        try:
            listing = self._get(LISTING_URL, MAX_LISTING, "listing").decode("utf-8", "replace")
            newest = newest_file(listing)
            if newest is None or not FILE_RE.match(newest[0]):
                raise WarningsError("no alert_cap_50 file in the listing")
            if newest == self._file:
                with self._lock:
                    self._good_at = self.now()
                return
            sent, warnings = parse(self._get(LISTING_URL + newest[0], MAX_FILE, "file"))
            with self._lock:
                if self._sent is not None and sent < self._sent:
                    raise WarningsError("the newest listed file is older than the last one read")
                self._warnings, self._sent, self._file, self._good_at = warnings, sent, newest, self.now()
        except WarningsError as exc:
            self.log(f"warnings: {exc}")

    def _get(self, url: str, limit: int, what: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self._urlopen(request, timeout=TIMEOUT) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise WarningsError(f"ČHMÚ {what}: HTTP {status}")
                body = response.read(limit + 1)
        except WarningsError:
            raise
        except urllib.error.HTTPError as exc:
            exc.close()
            raise WarningsError(f"ČHMÚ {what}: HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise WarningsError(f"ČHMÚ {what}: {_reason(exc.reason)}") from None
        except (OSError, ValueError) as exc:
            raise WarningsError(f"ČHMÚ {what}: {type(exc).__name__}") from None
        if len(body) > limit:
            raise WarningsError(f"ČHMÚ {what}: larger than {limit} bytes")
        return body

    # -- loop --------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dvb-weather-warnings", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.wanted():
                self.refresh()
            self._wake.wait(INTERVAL)
            self._wake.clear()


def _reason(reason: object) -> str:
    """urllib's reason without its text (it can quote the request): the exception's type, or "unreachable"."""
    return type(reason).__name__ if isinstance(reason, BaseException) else "unreachable"
