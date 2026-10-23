"""Past Anténa programme schedule (spec/dvb-gateway.md "EPG history").

TVHeadend drops an EPG event as soon as it ends, so without a recording a past programme vanished from
the guide. The gateway keeps the events it has seen (titles and times only, no video) in
``<state dir>/epg-history.json`` for the recording retention, and the guide merges them back in.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .tvheadend import DvrEntry, EpgEvent

FILE = "epg-history.json"
SAME_PROGRAMME = 120  # s: a recording and an EPG event this close on one channel are the same programme


class EpgHistory:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / FILE
        self._lock = threading.Lock()
        self._events: dict[str, EpgEvent] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw if isinstance(raw, list) else []:
                try:
                    event = EpgEvent(**item)
                except TypeError:
                    continue
                self._events[event.event_id] = event
        except (OSError, ValueError):
            pass

    def __len__(self) -> int:
        return len(self._events)

    def merge(self, fetched: Iterable[EpgEvent], now: float, window: tuple[int, int]) -> bool:
        """Add what TVHeadend just reported for ``window``. Its future part is authoritative: a stored
        programme that has not started yet and is no longer listed was rescheduled or cancelled."""
        fetched = list(fetched)
        ids = {e.event_id for e in fetched}
        lo, hi = window
        with self._lock:
            before = dict(self._events)
            for event_id, event in list(self._events.items()):
                if event.start >= now and lo <= event.start < hi and event_id not in ids:
                    del self._events[event_id]
            for event in fetched:
                self._events[event.event_id] = EpgEvent(**{**asdict(event), "dvr_uuid": "", "dvr_state": ""})
            return self._events != before

    def prune(self, before: float) -> bool:
        with self._lock:
            stale = [k for k, e in self._events.items() if e.stop < before]
            for k in stale:
                del self._events[k]
            return bool(stale)

    def between(self, start: int, end: int) -> list[EpgEvent]:
        with self._lock:
            return [e for e in self._events.values() if e.stop > start and e.start < end]

    def save(self) -> None:
        with self._lock:
            payload = json.dumps([asdict(e) for e in self._events.values()], ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)


def merge_past(live: list[EpgEvent], history: list[EpgEvent], entries: list[DvrEntry]) -> list[EpgEvent]:
    """TVHeadend's events plus remembered ones it no longer has; a remembered programme that has a DVR
    entry (shown from the recording) is left out, so nothing appears twice."""
    have = {e.event_id for e in live}
    recorded = [(d.channel, d.start) for d in entries if d.channel]
    extra = [h for h in history if h.event_id not in have
             and not any(ch == h.channel and abs(st - h.start) <= SAME_PROGRAMME for ch, st in recorded)]
    return live + extra
