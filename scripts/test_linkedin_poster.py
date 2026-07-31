"""Offline tests for the Day 7 LinkedIn poster."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from linkedin_poster import (  # noqa: E402
    LinkedInPosterError,
    linkedin_payload,
    post_approved_artifact,
    read_approved_post,
)


class LinkedInPosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.logs = self.root / "Logs"
        self.approval = self.root / "approved.md"
        self.approval.write_text(
            """---
action_id: linkedin_test_001
type: linkedin_post
post_body: |
  Exact approved content.
---
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_mock_preserves_content_and_writes_daily_log(self) -> None:
        metadata, content = read_approved_post(self.approval)
        result = post_approved_artifact(
            self.approval,
            mode="mock",
            logs_dir=self.logs,
        )

        self.assertEqual(metadata["action_id"], "linkedin_test_001")
        self.assertEqual(content, "Exact approved content.")
        self.assertEqual(result["mode"], "mock")
        log_file = next(self.logs.glob("*.json"))
        event = json.loads(log_file.read_text(encoding="utf-8"))[0]
        self.assertEqual(event["status"], "linkedin_post_mocked")

    def test_invalid_type_is_refused(self) -> None:
        self.approval.write_text(
            "---\naction_id: x\ntype: email_send\npost_body: hello\n---\n",
            encoding="utf-8",
        )
        with self.assertRaises(LinkedInPosterError):
            read_approved_post(self.approval)

    def test_payload_uses_exact_content(self) -> None:
        payload = linkedin_payload(
            author_urn="urn:li:person:123",
            content="Approved text",
        )
        self.assertEqual(payload["commentary"], "Approved text")
        self.assertEqual(payload["lifecycleState"], "PUBLISHED")


if __name__ == "__main__":
    unittest.main()
