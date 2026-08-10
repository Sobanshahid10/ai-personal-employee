"""Offline Day 4 tests; no Groq key or network access required."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

import reasoning_loop
import autonomy


def fake_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class FakeCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return fake_response(self.responses.pop(0))


class FakeGroq:
    def __init__(self, responses: list[str]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def read_frontmatter(path: Path) -> tuple[dict, str]:
    return reasoning_loop.parse_frontmatter_text(
        path.read_text(encoding="utf-8"),
        source=str(path),
    )


class ReasoningLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directories = {
            "NEEDS_ACTION_DIR": self.root / "Needs_Action",
            "PLANS_DIR": self.root / "Plans",
            "PENDING_APPROVAL_DIR": self.root / "Pending_Approval",
            "APPROVED_DIR": self.root / "Approved",
            "DONE_DIR": self.root / "Done",
            "FAILED_DIR": self.root / "Failed",
            "DIGESTS_DIR": self.root / "Logs" / "digests",
            "DECISIONS_DIR": self.root / "Logs" / "decisions",
        }
        for directory in self.directories.values():
            directory.mkdir(parents=True)
        self.patches = [
            patch.object(reasoning_loop, name, value)
            for name, value in self.directories.items()
            if hasattr(reasoning_loop, name)
        ]
        self.patches.extend(
            [
                patch.object(autonomy, "DIGESTS_DIR", self.directories["DIGESTS_DIR"]),
                patch.object(
                    autonomy, "DECISIONS_DIR", self.directories["DECISIONS_DIR"]
                ),
            ]
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def write_source(self, action_id: str = "email_test123") -> Path:
        source = self.directories["NEEDS_ACTION_DIR"] / "email_test123.md"
        source.write_text(
            """---
id: test123
action_id: email_test123
type: email
from: "Customer Example <customer@example.com>"
subject: "Refund policy deadline"
received_at: "2026-07-28T10:00:00Z"
priority: medium
status: needs_action
---

Hello,

I purchased the service ten days ago. What is the refund deadline, and what
information do you need from me?
""",
            encoding="utf-8",
        )
        if action_id != "email_test123":
            text = source.read_text(encoding="utf-8").replace(
                "email_test123", action_id
            )
            source.write_text(text, encoding="utf-8")
        return source

    def test_email_creates_plan_and_immutable_approval(self) -> None:
        self.write_source()
        assessment = json.dumps(
            {
                "action_required": True,
                "classifications": [
                    "EXTERNAL_COMMUNICATION",
                    "USER_ACTION_REQUIRED",
                ],
                "reply_intent": "required",
                "confidence": "high",
                "importance": "moderate",
                "risk": "moderate",
                "reversibility": "PARTIALLY_REVERSIBLE",
                "recommended_autonomy_mode": "ASK_USER",
                "summary": "Customer asks about the refund deadline.",
                "steps": [
                    "Confirm the purchase date and receipt.",
                    "Draft a policy-grounded reply for approval.",
                ],
            }
        )
        draft = (
            "Hello,\n\nThe standard submission deadline is 30 calendar days "
            "after purchase. Please send your receipt and purchase details.\n\n"
            "Best regards,\nChiefMind Team"
        )
        client = FakeGroq([assessment, draft])

        completed = reasoning_loop.run_once(client=client)

        self.assertEqual(completed, 1)
        self.assertFalse(
            (self.directories["NEEDS_ACTION_DIR"] / "email_test123.md").is_file()
        )
        plan_path = self.directories["PLANS_DIR"] / "Plan_email_test123.md"
        approval_path = (
            self.directories["PENDING_APPROVAL_DIR"] / "email_test123.md"
        )
        self.assertTrue(plan_path.is_file())
        self.assertTrue(approval_path.is_file())
        approval, _ = read_frontmatter(approval_path)
        self.assertEqual(approval["type"], "email_send")
        self.assertEqual(approval["to"], "customer@example.com")
        self.assertEqual(approval["draft_body"], draft)
        self.assertEqual(
            approval["draft_sha256"],
            hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        )
        self.assertTrue(
            any(
                reference.startswith("Knowledge Base page 4:")
                for reference in approval["knowledge_references"]
            )
        )
        self.assertIn("draft_body: |", approval_path.read_text(encoding="utf-8"))
        self.assertEqual(
            client.completions.calls[1]["temperature"],
            0.3,
        )

    def test_duplicate_guard_prevents_second_llm_call(self) -> None:
        self.write_source()
        duplicate = self.directories["APPROVED_DIR"] / "email_test123.md"
        duplicate.write_text(
            "---\naction_id: email_test123\ntype: email_send\n---\n",
            encoding="utf-8",
        )
        client = FakeGroq([])

        completed = reasoning_loop.run_once(client=client)

        self.assertEqual(completed, 0)
        self.assertEqual(client.completions.calls, [])
        self.assertFalse(
            (self.directories["NEEDS_ACTION_DIR"] / "email_test123.md").is_file()
        )
        self.assertTrue(
            any(
                path.name.startswith("email_test123")
                for path in self.directories["DONE_DIR"].glob("*.md")
            )
        )

    def test_auto_handled_item_moves_to_done_without_pending(self) -> None:
        source = self.write_source()
        client = FakeGroq(
            [
                json.dumps(
                    {
                        "action_required": False,
                        "classifications": ["INFORMATION_ONLY", "ROUTINE_ACTION"],
                        "reply_intent": "none",
                        "confidence": "high",
                        "importance": "low",
                        "risk": "low",
                        "reversibility": "REVERSIBLE",
                        "recommended_autonomy_mode": "AUTO_EXECUTE_AND_SUMMARIZE",
                        "summary": "Routine CI notification.",
                        "steps": [],
                    }
                )
            ]
        )

        completed = reasoning_loop.run_once(client=client)

        self.assertEqual(completed, 1)
        self.assertFalse(source.exists())
        self.assertTrue(
            (self.directories["DONE_DIR"] / source.name).is_file()
        )
        self.assertEqual(
            list(self.directories["PENDING_APPROVAL_DIR"].glob("*.md")),
            [],
        )
        self.assertEqual(
            list(self.directories["PLANS_DIR"].glob("*.md")),
            [],
        )
        digest_files = list(self.directories["DIGESTS_DIR"].glob("*.jsonl"))
        self.assertEqual(len(digest_files), 1)
        digest_lines = digest_files[0].read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(digest_lines), 1)
        digest_entry = json.loads(digest_lines[0])
        self.assertEqual(digest_entry["action_id"], "email_test123")
        decision_files = list(self.directories["DECISIONS_DIR"].glob("*.jsonl"))
        self.assertTrue(decision_files)

    def test_routine_platform_mail_needs_no_llm_client(self) -> None:
        source = self.directories["NEEDS_ACTION_DIR"] / "email_github.md"
        source.write_text(
            """---
id: github
action_id: email_github
type: email
from: "GitHub <notifications@github.com>"
subject: "You have been notified about activity in example/repo"
received_at: "2026-08-10T10:00:00Z"
priority: medium
status: needs_action
---

There is new activity in a watched repository. Unsubscribe.
""",
            encoding="utf-8",
        )

        completed = reasoning_loop.run_once(client=None)

        self.assertEqual(completed, 1)
        self.assertFalse(source.exists())
        done = self.directories["DONE_DIR"] / source.name
        self.assertTrue(done.is_file())
        metadata, _ = read_frontmatter(done)
        self.assertEqual(metadata["resolution"], "auto_handled")
        self.assertEqual(metadata["policy_rule_id"], "action.not_required")
        self.assertEqual(
            list(self.directories["PENDING_APPROVAL_DIR"].glob("*.md")),
            [],
        )

    def test_malformed_source_is_quarantined(self) -> None:
        source = self.directories["NEEDS_ACTION_DIR"] / "bad.md"
        source.write_text("not frontmatter", encoding="utf-8")

        completed = reasoning_loop.run_once(client=FakeGroq([]))

        self.assertEqual(completed, 0)
        self.assertFalse(source.exists())
        self.assertTrue((self.directories["FAILED_DIR"] / "bad.md").is_file())
        self.assertTrue(
            (self.directories["FAILED_DIR"] / "bad.error.txt").is_file()
        )

    def test_frontmatter_rejects_unsafe_python_objects(self) -> None:
        with self.assertRaises(reasoning_loop.MalformedSourceError):
            reasoning_loop.parse_frontmatter_text(
                "---\nvalue: !!python/object:builtins.str {}\n---\n",
                source="unsafe.md",
            )


if __name__ == "__main__":
    unittest.main()
