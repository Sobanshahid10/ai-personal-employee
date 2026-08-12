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
    LINKEDIN_DRAFT_TEMPERATURE,
    NEEDS_ACTION_DIR,
    PENDING_APPROVAL_DIR,
    PLANS_DIR,
    REASONING_MAX_TOKENS,
    REASONING_TOP_K,
    REJECTED_DIR,
    ensure_directories,
    setup_logging,
)
from autonomy import (
    AutonomyError,
    EventAssessment,
    FinalDecision,
    OperatorPolicy,
    append_decision_record,
    append_digest_entry,
    assess_routine_notification,
    is_automated_or_noreply_sender,
    load_operator_policy,
    parse_event_assessment,
    policy_prompt_excerpt,
    resolve_autonomy_mode,
)
from html_email import render_html_email
from knowledge import retrieve_relevant_sections


LOGGER = setup_logging("chiefmind.reasoning")
T = TypeVar("T")
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
    """Legacy plan metadata derived from autonomy assessment."""

    action_type: str
    priority: str
    category: str
    summary: str
    steps: tuple[str, ...]


def _priority_from_importance(importance: str) -> str:
    mapping = {
        "critical": "high",
        "high": "high",
        "moderate": "medium",
        "low": "low",
        "trivial": "low",
    }
    return mapping.get(importance, "medium")


def _category_from_classifications(classifications: tuple[str, ...]) -> str:
    if not classifications:
        return "general"
    primary = classifications[0].lower()
    return re.sub(r"[^a-z0-9]+", "_", primary).strip("_") or "general"


def _decision_from_assessment(
    assessment: EventAssessment,
    item: SourceItem | None = None,
) -> Decision:
    text_check = ""
    if item:
        text_check = f"{item.subject} {item.body} {assessment.summary}".lower()
    else:
        text_check = assessment.summary.lower()

    is_explicit_linkedin = bool(re.search(r"\blinkedin\b", text_check))
    is_opportunity = bool(re.search(
        r"\b(partnership|collaboration|collaborate|joint venture|milestone|business win|deal signed|client win|case study|product launch|feature launch|keynote|speaker|speaking|podcast guest|award|recognition|announcement|breakthrough|achievement)\b",
        text_check,
        re.IGNORECASE,
    ))

    if is_explicit_linkedin or (
        is_opportunity
        and (
            assessment.reply_intent == "required"
            or "EXTERNAL_COMMUNICATION" in assessment.classifications
            or "USER_ACTION_REQUIRED" in assessment.classifications
        )
    ):
        action_type = "linkedin_post"
    elif (
        assessment.reply_intent == "required"
        or "EXTERNAL_COMMUNICATION" in assessment.classifications
    ):
        action_type = "email_send"
    else:
        action_type = "manual"

    return Decision(
        action_type=action_type,
        priority=_priority_from_importance(assessment.importance),
        category=_category_from_classifications(assessment.classifications),
        summary=assessment.summary,
        steps=assessment.steps
        if assessment.steps
        else ("Review the prepared recommendation.",),
    )


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def isoformat_utc(value: datetime | None = None) -> str:
    current = value or utc_now()
    return current.astimezone(UTC).isoformat(timespec="seconds").replace(
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
    for directory in (PENDING_APPROVAL_DIR, APPROVED_DIR, REJECTED_DIR, DONE_DIR):
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
            if "tokens per day (TPD)" in str(exc) or attempt >= GROQ_RETRIES:
                raise LLMUnavailableError(
                    f"{description} failed: {exc}"
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


def assess_event(
    client: Groq | None,
    item: SourceItem,
    policy: OperatorPolicy,
) -> EventAssessment:
    """Ask Groq for a constrained autonomy assessment (policy engine decides final mode)."""
    routine = assess_routine_notification(
        policy=policy,
        sender=item.sender,
        subject=item.subject,
        body=item.body,
        metadata=item.metadata,
    )
    if routine is not None:
        LOGGER.info(
            "Deterministically classified %s as a routine notification.",
            item.action_id,
        )
        return routine

    active_client = client or build_client()
    system_prompt = """You assess inbound work for ChiefMind, an autonomous personal employee.
Return ONLY a JSON object with exactly these keys:
- action_required: boolean — false when no response, task, or decision is truly needed
- classifications: non-empty array of one or more of:
  INFORMATION_ONLY, ROUTINE_ACTION, USER_ACTION_REQUIRED, DECISION_REQUIRED,
  EXTERNAL_COMMUNICATION, FINANCIAL_ACTION, SECURITY_ACTION, IRREVERSIBLE_ACTION, CRITICAL_EVENT
- reply_intent: "none", "optional", or "required"
- confidence: "low", "medium", or "high" (understanding and appropriate handling)
- importance: "trivial", "low", "moderate", "high", or "critical"
- risk: "low", "moderate", "high", or "critical" (damage from a wrong autonomous action)
- reversibility: "REVERSIBLE", "PARTIALLY_REVERSIBLE", or "IRREVERSIBLE"
- recommended_autonomy_mode: one of AUTO_EXECUTE, AUTO_EXECUTE_AND_SUMMARIZE,
  HOLD_AND_SUMMARIZE, ASK_USER, ESCALATE (hint only; policy engine decides)
- summary: concise factual summary (1-2 sentences)
- steps: array of 0-5 concrete next-step strings (required when reply_intent is "required")

Rules:
- Do not treat marketing words like "urgent" as proof of importance.
- Direct instructions or requests from a human contact asking to draft, stage, create, post, publish, or reply (such as creating a LinkedIn post, social update, email reply, or task) MUST have action_required: true, classification USER_ACTION_REQUIRED or EXTERNAL_COMMUNICATION, and reply_intent "required".
- Automated notifications, third-party marketing offers, newsletters, advertisements, social digests, bulk announcements, event invitations, webinars, and promotional mail MUST have action_required: false and reply_intent: "none".
- EXTERNAL_COMMUNICATION applies when an email reply or social media post draft is required for approval.
- Never claim an action was executed."""
    body_text = item.body or "(empty body)"
    if len(body_text) > 3000:
        body_text = body_text[:3000] + "\n...[content truncated for assessment]..."
    user_prompt = (
        f"Operator policy excerpt:\n{policy_prompt_excerpt(policy)}\n\n"
        f"Source metadata:\n{json.dumps(item.metadata, ensure_ascii=False, default=str)}"
        f"\n\nSource body:\n{body_text}"
    )

    models_to_try = [GROQ_MODEL]
    if "llama-3.1-8b-instant" not in models_to_try:
        models_to_try.append("llama-3.1-8b-instant")

    response = None
    last_err = None
    for model_name in models_to_try:
        try:
            response = _retry(
                lambda m=model_name: active_client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=CLASSIFICATION_TEMPERATURE,
                    max_tokens=REASONING_MAX_TOKENS,
                    response_format={"type": "json_object"},
                ),
                f"Groq event assessment ({model_name})",
            )
            break
        except LLMUnavailableError as exc:
            last_err = exc
            LOGGER.warning("Assessment with model %s failed: %s; attempting fallback", model_name, exc)

    if response is None:
        raise last_err or LLMUnavailableError("All LLM models failed assessment.")
    data = _parse_json_response(_response_text(response))
    try:
        return parse_event_assessment(data)
    except AutonomyError as exc:
        raise LLMResponseError(str(exc)) from exc


def classify_item(client: Groq, item: SourceItem) -> Decision:
    """Compatibility wrapper returning plan fields from a live assessment."""
    policy = load_operator_policy()
    assessment = assess_event(client, item, policy)
    return _decision_from_assessment(assessment)


def draft_email(
    client: Groq | None,
    item: SourceItem,
    knowledge_context: str,
) -> str:
    """Generate the exact immutable reply body at the mandated temperature."""
    if client is None:
        sender_name = parseaddr(item.sender)[0] or "there"
        return (
            f"Hello {sender_name},\n\n"
            f"Thank you for reaching out regarding '{item.subject}'. "
            "I have received your message and staged it for review.\n\n"
            "Best regards,\nSoban"
        )

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
    models_to_try = [GROQ_MODEL]
    if "llama-3.1-8b-instant" not in models_to_try:
        models_to_try.append("llama-3.1-8b-instant")

    for model_name in models_to_try:
        try:
            response = _retry(
                lambda m=model_name: client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=EMAIL_DRAFT_TEMPERATURE,
                    max_tokens=REASONING_MAX_TOKENS,
                ),
                f"Groq email drafting ({model_name})",
            )
            draft = _response_text(response)
            if draft and not draft.startswith("```"):
                return draft
        except LLMUnavailableError as exc:
            LOGGER.warning("Drafting with model %s failed: %s; trying fallback", model_name, exc)

    sender_name = parseaddr(item.sender)[0] or "there"
    return (
        f"Hello {sender_name},\n\n"
        f"Thank you for reaching out regarding '{item.subject}'. "
        "I have received your message and staged it for review.\n\n"
        "Best regards,\nSoban"
    )


def _clean_linkedin_prompt_text(text: str) -> str:
    """Strip common lead-in phrases like 'Post on LinkedIn:' from user requests."""
    cleaned = re.sub(
        r"(?i)^(please\s+)?(post|share|publish|draft|create)\s+(this\s+)?(on\s+)?linkedin\s*:\s*",
        "",
        text.strip(),
    )
    cleaned = re.sub(
        r"(?i)^(linkedin\s+post\s+request|linkedin\s+post)\s*:\s*",
        "",
        cleaned.strip(),
    )
    return cleaned.strip()


def _clean_email_artifacts(text: str) -> str:
    """Strip salutations, email footers, sign-offs, and meta headers from intake body."""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"(?i)^(hi|hello|dear|hey)\b", line):
            continue
        if re.match(r"(?i)^(best regards|regards|thanks|thank you|sincerely|cheers)\b", line):
            continue
        if re.match(r"(?i)^(vp of|ceo|cto|founder|director|manager)\b", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _fallback_linkedin_post(item: SourceItem) -> str:
    """Generate executive-level production-grade LinkedIn post copy."""
    clean_subj = _clean_linkedin_prompt_text(item.subject)
    raw_clean = _clean_email_artifacts(_clean_linkedin_prompt_text(item.body))
    
    header = f"🚀 Strategic Milestone: {clean_subj}\n\n" if clean_subj else "🚀 Executive Strategic Update!\n\n"
    
    bullets: list[str] = []
    for line in raw_clean.splitlines():
        clean_line = line.strip("- *• ")
        if clean_line and len(clean_line) > 12 and not re.match(r"(?i)^(let's coordinate|following up)\b", clean_line):
            bullets.append(f"• {clean_line}")

    if not bullets:
        bullets = [
            "• Expanding enterprise AI automation capabilities across global operations.",
            "• Streamlining multi-channel workflows with human-in-the-loop governance.",
        ]

    bullets_text = "\n".join(bullets[:4])

    return (
        f"{header}"
        f"We are proud to announce a major breakthrough in enterprise AI collaboration:\n\n"
        f"{bullets_text}\n\n"
        f"This milestone reflects our commitment to building reliable, high-impact autonomous AI systems for modern enterprises.\n\n"
        f"How is your organization scaling AI workflow automation this year? Drop your insights below! 💬\n\n"
        f"#AI #EnterpriseTech #Innovation #Leadership #FutureOfWork"
    )


def _fallback_image_suggestion(item: SourceItem) -> str:
    """Generate a production-grade image/visual specification when LLM is unavailable."""
    clean_subj = _clean_linkedin_prompt_text(item.subject) or "Business Milestone"
    return (
        "🎨 PRODUCTION-GRADE VISUAL SPECIFICATION\n\n"
        "• COMPOSITION & LAYOUT: Split-screen horizontal layout (1200x627px for LinkedIn feed). "
        "Left 60%: Dynamic graphic container with high-contrast dark theme background. Right 40%: Typography overlay with key visual takeaway.\n\n"
        "• COLOR PALETTE:\n"
        "  - Background: Deep Obsidian Slate (#0F172A)\n"
        "  - Accent: Vibrant Electric Blue (#0EA5E9) & Gradient Cyan (#38BDF8)\n"
        "  - Text: Crisp White (#FFFFFF) & Subdued Light Silver (#94A3B8)\n\n"
        "• TYPOGRAPHY:\n"
        "  - Headline: Bold Modern Sans-Serif (Inter / Outfit), tight tracking.\n"
        "  - Subtitle: Medium Weight Sans-Serif, 80% opacity.\n\n"
        f"• GRAPHIC & PHOTO ELEMENTS: Abstract vector network nodes representing collaboration and growth. "
        f"Center badge highlight for: '{clean_subj}'. Subtle frosted glassmorphism card backdrop behind overlay text.\n\n"
        "• MOOD & AESTHETIC: Modern executive tech aesthetic; polished, dynamic, non-templated, and high-impact."
    )


def draft_linkedin_post(
    client: Groq | None,
    item: SourceItem,
) -> str:
    """Draft an engaging, tone-tailored LinkedIn post body from source request."""
    if client is None:
        return _fallback_linkedin_post(item)

    system_prompt = """You are an expert executive LinkedIn content strategist for ChiefMind.
Your task is to take raw email details or opportunity notes (partnerships, achievements, collaborations, product releases, client wins) and draft a highly polished, authentic, human, and high-impact LinkedIn post.

CRITICAL INSTRUCTIONS:
1. AUTHENTIC EXECUTIVE VOICE: Craft compelling copy reflecting a professional yet personable executive voice. Highlight impact, lessons learned, and genuine business value. Sound human, direct, and engaging—never corporate, templated, or robotic.
2. TAILOR TONE: Adjust tone based on the opportunity type:
   - Partnership / Collaboration: Warm, collaborative, value-driven, highlighting synergy.
   - Personal / Team Achievement: Celebratory yet humble, acknowledging team effort and key learnings.
   - Product / Feature Release: Sharp, visionary, focused on user impact and technical breakthrough.
3. STRUCTURE & FORMATTING:
   - HOOK: Open with an attention-grabbing 1-2 line hook to stop the feed scroll.
   - CONTEXT & STORY: Provide concise context on what happened, who was involved, and business impact.
   - HIGHLIGHTS: Use clean bullet points with clean emojis.
   - CTA: End with an engaging question or invitation for discussion.
   - HASHTAGS: Conclude with 3-5 strategically relevant, popular hashtags.
4. OUTPUT REQUIREMENT: Return ONLY the exact, complete post text. Do NOT include markdown code fences (```), meta-commentary, or headers like "Subject:"."""

    clean_subj = _clean_linkedin_prompt_text(item.subject)
    clean_body = _clean_linkedin_prompt_text(item.body)
    user_prompt = (
        f"Topic/Subject: {clean_subj}\n"
        f"Raw Input Details:\n{clean_body}\n\n"
        f"Transform this input into an authentic, beautifully formatted LinkedIn post."
    )

    models_to_try = [GROQ_MODEL]
    if "llama-3.1-8b-instant" not in models_to_try:
        models_to_try.append("llama-3.1-8b-instant")

    for model_name in models_to_try:
        try:
            response = _retry(
                lambda m=model_name: client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=LINKEDIN_DRAFT_TEMPERATURE,
                    max_tokens=REASONING_MAX_TOKENS,
                ),
                f"Groq LinkedIn drafting ({model_name})",
            )
            draft = _response_text(response)
            if draft and not draft.startswith("```"):
                draft_clean = re.sub(r"^```(?:markdown|text)?\n?", "", draft, flags=re.IGNORECASE)
                draft_clean = re.sub(r"\n?```$", "", draft_clean).strip()
                return draft_clean
        except LLMUnavailableError as exc:
            LOGGER.warning("LinkedIn drafting with model %s failed: %s; trying fallback", model_name, exc)

    return _fallback_linkedin_post(item)


def draft_image_suggestion(
    client: Groq | None,
    item: SourceItem,
    post_body: str = "",
) -> str:
    """Generate a production-grade image/visual specification for the post."""
    if client is None:
        return _fallback_image_suggestion(item)

    system_prompt = """You are an expert executive creative director and visual designer for LinkedIn content.
Your task is to create a production-grade visual/image description that complements the given LinkedIn post or business opportunity.

CRITICAL INSTRUCTIONS:
Provide a detailed, precise production-grade visual specification suitable for a designer or AI image generator (Midjourney, DALL-E, Canva).
Your output MUST include specific guidance for:
1. COMPOSITION & LAYOUT: Aspect ratio (1200x627 landscape or 1080x1350 vertical), grid alignment, visual hierarchy, and focal points.
2. COLOR PALETTE: Primary, secondary, and accent colors with explicit hex codes or color names.
3. TYPOGRAPHY: Font style, weight hierarchy, text overlay placement, and readability constraints.
4. GRAPHIC & PHOTO ELEMENTS: Specific visual assets, partner logos, vector graphics, chart types, or photographic style needed.
5. MOOD & AESTHETIC: Executive tech aesthetic (glassmorphism, dynamic lighting gradients, crisp modern UI).

OUTPUT REQUIREMENT: Return ONLY the exact formatted visual suggestion text without markdown code fences (```)."""

    user_prompt = (
        f"Opportunity Topic: {item.subject}\n"
        f"Post Body Copy:\n{post_body or item.body}\n\n"
        f"Generate a production-grade visual specification that perfectly complements this post."
    )

    models_to_try = [GROQ_MODEL]
    if "llama-3.1-8b-instant" not in models_to_try:
        models_to_try.append("llama-3.1-8b-instant")

    for model_name in models_to_try:
        try:
            response = _retry(
                lambda m=model_name: client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=LINKEDIN_DRAFT_TEMPERATURE,
                    max_tokens=REASONING_MAX_TOKENS,
                ),
                f"Groq image suggestion drafting ({model_name})",
            )
            draft = _response_text(response)
            if draft and not draft.startswith("```"):
                draft_clean = re.sub(r"^```(?:markdown|text)?\n?", "", draft, flags=re.IGNORECASE)
                draft_clean = re.sub(r"\n?```$", "", draft_clean).strip()
                return draft_clean
        except LLMUnavailableError as exc:
            LOGGER.warning("Image suggestion drafting with model %s failed: %s; trying fallback", model_name, exc)

    return _fallback_image_suggestion(item)


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


def _plan_text(value: Any) -> str:
    """Normalize untrusted source text before rendering it in a plan."""
    return " ".join(str(value or "").replace("\x00", "").split())


def _plan_table_text(value: Any) -> str:
    return _plan_text(value).replace("|", "\\|")


def _format_plan_body(
    item: SourceItem,
    decision: Decision,
    final: FinalDecision,
    references: list[str],
) -> str:
    """Render a human-readable plan document beneath YAML frontmatter."""
    steps = list(decision.steps) or ["Review the prepared recommendation."]
    step_lines = "\n".join(
        f"{index}. {_plan_text(step)}" for index, step in enumerate(steps, 1)
    )
    reference_lines = (
        "\n".join(f"- {_plan_text(reference)}" for reference in references)
        if references
        else "- No knowledge-base sections matched."
    )
    classifications = ", ".join(
        label.replace("_", " ").title() for label in final.classifications
    )
    return f"""# Action Plan

## Overview
{_plan_text(decision.summary)}

## Recommended Action
| Field | Value |
| --- | --- |
| Type | `{_plan_table_text(decision.action_type)}` |
| Priority | `{_plan_table_text(decision.priority)}` |
| Category | `{_plan_table_text(decision.category)}` |
| Autonomy mode | `{_plan_table_text(final.autonomy_mode)}` |
| Classifications | {_plan_table_text(classifications)} |

## Steps
{step_lines}

## Policy Routing
- **Rule:** `{_plan_text(final.policy_rule_id)}`
- **Reason:** {_plan_text(final.reason)}

## Source Email
- **From:** {_plan_text(item.sender)}
- **Subject:** {_plan_text(item.subject)}
- **Received:** {_plan_text(item.metadata.get("received_at", "unknown"))}

## Knowledge References
{reference_lines}
"""


def _create_plan(
    item: SourceItem,
    decision: Decision,
    final: FinalDecision,
    references: list[str],
    created_at: datetime,
) -> Path:
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    metadata = {
        "id": f"plan_{timestamp}_{_safe_fragment(item.action_id)}",
        "action_id": item.action_id,
        "source_email": item.path.name,
        "subject": item.subject,
        "summary": decision.summary,
        "priority": decision.priority,
        "category": decision.category,
        "recommended_action": decision.action_type,
        "autonomy_mode": final.autonomy_mode,
        "classifications": list(final.classifications),
        "steps": list(decision.steps),
        "knowledge_references": references,
        "created_at": isoformat_utc(created_at),
    }
    if item.metadata.get("received_at"):
        metadata["received_at"] = item.metadata["received_at"]
    path = PLANS_DIR / f"Plan_{_safe_fragment(item.action_id)}.md"
    body = _format_plan_body(item, decision, final, references)
    atomic_write(path, dump_frontmatter(metadata, body))
    return path


def _create_approval(
    item: SourceItem,
    decision: Decision,
    final: FinalDecision,
    references: list[str],
    created_at: datetime,
    draft_body: str | None,
    client: Groq | None = None,
) -> Path:
    metadata: dict[str, Any] = {
        "action_id": item.action_id,
        "type": decision.action_type,
        "source_file": item.path.name,
        "priority": decision.priority,
        "category": decision.category,
        "autonomy_mode": final.autonomy_mode,
        "classifications": list(final.classifications),
        "policy_rule_id": final.policy_rule_id,
        "created_at": isoformat_utc(created_at),
    }
    if item.metadata.get("received_at"):
        metadata["received_at"] = item.metadata["received_at"]
    if final.autonomy_mode == "ESCALATE":
        metadata["escalated"] = True
        metadata["escalation_reason"] = final.reason
    if decision.action_type == "email_send":
        if draft_body is None:
            raise ReasoningError("email_send approval requires draft_body.")
        _, recipient = parseaddr(item.sender)
        if not recipient or "@" not in recipient:
            raise ReasoningError(
                f"Cannot create email approval without a valid sender address: {item.sender!r}"
            )
        reply_subj = _reply_subject(item.subject)
        html_body = render_html_email(draft_body, subject=reply_subj)
        # draft_sha256 lets the future executor prove the approved text did not
        # change between approval and execution.
        email_meta: dict[str, Any] = {
            "to": recipient,
            "subject": reply_subj,
            "draft_body": LiteralString(draft_body),
            "draft_sha256": hashlib.sha256(
                draft_body.encode("utf-8")
            ).hexdigest(),
            "html_body": LiteralString(html_body),
            "html_sha256": hashlib.sha256(
                html_body.encode("utf-8")
            ).hexdigest(),
        }
        if item.metadata.get("thread_id"):
            email_meta["thread_id"] = str(item.metadata["thread_id"])
        if item.metadata.get("message_id_header"):
            email_meta["message_id_header"] = str(item.metadata["message_id_header"])
        metadata.update(email_meta)
    elif decision.action_type == "linkedin_post":
        post_body = draft_body if draft_body else draft_linkedin_post(client, item)
        image_suggestion = draft_image_suggestion(client, item, post_body)
        
        # Generate production-grade visual graphic banner
        rel_image_path = None
        try:
            from image_generator import generate_linkedin_banner
            banner_path = generate_linkedin_banner(
                action_id=_safe_fragment(item.action_id),
                category=decision.category.upper().replace("_", " "),
                headline=_clean_linkedin_prompt_text(item.subject),
                subtext=decision.summary[:90] if decision.summary else "Executive Strategic Announcement",
            )
            rel_image_path = f"/static/generated_images/{banner_path.name}"
        except Exception as img_exc:
            LOGGER.warning("Could not generate LinkedIn graphic banner: %s", img_exc)

        metadata_payload = {
            "post_body": LiteralString(post_body),
            "image_suggestion": LiteralString(image_suggestion),
            "summary": decision.summary,
            "instructions": list(decision.steps),
        }
        if rel_image_path:
            metadata_payload["image_path"] = rel_image_path
        metadata.update(metadata_payload)
    else:
        metadata.update(
            {
                "summary": decision.summary,
                "instructions": list(decision.steps),
                "why_it_matters": final.reason,
            }
        )
    metadata["knowledge_references"] = references
    metadata["created_at"] = isoformat_utc(created_at)

    path = PENDING_APPROVAL_DIR / f"{_safe_fragment(item.action_id)}.md"
    atomic_write(path, dump_frontmatter(metadata))
    return path


def _relocate_source(
    item: SourceItem,
    *,
    resolution: dict[str, Any],
) -> Path:
    """Write resolved source metadata to Done/ and remove Needs_Action copy."""
    metadata = dict(item.metadata)
    metadata.update(resolution)
    metadata["status"] = "done"
    metadata["resolved_at"] = isoformat_utc()
    destination = DONE_DIR / item.path.name
    if destination.exists():
        destination = DONE_DIR / (
            f"{item.path.stem}_{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
            f"{item.path.suffix}"
        )
    atomic_write(destination, dump_frontmatter(metadata, item.body))
    item.path.unlink(missing_ok=True)
    return destination


def _move_to_done(item: SourceItem) -> Path:
    return _relocate_source(item, resolution={"resolution": "archived"})


def _archive_duplicate_guard(item: SourceItem) -> Path:
    return _relocate_source(
        item,
        resolution={
            "resolution": "duplicate_guard",
            "note": "action_id already active in workflow ledger",
        },
    )


def _quarantine_malformed(path: Path, error: Exception) -> None:
    if not path.is_file():
        return
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


def process_item(client: Groq | None, item: SourceItem) -> tuple[Path | None, Path | None]:
    """Run autonomy assessment, policy routing, and workflow side effects."""
    policy = load_operator_policy()
    assessment = assess_event(client, item, policy)
    final = resolve_autonomy_mode(
        event_id=item.action_id,
        assessment=assessment,
        policy=policy,
        sender=item.sender,
        subject=item.subject,
        body=item.body,
    )
    decision = _decision_from_assessment(assessment, item)

    append_decision_record(
        {
            **final.to_audit_dict(),
            "agent": "reasoning",
            "source_file": item.path.name,
            "subject": item.subject,
            "from": item.sender,
            "details": f"Reasoned: {assessment.summary}",
        }
    )

    references: list[str] = []
    draft_body: str | None = None
    created_at = utc_now()

    requires_approval = final.requires_approval
    if requires_approval and decision.action_type == "email_send" and is_automated_or_noreply_sender(item.sender, item.metadata):
        LOGGER.warning("Overriding email_send decision for automated/noreply sender %s; auto-handling instead.", item.sender)
        requires_approval = False

    if requires_approval:
        query = " ".join((item.subject, item.body)).strip() or item.action_id
        knowledge_context = retrieve_relevant_sections(
            query,
            top_k=REASONING_TOP_K,
        )
        references = extract_knowledge_references(knowledge_context)
        
        if decision.action_type == "email_send":
            draft_body = draft_email(client, item, knowledge_context)
        elif decision.action_type == "linkedin_post":
            draft_body = draft_linkedin_post(client, item)

        plan_path = _create_plan(
            item,
            decision,
            final,
            references,
            created_at,
        )
        approval_path = _create_approval(
            item,
            decision,
            final,
            references,
            created_at,
            draft_body,
            client=client,
        )

        # Ensure both Email Reply and LinkedIn Post approval artifacts exist when LinkedIn is involved
        text_check = f"{item.subject} {item.body}".lower()
        is_linkedin_req = bool(re.search(r"\blinkedin\b", text_check))

        if is_linkedin_req and decision.action_type != "email_send" and item.sender:
            email_draft = draft_email(client, item, knowledge_context)
            email_decision = Decision(
                action_type="email_send",
                priority=decision.priority,
                category=decision.category,
                summary=f"Email Reply: {decision.summary}",
                steps=decision.steps,
            )
            email_item = SourceItem(
                path=item.path,
                metadata={**item.metadata, "action_id": f"reply_{item.action_id}"},
                body=item.body,
            )
            _create_approval(
                email_item,
                email_decision,
                final,
                references,
                created_at,
                email_draft,
                client=client,
            )

        if is_linkedin_req and decision.action_type != "linkedin_post":
            linkedin_post_body = draft_linkedin_post(client, item)
            linkedin_decision = Decision(
                action_type="linkedin_post",
                priority=decision.priority,
                category="external_communication",
                summary=f"LinkedIn Post: {decision.summary}",
                steps=decision.steps,
            )
            linkedin_item = SourceItem(
                path=item.path,
                metadata={**item.metadata, "action_id": f"linkedin_{item.action_id}"},
                body=item.body,
            )
            _create_approval(
                linkedin_item,
                linkedin_decision,
                final,
                references,
                created_at,
                linkedin_post_body,
                client=client,
            )
        done_path = _relocate_source(
            item,
            resolution={
                "resolution": "pending_approval",
                "autonomy_mode": final.autonomy_mode,
                "policy_rule_id": final.policy_rule_id,
            },
        )
        LOGGER.info(
            "Approval required for %s: plan=%s pending=%s archived=%s",
            item.action_id,
            plan_path,
            approval_path,
            done_path,
        )
        return plan_path, approval_path

    append_digest_entry(
        {
            "action_id": item.action_id,
            "from": item.sender,
            "subject": item.subject,
            "summary": assessment.summary,
            "autonomy_mode": final.autonomy_mode,
            "classifications": list(final.classifications),
            "policy_rule_id": final.policy_rule_id,
            "timestamp": isoformat_utc(created_at),
        }
    )
    done_path = _relocate_source(
        item,
        resolution={
            "resolution": "auto_handled",
            "autonomy_mode": final.autonomy_mode,
            "policy_rule_id": final.policy_rule_id,
            "digest_summary": assessment.summary,
        },
    )
    LOGGER.info(
        "Auto-handled %s (%s); done=%s",
        item.action_id,
        final.autonomy_mode,
        done_path,
    )
    return None, None


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
    # The client is created lazily by assess_event. Routine platform
    # notifications therefore drain even if Groq is unavailable.
    active_client = client
    completed = 0

    for path in paths:
        try:
            item = load_source_file(path)
        except MalformedSourceError as exc:
            _quarantine_malformed(path, exc)
            continue

        if str(item.metadata.get("type", "email")).lower() != "email":
            LOGGER.debug(
                "Leaving non-email item %s for its specialized agent.",
                item.action_id,
            )
            continue

        if item.action_id in guarded_ids:
            LOGGER.info(
                "Skipping duplicate action_id %s from %s",
                item.action_id,
                path,
            )
            try:
                _archive_duplicate_guard(item)
            except OSError:
                LOGGER.exception(
                    "Could not archive duplicate source %s.", item.action_id
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
