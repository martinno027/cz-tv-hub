"""Staging the web app for /opt/cztvhub/web (server/deploy/stage_web.py, called by install.sh)."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
_spec = importlib.util.spec_from_file_location("stage_web", DEPLOY / "stage_web.py")
stage_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stage_web)

INFO = {"version": "1.2", "build": "321", "commit": "abc1234", "time": "1. 11. 12:00"}


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class StageWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        web, app = self.repo / "apps" / "web", self.repo / "apps" / "tizen" / "app"
        write(web / "index.html", "<html>")
        write(web / "js" / "web-player.js")
        write(web / "css" / "web.css")
        write(web / "vendor" / "hls.min.js")
        write(web / "vendor" / "LICENSE", "Apache")
        write(web / "README.md", "pins")
        write(web / "test" / "player.test.js")
        write(web / ".eslintrc.json")
        write(app / "index.html", "<samsung>")
        write(app / "config.xml", "<widget>")
        write(app / "js" / "core" / "models.js")
        write(app / "js" / "ui" / "app.js")
        write(app / "js" / "local-config.js", "window.CT_LOCAL = {key: 'REAL-KEY'};")
        write(app / "js" / "build-info.js", "window.CT_BUILD_INFO = {old: 1};")
        write(app / "css" / "app.css")
        write(app / "img" / "tmdb-logo-short.svg", "<svg/>")
        write(app / ".buildResult" / "x.js")
        write(app / "fixtures" / "real.json", "{}")
        write(app / "js" / "fixtures" / "real.json", '{"real": "REAL-DATA"}')
        write(web / "fixtures" / "real.json", '{"real": "REAL-DATA"}')
        write(web / "js" / "tests" / "x.js", "REAL-DATA")
        write(Path(self.tmp.name) / "outside" / "secret.js", "secret")
        os.symlink(Path(self.tmp.name) / "outside" / "secret.js", app / "js" / "linked.js")
        os.symlink(Path(self.tmp.name) / "outside", web / "linked")
        self.target = Path(self.tmp.name) / "stage" / "web"

    def files(self):
        return sorted(str(p.relative_to(self.target)) for p in self.target.rglob("*") if p.is_file())

    def test_mapping(self):
        count = stage_web.stage(self.target, self.repo, INFO)
        self.assertEqual(self.files(), [
            "app/css/app.css", "app/img/tmdb-logo-short.svg", "app/index.html", "app/js/core/models.js",
            "app/js/ui/app.js", "css/web.css", "index.html", "js/build-info.js", "js/web-player.js",
            "vendor/LICENSE", "vendor/hls.min.js"])
        self.assertEqual(count, 11)
        self.assertEqual((self.target / "index.html").read_text(), "<html>")  # the web page at the top
        self.assertEqual((self.target / "app" / "index.html").read_text(), "<samsung>")  # boot.js reads it

    def test_never_the_key_preset_symlinks_or_fixtures(self):
        stage_web.stage(self.target, self.repo, INFO)
        everything = b"".join(p.read_bytes() for p in self.target.rglob("*") if p.is_file())
        self.assertNotIn(b"REAL-KEY", everything)
        self.assertNotIn(b"REAL-DATA", everything)
        self.assertNotIn(b"secret", everything)
        self.assertNotIn(b"old: 1", everything)
        self.assertFalse(any(p.is_symlink() for p in self.target.rglob("*")))

    def test_build_info_where_boot_js_loads_it(self):
        write(self.repo / "apps" / "web" / "js" / "build-info.js", "window.CT_BUILD_INFO = {dev: 1};")
        stage_web.stage(self.target, self.repo, INFO)
        script = (self.target / "js" / "build-info.js").read_text()
        self.assertTrue(script.startswith("window.CT_BUILD_INFO = "))
        self.assertEqual(json.loads(script[len("window.CT_BUILD_INFO = "):].rstrip().rstrip(";")), INFO)

    def test_idempotent_and_fresh(self):
        stage_web.stage(self.target, self.repo, INFO)
        write(self.target / "stale.js")
        os.remove(self.repo / "apps" / "web" / "js" / "web-player.js")
        stage_web.stage(self.target, self.repo, INFO)
        self.assertNotIn("stale.js", self.files())
        self.assertNotIn("js/web-player.js", self.files())

    def test_no_web_app_no_files(self):
        os.remove(self.repo / "apps" / "web" / "index.html")
        self.assertEqual(stage_web.stage(self.target, self.repo, INFO), 0)
        self.assertFalse(self.target.exists())

    def test_served_types_match_the_gateway(self):
        from cztvhub.dvb import web
        self.assertEqual(stage_web.SERVED, set(web.CONTENT_TYPES))

    def test_build_info_from_the_samsung_build(self):
        info = stage_web.build_info(stage_web.ROOT)
        self.assertEqual(set(info), {"version", "build", "commit", "time"})


class InstallScriptTest(unittest.TestCase):
    def test_install_stages_and_swaps(self):
        text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
        self.assertIn('python3 deploy/stage_web.py "$web_stage/web"', text)
        self.assertIn("sudo mv /opt/cztvhub/web.new /opt/cztvhub/web", text)
        self.assertLess(text.index("stage_web.py"), text.index("systemctl restart"))


if __name__ == "__main__":
    unittest.main()
