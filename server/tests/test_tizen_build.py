import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
spec = importlib.util.spec_from_file_location("tizen_build", ROOT / "apps" / "tizen" / "build.py")
tizen_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tizen_build)

KEY = "k" * 43


class TizenBuildTest(unittest.TestCase):
    def test_stage_copies_app_without_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = tizen_build.stage(pathlib.Path(tmp))
            for name in ("config.xml", "index.html", "icon.png", "js/core/api.js", "js/ui/player.js"):
                self.assertTrue((target / name).is_file(), name)
            self.assertFalse((target / "js" / "local-config.js").exists())

    def test_gateway_preset_only_in_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = tizen_build.stage(pathlib.Path(tmp), " pi.tail1234.ts.net ", KEY)
            text = (target / "js" / "local-config.js").read_text(encoding="utf-8")
        preset = json.loads(text.split("=", 1)[1].rstrip(";\n"))
        self.assertEqual(preset, {"gatewayAddress": "pi.tail1234.ts.net", "gatewayKey": KEY})
        self.assertFalse((tizen_build.APP_DIR / "js" / "local-config.js").exists())

    def test_build_info_only_in_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = tizen_build.stage(pathlib.Path(tmp))
            text = (target / "js" / "build-info.js").read_text(encoding="utf-8")
        info = json.loads(text.split("=", 1)[1].rstrip(";\n"))
        self.assertEqual(set(info), {"version", "build", "commit", "time"})
        self.assertRegex(info["version"], r"^\d+\.\d+\.\d+$")  # from config.xml
        self.assertFalse((tizen_build.APP_DIR / "js" / "build-info.js").exists())

    def test_build_info_values(self):
        from datetime import datetime, timezone
        from unittest import mock

        answers = {("rev-parse", "--short", "HEAD"): "abc1234", ("status", "--porcelain"): " M x",
                   ("rev-list", "--count", "HEAD"): "123"}
        with mock.patch.object(tizen_build, "_git", side_effect=lambda *a: answers[a]):
            info = tizen_build.build_info(datetime(2026, 10, 23, 13, 20, tzinfo=timezone.utc))
        self.assertEqual((info["build"], info["commit"], info["time"]), ("123", "abc1234+", "23. 10. 15:20"))
        with mock.patch.object(tizen_build, "_git", return_value=""):
            info = tizen_build.build_info(datetime(2026, 1, 5, 8, 7, tzinfo=timezone.utc))
        self.assertEqual((info["build"], info["commit"], info["time"]), ("?", "?", "5. 1. 09:07"))

    def test_short_key_rejected(self):
        with self.assertRaises(ValueError):
            tizen_build.local_config("pi", "short")


if __name__ == "__main__":
    unittest.main()
