"""Offline API tests for all eight ChiefMind dashboard endpoints."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dashboard.app import create_app


class DashboardAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder_keys = {
            key: self.root / folder
            for key, folder in {
                "inbox": "Inbox",
                "needs_action": "Needs_Action",
                "plans": "Plans",
                "pending_approval": "Pending_Approval",
                "approved": "Approved",
                "done": "Done",
                "rejected": "Rejected",
                "failed": "Failed",
            }.items()
        }
        for path in self.folder_keys.values():
            path.mkdir()
        self.logs = self.root / "Logs"
        self.logs.mkdir()
        self.agent_log = self.logs / "agent.log"
        self.app = create_app(
            folder_keys=self.folder_keys,
            logs_dir=self.logs,
            agent_log_file=self.agent_log,
            approval_token="test-secret",
        )
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_item(
        self,
        folder: str,
        name: str,
        action_id: str,
    ) -> Path:
        path = self.folder_keys[folder] / name
        path.write_text(
            f"""---
action_id: {action_id}
type: email_send
subject: "Test item"
---

Markdown body.
""",
            encoding="utf-8",
        )
        return path

    def test_stats_returns_counts_and_recent_activity(self) -> None:
        self.write_item("pending_approval", "pending.md", "email_pending")
        self.write_item("done", "done.md", "email_done")
        (self.logs / "2026-07-30.json").write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2026-07-30T10:00:00Z",
                        "status": "executed",
                    }
                ]
            ),
            encoding="utf-8",
        )

        response = self.client.get("/api/stats")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["pending"], 1)
        self.assertEqual(payload["done"], 1)
        self.assertEqual(payload["recent_activity"][0]["status"], "executed")

    def test_folder_lists_markdown_files(self) -> None:
        self.write_item("needs_action", "one.md", "email_one")
        (self.folder_keys["needs_action"] / "ignored.txt").write_text("x")

        response = self.client.get("/api/folder/needs_action")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["name"], "one.md")
        self.assertEqual(
            self.client.get("/api/folder/unknown").status_code,
            404,
        )

    def test_file_returns_frontmatter_and_body(self) -> None:
        self.write_item("plans", "Plan_test.md", "plan_test")

        response = self.client.get("/api/file/plans/Plan_test.md")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["metadata"]["action_id"], "plan_test")
        self.assertIn("Markdown body.", payload["body"])
        self.assertEqual(
            self.client.get("/api/file/plans/not-markdown.txt").status_code,
            400,
        )

    def test_approve_requires_token_and_moves_atomically(self) -> None:
        source = self.write_item(
            "pending_approval",
            "approve.md",
            "email_approve",
        )

        forbidden = self.client.post("/api/approve/approve.md")
        response = self.client.post(
            "/api/approve/approve.md",
            headers={"X-Approval-Token": "test-secret"},
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(source.exists())
        self.assertTrue(
            (self.folder_keys["approved"] / "approve.md").is_file()
        )

    def test_reject_requires_token_and_moves_atomically(self) -> None:
        self.write_item(
            "pending_approval",
            "reject.md",
            "email_reject",
        )

        response = self.client.post(
            "/api/reject/reject.md",
            headers={"X-Approval-Token": "test-secret"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            (self.folder_keys["rejected"] / "reject.md").is_file()
        )

    def test_logs_returns_only_dated_json_entries(self) -> None:
        (self.logs / "2026-07-30.json").write_text(
            '[{"timestamp":"2026-07-30T10:00:00Z","status":"approved"}]',
            encoding="utf-8",
        )
        (self.logs / "execution_receipts.json").write_text(
            '{"email_test":{"state":"executed"}}',
            encoding="utf-8",
        )

        response = self.client.get("/api/logs")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["entries"][0]["status"], "approved")

    def test_agent_log_returns_only_last_200_lines(self) -> None:
        self.agent_log.write_text(
            "".join(f"line {index}\n" for index in range(250)),
            encoding="utf-8",
        )

        response = self.client.get("/api/agent-log")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 200)
        self.assertEqual(payload["lines"][0], "line 50")
        self.assertEqual(payload["lines"][-1], "line 249")

    def test_all_items_aggregates_every_folder(self) -> None:
        self.write_item("inbox", "inbox.md", "email_inbox")
        self.write_item("failed", "failed.md", "email_failed")

        response = self.client.get("/api/all-items")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["counts"]["inbox"], 1)
        self.assertEqual(payload["counts"]["failed"], 1)

    def test_cors_preflight_allows_approval_header(self) -> None:
        response = self.client.options(
            "/api/approve/example.md",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "*",
        )
        self.assertIn(
            "X-Approval-Token",
            response.headers["Access-Control-Allow-Headers"],
        )


if __name__ == "__main__":
    unittest.main()
