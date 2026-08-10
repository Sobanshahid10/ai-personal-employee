"""Tests for policy-driven autonomy routing."""

from __future__ import annotations

import unittest

from autonomy import (
    DEFAULT_POLICY,
    EventAssessment,
    OperatorPolicy,
    assess_routine_notification,
    digest_batch_summary,
    parse_event_assessment,
    resolve_autonomy_mode,
)


class AutonomyPolicyTests(unittest.TestCase):
    def test_github_notification_is_deterministically_routine(self) -> None:
        assessment = assess_routine_notification(
            policy=DEFAULT_POLICY,
            sender="GitHub <notifications@github.com>",
            subject="You have been notified about activity in example/repo",
            body="There is new activity in a watched repository. Unsubscribe.",
        )

        self.assertIsNotNone(assessment)
        self.assertFalse(assessment.action_required)
        self.assertEqual(assessment.reply_intent, "none")

    def test_sensitive_platform_notification_uses_full_reasoning(self) -> None:
        assessment = assess_routine_notification(
            policy=DEFAULT_POLICY,
            sender="GitHub <notifications@github.com>",
            subject="Action required: suspicious sign-in detected",
            body="Review your account.",
        )

        self.assertIsNone(assessment)

    def test_informational_oauth_share_notice_is_summarized(self) -> None:
        assessment = assess_routine_notification(
            policy=DEFAULT_POLICY,
            sender="Google <noreply-accounts@google.com>",
            subject="You shared some Google Account data with Credly",
            body="You allowed Credly to access selected profile data.",
        )

        self.assertIsNotNone(assessment)
        self.assertFalse(assessment.action_required)
        self.assertIn("INFORMATION_ONLY", assessment.classifications)

    def test_linkedin_direct_message_still_uses_full_reasoning(self) -> None:
        assessment = assess_routine_notification(
            policy=DEFAULT_POLICY,
            sender="LinkedIn <messages-noreply@linkedin.com>",
            subject="A recruiter sent you a message",
            body="You have a new InMail.",
        )

        self.assertIsNone(assessment)

    def test_linkedin_digest_is_deterministically_routine(self) -> None:
        assessment = assess_routine_notification(
            policy=DEFAULT_POLICY,
            sender="LinkedIn <updates@e.linkedin.com>",
            subject="Your weekly update: people viewed your profile",
            body="See this week's profile activity.",
            metadata={"list_unsubscribe": "<https://example.test/unsubscribe>"},
        )

        self.assertIsNotNone(assessment)
        self.assertIn("LinkedIn", assessment.summary)

    def test_sponsored_mail_from_unknown_website_is_routine(self) -> None:
        assessment = assess_routine_notification(
            policy=DEFAULT_POLICY,
            sender="Example Store <offers@brand.example>",
            subject="Sponsored: a limited time offer for you",
            body="Browse this week's deals.",
        )

        self.assertIsNotNone(assessment)
        self.assertIn("Example Store", assessment.summary)

    def test_unsubscribable_bulk_newsletter_is_routine(self) -> None:
        assessment = assess_routine_notification(
            policy=DEFAULT_POLICY,
            sender="Research Digest <newsletter@research.example>",
            subject="This week's artificial intelligence research",
            body="A curated research roundup. Unsubscribe at any time.",
            metadata={"list_id": "research.example"},
        )

        self.assertIsNotNone(assessment)
        self.assertFalse(assessment.action_required)

    def test_no_action_routes_to_auto_summarize(self) -> None:
        assessment = EventAssessment(
            action_required=False,
            classifications=("INFORMATION_ONLY",),
            reply_intent="none",
            confidence="high",
            importance="low",
            risk="low",
            reversibility="REVERSIBLE",
            recommended_autonomy_mode="HOLD_AND_SUMMARIZE",
            summary="CI workflow completed.",
            steps=(),
        )
        final = resolve_autonomy_mode(
            event_id="email_1",
            assessment=assessment,
            policy=DEFAULT_POLICY,
            sender="GitHub <notifications@github.com>",
            subject="Run succeeded",
            body="All jobs passed.",
        )
        self.assertFalse(final.requires_approval)
        self.assertIn(
            final.autonomy_mode,
            {"HOLD_AND_SUMMARIZE", "AUTO_EXECUTE_AND_SUMMARIZE"},
        )

    def test_external_reply_requires_approval(self) -> None:
        assessment = EventAssessment(
            action_required=True,
            classifications=("EXTERNAL_COMMUNICATION", "USER_ACTION_REQUIRED"),
            reply_intent="required",
            confidence="high",
            importance="moderate",
            risk="moderate",
            reversibility="PARTIALLY_REVERSIBLE",
            recommended_autonomy_mode="ASK_USER",
            summary="Client asked for an updated proposal.",
            steps=("Prepare response.",),
        )
        final = resolve_autonomy_mode(
            event_id="email_2",
            assessment=assessment,
            policy=DEFAULT_POLICY,
            sender="Client <client@example.com>",
            subject="Proposal update",
            body="Can you send the revised proposal?",
        )
        self.assertTrue(final.requires_approval)
        self.assertEqual(final.autonomy_mode, "ASK_USER")

    def test_financial_classification_requires_approval(self) -> None:
        assessment = EventAssessment(
            action_required=True,
            classifications=("FINANCIAL_ACTION",),
            reply_intent="optional",
            confidence="high",
            importance="moderate",
            risk="low",
            reversibility="REVERSIBLE",
            recommended_autonomy_mode="AUTO_EXECUTE",
            summary="Payment receipt.",
            steps=(),
        )
        final = resolve_autonomy_mode(
            event_id="email_3",
            assessment=assessment,
            policy=DEFAULT_POLICY,
            sender="Billing <billing@example.com>",
            subject="Invoice",
            body="Your invoice is ready.",
        )
        self.assertEqual(final.autonomy_mode, "ASK_USER")
        self.assertEqual(final.policy_rule_id, "safety.approval_classification")

    def test_parse_event_assessment_validates_enums(self) -> None:
        with self.assertRaises(Exception):
            parse_event_assessment(
                {
                    "action_required": True,
                    "classifications": ["NOT_A_REAL_CLASS"],
                    "reply_intent": "none",
                    "confidence": "high",
                    "importance": "low",
                    "risk": "low",
                    "reversibility": "REVERSIBLE",
                    "recommended_autonomy_mode": "ASK_USER",
                    "summary": "Test",
                    "steps": [],
                }
            )

    def test_vip_sender_forces_approval(self) -> None:
        policy = OperatorPolicy(
            version=1,
            vip_senders=frozenset({"boss@example.com"}),
            always_approval_senders=frozenset(),
            muted_domains=frozenset(),
            muted_local_parts=frozenset(),
            approval_topics=frozenset(),
            allow_auto_internal=True,
        )
        assessment = EventAssessment(
            action_required=False,
            classifications=("INFORMATION_ONLY",),
            reply_intent="none",
            confidence="high",
            importance="low",
            risk="low",
            reversibility="REVERSIBLE",
            recommended_autonomy_mode="AUTO_EXECUTE_AND_SUMMARIZE",
            summary="FYI update.",
            steps=(),
        )
        final = resolve_autonomy_mode(
            event_id="email_4",
            assessment=assessment,
            policy=policy,
            sender="Boss <boss@example.com>",
            subject="Quick note",
            body="See you tomorrow.",
        )
        self.assertEqual(final.autonomy_mode, "ASK_USER")
        self.assertEqual(final.policy_rule_id, "senders.vip")

    def test_digest_batch_summary_groups_classifications(self) -> None:
        summary = digest_batch_summary(
            [
                {
                    "classifications": ["INFORMATION_ONLY", "ROUTINE_ACTION"],
                },
                {"classifications": ["INFORMATION_ONLY"]},
            ]
        )
        self.assertIn("Handled 2 item", summary)
        self.assertIn("information only", summary)


if __name__ == "__main__":
    unittest.main()
