"""Ad breaks of "Anténa" recordings (spec/ad-breaks.md): validation, storage and the worker's job list.

The gateway only stores timestamps uploaded by the household's own detection worker; it never analyses or
copies video itself.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .epg import recording_state
from .tvheadend import Channel, DvrEntry

UUID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ANALYZER_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
MIN_BREAK, MAX_BREAK = 20.0, 20 * 60.0
MAX_BREAKS = 200
MAX_JOBS = 10


class InvalidBreaks(ValueError):
    pass


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidBreaks(f"{name}: expected a number")
    return float(value)


def validate(payload: Any) -> tuple[str, list[dict[str, float]]]:
    """``{"analyzer": str, "breaks": [{start, end, confidence}]}`` → (analyzer, breaks); raises InvalidBreaks."""
    if not isinstance(payload, dict):
        raise InvalidBreaks("expected a JSON object")
    analyzer = payload.get("analyzer")
    if not isinstance(analyzer, str) or not ANALYZER_RE.match(analyzer):
        raise InvalidBreaks("analyzer: expected a short name")
    items = payload.get("breaks")
    if not isinstance(items, list) or len(items) > MAX_BREAKS:
        raise InvalidBreaks(f"breaks: expected a list of at most {MAX_BREAKS}")
    result: list[dict[str, float]] = []
    previous_end = 0.0
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise InvalidBreaks(f"breaks[{i}]: expected an object")
        start = _number(item.get("start"), f"breaks[{i}].start")
        end = _number(item.get("end"), f"breaks[{i}].end")
        confidence = _number(item.get("confidence", 1.0), f"breaks[{i}].confidence")
        if start < previous_end:
            raise InvalidBreaks(f"breaks[{i}]: not sorted or overlapping")
        if not MIN_BREAK <= end - start <= MAX_BREAK:
            raise InvalidBreaks(f"breaks[{i}]: length must be {MIN_BREAK:.0f} s – {MAX_BREAK:.0f} s")
        if not 0.0 <= confidence <= 1.0:
            raise InvalidBreaks(f"breaks[{i}].confidence: expected 0–1")
        result.append({"start": start, "end": end, "confidence": confidence})
        previous_end = end
    return analyzer, result


class BreakStore:
    """One small JSON file per recording; writes are atomic (temp file + rename)."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, recording_id: str) -> Path:
        if not UUID_RE.match(recording_id):
            raise ValueError(f"bad recording id {recording_id!r}")
        return self.directory / f"{recording_id}.json"

    def get(self, recording_id: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._path(recording_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def put(self, recording_id: str, analyzer: str, breaks: list[dict[str, float]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(recording_id)
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"analyzer": analyzer, "breaks": breaks}, f)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def ids(self) -> set[str]:
        try:
            return {p.stem for p in self.directory.glob("*.json") if UUID_RE.match(p.stem)}
        except OSError:
            return set()

    def prune(self, keep: Iterable[str]) -> list[str]:
        """Deletes the breaks of recordings that no longer exist; returns their ids."""
        removed = sorted(self.ids() - set(keep))
        for recording_id in removed:
            self._path(recording_id).unlink(missing_ok=True)
        return removed


def channel_selected(channel: Channel | None, names: list[str] | None) -> bool:
    if channel is None:
        return False
    return names is None or channel.name in names


def analysable(entry: DvrEntry | None, channels: dict[str, Channel], names: list[str] | None) -> bool:
    """A completed recording on one of the `[ads] channels` (what the worker may read and annotate)."""
    return (entry is not None and recording_state(entry.sched_status) == "completed"
            and channel_selected(channels.get(entry.channel), names))


def jobs(entries: list[DvrEntry], channels: dict[str, Channel], names: list[str] | None, store: BreakStore,
         analyzer: str, limit: int = MAX_JOBS) -> list[DvrEntry]:
    """Analysable recordings without breaks from ``analyzer``, oldest first."""
    pending = []
    for entry in sorted(entries, key=lambda e: e.start):
        if not analysable(entry, channels, names):
            continue
        stored = store.get(entry.uuid)
        if stored is None or stored.get("analyzer") != analyzer:
            pending.append(entry)
        if len(pending) >= limit:
            break
    return pending
