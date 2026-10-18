"""Shared helpers for the DVB tests: TVHeadend fixtures and a fake client."""
import json
from pathlib import Path

from cztvhub.dvb import tvheadend as tvh

FIXTURES = Path(__file__).parent / "fixtures" / "tvheadend"
CT1 = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
NOVA = "0b3e1d5a7c9f4e2a8b6d4c2e0f1a3b5c"
REC_RECORDING = "d1d2d3d4d5d6d7d8d9d0e1e2e3e4e5e6"
REC_COMPLETED = "c1c2c3c4c5c6c7c8c9c0b1b2b3b4b5b6"
REC_FAILED = "e1e2e3e4e5e6e7e8e9e0f1f2f3f4f5f6"
REC_SCHEDULED = "f1f2f3f4f5f6f7f8f9f0a1a2a3a4a5a6"
NOW = 1792300800 + 600  # 2026-10-18 07:30 Prague


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, data=b"", status=200, chunk=None):
        self.data, self.status, self.pos, self.closed = data, status, 0, False
        self.chunk = chunk

    def read(self, size=-1):
        if self.closed:
            return b""
        if size < 0:
            size = len(self.data)
        if self.chunk:
            size = min(size, self.chunk)
        out = self.data[self.pos:self.pos + size]
        self.pos += len(out)
        return out

    def close(self):
        self.closed = True


class FakeClient:
    """Stands in for TvheadendClient, backed by the fixtures."""

    base = "http://127.0.0.1:9981"

    def __init__(self):
        self.calls = []
        self.live_error = None
        self.live_data = b"\x47" * 1000
        self.recording_data = b"\x47" * 2000
        self.unreachable = False
        self.removed = []

    def _check(self):
        if self.unreachable:
            raise tvh.TvhError("down")

    def channels(self):
        self._check()
        self.calls.append("channels")
        return tvh.parse_channels(load("channel_grid.json"))

    def epg_events(self, start, stop):
        self._check()
        self.calls.append(("epg", start, stop))
        return [e for e in tvh.parse_events(load("epg_events_grid.json")) if e.stop > start and e.start < stop]

    def dvr_entries(self, grid="grid"):
        self._check()
        entries = tvh.parse_dvr_entries(load("dvr_entry_grid.json"))
        entries = [e for e in entries if e.uuid not in self.removed]
        if grid == "grid_finished":
            return [e for e in entries if e.sched_status == "completed"]
        if grid == "grid_failed":
            return [e for e in entries if e.sched_status == "completedError"]
        return entries

    def tuners(self):
        self._check()
        return tvh.parse_inputs(load("status_inputs.json"))

    def remove_dvr_entries(self, uuids):
        self.removed.extend(uuids)

    def open_live(self, uuid):
        self.calls.append(("live", uuid))
        if self.live_error:
            raise self.live_error
        return FakeResponse(self.live_data)

    def open_recording(self, uuid, offset=0):
        self.calls.append(("recording", uuid, offset))
        return FakeResponse(self.recording_data[offset:], 206 if offset else 200)

    def fetch_icon(self, icon):
        return b"PNG", "image/png"
