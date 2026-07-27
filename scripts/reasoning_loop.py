"""ChiefMind reasoning agent: source item -> plan -> approval artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, TypeVar

import yaml
from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    Groq,
    RateLimitError,
)

from config import (
    APPROVED_DIR,
    CLASSIFICATION_TEMPERATURE,
    DONE_DIR,
    EMAIL_DRAFT_TEMPERATURE,
    FAILED_DIR,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_RETRIES,
    GROQ_RETRY_DELAY,
    NEEDS_ACTION_DIR,
    PENDING_APPROVAL_DIR,
    PLANS_DIR,
    REASONING_MAX_TOKENS,
    REASONING_TOP_K,
    ensure_directories,
    setup_logging,
)
from knowledge import retrieve_relevant_sections


LOGGER = setup_logging("chiefmind.reasoning")
T = TypeVar("T")
VALID_ACTION_TYPES = {"email_send", "manual"}
VALID_PRIORITIES = {"high", "medium", "low"}
FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|$)(?P<body>.*)\Z",
    re.DOTALL,
)
KNOWLEDGE_SECTION_PATTERN = re.compile(
    r"(?m)^##[ \t]+(?P<title>.+?)\r?\n"
    r"<!--\s*page\s+(?P<page>\d+)\s*-->",
)


class ReasoningError(RuntimeError):
    """Base error for a recoverable reasoning-agent failure."""


class MalformedSourceError(ReasoningError):
    """Raised when a source markdown file violates the input contract."""


class LLMUnavailableError(ReasoningError):
    """Raised after all configured Groq retries are exhausted."""


class LLMResponseError(ReasoningError):
    """Raised when the LLM returns an invalid structured response."""


class LiteralString(str):
    """Marker used to serialize immutable draft text with YAML `|` style."""


class ChiefMindDumper(yaml.SafeDumper):
    """Safe YAML dumper with readable multiline strings."""


def _represent_literal_string(
    dumper: yaml.SafeDumper, value: LiteralString
) -> yaml.ScalarNode:
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", str(value), style="|"
    )


ChiefMindDumper.add_representer(
    LiteralString,
    _represent_literal_string,
)


@dataclass(frozen=True)
class SourceItem:
    path: Path
    metadata: dict[str, Any]
    body: str

    @property
    def action_id(self) -> str:
        return str(self.metadata["action_id"])

    @property
    def subject(self) -> str:
        return str(self.metadata.get("subject", "(no subject)"))

    @property
    def sender(self) -> str:
        return str(self.metadata.get("from", ""))


@dataclass(frozen=True)
class Decision:
    action_type: str
    informational: bool
    priority: str
    category: str
    summary: str
    steps: tuple[str, ...]


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_frontmatter_text(text: str, *, source: str = "<memory>") -> tuple[dict[str, Any], str]:
    """Parse and validate YAML frontmatter without accepting Python objects."""
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        raise MalformedSourceError(
            f"{source} must start with YAML frontmatter delimited by `---`."
        )
    try:
        metadata = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise MalformedSourceError(f"Invalid YAML in {source}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise MalformedSourceError(
            f"Frontmatter in {source} must be a YAML mapping."
        )
    body = match.group("body").strip()
    return metadata, body


def load_source_file(path: Path) -> SourceItem:
    """Load a workflow item and enforce the fields required for idempotency."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MalformedSourceError(f"Could not read {path}: {exc}") from exc
    metadata, body = parse_frontmatter_text(text, source=str(path))
    action_id = metadata.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        raise MalformedSourceError(
            f"{path} requires a non-empty string `action_id`."
        )
    metadata["action_id"] = action_id.strip()
    return SourceItem(path=path, metadata=metadata, body=body)


def dump_frontmatter(metadata: dict[str, Any], body: str = "") -> str:
    """Serialize a workflow artifact with deterministic safe YAML."""
    yaml_text = yaml.dump(
        metadata,
        Dumper=ChiefMindDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    ).rstrip()
    result = f"---\n{yaml_text}\n---\n"
    if body:
        result += f"\n{body.rstrip()}\n"
    return result


def atomic_write(path: Path, content: str) -> None:
    """Replace one artifact atomically so downstream agents never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)
        temporary_path.replace(path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _artifact_action_id(path: Path) -> str | None:
    try:
        metadata, _ = parse_frontmatter_text(
            path.read_text(encoding="utf-8"),
            source=str(path),
        )
    except (OSError, MalformedSourceError) as exc:
        LOGGER.warning("Ignoring malformed duplicate-guard artifact %s: %s", path, exc)
        return None
    action_id = metadata.get("action_id")
    return action_id.strip() if isinstance(action_id, str) else None


def collect_guarded_action_ids() -> set[str]:
    """Collect immutable/active action IDs from every guarded workflow state."""
    guarded_ids: set[str] = set()
    for directory in (PENDING_APPROVAL_DIR, APPROVED_DIR, DONE_DIR):
        for path in sorted(directory.glob("*.md")):
            action_id = _artifact_action_id(path)
            if action_id:
                guarded_ids.add(action_id)
    return guarded_ids


def _retry(operation: Callable[[], T], description: str) -> T:
    retryable = (
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
        APIStatusError,
    )
    for attempt in range(GROQ_RETRIES + 1):
        try:
            return operation()
        except retryable as exc:
            if attempt >= GROQ_RETRIES:
                raise LLMUnavailableError(
                    f"{description} failed after {attempt + 1} attempt(s): {exc}"
                ) from exc
            delay = GROQ_RETRY_DELAY * (2**attempt)
            LOGGER.warning(
                "%s failed (%s). Retrying in %ss [%s/%s].",
                description,
                exc,
                delay,
                attempt + 1,
                GROQ_RETRIES,
            )
            time.sleep(delay)
    raise RuntimeError("Unreachable retry state")


def _response_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMResponseError("Groq returned no message content.") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMResponseError("Groq returned empty message content.")
    return content.strip()


def _parse_json_response(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"\A```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\Z", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"Classification was not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMResponseError("Classification JSON must be an object.")
    return value


def classify_item(client: Groq, item: SourceItem) -> Decision:
    """Ask Groq for a constrained, machine-validated workflow decision."""
    system_prompt = """You classify inbound items for ChiefMind.
Return ONLY a JSON object with exactly these keys:
- action_type: "email_send" when a reply should be drafted, otherwise "manual"
- informational: true only when no reply and no human action is needed
- priority: "high", "medium", or "low"
- category: short lowercase snake_case label
- summary: concise factual summary
- steps: array of 2-5 concrete next-step strings

Never claim an action was executed. Use manual for legal, security, financial,
ambiguous, or non-email work that requires human judgment."""
    user_prompt = (
        f"Source metadata:\n{json.dumps(item.metadata, ensure_ascii=False, default=str)}"
        f"\n\nSource body:\n{item.body or '(empty body)'}"
    )

    response = _retry(
        lambda: client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=CLASSIFICATION_TEMPERATURE,
            max_tokens=REASONING_MAX_TOKENS,
            response_format={"type": "json_object"},
        ),
        "Groq classification",
    )
    data = _parse_json_response(_response_text(response))

    action_type = str(data.get("action_type", "")).strip().lower()
    if action_type not in VALID_ACTION_TYPES:
        raise LLMResponseError(
            f"Unsupported action_type {action_type!r}; expected email_send or manual."
        )
    informational = data.get("informational")
    if not isinstance(informational, bool):
        raise LLMResponseError("Classification `informational` must be boolean.")
    if informational and action_type != "manual":
        raise LLMResponseError("Informational items must use action_type `manual`.")

    priority = str(data.get("priority", "")).strip().lower()
    if priority not in VALID_PRIORITIES:
        raise LLMResponseError(
            f"Unsupported priority {priority!r}; expected high, medium, or low."
        )
    category = str(data.get("category", "")).strip().lower()
    category = re.sub(r"[^a-z0-9]+", "_", category).strip("_")
    if not category:
        raise LLMResponseError("Classification requires a non-empty category.")
    summary = str(data.get("summary", "")).strip()
    if not summary:
        raise LLMResponseError("Classification requires a non-empty summary.")
    raw_steps = data.get("steps")
    if (
        not isinstance(raw_steps, list)
        or not 2 <= len(raw_steps) <= 5
        or not all(isinstance(step, str) and step.strip() for step in raw_steps)
    ):
        raise LLMResponseError(
            "Classification `steps` must contain 2-5 non-empty strings."
        )
    return Decision(
        action_type=action_type,
        informational=informational,
        priority=priority,
        category=category,
        summary=summary,
        steps=tuple(step.strip() for step in raw_steps),
    )


def draft_email(
    client: Groq,
    item: SourceItem,
    knowledge_context: str,
) -> str:
    """Generate the exact immutable reply body at the mandated temperature."""
    system_prompt = """You draft professional email replies for ChiefMind.
Return ONLY the complete reply body: no subject line, analysis, JSON, markdown
fence, or commentary. Ground policy claims only in the supplied knowledge
sections. If facts are missing, ask for them. Never promise approval, payment,
refund, execution, or an outcome that has not occurred. The draft will be
stored unchanged for human approval and sent verbatim if approved."""
    user_prompt = f"""Original email
From: {item.sender}
Subject: {item.subject}

{item.body or "(empty body)"}

Retrieved knowledge
{knowledge_context or "(No matching knowledge section. Ask for clarification or avoid unsupported claims.)"}

Write the exact ready-to-send reply body."""

    response = _retry(
        lambda: client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=EMAIL_DRAFT_TEMPERATURE,
            max_tokens=REASONING_MAX_TOKENS,
        ),
        "Groq email drafting",
    )
    draft = _response_text(response)
    if draft.startswith("```") or not draft:
        raise LLMResponseError("Email draft must be plain, non-empty text.")
    return draft


def extract_knowledge_references(context: str) -> list[str]:
    references: list[str] = []
    for match in KNOWLEDGE_SECTION_PATTERN.finditer(context):
        references.append(
            f"Knowledge Base page {match.group('page')}: "
            f"{match.group('title').strip()}"
        )
    return references


def _safe_fragment(value: str) -> str:
    fragment = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return fragment[:120] or hashlib.sha256(value.encode()).hexdigest()[:16]


def _reply_subject(subject: str) -> str:
    return subject if re.match(r"(?i)^re\s*:", subject.strip()) else f"Re: {subject}"


def _create_plan(
    item: SourceItem,
    decision: Decision,
    references: list[str],
    created_at: datetime,
) -> Path:
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    metadata = {
        "id": f"plan_{timestamp}_{_safe_fragment(item.action_id)}",
        "action_id": item.action_id,
        "source_email": item.path.name,
        "priority": decision.priority,
        "category": decision.category,
        "recommended_action": decision.action_type,
        "steps": list(decision.steps),
        "knowledge_references": references,
        "created_at": isoformat_utc(created_at),
    }
    path = PLANS_DIR / f"Plan_{_safe_fragment(item.action_id)}.md"
    atomic_write(path, dump_frontmatter(metadata, decision.summary))
    return path


def _create_approval(
    item: SourceItem,
    decision: Decision,
    references: list[str],
    created_at: datetime,
    draft_body: str | None,
) -> Path:
    metadata: dict[str, Any] = {
        "action_id": item.action_id,
        "type": decision.action_type,
        "source_file": item.path.name,
        "priority": decision.priority,
        "category": decision.category,
    }
    if decision.action_type == "email_send":
        if draft_body is None:
            raise ReasoningError("email_send approval requires draft_body.")
        _, recipient = parseaddr(item.sender)
        if not recipient or "@" not in recipient:
            raise ReasoningError(
                f"Cannot create email approval without a valid sender address: {item.sender!r}"
            )
        # draft_sha256 lets the future executor prove the approved text did not
        # change between approval and execution.
        metadata.update(
            {
                "to": recipient,
                "subject": _reply_subject(item.subject),
                "draft_body": LiteralString(draft_body),
                "draft_sha256": hashlib.sha256(
                    draft_body.encode("utf-8")
                ).hexdigest(),
            }
        )
    else:
        metadata.update(
            {
                "summary": decision.summary,
                "instructions": list(decision.steps),
            }
        )
    metadata["knowledge_references"] = references
    metadata["created_at"] = isoformat_utc(created_at)

    path = PENDING_APPROVAL_DIR / f"{_safe_fragment(item.action_id)}.md"
    atomic_write(path, dump_frontmatter(metadata))
    return path


def _move_to_done(item: SourceItem) -> Path:
    destination = DONE_DIR / item.path.name
    if destination.exists():
        destination = DONE_DIR / (
            f"{item.path.stem}_{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
            f"{item.path.suffix}"
        )
    return Path(shutil.move(str(item.path), str(destination)))


def _quarantine_malformed(path: Path, error: Exception) -> None:
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    destination = FAILED_DIR / path.name
    if destination.exists():
        destination = FAILED_DIR / (
            f"{path.stem}_{utc_now().strftime('%Y%m%dT%H%M%SZ')}{path.suffix}"
        )
    try:
        shutil.move(str(path), str(destination))
        error_path = destination.with_suffix(".error.txt")
        atomic_write(error_path, f"{type(error).__name__}: {error}\n")
        LOGGER.error("Quarantined malformed source %s at %s", path, destination)
    except OSError:
        LOGGER.exception("Could not quarantine malformed source %s", path)


def process_item(client: Groq, item: SourceItem) -> tuple[Path, Path | None]:
    """Run the seven-step workflow for one already-validated source item."""
    decision = classify_item(client, item)
    query = " ".join((item.subject, item.body)).strip() or item.action_id
    knowledge_context = retrieve_relevant_sections(
        query,
        top_k=REASONING_TOP_K,
    )
    references = extract_knowledge_references(knowledge_context)

    draft_body: str | None = None
    if decision.action_type == "email_send":
        draft_body = draft_email(client, item, knowledge_context)

    created_at = utc_now()
    plan_path = _create_plan(
        item,
        decision,
        references,
        created_at,
    )

    if decision.informational:
        done_path = _move_to_done(item)
        LOGGER.info(
            "Completed informational item %s; plan=%s done=%s",
            item.action_id,
            plan_path,
            done_path,
        )
        return plan_path, None

    approval_path = _create_approval(
        item,
        decision,
        references,
        created_at,
        draft_body,
    )
    LOGGER.info(
        "Created plan and approval for %s: %s, %s",
        item.action_id,
        plan_path,
        approval_path,
    )
    return plan_path, approval_path


def build_client() -> Groq:
    if not GROQ_API_KEY:
        raise ReasoningError(
            "GROQ_API_KEY is not set. Copy scripts/.env.example to "
            "scripts/.env and add your private Groq API key."
        )
    return Groq(api_key=GROQ_API_KEY)


def run_once(client: Groq | None = None, *, limit: int | None = None) -> int:
    """Process the current inbox once and return the number of completed items."""
    ensure_directories()
    paths = sorted(NEEDS_ACTION_DIR.glob("*.md"))
    if limit is not None:
        paths = paths[:limit]
    if not paths:
        LOGGER.info("No markdown files found in %s", NEEDS_ACTION_DIR)
        return 0

    guarded_ids = collect_guarded_action_ids()
    active_client = client or build_client()
    completed = 0

    for path in paths:
        try:
            item = load_source_file(path)
        except MalformedSourceError as exc:
            _quarantine_malformed(path, exc)
            continue

        if item.action_id in guarded_ids:
            LOGGER.info(
                "Skipping duplicate action_id %s from %s",
                item.action_id,
                path,
            )
            continue

        try:
            process_item(active_client, item)
            guarded_ids.add(item.action_id)
            completed += 1
        except (LLMUnavailableError, LLMResponseError, ReasoningError):
            # Leave valid source items in Needs_Action so transient API or
            # validation failures can be retried without data loss.
            LOGGER.exception(
                "Reasoning failed for %s; source remains in Needs_Action.",
                item.action_id,
            )
        except Exception:
            LOGGER.exception(
                "Unexpected failure for %s; continuing with remaining items.",
                item.action_id,
            )
    return completed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most this many source files.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.limit is not None and args.limit < 1:
        LOGGER.error("--limit must be a positive integer.")
        return 2
    try:
        completed = run_once(limit=args.limit)
    except ReasoningError as exc:
        LOGGER.error("%s", exc)
        return 1
    LOGGER.info("Reasoning cycle completed %s item(s).", completed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
