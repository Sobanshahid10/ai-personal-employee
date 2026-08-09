"""Focused lifecycle tests for the Gmail polling agent."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import gmail_watcher  # noqa: E402


class GmailWatcherShutdownTests(unittest.TestCase):
    def test_poll_stops_between_messages_and_defers_reasoning(self) -> None:
        stop_event = threading.Event()
        staged_ids: list[str] = []

        def stage_first(message: dict[str, str]) -> Path:
            staged_ids.append(message["id"])
            stop_event.set()
            return Path(f"/tmp/email_{message['id']}.md")

        with (
            patch.object(gmail_watcher, "load_processed_ids", return_value=set()),
            patch.object(
                gmail_watcher,
                "_list_message_ids",
                return_value=["first", "second", "third"],
            ),
            patch.object(
                gmail_watcher,
                "_get_message",
                side_effect=lambda _service, message_id: {"id": message_id},
            ) as get_message,
            patch.object(gmail_watcher, "_write_message_file", side_effect=stage_first),
            patch.object(gmail_watcher, "_mark_as_read"),
            patch.object(gmail_watcher, "save_processed_ids"),
            patch.object(gmail_watcher, "trigger_reasoning_loop") as reasoning,
        ):
            completed = gmail_watcher.poll_once(
                Mock(),
                stop_event=stop_event,
            )

        self.assertEqual(completed, 1)
        self.assertEqual(staged_ids, ["first"])
        self.assertEqual(get_message.call_count, 1)
        reasoning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
