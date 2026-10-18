import unittest

from cztvhub.dvb import tvheadend as tvh
from cztvhub.dvb.sources import GrowingRecording
from dvb_fixtures import FakeResponse


class GrowingFile:
    """A /dvrfile that grows by one block per poll and stops after ``blocks``."""

    def __init__(self, blocks=3, ignore_range=False):
        self.data = b""
        self.blocks, self.ignore_range, self.opens = blocks, ignore_range, []

    def grow(self):
        if len(self.data) < self.blocks * 4:
            self.data += bytes([65 + len(self.data) // 4]) * 4

    def open_recording(self, uuid, offset=0):
        self.opens.append(offset)
        if self.ignore_range:
            return FakeResponse(self.data, 200)
        if offset >= len(self.data):
            raise tvh.TvhError("416")
        return FakeResponse(self.data[offset:], 206 if offset else 200)

    def recording(self):
        return len(self.data) < self.blocks * 4


class GrowingRecordingTest(unittest.TestCase):
    def read_all(self, f, source):
        out = b""
        while True:
            chunk = source.read(100)
            if not chunk:
                return out
            out += chunk

    def run_case(self, f):
        f.grow()
        source = GrowingRecording(f, "u", f.recording, poll=0)
        original_wait = source._closed.wait
        source._closed.wait = lambda t: (f.grow(), original_wait(0))[1]
        return self.read_all(f, source)

    def test_follows_file_until_recording_ends(self):
        f = GrowingFile()
        self.assertEqual(self.run_case(f), b"AAAABBBBCCCC")
        self.assertEqual(f.opens[0], 0)
        self.assertIn(4, f.opens)

    def test_server_ignoring_range(self):
        f = GrowingFile(ignore_range=True)
        self.assertEqual(self.run_case(f), b"AAAABBBBCCCC")

    def test_close_stops_reading(self):
        f = GrowingFile(blocks=100)
        source = GrowingRecording(f, "u", lambda: True, poll=0)
        source.close()
        self.assertEqual(source.read(10), b"")
