"""``cztvhub dvb-starts``: how far real programme starts (EIT present/following) are from the EPG (T14 phase 1).

Read-only. Takes the changes the recorder noted in ``<state_dir>/eit-starts.jsonl`` (eit.py), matches each with
the EPG programme of its channel (TVHeadend + the gateway's EPG history) by time and title and prints per channel
the programmes with ``seenAt − EPG start`` and ``eitStart − EPG start``, then a summary.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Callable

from ..config import Config
from .eit import EitStore
from .epg import PRAGUE
from .epgstore import EpgHistory
from .thumbs import Settings
from .tvheadend import EpgEvent, TvheadendClient, TvhError

TITLE_WINDOW = 3 * 3600  # a programme with the same title starting this close is the same one
NEAR_WINDOW = 15 * 60  # without a title match: the programme airing then, or starting this close
ACCURATE = 60  # s: "within ±1 min"
NO_EIT = {
    "none": "v proudu není EIT present/following žádné služby (vysílatel ho neposílá, nebo ho TVHeadend v profilu "
            "pass odfiltroval)",
    "otherServices": "EIT jen pro jiné služby než naši (TVHeadend přepsal service id? volba profilu Rewrite EIT)",
    "unknownService": "EIT více služeb a PAT neřekne, která je naše",
}


@dataclass
class Row:
    title: str
    epg_title: str | None
    epg_start: int | None
    eit_start: int | None
    seen: int
    running: int
    at_tune: bool
    same_title: bool

    @property
    def delay(self) -> int | None:
        return None if self.epg_start is None else self.seen - self.epg_start

    @property
    def eit_delta(self) -> int | None:
        return None if self.epg_start is None or self.eit_start is None else self.eit_start - self.epg_start

    @property
    def counted(self) -> bool:
        """In the summary: matched, and seen when it changed (not when the recorder tuned in)."""
        return self.epg_start is not None and not self.at_tune


def _epoch(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _norm(title: str) -> str:
    return " ".join(title.casefold().split())


def occurrences(changes: list[dict]) -> list[dict]:
    """One line per airing: consecutive lines of the same event (start or running_status changed) become one,
    represented by the line where it became running (4), else its first line."""
    out: list[list[dict]] = []
    for change in changes:
        if out and out[-1][0].get("eventId") == change.get("eventId"):
            out[-1].append(change)
        else:
            out.append([change])
    return [next((c for c in group if c.get("running") == 4), group[0]) for group in out]


def match(change: dict, programmes: list[EpgEvent]) -> tuple[EpgEvent | None, bool]:
    """The EPG programme a present event is: same title and nearest start, else the one airing / starting near
    it. Returns (programme, title matches)."""
    seen = _epoch(change.get("seenAt")) or 0
    ref = _epoch(change.get("eitStart")) or seen
    title = _norm(str(change.get("title") or ""))
    real = [p for p in programmes if p.stop > p.start]
    if title:
        same = [p for p in real if _norm(p.title) == title and abs(p.start - ref) <= TITLE_WINDOW]
        if same:
            return min(same, key=lambda p: abs(p.start - ref)), True
    airing = [p for p in real if p.start <= ref < p.stop]
    near = [p for p in real if abs(p.start - ref) <= NEAR_WINDOW]
    pick = min(near or airing, key=lambda p: abs(p.start - ref), default=None)
    return pick, False


def rows(changes: list[dict], programmes: list[EpgEvent]) -> list[Row]:
    out = []
    for change in occurrences(changes):
        programme, same = match(change, programmes)
        out.append(Row(str(change.get("title") or ""), programme.title if programme else None,
                       programme.start if programme else None, _epoch(change.get("eitStart")),
                       _epoch(change.get("seenAt")) or 0, int(change.get("running") or 0),
                       bool(change.get("atTune")), same))
    return out


def summary(values: list[int]) -> str:
    if not values:
        return "nic k porovnání"
    within = sum(1 for v in values if abs(v) <= ACCURATE)
    return (f"medián {fmt_delta(int(median(values)))}, rozsah {fmt_delta(min(values))} až {fmt_delta(max(values))}, "
            f"do ±1 min {within}/{len(values)} ({round(100 * within / len(values))} %)")


def fmt_delta(seconds: int | None) -> str:
    if seconds is None:
        return "–"
    sign = "-" if seconds < 0 else "+"
    s = abs(seconds)
    return f"{sign}{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{sign}{s // 60}:{s % 60:02d}"


def clock(epoch: int | None) -> str:
    if epoch is None:
        return "–"
    return datetime.fromtimestamp(epoch, PRAGUE).strftime("%d.%m. %H:%M:%S")


def report(records: list[dict], names: dict[str, str], recorded: set[str],
           programmes: Callable[[str, int, int], list[EpgEvent]], since: int, until: int,
           out: Callable[[str], None] = print, channel: str | None = None) -> int:
    """Print the report; returns the number of matched programmes counted in the summary."""
    window = [r for r in records if (_epoch(r.get("seenAt")) or 0) >= since]
    by_channel: dict[str, list[dict]] = {}
    for record in window:
        by_channel.setdefault(record["channel"], []).append(record)
    channels = sorted(set(by_channel) | recorded, key=lambda c: (names.get(c, c).casefold(), c))
    if channel is not None:
        channels = [c for c in channels if names.get(c, c).casefold() == channel.casefold() or c == channel]
        if not channels:
            out(f"Kanál {channel!r} nemá v posledních hodinách žádný záznam EIT.")
            return 0
    all_delays: list[int] = []
    all_eit: list[int] = []
    for ch in channels:
        lines = by_channel.get(ch, [])
        out(f"== {names.get(ch, ch)} ({ch})")
        for marker in (r for r in lines if "noEit" in r):
            reason = NO_EIT.get(str(marker["noEit"]), str(marker["noEit"]))
            services = f", služby v EIT: {marker['services']}" if marker.get("services") else ""
            out(f"   {clock(_epoch(marker.get('seenAt')))}  žádné EIT p/f do 5 min: {reason}{services}")
        changes = [r for r in lines if "noEit" not in r]
        if not changes:
            out("   žádná změna pořadu z EIT" + ("" if lines else " (nahrává se? EIT neposlán?)"))
            continue
        changes.sort(key=lambda r: str(r.get("seenAt")))
        found = rows(changes, programmes(ch, since - 6 * 3600, until + 3600))
        out(f"   {'pořad':32} {'EPG začátek':15} {'EIT začátek':15} {'vidět':15} {'zpoždění':>9} {'EIT−EPG':>8}  pozn.")
        for row in found:
            notes = []
            if row.at_tune:
                notes.append("při naladění, nepočítá se")
            if row.epg_start is None:
                notes.append("bez pořadu v EPG")
            elif not row.same_title:
                notes.append(f"v EPG: {row.epg_title}")
            if row.running != 4:
                notes.append(f"running_status {row.running}")
            out(f"   {(row.title or '?')[:32]:32} {clock(row.epg_start):15} {clock(row.eit_start):15} "
                f"{clock(row.seen):15} {fmt_delta(row.delay):>9} {fmt_delta(row.eit_delta):>8}  {'; '.join(notes)}")
        delays = [r.delay for r in found if r.counted and r.delay is not None]
        eits = [r.eit_delta for r in found if r.counted and r.eit_delta is not None]
        all_delays += delays
        all_eit += eits
        out(f"   zpoždění (vidět − EPG): {summary(delays)}")
        out(f"   EIT začátek − EPG:      {summary(eits)}")
    out("== Celkem")
    out(f"   zpoždění (vidět − EPG): {summary(all_delays)}")
    out(f"   EIT začátek − EPG:      {summary(all_eit)}")
    return len(all_delays)


def run_starts(cfg: Config, hours: int, channel: str | None, out: Callable[[str], None] = print,
               client: TvheadendClient | None = None, now: Callable[[], float] = time.time) -> int:
    dvb = cfg.dvb
    store = EitStore(dvb.state_dir, lambda: 14)
    records = store.read()
    if not store.path.exists():
        out(f"{store.path} neexistuje: brána ještě nic nezměřila (nahrává se nějaký kanál?)")
        return 1
    until = int(now())
    since = until - max(1, hours) * 3600
    tvh = client or TvheadendClient(cfg.tvheadend.url, cfg.tvheadend.user, cfg.tvheadend.password)
    history = EpgHistory(dvb.state_dir)
    names: dict[str, str] = {}
    try:
        names = {c.uuid: c.name for c in tvh.channels()}
    except TvhError as exc:
        out(f"TVHeadend neodpovídá ({exc}): kanály bez jmen, EPG jen z historie brány")
    live: list[EpgEvent] | None = None

    def programmes(ch: str, start: int, end: int) -> list[EpgEvent]:
        nonlocal live
        if live is None:
            try:
                live = tvh.epg_events(start, end)
            except TvhError:
                live = []
        events = [e for e in history.between(start, end) if e.channel == ch]
        have = {e.event_id for e in events}
        return events + [e for e in live if e.channel == ch and e.event_id not in have]

    value = Settings(dvb.state_dir).get("recordChannels", [])
    recorded = {c for c in value if isinstance(c, str)} if isinstance(value, list) else set()
    out(f"Skutečné začátky pořadů z EIT, posledních {hours} h (časy v Praze; zpoždění = kdy se pořad v EIT objevil"
        f" − začátek v EPG)")
    report(records, names, recorded, programmes, since, until, out, channel)
    return 0
