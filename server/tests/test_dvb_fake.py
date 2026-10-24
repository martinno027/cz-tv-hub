"""The fake TVHeadend's programmes vary like real broadcasts (T09)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cztvhub.dvb.fake import FakeTvheadend


class FakeEventsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeTvheadend(Path(self.tmp.name), log=lambda m: None)
        channel = self.fake.channels[0]
        self.events = [self.fake.event_for(channel, k) for k in range(12)]

    def test_episode_lines_vary(self):
        # The guide's fixed detail must be checked with no episode, a short one and one too long for the line.
        subtitles = [e["subtitle"] for e in self.events]
        self.assertIn("", subtitles)
        self.assertTrue(any(s.startswith("Díl ") for s in subtitles))
        self.assertTrue(any(len(s) > 80 for s in subtitles))

    def test_some_programmes_have_no_text_at_all(self):
        # Neither description nor summary: the case that moved the Apple TV detail (T09).
        bare = [e for e in self.events if not e["description"] and not e["summary"]]
        self.assertTrue(bare)
        self.assertTrue(any(len(e["description"]) > 120 for e in self.events))

    def test_events_differ(self):
        # A test comparing variants also asserts that they differ.
        self.assertGreater(len({(e["subtitle"], e["description"], e["summary"]) for e in self.events}), 3)


if __name__ == "__main__":
    unittest.main()
