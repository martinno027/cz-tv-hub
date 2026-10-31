"""The systemd unit (server/deploy/cztvhub.service): what the Pi's service depends on (T24)."""
import unittest
from pathlib import Path

UNIT = (Path(__file__).resolve().parents[1] / "deploy" / "cztvhub.service").read_text(encoding="utf-8")


def values(key):
    return [line.split("=", 1)[1] for line in UNIT.splitlines() if line.startswith(key + "=")]


class UnitTest(unittest.TestCase):
    def test_log_lines_are_not_buffered(self):
        # stdout to the journal is block-buffered: two days of gateway messages were one line (2026-10-31)
        self.assertIn("PYTHONUNBUFFERED=1", values("Environment"))

    def test_waits_for_the_recordings_disk_but_does_not_need_it(self):
        self.assertTrue(any("srv-tv.mount" in v.split() for v in values("After")))
        # a missing disk must not stop the CT streams: ordering only, no Requires/BindsTo/RequiresMountsFor
        for key in ("Requires", "BindsTo", "RequiresMountsFor", "Requisite"):
            self.assertFalse(any("srv-tv" in v or "/srv/tv" in v for v in values(key)), key)


if __name__ == "__main__":
    unittest.main()
