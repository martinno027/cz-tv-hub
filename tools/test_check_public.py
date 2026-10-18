import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent / "check_public.py"


def run(repo, *args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=repo, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


class CheckPublicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("hello\n")
        self.add("README.md")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, *paths):
        subprocess.run(["git", "add", *paths], cwd=self.repo, check=True)

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.add(rel)

    def test_clean_repo_passes(self):
        self.assertEqual(run(self.repo).returncode, 0)

    def test_private_files_are_refused(self):
        for rel in ("CLAUDE.md", ".claude/settings.json", "notes/devices.md", "dist/plugin.zip", "app.apk"):
            with self.subTest(rel):
                self.write(rel, "x\n")
                result = run(self.repo, "--staged")
                self.assertEqual(result.returncode, 1)
                self.assertIn(rel, result.stderr)
                subprocess.run(["git", "rm", "-q", "--cached", rel], cwd=self.repo, check=True)

    def test_real_ct_host_in_fixtures_is_refused(self):
        self.write("spec/fixtures/live.json", '{"url": "https://api.ceskatelevize.cz/x"}\n')
        self.assertEqual(run(self.repo).returncode, 1)

    def test_private_patterns_file(self):
        self.write("docs/a.md", "Contact: someone.private@example.org\n")
        patterns = self.repo.parent / "patterns.txt"
        patterns.write_text("# comment\nsomeone\\.private\n")
        self.addCleanup(patterns.unlink)
        self.assertEqual(run(self.repo).returncode, 0)
        self.assertEqual(run(self.repo, env={"CZTVHUB_PRIVATE_PATTERNS": str(patterns)}).returncode, 1)

    def test_secret_key_is_refused_but_dev_key_allowed(self):
        self.write("server/deploy/dev.conf", "key = dev-key-dev-key-dev-key-dev-key-0000\n")
        self.assertEqual(run(self.repo).returncode, 0)
        fake_secret = "api" + "_key = " + "Ab1" * 11  # built at runtime so this file passes the guard
        self.write("server/deploy/real.conf", fake_secret + "\n")
        self.assertEqual(run(self.repo).returncode, 1)


if __name__ == "__main__":
    unittest.main()
