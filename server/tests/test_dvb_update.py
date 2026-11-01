"""Updating the Pi from /admin (T33, dvb/update.py, spec/dvb-gateway.md): request file, status, "not installed",
the admin route. No systemd, no git: the path unit and the files are temporary; deploy/update.sh itself is
tested by deploy/update.test.sh."""
import base64
import http.client
import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from cztvhub.api import StreamResolver
from cztvhub.config import AdminConfig, Config, DvbConfig
from cztvhub.dvb.admin import AdminSite
from cztvhub.dvb.update import STALE_SECONDS, Updater
from cztvhub.server import Server, make_handler

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
PASSWORD = "admin-password-1"
AUTH = {"Authorization": "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()}
POST = {**AUTH, "X-Cztvhub-Admin": "1", "Host": "pi:8098"}
NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z
STARTED = "2027-01-15T07:58:00Z"
A, B = "a" * 40, "b" * 40


class Files(unittest.TestCase):
    """A state dir, a path unit and install.json in a temporary directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.state = self.dir / "state"
        self.state.mkdir()
        self.unit = self.dir / "cztvhub-update.path"
        self.info = self.dir / "install.json"
        self.now = NOW
        self.up = Updater(self.state, path_unit=self.unit, install_info=self.info, now=lambda: self.now)

    def install_unit(self, watched=None):
        self.unit.write_text(f"[Path]\nPathExists={watched or self.state / 'update-request'}\n"
                             "Unit=cztvhub-update.service\n", encoding="utf-8")

    def write_status(self, doc):
        (self.state / "update-status.json").write_text(doc if isinstance(doc, str) else json.dumps(doc),
                                                       encoding="utf-8")


class UpdaterTest(Files):
    # -- "not installed" ---------------------------------------------------------------------
    def test_not_installed_without_the_path_unit(self):
        # a dev Mac / an install before T33: no unit, so no request and no button
        self.assertFalse(self.up.installed())
        self.assertIn("není nainstalovaná", self.up.request())
        self.assertFalse((self.state / "update-request").exists())

    def test_installed_only_when_the_unit_watches_our_request_file(self):
        self.install_unit()
        self.assertTrue(self.up.installed())
        self.install_unit("/var/lib/elsewhere/update-request")  # [dvb] state_dir moved: the unit would never see it
        self.assertFalse(self.up.installed())
        self.unit.write_text("[Path]\n", encoding="utf-8")
        self.assertFalse(self.up.installed())
        self.unit.write_bytes(b"\xff\xfe")
        self.assertFalse(self.up.installed())

    def test_the_shipped_unit_matches_the_default_state_dir(self):
        shipped = Updater(DvbConfig().state_dir, path_unit=DEPLOY / "cztvhub-update.path")
        self.assertTrue(shipped.installed())

    # -- request -----------------------------------------------------------------------------
    def test_request_writes_the_file(self):
        self.install_unit()
        self.assertIsNone(self.up.request())
        request = self.state / "update-request"
        self.assertTrue(request.is_file())
        self.assertEqual(request.read_text(encoding="utf-8"), "requested 2027-01-15T08:00:00Z\n")
        self.assertFalse((self.state / "update-request.tmp").exists())
        self.assertTrue(self.up.overview()["requested"])

    def test_one_at_a_time(self):
        self.install_unit()
        self.write_status({"state": "running", "started": STARTED})
        self.assertEqual(self.up.request(), "Aktualizace už probíhá.")
        self.assertFalse((self.state / "update-request").exists())
        self.write_status({"state": "ok", "started": STARTED, "finished": STARTED})
        self.assertIsNone(self.up.request())
        self.assertEqual(self.up.request(), "Aktualizace už probíhá.")  # the first request not picked up yet

    def test_a_stale_running_status_does_not_block(self):
        # killed by the unit's backstop before it could write its end
        self.install_unit()
        self.write_status({"state": "running", "started": STARTED})
        self.now = NOW + STALE_SECONDS
        self.assertTrue(self.up.status()["stale"])
        self.assertIsNone(self.up.request())

    # -- status ------------------------------------------------------------------------------
    def test_status_missing(self):
        self.assertIsNone(self.up.status())
        self.assertFalse(self.up.running())

    def test_status_shapes(self):
        for state, step in (("running", None), ("ok", None), ("up-to-date", None), ("failed", "install"),
                            ("failed", "timeout"), ("failed", "pull"), ("failed", "check")):
            doc = {"state": state, "started": STARTED, "finished": None if state == "running" else STARTED,
                   "before": A, "after": B, "step": step, "message": "fatal: x" if step else None}
            self.write_status(doc)
            self.assertEqual(self.up.status(), {**doc, "stale": False}, state)
        self.assertFalse(self.up.running())
        self.write_status({"state": "running", "started": STARTED})
        self.assertTrue(self.up.running())

    def test_malformed_status_is_neutral(self):
        empty = {"state": "unknown", "started": None, "finished": None, "before": None, "after": None,
                 "step": None, "message": None, "stale": False}
        for doc in ("{not json", "[1, 2]", '"ok"', "", "\xff", "1e999999" * 3000):
            self.write_status(doc)
            self.assertEqual(self.up.status(), empty, doc[:20])
        (self.state / "update-status.json").write_bytes(b"\xff\xfe{")
        self.assertEqual(self.up.status(), empty)

    def test_unknown_values_stay_unknown(self):
        self.write_status({"state": "rebooting", "started": "yesterday", "finished": 5, "before": "HEAD",
                           "after": "rm -rf /", "step": "bash -c x", "message": 42, "extra": "dropped"})
        self.assertEqual(self.up.status(), {"state": "unknown", "started": None, "finished": None, "before": None,
                                            "after": None, "step": None, "message": None, "stale": False})

    def test_running_without_a_start_time_is_stale(self):
        self.write_status({"state": "running", "started": "not a time"})
        self.assertTrue(self.up.status()["stale"])
        self.assertFalse(self.up.running())

    def test_message_capped(self):
        self.write_status({"state": "failed", "step": "install", "message": "x" * 5000 + "END"})
        message = self.up.status()["message"]
        self.assertEqual(len(message), 2000)
        self.assertTrue(message.endswith("END"))

    # -- version line ------------------------------------------------------------------------
    def test_version(self):
        self.assertIsNone(self.up.version())
        self.info.write_text(json.dumps({"commit": A, "installed": STARTED}), encoding="utf-8")
        self.assertEqual(self.up.version(), {"commit": A, "installed": STARTED})
        for doc in ('{"commit": null}', "{bad", '{"commit": "main"}', "[]"):
            self.info.write_text(doc, encoding="utf-8")
            self.assertIsNone(self.up.version(), doc)


class AdminUpdateRouteTest(Files):
    def setUp(self):
        super().setUp()
        self.logged = []
        gateway = SimpleNamespace(log=self.logged.append, client=None, now=time.time)
        cfg = Config(dvb=DvbConfig(enabled=True, key="k" * 40), admin=AdminConfig(password=PASSWORD))
        self.site = AdminSite(cfg, gateway, admin=object(), updater=self.up)

    def call(self, method, headers, body=b"{}"):
        resp = self.site.handle(method, "/admin/api/update", headers, body)
        return resp.status, json.loads(resp.body) if resp.content_type.startswith("application/json") else None

    def request_file(self):
        return self.state / "update-request"

    def test_login_required(self):
        self.install_unit()
        for headers in ({}, {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()},
                        {"Authorization": "Bearer " + "k" * 40}):
            for method in ("GET", "POST"):
                status, _ = self.call(method, {**headers, "X-Cztvhub-Admin": "1"})
                self.assertEqual(status, 401, (headers, method))
        self.assertFalse(self.request_file().exists())
        self.assertEqual(self.logged, [])

    def test_post_needs_the_admin_header_and_same_origin(self):
        self.install_unit()
        for headers in (AUTH, {**POST, "X-Cztvhub-Admin": "0"}, {**POST, "Origin": "http://evil.example"},
                        {**POST, "Origin": "http://pi:8098.evil.example"}):
            status, _ = self.call("POST", headers)
            self.assertEqual(status, 403, headers)
        self.assertFalse(self.request_file().exists())
        status, data = self.call("POST", {**POST, "Origin": "http://pi:8098"})
        self.assertEqual((status, data), (202, {"requested": True}))
        self.assertTrue(self.request_file().exists())

    def test_post_is_logged(self):
        self.install_unit()
        status, _ = self.call("POST", POST, b"anything: the body is ignored")
        self.assertEqual(status, 202)
        self.assertEqual(self.logged, ["admin: update requested"])
        status, data = self.call("POST", POST)
        self.assertEqual((status, data["error"]), (409, "Aktualizace už probíhá."))
        self.assertEqual(self.logged[-1], "admin: update refused: Aktualizace už probíhá.")

    def test_not_installed(self):
        status, data = self.call("POST", POST)
        self.assertEqual(status, 409)
        self.assertIn("bash server/deploy/install.sh", data["error"])
        self.assertFalse(self.request_file().exists())
        self.assertTrue(self.logged[-1].startswith("admin: update refused: "))
        status, data = self.call("GET", AUTH)
        self.assertEqual((status, data["installed"], data["status"]), (200, False, None))

    def test_write_error(self):
        self.install_unit()
        self.state.rmdir()
        status, data = self.call("POST", POST)
        self.assertEqual(status, 500)
        self.assertTrue(self.logged[-1].startswith("admin: update request failed: "))

    def test_get(self):
        self.install_unit()
        self.info.write_text(json.dumps({"commit": A, "installed": STARTED}), encoding="utf-8")
        self.write_status({"state": "failed", "started": STARTED, "finished": STARTED, "before": A, "after": B,
                           "step": "pull", "message": "fatal: Not possible to fast-forward, aborting."})
        status, data = self.call("GET", AUTH, b"")
        self.assertEqual(status, 200)
        self.assertEqual(data["installed"], True)
        self.assertEqual(data["version"], {"commit": A, "installed": STARTED})
        self.assertEqual(data["status"]["state"], "failed")
        self.assertEqual(data["status"]["step"], "pull")
        self.assertFalse(data["requested"])
        self.assertEqual(self.logged, [])  # reading changes nothing

    def test_other_methods(self):
        self.install_unit()
        for method in ("PUT", "DELETE", "PATCH"):
            status, _ = self.call(method, POST)
            self.assertEqual(status, 405, method)
        self.assertFalse(self.request_file().exists())

    def test_over_http(self):
        # through the real server (port 0, never 8098): the POST reaches the site with its headers
        self.install_unit()
        server = Server(("127.0.0.1", 0), make_handler(StreamResolver(fetch=lambda c: "x"), None, self.site))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def call(method, headers):
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request(method, "/admin/api/update", body=b"{}" if method == "POST" else None, headers=headers)
            resp = conn.getresponse()
            data = json.loads(resp.read() or b"null")
            conn.close()
            return resp.status, data

        self.assertEqual(call("POST", {**AUTH, "X-Cztvhub-Admin": "1", "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(call("POST", {**AUTH, "X-Cztvhub-Admin": "1", "Origin": f"http://127.0.0.1:{port}"}),
                         (202, {"requested": True}))
        status, data = call("GET", AUTH)
        self.assertEqual((status, data["requested"], data["installed"]), (200, True, True))

    def test_page_has_the_update_tab(self):
        resp = self.site.handle("GET", "/admin", AUTH)
        page = resp.body.decode("utf-8")
        self.assertIn('id="tab-update"', page)
        self.assertIn("Aktualizovat Raspberry", page)
        self.assertIn("/admin/api/update", page)
        self.assertIn("Aktualizace z prohlížeče není nainstalovaná", page)


class UpdateUnitsTest(unittest.TestCase):
    """deploy/cztvhub-update.path, cztvhub-update.service.in and what install.sh does with them."""

    def setUp(self):
        self.path_unit = (DEPLOY / "cztvhub-update.path").read_text(encoding="utf-8")
        self.service = (DEPLOY / "cztvhub-update.service.in").read_text(encoding="utf-8")
        self.install = (DEPLOY / "install.sh").read_text(encoding="utf-8")

    @staticmethod
    def values(text, key):
        return [line.split("=", 1)[1] for line in text.splitlines() if line.startswith(key + "=")]

    def test_path_unit(self):
        self.assertEqual(self.values(self.path_unit, "PathExists"), [str(DvbConfig().state_dir / "update-request")])
        self.assertEqual(self.values(self.path_unit, "Unit"), ["cztvhub-update.service"])

    def test_service_runs_only_the_fixed_script(self):
        self.assertEqual(self.values(self.service, "Type"), ["oneshot"])
        self.assertEqual(self.values(self.service, "ExecStart"), ["/bin/bash @REPO@/server/deploy/update.sh"])
        self.assertEqual(sorted(self.values(self.service, "Environment")),
                         ["CZTVHUB_OWNER=@OWNER@", "CZTVHUB_REPO=@REPO@"])
        self.assertEqual(self.values(self.service, "User"), [])  # root: install.sh needs it
        # the script's own limit is 10 min; the unit's backstop is longer and below update.py's stale limit
        self.assertEqual(self.values(self.service, "TimeoutStartSec"), ["15min"])
        self.assertNotIn("[Install]", self.service)  # started only by the path unit

    def test_gateway_cannot_reach_the_clone_or_gain_root(self):
        # what keeps the gateway user from changing what root runs: it never sees the owner's clone in /home
        gateway = (DEPLOY / "cztvhub.service").read_text(encoding="utf-8")
        for line in ("User=cztvhub", "NoNewPrivileges=true", "ProtectHome=true", "ProtectSystem=strict"):
            self.assertIn(line, gateway.splitlines())

    def test_install_sh_installs_and_enables_them(self):
        self.assertIn("deploy/cztvhub-update.service.in", self.install)
        self.assertIn('-e "s|@REPO@|$repo|g" -e "s|@OWNER@|$owner|g"', self.install)
        self.assertIn("/etc/systemd/system/cztvhub-update.service", self.install)
        self.assertIn("/etc/systemd/system/cztvhub-update.path", self.install)
        self.assertIn("sudo systemctl enable --now cztvhub-update.path", self.install)
        # the clone path goes into a unit file: only plain characters
        self.assertIn("^/[A-Za-z0-9._/-]+$", self.install)
        # the installed commit is written only once the restarted service answered
        self.assertLess(self.install.index("wait_for_health"), self.install.index("/opt/cztvhub/install.json"))


if __name__ == "__main__":
    unittest.main()
