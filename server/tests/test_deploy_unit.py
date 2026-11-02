"""The systemd units (server/deploy/): what the Pi's service depends on (T24), the certificate job (T47)."""
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


DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def unit_values(text, key):
    return [line.split("=", 1)[1] for line in text.splitlines() if line.startswith(key + "=")]


class CertUnitsTest(unittest.TestCase):
    """cztvhub-cert.service/.timer and what install.sh does with them (spec "HTTPS for the installed web app")."""

    def setUp(self):
        self.service = (DEPLOY / "cztvhub-cert.service").read_text(encoding="utf-8")
        self.timer = (DEPLOY / "cztvhub-cert.timer").read_text(encoding="utf-8")
        self.install = (DEPLOY / "install.sh").read_text(encoding="utf-8")

    def test_service_runs_the_root_owned_copy_of_the_script(self):
        self.assertEqual(unit_values(self.service, "Type"), ["oneshot"])
        # /opt/cztvhub (root's copy), never the clone or a path the gateway user can write
        self.assertEqual(unit_values(self.service, "ExecStart"), ["/bin/bash /opt/cztvhub/cert.sh"])
        self.assertEqual(unit_values(self.service, "User"), [])  # root: only root may ask tailscaled
        self.assertNotIn("[Install]", self.service)  # started by the timer

    def test_timer_is_daily(self):
        self.assertEqual(unit_values(self.timer, "OnCalendar"), ["daily"])
        self.assertEqual(unit_values(self.timer, "Persistent"), ["true"])
        self.assertEqual(unit_values(self.timer, "WantedBy"), ["timers.target"])

    def test_install_sh_installs_enables_and_does_not_fail_on_it(self):
        self.assertIn("sudo install -m 0755 deploy/cert.sh /opt/cztvhub/cert.sh", self.install)
        self.assertIn("/etc/systemd/system/cztvhub-cert.service", self.install)
        self.assertIn("/etc/systemd/system/cztvhub-cert.timer", self.install)
        self.assertIn("sudo systemctl enable --now cztvhub-cert.timer", self.install)
        # run once after the service made /var/lib/cztvhub; a failure is a printed reason, not a failed install
        run = self.install.index("if sudo timeout 300 bash /opt/cztvhub/cert.sh; then")
        self.assertLess(self.install.index("wait_for_health"), run)


if __name__ == "__main__":
    unittest.main()
