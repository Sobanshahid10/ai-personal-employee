"""Poll Gmail and place actionable messages into ChiefMind's file workflow."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, TypeVar
import threading

from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from authenticate_gmail import GmailAuthenticationError, load_gmail_credentials
from config import (
    GMAIL_MAX_RESULTS,
    GMAIL_POLL_INTERVAL,
    GMAIL_QUERY,
    GMAIL_RETRIES,
    GMAIL_RETRY_DELAY,
    GMAIL_USER_ID,
    NEEDS_ACTION_DIR,
    REASONING_LOOP_FILE,
    REASONING_TRIGGER_TIMEOUT,
    SCRIPTS_DIR,
    ensure_directories,
    load_processed_ids,
    save_processed_ids,
    setup_logging,
)
from workflow_utils import atomic_write_text


LOGGER = setup_logging("chiefmind.gmail.watcher")
T = TypeVar("T")


class _HTMLTextExtractor(HTMLParser):
    """Convert an HTML fallback body into readable plain text."""

    BLOCK_TAGS = {
        "br",
        "div",
        "p",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line).strip()


def _with_retry(operation: Callable[[], T], description: str) -> T:
    """Execute an API operation with configured exponential backoff."""
    for attempt in range(GMAIL_RETRIES + 1):
        try:
            return operation()
        except (HttpError, OSError) as exc:
            if attempt >= GMAIL_RETRIES:
                raise
            delay = GMAIL_RETRY_DELAY * (2**attempt)
            LOGGER.warning(
                "%s failed (%s). Retrying in %s seconds [%s/%s].",
                description,
                exc,
                delay,
                attempt + 1,
                GMAIL_RETRIES,
            )
            time.sleep(delay)
    raise RuntimeError("Unreachable retry state")


def _decode_body(data: str | None) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    try:
        decoded = base64.urlsafe_b64decode(data + padding)
        return decoded.decode("utf-8", errors="replace")
    except (ValueError, TypeError) as exc:
        LOGGER.warning("Could not decode one Gmail body part: %s", exc)
        return ""


def _collect_mime_parts(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime_type == "text/plain" and data:
            plain_parts.append(_decode_body(data))
        elif mime_type == "text/html" and data:
            html_parts.append(_decode_body(data))
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    return plain_parts, html_parts


def extract_message_body(payload: dict[str, Any]) -> str:
    """Return the complete plain-text body, with HTML as a fallback."""
    plain_parts, html_parts = _collect_mime_parts(payload)
    if plain_parts:
        return "\n\n".join(part.strip() for part in plain_parts if part.strip())
    if html_parts:
        extractor = _HTMLTextExtractor()
        extractor.feed("\n".join(html_parts))
        return extractor.text()
    return "(No readable message body.)"


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in payload.get("headers", [])
    }


def _received_at(message: dict[str, Any], headers: dict[str, str]) -> str:
    date_header = headers.get("date")
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            LOGGER.warning("Could not parse Date header %r; using internalDate.", date_header)

    try:
        milliseconds = int(message.get("internalDate", "0"))
    except (TypeError, ValueError):
        milliseconds = 0
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat().replace(
        "+00:00", "Z"
    )


def _yaml_string(value: str) -> str:
    """JSON strings are valid YAML scalars and safely escape arbitrary headers."""
    return json.dumps(value, ensure_ascii=False)


def render_markdown(message: dict[str, Any]) -> str:
    """Render a Gmail API message as ChiefMind markdown with YAML frontmatter."""
    message_id = str(message["id"])
    payload = message.get("payload", {})
    headers = _headers(payload)
    sender = headers.get("from", "(unknown sender)")
    subject = headers.get("subject", "(no subject)")
    received_at = _received_at(message, headers)
    body = extract_message_body(payload)
    list_unsubscribe = headers.get("list-unsubscribe", "")
    list_id = headers.get("list-id", "")
    precedence = headers.get("precedence", "")
    auto_submitted = headers.get("auto-submitted", "")

    return (
        "---\n"
        f"id: {_yaml_string(message_id)}\n"
        f"action_id: {_yaml_string(f'email_{message_id}')}\n"
        "type: email\n"
        f"from: {_yaml_string(sender)}\n"
        f"subject: {_yaml_string(subject)}\n"
        f"received_at: {_yaml_string(received_at)}\n"
        f"list_unsubscribe: {_yaml_string(list_unsubscribe)}\n"
        f"list_id: {_yaml_string(list_id)}\n"
        f"precedence: {_yaml_string(precedence)}\n"
        f"auto_submitted: {_yaml_string(auto_submitted)}\n"
        "priority: medium\n"
        "status: needs_action\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


def _write_message_file(message: dict[str, Any]) -> Path:
    """Atomically create or replace the deterministic message markdown file."""
    ensure_directories()
    message_id = str(message["id"])
    destination = NEEDS_ACTION_DIR / f"email_{message_id}.md"
    atomic_write_text(destination, render_markdown(message))
    return destination


def _list_message_ids(service: Resource) -> list[str]:
    message_ids: list[str] = []
    page_token: str | None = None

    while len(message_ids) < GMAIL_MAX_RESULTS:
        remaining = min(500, GMAIL_MAX_RESULTS - len(message_ids))
        response = _with_retry(
            lambda: service.users()
            .messages()
            .list(
                userId=GMAIL_USER_ID,
                q=GMAIL_QUERY,
                maxResults=remaining,
                pageToken=page_token,
            )
            .execute(),
            "Listing Gmail messages",
        )
        message_ids.extend(
            str(item["id"]) for item in response.get("messages", [])
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return message_ids


def _get_message(service: Resource, message_id: str) -> dict[str, Any]:
    return _with_retry(
        lambda: service.users()
        .messages()
        .get(userId=GMAIL_USER_ID, id=message_id, format="full")
        .execute(),
        f"Fetching Gmail message {message_id}",
    )


def _mark_as_read(service: Resource, message_id: str) -> None:
    _with_retry(
        lambda: service.users()
        .messages()
        .modify(
            userId=GMAIL_USER_ID,
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        )
        .execute(),
        f"Marking Gmail message {message_id} as read",
    )


def trigger_reasoning_loop() -> bool:
    """Run the Day 4 reasoning entry point once after new mail is staged."""
    if not REASONING_LOOP_FILE.is_file():
        LOGGER.warning(
            "Reasoning loop not found at %s; staged mail remains in Needs_Action.",
            REASONING_LOOP_FILE,
        )
        return False

    try:
        result = subprocess.run(
            [sys.executable, str(REASONING_LOOP_FILE)],
            cwd=SCRIPTS_DIR,
            capture_output=True,
            text=True,
            timeout=REASONING_TRIGGER_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOGGER.exception("Could not run reasoning loop: %s", exc)
        return False

    if result.stdout.strip():
        LOGGER.info("Reasoning loop output: %s", result.stdout.strip())
    if result.returncode != 0:
        LOGGER.error(
            "Reasoning loop exited with code %s: %s",
            result.returncode,
            result.stderr.strip(),
        )
        return False
    LOGGER.info("Reasoning loop completed successfully.")
    return True


def build_gmail_service() -> Resource:
    credentials = load_gmail_credentials(interactive=False)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def poll_once(
    service: Resource,
    *,
    stop_event: threading.Event | None = None,
) -> int:
    """Process one Gmail query result and return the number staged.

    The shared shutdown event is checked between messages so a large Gmail
    result cannot delay supervisor shutdown for an entire batch.
    """
    processed_ids = load_processed_ids()
    candidate_ids = _list_message_ids(service)
    new_ids = [message_id for message_id in candidate_ids if message_id not in processed_ids]
    LOGGER.info(
        "Gmail poll found %s candidate(s), %s new.",
        len(candidate_ids),
        len(new_ids),
    )

    completed = 0
    for message_id in new_ids:
        if stop_event and stop_event.is_set():
            LOGGER.info(
                "Gmail shutdown requested; leaving %s message(s) for the next poll.",
                len(new_ids) - completed,
            )
            break
        try:
            message = _get_message(service, message_id)
            destination = _write_message_file(message)
            _mark_as_read(service, message_id)
            processed_ids.add(message_id)
            save_processed_ids(processed_ids)
            completed += 1
            LOGGER.info("Staged Gmail message %s at %s", message_id, destination)
        except Exception:
            LOGGER.exception(
                "Failed to process Gmail message %s; continuing.", message_id
            )

    # Do not start a potentially long LLM subprocess while shutting down.
    if completed and not (stop_event and stop_event.is_set()):
        trigger_reasoning_loop()
    return completed


def run_watcher(
    *,
    once: bool = False,
    stop_event: threading.Event | None = None,
) -> int:
    """Run the resilient polling loop; return only on --once or interruption."""
    LOGGER.info(
        "Starting Gmail watcher: query=%r interval=%ss max_results=%s",
        GMAIL_QUERY,
        GMAIL_POLL_INTERVAL,
        GMAIL_MAX_RESULTS,
    )
    service: Resource | None = None

    while not (stop_event and stop_event.is_set()):
        try:
            if service is None:
                service = build_gmail_service()
            poll_once(service, stop_event=stop_event)
        except GmailAuthenticationError as exc:
            LOGGER.error("%s", exc)
            service = None
            if once:
                return 1
        except Exception:
            LOGGER.exception("Gmail polling cycle failed; watcher will continue.")
            service = None
            if once:
                return 1

        if once:
            return 0
        if stop_event:
            stop_event.wait(GMAIL_POLL_INTERVAL)
        else:
            time.sleep(GMAIL_POLL_INTERVAL)
    LOGGER.info("Gmail watcher received a graceful shutdown request.")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one polling cycle and exit (useful for testing).",
    )
    return parser.parse_args()


def main() -> int:
    try:
        return run_watcher(once=_parse_args().once)
    except KeyboardInterrupt:
        LOGGER.info("Gmail watcher stopped by user.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
