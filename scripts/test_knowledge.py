"""Day 3 regression tests for the transparent knowledge retriever."""

from __future__ import annotations

import unittest

from knowledge import parse_sections, retrieve_relevant_sections, tokenize


class KnowledgeRetrievalTests(unittest.TestCase):
    def test_refund_policy_deadline_returns_grounded_sections(self) -> None:
        result = retrieve_relevant_sections("refund policy deadline", top_k=3)

        self.assertTrue(
            result.startswith("## Refund, Reimbursement, and Expense Policy")
        )
        self.assertIn("<!-- page 4 -->", result)
        self.assertIn("30 calendar days", result)
        self.assertIn("## FAQ — Refunds and Reimbursements", result)

    def test_escalation_query_returns_escalation_rules(self) -> None:
        result = retrieve_relevant_sections(
            "phishing credentials security escalation", top_k=1
        )

        self.assertTrue(result.startswith("## Escalation and Refusal Criteria"))
        self.assertIn("<!-- page 12 -->", result)

    def test_no_overlap_returns_empty_string(self) -> None:
        self.assertEqual(retrieve_relevant_sections("xylophone", top_k=3), "")

    def test_invalid_arguments_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            retrieve_relevant_sections("")
        with self.assertRaises(ValueError):
            retrieve_relevant_sections("refund", top_k=0)

    def test_parser_only_uses_level_two_headings(self) -> None:
        sections = parse_sections(
            "# Title\n\n## First\n<!-- page 1 -->\nText\n\n"
            "### Detail\nMore\n\n## Second\n<!-- page 2 -->\nText"
        )

        self.assertEqual([section.heading for section in sections], ["## First", "## Second"])
        self.assertIn("### Detail", sections[0].content)

    def test_plural_keyword_matches_singular(self) -> None:
        self.assertIn("refund", tokenize("refunds"))


if __name__ == "__main__":
    unittest.main()
