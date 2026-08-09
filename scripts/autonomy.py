"""Policy-driven autonomy routing for ChiefMind reasoning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import yaml

from config import (
    DECISIONS_DIR,
    DIGESTS_DIR,
    DOCS_DIR,
    OPERATOR_POLICY_FILE,
)
from workflow_utils import WorkflowFileError, atomic_write_text


VALID_CLASSIFICATIONS = frozenset(
    {
        "INFORMATION_ONLY",
        "ROUTINE_ACTION",
        "USER_ACTION_REQUIRED",
        "DECISION_REQUIRED",
        "EXTERNAL_COMMUNICATION",
        "FINANCIAL_ACTION",
        "SECURITY_ACTION",
        "IRREVERSIBLE_ACTION",
        "CRITICAL_EVENT",
    }
)
VALID_REPLY_INTENT = frozenset({"none", "optional", "required"})
VALID_CONFIDENCE = frozenset({"low", "medium", "high"})
VALID_IMPORTANCE = frozenset(
    {"trivial", "low", "moderate", "high", "critical"}
)
VALID_RISK = frozenset({"low", "moderate", "high", "critical"})
VALID_REVERSIBILITY = frozenset(
    {"REVERSIBLE", "PARTIALLY_REVERSIBLE", "IRREVERSIBLE"}
)
VALID_AUTONOMY_MODES = frozenset(
    {
        "AUTO_EXECUTE",
        "AUTO_EXECUTE_AND_SUMMARIZE",
        "HOLD_AND_SUMMARIZE",
        "ASK_USER",
        "ESCALATE",
    }
)

HARD_ESCALATE_CLASSIFICATIONS = frozenset(
    {
        "CRITICAL_EVENT",
        "SECURITY_ACTION",
    }
)
HARD_APPROVAL_CLASSIFICATIONS = frozenset(
    {
        "FINANCIAL_ACTION",
        "IRREVERSIBLE_ACTION",
    }
)


class AutonomyError(ValueError):
    """Invalid assessment or policy data."""


@dataclass(frozen=True)
class OperatorPolicy:
    version: int
    vip_senders: frozenset[str]
    always_approval_senders: frozenset[str]
    muted_domains: frozenset[str]
    muted_local_parts: frozenset[str]
    approval_topics: frozenset[str]
    allow_auto_internal: bool

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> OperatorPolicy:
        senders = data.get("senders") or {}
        routing = data.get("routing") or {}
        autonomy = data.get("autonomy") or {}

        def norm_emails(values: Any) -> frozenset[str]:
            if not isinstance(values, list):
                return frozenset()
            return frozenset(
                str(item).strip().lower()
                for item in values
                if isinstance(item, str) and item.strip()
            )

        def norm_strings(values: Any) -> frozenset[str]:
            if not isinstance(values, list):
                return frozenset()
            return frozenset(
                str(item).strip().lower()
                for item in values
                if isinstance(item, str) and item.strip()
            )

        version = data.get("version", 1)
        if not isinstance(version, int):
            version = 1

        return cls(
            version=version,
            vip_senders=norm_emails(senders.get("vip")),
            always_approval_senders=norm_emails(senders.get("always_approval")),
            muted_domains=norm_strings(routing.get("muted_domains")),
            muted_local_parts=norm_strings(routing.get("muted_local_parts")),
            approval_topics=norm_strings(routing.get("approval_topics")),
            allow_auto_internal=bool(autonomy.get("allow_auto_internal", True)),
        )


DEFAULT_POLICY = OperatorPolicy(
    version=1,
    vip_senders=frozenset(),
    always_approval_senders=frozenset(),
    muted_domains=frozenset(),
    muted_local_parts=frozenset(
        {"noreply", "no-reply", "notifications", "mailer-daemon"}
    ),
    approval_topics=frozenset(
        {"refund", "invoice", "contract", "password", "wire", "legal"}
    ),
    allow_auto_internal=True,
)


def load_operator_policy() -> OperatorPolicy:
    """Load operator policy from credentials, then docs example, then defaults."""
    candidates = (
        OPERATOR_POLICY_FILE,
        DOCS_DIR / "operator_policy.example.yaml",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise AutonomyError(f"Could not load policy {path}: {exc}") from exc
        if isinstance(raw, dict):
            return OperatorPolicy.from_mapping(raw)
    return DEFAULT_POLICY


@dataclass(frozen=True)
class EventAssessment:
    action_required: bool
    classifications: tuple[str, ...]
    reply_intent: str
    confidence: str
    importance: str
    risk: str
    reversibility: str
    recommended_autonomy_mode: str
    summary: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class FinalDecision:
    event_id: str
    action_required: bool
    classifications: tuple[str, ...]
    reply_intent: str
    confidence: str
    importance: str
    risk: str
    reversibility: str
    autonomy_mode: str
    requires_approval: bool
    reason: str
    policy_rule_id: str
    policy_version: int
    recommended_action: str

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "classifications": list(self.classifications),
            "action_required": self.action_required,
            "reply_intent": self.reply_intent,
            "confidence": self.confidence,
            "importance": self.importance,
            "risk": self.risk,
            "reversibility": self.reversibility,
            "autonomy_mode": self.autonomy_mode,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "policy_rule_id": self.policy_rule_id,
            "policy_version": self.policy_version,
            "recommended_action": self.recommended_action,
        }


def parse_event_assessment(data: dict[str, Any]) -> EventAssessment:
    action_required = data.get("action_required")
    if not isinstance(action_required, bool):
        raise AutonomyError("Assessment `action_required` must be boolean.")

    raw_classifications = data.get("classifications")
    if not isinstance(raw_classifications, list) or not raw_classifications:
        raise AutonomyError("Assessment requires non-empty `classifications`.")
    classifications = tuple(
        str(item).strip().upper()
        for item in raw_classifications
        if isinstance(item, str) and item.strip()
    )
    if not classifications:
        raise AutonomyError("Assessment requires valid `classifications`.")
    unknown = set(classifications) - VALID_CLASSIFICATIONS
    if unknown:
        raise AutonomyError(
            f"Unsupported classifications: {', '.join(sorted(unknown))}."
        )

    reply_intent = str(data.get("reply_intent", "")).strip().lower()
    if reply_intent not in VALID_REPLY_INTENT:
        raise AutonomyError("Assessment `reply_intent` is invalid.")

    confidence = str(data.get("confidence", "")).strip().lower()
    if confidence not in VALID_CONFIDENCE:
        raise AutonomyError("Assessment `confidence` is invalid.")

    importance = str(data.get("importance", "")).strip().lower()
    if importance not in VALID_IMPORTANCE:
        raise AutonomyError("Assessment `importance` is invalid.")

    risk = str(data.get("risk", "")).strip().lower()
    if risk not in VALID_RISK:
        raise AutonomyError("Assessment `risk` is invalid.")

    reversibility = str(data.get("reversibility", "")).strip().upper()
    if reversibility not in VALID_REVERSIBILITY:
        raise AutonomyError("Assessment `reversibility` is invalid.")

    recommended = str(data.get("recommended_autonomy_mode", "")).strip().upper()
    if recommended not in VALID_AUTONOMY_MODES:
        raise AutonomyError("Assessment `recommended_autonomy_mode` is invalid.")

    summary = str(data.get("summary", "")).strip()
    if not summary:
        raise AutonomyError("Assessment requires a non-empty `summary`.")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        raw_steps = []
    steps = tuple(
        step.strip()
        for step in raw_steps
        if isinstance(step, str) and step.strip()
    )

    if action_required and reply_intent == "required" and not steps:
        raise AutonomyError(
            "Actionable items with reply_intent `required` need at least one step."
        )

    return EventAssessment(
        action_required=action_required,
        classifications=classifications,
        reply_intent=reply_intent,
        confidence=confidence,
        importance=importance,
        risk=risk,
        reversibility=reversibility,
        recommended_autonomy_mode=recommended,
        summary=summary,
        steps=steps,
    )


def _sender_email(sender: str) -> str:
    _, address = parseaddr(sender)
    return address.strip().lower()


def _sender_domain(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].lower()


def _matches_muted_sender(sender: str, policy: OperatorPolicy) -> bool:
    email = _sender_email(sender)
    if not email:
        return False
    local, _, domain = email.partition("@")
    if domain and domain in policy.muted_domains:
        return True
    if local in policy.muted_local_parts:
        return True
    return False


def _body_matches_approval_topics(text: str, policy: OperatorPolicy) -> bool:
    lowered = text.lower()
    return any(topic in lowered for topic in policy.approval_topics)


def _classification_set(assessment: EventAssessment) -> set[str]:
    return set(assessment.classifications)


def resolve_autonomy_mode(
    *,
    event_id: str,
    assessment: EventAssessment,
    policy: OperatorPolicy,
    sender: str,
    subject: str,
    body: str,
) -> FinalDecision:
    """Apply ordered policy rules; LLM recommendation is never final alone."""
    classes = _classification_set(assessment)
    combined_text = f"{subject}\n{body}"
    email = _sender_email(sender)

    def finish(
        mode: str,
        *,
        rule_id: str,
        reason: str,
        requires_approval: bool | None = None,
    ) -> FinalDecision:
        if mode not in VALID_AUTONOMY_MODES:
            mode = "ASK_USER"
        if requires_approval is None:
            requires_approval = mode in {"ASK_USER", "ESCALATE"}
        return FinalDecision(
            event_id=event_id,
            action_required=assessment.action_required,
            classifications=assessment.classifications,
            reply_intent=assessment.reply_intent,
            confidence=assessment.confidence,
            importance=assessment.importance,
            risk=assessment.risk,
            reversibility=assessment.reversibility,
            autonomy_mode=mode,
            requires_approval=requires_approval,
            reason=reason,
            policy_rule_id=rule_id,
            policy_version=policy.version,
            recommended_action=assessment.summary,
        )

    if email and email in policy.always_approval_senders:
        return finish(
            "ASK_USER",
            rule_id="senders.always_approval",
            reason="Sender is configured for always-approval.",
        )

    if email and email in policy.vip_senders:
        return finish(
            "ASK_USER",
            rule_id="senders.vip",
            reason="Sender is configured as VIP.",
        )

    if classes & HARD_ESCALATE_CLASSIFICATIONS:
        return finish(
            "ESCALATE",
            rule_id="safety.escalate_classification",
            reason="Event matches critical or security classification.",
        )

    if classes & HARD_APPROVAL_CLASSIFICATIONS:
        return finish(
            "ASK_USER",
            rule_id="safety.approval_classification",
            reason="Event matches financial or irreversible classification.",
        )

    if assessment.reversibility == "IRREVERSIBLE":
        return finish(
            "ASK_USER",
            rule_id="safety.irreversible",
            reason="Proposed handling is irreversible.",
        )

    if assessment.risk in {"high", "critical"}:
        mode = "ESCALATE" if assessment.risk == "critical" else "ASK_USER"
        return finish(
            mode,
            rule_id="safety.high_risk",
            reason=f"Risk level is {assessment.risk}.",
        )

    if _body_matches_approval_topics(combined_text, policy):
        return finish(
            "ASK_USER",
            rule_id="routing.approval_topics",
            reason="Content matches configured approval topics.",
        )

    if not assessment.action_required:
        mode = (
            "HOLD_AND_SUMMARIZE"
            if assessment.importance in {"trivial", "low"}
            else "AUTO_EXECUTE_AND_SUMMARIZE"
        )
        return finish(
            mode,
            rule_id="action.not_required",
            reason="No action required; summarize without interrupting.",
            requires_approval=False,
        )

    if (
        "EXTERNAL_COMMUNICATION" in classes
        or assessment.reply_intent == "required"
    ):
        if assessment.confidence == "low":
            return finish(
                "ASK_USER",
                rule_id="external.low_confidence",
                reason="External or reply-required work with low confidence.",
            )
        return finish(
            "ASK_USER",
            rule_id="external.communication",
            reason="External communication requires prepared approval.",
        )

    if assessment.confidence == "low":
        if assessment.importance in {"high", "critical"}:
            return finish(
                "ESCALATE",
                rule_id="confidence.low_important",
                reason="Important event with insufficient confidence.",
            )
        return finish(
            "HOLD_AND_SUMMARIZE",
            rule_id="confidence.low",
            reason="Low confidence; hold for digest instead of guessing.",
            requires_approval=False,
        )

    if _matches_muted_sender(sender, policy) and assessment.reply_intent == "none":
        return finish(
            "AUTO_EXECUTE_AND_SUMMARIZE",
            rule_id="routing.muted_sender",
            reason="Muted automated sender with no reply required.",
            requires_approval=False,
        )

    if classes <= {"INFORMATION_ONLY", "ROUTINE_ACTION"} and assessment.risk == "low":
        return finish(
            "AUTO_EXECUTE_AND_SUMMARIZE",
            rule_id="routine.auto_summarize",
            reason="Routine or information-only event with low risk.",
            requires_approval=False,
        )

    if assessment.importance in {"high", "critical"}:
        return finish(
            "ASK_USER",
            rule_id="importance.high",
            reason=f"Importance is {assessment.importance}.",
        )

    if assessment.recommended_autonomy_mode in {
        "AUTO_EXECUTE",
        "AUTO_EXECUTE_AND_SUMMARIZE",
        "HOLD_AND_SUMMARIZE",
    }:
        if not policy.allow_auto_internal:
            return finish(
                "HOLD_AND_SUMMARIZE",
                rule_id="autonomy.auto_disabled",
                reason="Automatic internal handling disabled by policy.",
                requires_approval=False,
            )
        return finish(
            assessment.recommended_autonomy_mode,
            rule_id="llm.recommendation",
            reason="Policy allowed the model recommendation.",
            requires_approval=False,
        )

    return finish(
        "ASK_USER",
        rule_id="default.ask_user",
        reason="Default to human review when no auto rule matched.",
    )


def isoformat_utc(value: datetime | None = None) -> str:
    current = value or datetime.now(tz=UTC)
    return current.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _digest_path(for_date: datetime | None = None) -> Path:
    day = (for_date or datetime.now(tz=UTC)).astimezone(UTC).date().isoformat()
    return DIGESTS_DIR / f"{day}.jsonl"


def append_digest_entry(entry: dict[str, Any]) -> Path:
    """Append one auto-handled line to the daily digest."""
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _digest_path()
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return path


def append_decision_record(record: dict[str, Any]) -> Path:
    """Append one autonomy decision to the daily audit JSONL."""
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(tz=UTC).date().isoformat()
    path = DECISIONS_DIR / f"{day}.jsonl"
    payload = dict(record)
    payload.setdefault("timestamp", isoformat_utc())
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return path


def load_digest_entries(for_date: str | None = None) -> list[dict[str, Any]]:
    """Load digest entries for one UTC date (YYYY-MM-DD)."""
    day = for_date or datetime.now(tz=UTC).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise WorkflowFileError("Digest date must be YYYY-MM-DD.")
    path = DIGESTS_DIR / f"{day}.jsonl"
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def digest_batch_summary(entries: list[dict[str, Any]]) -> str:
    """One-line batched summary for dashboard display."""
    if not entries:
        return ""
    counts: dict[str, int] = {}
    for entry in entries:
        labels = entry.get("classifications") or ["handled"]
        if isinstance(labels, str):
            labels = [labels]
        for label in labels:
            key = str(label).lower().replace("_", " ")
            counts[key] = counts.get(key, 0) + 1
    parts = [
        f"{count} {label}"
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return f"Handled {len(entries)} item(s): " + ", ".join(parts[:6])


def policy_prompt_excerpt(policy: OperatorPolicy) -> str:
    """Compact policy summary for the classifier prompt."""
    return json.dumps(
        {
            "vip_senders": sorted(policy.vip_senders),
            "always_approval_senders": sorted(policy.always_approval_senders),
            "muted_domains": sorted(policy.muted_domains),
            "approval_topics": sorted(policy.approval_topics),
        },
        ensure_ascii=False,
    )
