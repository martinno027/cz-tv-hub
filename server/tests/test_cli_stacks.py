"""``kill -USR1`` on the service prints every thread's stack and the service goes on (T45, docs/SCRIPTS.md)."""
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
CHILD = """
import sys, threading, time
from cztvhub.cli import _stacks_on_sigusr1
_stacks_on_sigusr1()
threading.Thread(target=lambda: time.sleep(30), name="waiting-worker", daemon=True).start()
print("ready", flush=True)
time.sleep(3)
print("still running", flush=True)
"""


@unittest.skipUnless(hasattr(signal, "SIGUSR1"), "no SIGUSR1 on this platform")
class StacksOnSigusr1Test(unittest.TestCase):
    def test_dump_and_keep_running(self):
        child = subprocess.Popen([sys.executable, "-c", CHILD], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env={**os.environ, "PYTHONPATH": SRC})
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        time.sleep(0.2)
        child.send_signal(signal.SIGUSR1)
        out, err = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 0, err)
        self.assertIn("still running", out)
        self.assertIn("most recent call first", err)
        self.assertGreaterEqual(err.count("Thread 0x") + err.count("Current thread 0x"), 2, err)


if __name__ == "__main__":
    unittest.main()
