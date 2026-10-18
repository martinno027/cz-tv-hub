import importlib.util
import pathlib
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
spec = importlib.util.spec_from_file_location("kodi_build", ROOT / "kodi-addon" / "build.py")
kodi_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kodi_build)


class BuildTest(unittest.TestCase):
    def test_zip_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = kodi_build.build(pathlib.Path(tmp))
            names = set(zipfile.ZipFile(target).namelist())
        prefix = "plugin.video.cztvhub/"
        for name in ("addon.xml", "main.py", "service.py", "resources/lib/cztvhub/api.py",
                     "resources/lib/cztvhub/guide.py", "resources/lib/cztvhub/plugin.py"):
            self.assertIn(prefix + name, names)
        self.assertNotIn(prefix + "resources/lib/cztvhub/server.py", names)
        self.assertTrue(target.name.startswith("plugin.video.cztvhub-"))
