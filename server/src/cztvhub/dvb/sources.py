"""MPEG-TS byte sources for the HLS remuxer: live channel, finished and in-progress recordings."""
from __future__ import annotations

import threading
from typing import Any, Callable

from .tvheadend import TvheadendClient, TvhError


class GrowingRecording:
    """Reads ``/dvrfile/<uuid>`` while TVHeadend is still writing it.

    At the end of the file it polls (Range from the current offset) until
    ``still_recording()`` is False, then delivers the tail and ends.
    """

    def __init__(self, client: TvheadendClient, dvr_uuid: str, still_recording: Callable[[], bool],
                 poll: float = 2.0):
        self.client, self.dvr_uuid, self.still_recording, self.poll = client, dvr_uuid, still_recording, poll
        self.offset = 0
        self._response: Any = None
        self._closed = threading.Event()
        self._last_round = False

    def _open(self) -> None:
        try:
            response = self.client.open_recording(self.dvr_uuid, self.offset)
        except TvhError:  # 416 at the current end of the file, or a transient error
            return
        if self.offset and getattr(response, "status", 206) == 200:
            remaining = self.offset  # server ignored Range: skip what we already sent
            while remaining > 0:
                skipped = response.read(min(remaining, 1 << 20))
                if not skipped:
                    response.close()
                    return
                remaining -= len(skipped)
        self._response = response

    def read(self, size: int) -> bytes:
        while not self._closed.is_set():
            if self._response is None:
                self._open()
            if self._response is not None:
                try:
                    data = self._response.read(size)
                except OSError:
                    data = b""
                if data:
                    self.offset += len(data)
                    return data
                self._response.close()
                self._response = None
            if self._last_round:
                return b""
            try:
                recording = self.still_recording()
            except TvhError:
                recording = True
            if not recording:
                self._last_round = True  # one more read for the bytes written since
                continue
            self._closed.wait(self.poll)
        return b""

    def close(self) -> None:
        self._closed.set()
        if self._response is not None:
            try:
                self._response.close()
            except Exception:
                pass
