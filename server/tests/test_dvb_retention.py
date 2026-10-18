import unittest

from cztvhub.dvb.retention import enforce_retention, select_expired
from cztvhub.dvb.tvheadend import DvrEntry
from dvb_fixtures import REC_COMPLETED, REC_FAILED, FakeClient

DAY = 86400
NOW = 1_800_000_000


def entry(uuid, status, stop):
    return DvrEntry(uuid, "ch", "t", "", "", [], stop - 3600, stop, status)


class RetentionTest(unittest.TestCase):
    def test_selects_only_finished_older_than_retention(self):
        entries = [
            entry("old-done", "completed", NOW - 3 * DAY - 1),
            entry("edge", "completed", NOW - 3 * DAY),
            entry("young", "completed", NOW - 3 * DAY + 60),
            entry("old-failed", "completedError", NOW - 5 * DAY),
            entry("old-warning", "completedWarning", NOW - 4 * DAY),
            entry("recording", "recording", NOW - 10 * DAY),  # never touch a running recording
            entry("scheduled", "scheduled", NOW - 10 * DAY),
            entry("no-stop", "completed", 0),
        ]
        self.assertEqual(select_expired(entries, NOW, 3), ["old-done", "edge", "old-failed", "old-warning"])

    def test_retention_days_respected(self):
        entries = [entry("a", "completed", NOW - 2 * DAY), entry("b", "completed", NOW - 15 * DAY)]
        self.assertEqual(select_expired(entries, NOW, 1), ["a", "b"])
        self.assertEqual(select_expired(entries, NOW, 14), ["b"])

    def test_enforce_removes_through_api(self):
        client = FakeClient()
        removed = enforce_retention(client, 3, now=1792300800 + 10 * DAY)
        self.assertEqual(set(removed), {REC_COMPLETED, REC_FAILED})
        self.assertEqual(set(client.removed), {REC_COMPLETED, REC_FAILED})
