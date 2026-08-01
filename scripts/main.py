"""Run the complete ChiefMind system with coordinated graceful shutdown."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from werkzeug.serving import BaseWSGIServer, make_server

import config
from approval_watcher import watch as watch_approvals
from gmail_watcher import run_watcher as watch_gmail


# scripts/main.py starts with scripts/ on sys.path; add only the project root so
# the existing dashboard package can be imported. Runtime paths remain in config.
if str(config.PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(config.PROJECT_ROOT))

from dashboard.app import app as dashboard_app  # noqa: E402


LOGGER = config.setup_logging("chiefmind.main")
Worker = Callable[[threading.Event], Any]


class ChiefMindRuntime:
    """Own worker threads and stop every service when one service fails."""

    def __init__(self, services: dict[str, Worker] | None = None) -> None:
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.failures: list[tuple[str, BaseException]] = []
        self.dashboard_server: BaseWSGIServer | None = None
        self._stopping = False
        self.services = services

    def _guard(self, name: str, worker: Worker) -> None:
        try:
            worker(self.stop_event)
        except BaseException as exc:  # capture worker failure for the main thread
            LOGGER.exception("Service %s stopped unexpectedly.", name)
            self.failures.append((name, exc))
            self.stop_event.set()

    def _dashboard(self, stop_event: threading.Event) -> None:
        self.dashboard_server = make_server(
            config.DASHBOARD_HOST,
            config.DASHBOARD_PORT,
            dashboard_app,
            threaded=True,
        )
        LOGGER.info(
            "Dashboard listening at http://%s:%s",
            config.DASHBOARD_HOST,
            config.DASHBOARD_PORT,
        )
        # serve_forever exits when shutdown() is called by the main thread.
        self.dashboard_server.serve_forever()

    def start(self) -> None:
        services = self.services or {
            "approval_watcher": lambda event: watch_approvals(event),
            "gmail_watcher": lambda event: watch_gmail(stop_event=event),
            "dashboard": self._dashboard,
        }
        for name, worker in services.items():
            thread = threading.Thread(
                target=self._guard,
                args=(name, worker),
                name=f"chiefmind-{name}",
                daemon=False,
            )
            thread.start()
            self.threads.append(thread)
            LOGGER.info("Started %s.", name)

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        LOGGER.info("Stopping ChiefMind services.")
        self.stop_event.set()
        if self.dashboard_server is not None:
            self.dashboard_server.shutdown()

    def wait(self) -> None:
        try:
            while any(thread.is_alive() for thread in self.threads):
                for thread in self.threads:
                    thread.join(timeout=0.5)
                if self.failures:
                    self.stop()
                elif self.stop_event.is_set():
                    self.stop()
        finally:
            self.stop()
            for thread in self.threads:
                thread.join(timeout=10)


def validate_runtime() -> None:
    """Fail before starting threads if an unattended runtime cannot work."""
    config.validate_config(strict=True)
    if not config.GOOGLE_TOKEN_FILE.is_file():
        raise config.ConfigError(
            "Missing Gmail token.json. Run scripts/authenticate_gmail.py first."
        )
    config.ensure_directories()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration and exit without starting services.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_runtime()
    except (config.ConfigError, ValueError) as exc:
        LOGGER.error("Startup validation failed: %s", exc)
        return 2
    if args.check:
        LOGGER.info("ChiefMind runtime configuration is valid.")
        return 0

    runtime = ChiefMindRuntime()

    def request_shutdown(signum: int, _frame: Any) -> None:
        LOGGER.info("Received signal %s.", signum)
        runtime.stop_event.set()
        # Do not call server.shutdown() from an asynchronous signal handler.

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_shutdown)

    try:
        runtime.start()
        runtime.wait()
    except KeyboardInterrupt:
        LOGGER.info("ChiefMind interrupted by user.")
        runtime.stop()
        runtime.wait()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    if runtime.failures:
        for name, failure in runtime.failures:
            LOGGER.error("%s failed: %s", name, failure)
        return 1
    LOGGER.info("ChiefMind stopped cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
