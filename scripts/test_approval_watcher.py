"""Offline Day 5 tests; these tests never contact Gmail or LinkedIn."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import approval_watcher


class FakeEmailSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send_exact(
        self,
        *,
        recipient: str,
        subject: str,
        draft_body: str,
    ) -> dict[str, str]:
        self.calls.append(
            {
                "recipient": recipient,
                "subject": subject,
                "draft_body": draft_body,
            }
        )
        return {
            "provider": "fake_gmail",
            "message_id": "fake-message-123",
            "thread_id": "fake-thread-123",
        }


class ApprovalWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.approved = self.root / "Approved"
        self.done = self.root / "Done"
        self.failed = self.root / "Failed"
        self.logs = self.root / "Logs"
        for directory in (self.approved, self.done, self.failed, self.logs):
            directory.mkdir()
        self.receipts = self.logs / "execution_receipts.json"
        self.patches = [
            patch.object(approval_watcher, "APPROVED_DIR", self.approved),
            patch.object(approval_watcher, "DONE_DIR", self.done),
            patch.object(approval_watcher, "FAILED_DIR", self.failed),
            patch.object(approval_watcher, "LOGS_DIR", self.logs),
            patch.object(
                approval_watcher,
                "EXECUTION_RECEIPTS_FILE",
                self.receipts,
            ),
            patch.object(approval_watcher, "APPROVAL_SETTLE_SECONDS", 0),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def write_email(
        self,
        *,
        action_id: str = "email_test_001",
        include_body: bool = True,
        draft_hash: str | None = None,
    ) -> Path:
        draft = "Hello,\n\nExact approved body.\n\nRegards,\nChiefMind"
        fields = [
            "---",
            f"action_id: {action_id}",
            "type: email_send",
            'to: "recipient@example.net"',
            'subject: "Day 5 test"',
        ]
        if include_body:
            fields.extend(
                [
                    "draft_body: |",
                    "  Hello,",
                    "",
                    "  Exact approved body.",
                    "",
                    "  Regards,",
                    "  ChiefMind",
                ]
            )
        if draft_hash is not None:
            fields.append(f"draft_sha256: {draft_hash}")
        fields.append("---")
        path = self.approved / f"{action_id}.md"
        path.write_text("\n".join(fields) + "\n", encoding="utf-8")
        return path

    def test_email_executes_exact_body_receipts_logs_and_routes(self) -> None:
        draft = "Hello,\n\nExact approved body.\n\nRegards,\nChiefMind\n"
        path = self.write_email(
            draft_hash=hashlib.sha256(draft.encode()).hexdigest()
        )
        sender = FakeEmailSender()

        result = approval_watcher.process_approval(
            path,
            gmail_sender=sender,
        )

        self.assertEqual(result, "executed")
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(sender.calls[0]["draft_body"], draft)
        self.assertTrue((self.done / path.name).is_file())
        receipt_data = json.loads(self.receipts.read_text(encoding="utf-8"))
        self.assertEqual(receipt_data["email_test_001"]["state"], "executed")
        daily_files = list(self.logs.glob("????-??-??.json"))
        self.assertEqual(len(daily_files), 1)
        events = json.loads(daily_files[0].read_text(encoding="utf-8"))
        self.assertEqual(
            [event["status"] for event in events],
            ["execution_started", "executed"],
        )

    def test_duplicate_action_id_never_sends_twice(self) -> None:
        sender = FakeEmailSender()
        first = self.write_email()
        self.assertEqual(
            approval_watcher.process_approval(first, gmail_sender=sender),
            "executed",
        )
        duplicate = self.write_email()

        result = approval_watcher.process_approval(
            duplicate,
            gmail_sender=sender,
        )

        self.assertEqual(result, "duplicate")
        self.assertEqual(len(sender.calls), 1)

    def test_missing_draft_moves_to_failed(self) -> None:
        path = self.write_email(include_body=False)
        sender = FakeEmailSender()

        result = approval_watcher.process_approval(
            path,
            gmail_sender=sender,
        )

        self.assertEqual(result, "failed")
        self.assertEqual(sender.calls, [])
        self.assertTrue((self.failed / path.name).is_file())

    def test_hash_mismatch_refuses_execution(self) -> None:
        path = self.write_email(draft_hash=f'"{"0" * 64}"')
        sender = FakeEmailSender()

        result = approval_watcher.process_approval(
            path,
            gmail_sender=sender,
        )

        self.assertEqual(result, "failed")
        self.assertEqual(sender.calls, [])
        self.assertTrue((self.failed / path.name).is_file())

    def test_plan_has_no_external_action(self) -> None:
        path = self.approved / "plan_test.md"
        path.write_text(
            "---\naction_id: plan_test_001\ntype: plan\n---\n",
            encoding="utf-8",
        )
        sender = FakeEmailSender()

        result = approval_watcher.process_approval(
            path,
            gmail_sender=sender,
        )

        self.assertEqual(result, "executed")
        self.assertEqual(sender.calls, [])
        self.assertTrue((self.done / path.name).is_file())


if __name__ == "__main__":
    unittest.main()
