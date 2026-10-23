"""Programme thumbnails from the household's own recordings (spec/dvb-gateway.md "Thumbnails",
approved by the user on 2026-10-23, docs/legal-notes.md).

One still per recording, made on request: a byte range of the recording (never a live tuner, never
CT internet streams) piped through ffmpeg into one small JPEG on stdout. Kept in memory only.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from .tvheadend import DvrEntry, TvheadendClient, TvhError

MAX_WIDTH = 480
QUALITY = 5  # mjpeg -q:v (2 best .. 31 worst), fixed
READ_BYTES = 2 * 1024 * 1024
FRAME_AT_SECONDS = 300  # "a few minutes in" (not current: it only has to show what the programme is)
FRAME_AT_SHARE = 0.2
CACHE_SIZE = 600  # the apps prefetch around the guide focus (spec/apps.md "Image prefetch")
REFRESH_SECONDS = 60.0
TIMEOUT = 10.0
MAX_JOBS = 2
FLIGHT_WAIT = 3 * TIMEOUT  # a follower waits out the slot wait, the recording read and ffmpeg (10 s each)
JPEG_SOI = b"\xff\xd8"


class ThumbsBusy(Exception):
    """Both ffmpeg slots stayed taken for ``TIMEOUT``: try again later (503), not "no frame" (404)."""


def ffmpeg_command(ffmpeg: str = "ffmpeg") -> list[str]:
    """stdin (MPEG-TS bytes) → stdout (one JPEG). Pipes only, by design."""
    return [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error",
            "-f", "mpegts", "-i", "pipe:0",
            "-frames:v", "1", "-vf", f"scale='min({MAX_WIDTH},iw)':-2", "-q:v", str(QUALITY),
            "-f", "image2", "-c:v", "mjpeg", "pipe:1"]


def frame_offset(size: int, recorded_seconds: float) -> tuple[int, bool]:
    """Byte offset of the frame ``min(300 s, 20 %)`` into the recording, and whether that point is
    final (it will not move any more as the recording grows)."""
    recorded = max(1.0, recorded_seconds)
    at = min(FRAME_AT_SECONDS, FRAME_AT_SHARE * recorded)
    offset = int(size / recorded * at)
    offset = max(0, min(offset, size - READ_BYTES))
    return offset, at >= FRAME_AT_SECONDS


class _Flight:
    """One ffmpeg run that concurrent requests for the same key wait for."""

    def __init__(self):
        self.done = threading.Event()
        self.data: bytes | None = None
        self.busy = False


class Settings:
    """Household switches set on the /admin page, in ``<state dir>/settings.json``."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "settings.json"
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data = loaded
        except (OSError, ValueError):
            pass

    @property
    def thumbnails(self) -> bool:
        return self._data.get("thumbnails", True) is not False

    def set_thumbnails(self, on: bool) -> None:
        self.update(thumbnails=bool(on))

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def update(self, **values: Any) -> None:
        with self._lock:
            self._data.update(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data), encoding="utf-8")
            tmp.replace(self.path)


class Thumbnails:
    def __init__(self, client: TvheadendClient, settings: Settings, ffmpeg: str = "ffmpeg",
                 run: Callable[..., Any] = subprocess.run, now: Callable[[], float] = time.time,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print):
        self.client, self.settings, self.ffmpeg = client, settings, ffmpeg
        self.run, self.now, self.clock, self.log = run, now, clock, log
        self._cache: OrderedDict[str, tuple[bytes, bool, float]] = OrderedDict()  # uuid → (jpeg, final, made at)
        self._lock = threading.Lock()
        self._jobs = threading.BoundedSemaphore(MAX_JOBS)
        self._flights: dict[str, _Flight] = {}  # key → the one run in progress for it

    @property
    def enabled(self) -> bool:
        return self.settings.thumbnails

    def set_enabled(self, on: bool) -> None:
        self.settings.set_thumbnails(on)
        if not on:
            self.clear()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def prune(self, existing) -> None:
        keep = set(existing)
        with self._lock:
            for uuid in [u for u in self._cache if u not in keep]:
                del self._cache[uuid]

    def forget(self, dvr_uuid: str) -> None:
        with self._lock:
            self._cache.pop(dvr_uuid, None)

    def get(self, entry: DvrEntry, recording: bool) -> bytes | None:
        """JPEG for a ``completed`` (``recording=False``) or still-recording entry, or None.
        Raises ThumbsBusy when no ffmpeg slot came free in time."""
        hit = self._fresh(entry.uuid)
        if hit is not None:
            return hit
        recorded = (min(self.now(), entry.stop) if recording else entry.stop) - entry.start
        if entry.filesize <= 0 or recorded <= 0:
            return None
        offset, at_final = frame_offset(entry.filesize, recorded)
        return self.get_from(entry.uuid, at_final or not recording, lambda: self._make(entry.uuid, offset))

    def get_from(self, key: str, final: bool, produce: Callable[[], bytes | None]) -> bytes | None:
        """Cached still for ``key``, else made by ``produce`` in one of the ffmpeg slots. Requests for the same
        key at the same time share one run (single flight); they all get its result or ThumbsBusy."""
        with self._lock:
            hit = self._fresh_locked(key)
            if hit is not None:
                return hit
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
        if not leader:
            if not flight.done.wait(FLIGHT_WAIT):
                raise ThumbsBusy()
            if flight.busy:
                raise ThumbsBusy()
            return flight.data
        try:
            if not self._jobs.acquire(timeout=TIMEOUT):
                flight.busy = True
                raise ThumbsBusy()
            try:
                flight.data = produce()
            finally:
                self._jobs.release()
            if flight.data is not None:
                with self._lock:
                    self._cache[key] = (flight.data, final, self.clock())
                    self._cache.move_to_end(key)
                    while len(self._cache) > CACHE_SIZE:
                        self._cache.popitem(last=False)
            return flight.data
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight.done.set()

    def _fresh(self, key: str) -> bytes | None:
        with self._lock:
            return self._fresh_locked(key)

    def _fresh_locked(self, key: str) -> bytes | None:
        hit = self._cache.get(key)
        if hit is None:
            return None
        data, final, made = hit
        if not final and self.clock() - made >= REFRESH_SECONDS:
            return None
        self._cache.move_to_end(key)
        return data

    def jpeg_from_mp4(self, data: bytes) -> bytes | None:
        """One JPEG from fMP4 bytes (init + one segment) on stdin; pipes only."""
        cmd = [self.ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-f", "mp4", "-i", "pipe:0",
               "-frames:v", "1", "-vf", f"scale='min({MAX_WIDTH},iw)':-2", "-q:v", str(QUALITY),
               "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
        try:
            result = self.run(cmd, input=data, capture_output=True, timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log(f"dvb: thumbnail: ffmpeg: {exc}")
            return None
        out = getattr(result, "stdout", b"") or b""
        return out if getattr(result, "returncode", 1) == 0 and out.startswith(JPEG_SOI) else None

    def _make(self, dvr_uuid: str, offset: int) -> bytes | None:
        try:
            response = self.client.open_recording(dvr_uuid, offset)
        except TvhError as exc:
            self.log(f"dvb: thumbnail {dvr_uuid}: {exc}")
            return None
        try:
            if offset and getattr(response, "status", 206) == 200:
                return None  # Range ignored: we would get the file's start, not the frame we chose
            chunks, size = [], 0
            while size < READ_BYTES:
                chunk = response.read(min(65536, READ_BYTES - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        except OSError:
            return None
        finally:
            response.close()
        if not chunks:
            return None
        try:
            result = self.run(ffmpeg_command(self.ffmpeg), input=b"".join(chunks), capture_output=True,
                              timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log(f"dvb: thumbnail {dvr_uuid}: ffmpeg: {exc}")
            return None
        out = getattr(result, "stdout", b"") or b""
        return out if getattr(result, "returncode", 1) == 0 and out.startswith(JPEG_SOI) else None
