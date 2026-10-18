"""Recording retention: delete finished recordings older than ``retention_days``.

TVHeadend's own DVR removal period is configured by ``dvb-setup`` as well; this loop
is the authoritative enforcement (it also covers any value TVHeadend rounds up).
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .epg import recording_state
from .tvheadend import DvrEntry, TvheadendClient, TvhError

CHECK_INTERVAL = 3600.0


def select_expired(entries: list[DvrEntry], now: float, retention_days: int) -> list[str]:
    """UUIDs of finished (completed or failed) entries that ended more than ``retention_days`` ago."""
    cutoff = now - retention_days * 86400
    expired = []
    for entry in entries:
        if recording_state(entry.sched_status) in ("recording", "scheduled"):
            continue
        if 0 < entry.stop <= cutoff:
            expired.append(entry.uuid)
    return expired


def enforce_retention(client: TvheadendClient, retention_days: int, now: float | None = None) -> list[str]:
    entries = client.dvr_entries("grid_finished") + client.dvr_entries("grid_failed")
    expired = select_expired(entries, time.time() if now is None else now, retention_days)
    client.remove_dvr_entries(expired)
    return expired


class RetentionLoop:
    def __init__(self, client: TvheadendClient, retention_days: int, interval: float = CHECK_INTERVAL,
                 log: Callable[[str], None] = print):
        self.client = client
        self.retention_days = retention_days
        self.interval = interval
        self.log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="dvb-retention", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        delay = min(60.0, self.interval)  # first run shortly after start
        while not self._stop.wait(delay):
            delay = self.interval
            try:
                removed = enforce_retention(self.client, self.retention_days)
                if removed:
                    self.log(f"dvb: removed {len(removed)} recording(s) older than {self.retention_days} days")
            except TvhError as exc:
                self.log(f"dvb: retention check failed: {exc}")
