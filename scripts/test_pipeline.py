"""ChiefMind Day 9 integration-contract tests.

The suite uses temporary workflow folders and fake external senders. It never
sends email, posts to LinkedIn, or modifies production workflow artifacts.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import approval_watcher
import authenticate_gmail
import config
from dashboard.app import create_app
from knowledge import retrieve_relevant_sections
from workflow_utils import atomic_write_json, parse_frontmatter, write_frontmatter


class FakeEmailSender:
    """Capture approved sends without contacting Gmail."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send_exact(
        self,
        *,
        recipient: str,
        subject: str,
        draft_body: str,
        html_body: str | None = None,
        **kwargs: Any,
    ) -> dict[str, str]:
        call_item = {
            "recipient": recipient,
            "subject": subject,
            "draft_body": draft_body,
        }
        if html_body is not None:
            call_item["html_body"] = html_body
        self.calls.append(call_item)
        return {
            "provider": "fake_gmail",
            "message_id": "test-message-id",
            "thread_id": "test-thread-id",
        }


class PipelineTests(unittest.TestCase):
    """Validate boundaries between configuration, files, APIs, and execution."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.folder_keys = {
            "inbox": self.root / "Inbox",
            "needs_action": self.root / "Needs_Action",
            "plans": self.root / "Plans",
            "pending_approval": self.root / "Pending_Approval",
            "approved": self.root / "Approved",
            "done": self.root / "Done",
            "rejected": self.root / "Rejected",
            "failed": self.root / "Failed",
        }
        for directory in self.folder_keys.values():
            directory.mkdir(parents=True)
        self.logs = self.root / "Logs"
        self.logs.mkdir()
        self.receipts = self.logs / "execution_receipts.json"

        # The execution tests patch every mutable location, keeping the real
        # ChiefMind queues and receipts untouched.
        self.approval_patches = [
            patch.object(
                approval_watcher,
                "APPROVED_DIR",
                self.folder_keys["approved"],
            ),
            patch.object(
                approval_watcher,
                "DONE_DIR",
                self.folder_keys["done"],
            ),
            patch.object(
                approval_watcher,
                "FAILED_DIR",
                self.folder_keys["failed"],
            ),
            patch.object(approval_watcher, "LOGS_DIR", self.logs),
            patch.object(
                approval_watcher,
                "EXECUTION_RECEIPTS_FILE",
                self.receipts,
            ),
            patch.object(approval_watcher, "APPROVAL_SETTLE_SECONDS", 0),
        ]
        for active_patch in self.approval_patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.approval_patches):
            active_patch.stop()
        self.temporary_directory.cleanup()

    def _approval_file(self, action_id: str) -> Path:
        path = self.folder_keys["approved"] / f"{action_id}.md"
        write_frontmatter(
            path,
            {
                "action_id": action_id,
                "type": "email_send",
                "to": "recipient@example.net",
                "subject": "Pipeline test",
                "draft_body": "Exact approved test body.\n",
            },
        )
        return path

    def test_config_loads(self) -> None:
        required_constants = (
            "PROJECT_ROOT",
            "GOOGLE_CREDENTIALS_FILE",
            "GOOGLE_TOKEN_FILE",
            "GROQ_MODEL",
            "GMAIL_POLL_INTERVAL",
            "INBOX_DIR",
            "NEEDS_ACTION_DIR",
            "PENDING_APPROVAL_DIR",
            "APPROVED_DIR",
            "DONE_DIR",
            "FAILED_DIR",
            "LOGS_DIR",
        )
        for name in required_constants:
            with self.subTest(name=name):
                self.assertTrue(hasattr(config, name), f"Missing config.{name}")
        for directory in config.WORKFLOW_DIRS:
            with self.subTest(directory=directory):
                self.assertTrue(directory.is_absolute())
                self.assertTrue(directory.is_dir())

    def test_gmail_auth(self) -> None:
        if (
            not config.GOOGLE_CREDENTIALS_FILE.is_file()
            or not config.GOOGLE_TOKEN_FILE.is_file()
        ):
            self.skipTest("Gmail credentials or token file not present")
        # Force the parsed credential object to appear current so this offline
        # test never performs a token refresh request.
        with patch.object(
            authenticate_gmail.Credentials,
            "valid",
            new_callable=PropertyMock,
            return_value=True,
        ):
            credentials = authenticate_gmail.load_gmail_credentials(
                interactive=False
            )
        self.assertIsNotNone(credentials.refresh_token)
        self.assertTrue(credentials.has_scopes(config.GMAIL_SCOPES))

    def test_knowledge_retrieval(self) -> None:
        result = retrieve_relevant_sections("refund policy deadline")
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip())
        self.assertIn("refund", result.lower())

    def test_frontmatter_parse(self) -> None:
        sample = self.root / "sample.md"
        write_frontmatter(
            sample,
            {"action_id": "email_sample", "type": "email"},
            "Full markdown body.",
        )
        metadata, body = parse_frontmatter(sample)
        self.assertEqual(metadata["action_id"], "email_sample")
        self.assertEqual(metadata["type"], "email")
        self.assertEqual(body, "Full markdown body.")

    def test_processed_ids(self) -> None:
        processed_ids_file = self.root / "processed_ids.json"
        expected = {"gmail-a", "gmail-b", "gmail-c"}
        with patch.object(config, "PROCESSED_IDS_FILE", processed_ids_file):
            config.save_processed_ids(expected)
            actual = config.load_processed_ids()
        self.assertEqual(actual, expected)
        stored = json.loads(processed_ids_file.read_text(encoding="utf-8"))
        self.assertEqual(stored, sorted(expected))

    def test_dashboard_stats(self) -> None:
        write_frontmatter(
            self.folder_keys["pending_approval"] / "pending.md",
            {"action_id": "email_pending", "type": "email_send"},
        )
        app = create_app(
            folder_keys=self.folder_keys,
            logs_dir=self.logs,
            agent_log_file=self.logs / "agent.log",
            approval_token="test-token",
        )
        app.config.update(TESTING=True)
        response = app.test_client().get("/api/stats")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["pending"], 1)
        self.assertIn("counts", payload)
        self.assertIn("recent_activity", payload)

    def test_duplicate_guard(self) -> None:
        action_id = "email_duplicate"
        atomic_write_json(
            self.receipts,
            {
                action_id: {
                    "state": "executed",
                    "type": "email_send",
                    "message_id": "already-sent",
                }
            },
        )
        source = self._approval_file(action_id)
        sender = FakeEmailSender()

        result = approval_watcher.process_approval(
            source,
            gmail_sender=sender,
        )

        self.assertEqual(result, "duplicate")
        self.assertEqual(sender.calls, [])
        self.assertTrue((self.folder_keys["done"] / source.name).is_file())

    def test_approval_routing(self) -> None:
        source = self._approval_file("email_route_test")
        sender = FakeEmailSender()

        result = approval_watcher.process_approval(
            source,
            gmail_sender=sender,
        )

        self.assertEqual(result, "executed")
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(
            sender.calls[0],
            {
                "recipient": "recipient@example.net",
                "subject": "Pipeline test",
                "draft_body": "Exact approved test body.\n",
            },
        )
        self.assertTrue((self.folder_keys["done"] / source.name).is_file())
        receipt = json.loads(self.receipts.read_text(encoding="utf-8"))
        self.assertEqual(receipt["email_route_test"]["state"], "executed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
