"""Lifecycle test for the unified Day 8 runtime."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from main import ChiefMindRuntime  # noqa: E402


class ChiefMindRuntimeTests(unittest.TestCase):
    def test_workers_receive_one_shared_shutdown_event(self) -> None:
        started = {"one": threading.Event(), "two": threading.Event()}
        stopped = {"one": threading.Event(), "two": threading.Event()}

        def worker(name: str):
            def run(stop_event: threading.Event) -> None:
                started[name].set()
                stop_event.wait(2)
                stopped[name].set()

            return run

        runtime = ChiefMindRuntime(
            services={"one": worker("one"), "two": worker("two")}
        )
        runtime.start()
        self.assertTrue(started["one"].wait(1))
        self.assertTrue(started["two"].wait(1))

        runtime.stop()
        runtime.wait()

        self.assertTrue(stopped["one"].is_set())
        self.assertTrue(stopped["two"].is_set())
        self.assertFalse(runtime.failures)


if __name__ == "__main__":
    unittest.main()
