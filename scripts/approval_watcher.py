"""Execute exact human-approved ChiefMind artifacts from Approved/."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Protocol

from googleapiclient.discovery import Resource, build
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from authenticate_gmail import load_gmail_credentials
from html_email import render_html_email
from config import (
    APPROVAL_SETTLE_SECONDS,
    APPROVED_DIR,
    DONE_DIR,
    EXECUTION_RECEIPTS_FILE,
    FAILED_DIR,
    GMAIL_RETRIES,
    GMAIL_USER_ID,
    LINKEDIN_EXECUTION_TIMEOUT,
    LINKEDIN_POSTER_FILE,
    LOGS_DIR,
    MAX_EMAIL_SENDS_PER_HOUR,
    MAX_EXTERNAL_ACTIONS_PER_DAY,
    MAX_LINKEDIN_POSTS_PER_DAY,
    ensure_directories,
    setup_logging,
)
from workflow_utils import (
    WorkflowFileError,
    append_json_array,
    atomic_write_json,
    load_frontmatter_file,
    load_json_object,
    move_file,
)


LOGGER = setup_logging("chiefmind.approval")
PROCESS_LOCK = threading.RLock()
EMAIL_TYPES = {"email", "email_send"}
NO_EXTERNAL_ACTION_TYPES = {"plan", "manual"}
SUPPORTED_TYPES = EMAIL_TYPES | NO_EXTERNAL_ACTION_TYPES | {"linkedin_post"}


class ApprovalError(RuntimeError):
    """Base class for validation and execution failures."""


class ApprovalValidationError(ApprovalError):
    """Raised before an external action when an artifact is invalid."""


class ActionExecutionError(ApprovalError):
    """Raised when an approved action cannot be completed."""


class EmailSender(Protocol):
    def send_exact(
        self,
        *,
        recipient: str,
        subject: str,
        draft_body: str,
        html_body: str | None = None,
        thread_id: str | None = None,
        message_id_header: str | None = None,
    ) -> dict[str, Any]:
        """Send the exact approved draft and return provider metadata."""


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def isoformat_utc(value: datetime | None = None) -> str:
    current = value or utc_now()
    return current.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def initialize_execution_files() -> None:
    """Create the receipts store without overwriting an existing audit trail."""
    ensure_directories()
    if not EXECUTION_RECEIPTS_FILE.exists():
        atomic_write_json(EXECUTION_RECEIPTS_FILE, {})


def daily_log_path(value: datetime | None = None) -> Path:
    current = value or utc_now()
    return LOGS_DIR / f"{current.astimezone(UTC).date().isoformat()}.json"


def append_audit_event(event: dict[str, Any]) -> None:
    """Append one operation to the current valid JSON audit array."""
    append_json_array(daily_log_path(), event)


def load_receipts() -> dict[str, Any]:
    initialize_execution_files()
    return load_json_object(EXECUTION_RECEIPTS_FILE)


def save_receipts(receipts: dict[str, Any]) -> None:
    atomic_write_json(EXECUTION_RECEIPTS_FILE, receipts)


def _receipt_timestamp(receipt: dict[str, Any]) -> datetime | None:
    """Return the earliest safety-relevant reservation/completion time."""
    for field in ("reserved_at", "executed_at", "failed_at"):
        value = receipt.get(field)
        if not isinstance(value, str):
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
    return None


def enforce_rate_limits(
    receipts: dict[str, Any],
    action_type: str,
    *,
    now: datetime | None = None,
) -> None:
    """Fail closed before external I/O when configured limits are reached."""
    if action_type not in EMAIL_TYPES | {"linkedin_post"}:
        return
    current = (now or utc_now()).astimezone(UTC)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_start = current.replace(minute=0, second=0, microsecond=0)
    recent: list[tuple[str, datetime]] = []
    for receipt in receipts.values():
        if not isinstance(receipt, dict):
            continue
        timestamp = _receipt_timestamp(receipt)
        receipt_type = str(receipt.get("type", ""))
        if timestamp is not None:
            recent.append((receipt_type, timestamp))

    external_today = sum(
        receipt_type in EMAIL_TYPES | {"linkedin_post"}
        and timestamp >= day_start
        for receipt_type, timestamp in recent
    )
    if external_today >= MAX_EXTERNAL_ACTIONS_PER_DAY:
        raise ActionExecutionError(
            f"Daily external-action limit reached ({MAX_EXTERNAL_ACTIONS_PER_DAY})."
        )
    if action_type in EMAIL_TYPES:
        emails_this_hour = sum(
            receipt_type in EMAIL_TYPES and timestamp >= hour_start
            for receipt_type, timestamp in recent
        )
        if emails_this_hour >= MAX_EMAIL_SENDS_PER_HOUR:
            raise ActionExecutionError(
                f"Hourly email limit reached ({MAX_EMAIL_SENDS_PER_HOUR})."
            )
    if action_type == "linkedin_post":
        linkedin_today = sum(
            receipt_type == "linkedin_post" and timestamp >= day_start
            for receipt_type, timestamp in recent
        )
        if linkedin_today >= MAX_LINKEDIN_POSTS_PER_DAY:
            raise ActionExecutionError(
                f"Daily LinkedIn limit reached ({MAX_LINKEDIN_POSTS_PER_DAY})."
            )


def validate_approval(path: Path) -> dict[str, Any]:
    """Parse and validate fields without mutating or executing anything."""
    metadata, _ = load_frontmatter_file(path)
    action_id = metadata.get("action_id")
    action_type = metadata.get("type")
    if not isinstance(action_id, str) or not action_id.strip():
        raise ApprovalValidationError("Missing non-empty string `action_id`.")
    if not isinstance(action_type, str) or not action_type.strip():
        raise ApprovalValidationError("Missing non-empty string `type`.")

    metadata["action_id"] = action_id.strip()
    metadata["type"] = action_type.strip().lower()
    if metadata["type"] not in SUPPORTED_TYPES:
        raise ApprovalValidationError(
            f"Unsupported action type {metadata['type']!r}. "
            f"Expected one of {sorted(SUPPORTED_TYPES)}."
        )

    if metadata["type"] in EMAIL_TYPES:
        for field in ("to", "subject", "draft_body"):
            value = metadata.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ApprovalValidationError(
                    f"Email approval requires non-empty string `{field}`."
                )
        if "\r" in metadata["to"] or "\n" in metadata["to"]:
            raise ApprovalValidationError("Email `to` contains a newline.")
        if "\r" in metadata["subject"] or "\n" in metadata["subject"]:
            raise ApprovalValidationError("Email `subject` contains a newline.")
        _, address = parseaddr(metadata["to"])
        if not address or "@" not in address:
            raise ApprovalValidationError(
                f"Invalid recipient address: {metadata['to']!r}."
            )
        metadata["to"] = address

        expected_hash = metadata.get("draft_sha256")
        if expected_hash is not None:
            if not isinstance(expected_hash, str) or not re_full_sha256(
                expected_hash
            ):
                raise ApprovalValidationError(
                    "`draft_sha256` must be a 64-character hexadecimal string."
                )
            actual_hash = hashlib.sha256(
                metadata["draft_body"].encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(actual_hash, expected_hash.lower()):
                raise ApprovalValidationError(
                    "draft_body integrity check failed; approved text changed."
                )

        expected_html_hash = metadata.get("html_sha256")
        if expected_html_hash is not None and "html_body" in metadata:
            if not isinstance(expected_html_hash, str) or not re_full_sha256(
                expected_html_hash
            ):
                raise ApprovalValidationError(
                    "`html_sha256` must be a 64-character hexadecimal string."
                )
            actual_html_hash = hashlib.sha256(
                metadata["html_body"].encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(actual_html_hash, expected_html_hash.lower()):
                raise ApprovalValidationError(
                    "html_body integrity check failed; approved text changed."
                )
    return metadata


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


class GmailDirectSender:
    """Lazy, reusable Gmail API sender using ChiefMind's OAuth token."""

    def __init__(self) -> None:
        self._service: Resource | None = None
        self._from_address: str | None = None

    def _connect(self) -> tuple[Resource, str]:
        if self._service is None:
            credentials = load_gmail_credentials(interactive=False)
            self._service = build(
                "gmail",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )
            profile = (
                self._service.users()
                .getProfile(userId=GMAIL_USER_ID)
                .execute(num_retries=GMAIL_RETRIES)
            )
            self._from_address = str(profile["emailAddress"])
        return self._service, self._from_address or ""

    def send_exact(
        self,
        *,
        recipient: str,
        subject: str,
        draft_body: str,
        html_body: str | None = None,
        thread_id: str | None = None,
        message_id_header: str | None = None,
    ) -> dict[str, Any]:
        service, from_address = self._connect()
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = from_address
        message["Subject"] = subject
        if message_id_header:
            message["In-Reply-To"] = message_id_header
            message["References"] = message_id_header
        message.set_content(draft_body)
        
        html_payload = html_body if html_body else render_html_email(draft_body, subject=subject)
        message.add_alternative(html_payload, subtype="html")

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        send_body: dict[str, Any] = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id
        response = (
            service.users()
            .messages()
            .send(userId=GMAIL_USER_ID, body=send_body)
            .execute(num_retries=GMAIL_RETRIES)
        )
        return {
            "provider": "gmail",
            "message_id": response.get("id"),
            "thread_id": response.get("threadId"),
        }


def execute_linkedin(path: Path) -> dict[str, Any]:
    """Invoke Day 7's poster with the approved artifact path."""
    if not LINKEDIN_POSTER_FILE.is_file():
        raise ActionExecutionError(
            f"LinkedIn poster is not available yet: {LINKEDIN_POSTER_FILE}"
        )
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(LINKEDIN_POSTER_FILE),
                "--approval-file",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=LINKEDIN_EXECUTION_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActionExecutionError(f"LinkedIn execution failed: {exc}") from exc
    if result.returncode != 0:
        raise ActionExecutionError(
            f"LinkedIn poster exited with {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    try:
        provider_result = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        provider_result = {"provider": "linkedin", "result": "completed"}
    return provider_result


def route_file(path: Path, destination_dir: Path) -> Path:
    """Move an artifact without overwriting an existing audit record."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / path.name
    if destination.exists():
        destination = destination_dir / (
            f"{path.stem}_{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}_"
            f"{uuid.uuid4().hex[:8]}{path.suffix}"
        )
    return move_file(path, destination)


def wait_until_stable(path: Path) -> None:
    """Wait briefly for copy-based producers to finish writing a new file."""
    if APPROVAL_SETTLE_SECONDS <= 0:
        return
    previous: tuple[int, int] | None = None
    for _ in range(4):
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime_ns)
        if current == previous:
            return
        previous = current
        time.sleep(APPROVAL_SETTLE_SECONDS)


def _event(
    *,
    action_id: str | None,
    action_type: str | None,
    status: str,
    source_file: str,
    details: dict[str, Any] | None = None,
    error: str | None = None,
    traceback_text: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "timestamp": isoformat_utc(),
        "agent": "approval_watcher",
        "action_id": action_id,
        "type": action_type,
        "status": status,
        "source_file": source_file,
    }
    if details:
        event["details"] = details
    if error:
        event["error"] = error
    if traceback_text:
        event["traceback"] = traceback_text
    return event


def _execute_action(
    path: Path,
    metadata: dict[str, Any],
    gmail_sender: EmailSender,
) -> dict[str, Any]:
    action_type = metadata["type"]
    if action_type in EMAIL_TYPES:
        kwargs: dict[str, Any] = {
            "recipient": metadata["to"],
            "subject": metadata["subject"],
            "draft_body": metadata["draft_body"],
            "html_body": metadata.get("html_body"),
        }
        if metadata.get("thread_id"):
            kwargs["thread_id"] = metadata["thread_id"]
        if metadata.get("message_id_header"):
            kwargs["message_id_header"] = metadata["message_id_header"]
        try:
            return gmail_sender.send_exact(**kwargs)
        except TypeError:
            return gmail_sender.send_exact(
                recipient=metadata["to"],
                subject=metadata["subject"],
                draft_body=metadata["draft_body"],
                html_body=metadata.get("html_body"),
            )
    if action_type == "linkedin_post":
        return execute_linkedin(path)
    return {
        "provider": "local",
        "result": "acknowledged_without_external_action",
    }


def process_approval(
    path: Path,
    *,
    gmail_sender: EmailSender | None = None,
) -> str:
    """Validate, guard, execute, receipt, audit, and route one artifact."""
    if path.suffix.lower() != ".md" or path.name.startswith("."):
        return "ignored"

    with PROCESS_LOCK:
        sender = gmail_sender or GmailDirectSender()
        action_id: str | None = None
        action_type: str | None = None
        reservation_written = False
        try:
            wait_until_stable(path)
            metadata = validate_approval(path)
            action_id = metadata["action_id"]
            action_type = metadata["type"]

            receipts = load_receipts()
            if action_id in receipts:
                LOGGER.warning("Duplicate action_id %s; execution skipped.", action_id)
                append_audit_event(
                    _event(
                        action_id=action_id,
                        action_type=action_type,
                        status="duplicate_skipped",
                        source_file=path.name,
                        details={"existing_receipt": receipts[action_id]},
                    )
                )
                route_file(path, DONE_DIR)
                return "duplicate"

            enforce_rate_limits(receipts, action_type)

            append_audit_event(
                _event(
                    action_id=action_id,
                    action_type=action_type,
                    status="execution_started",
                    source_file=path.name,
                )
            )
            # Persist a reservation before external I/O. If the process crashes
            # during Gmail send, restart will fail closed instead of sending twice.
            receipts[action_id] = {
                "state": "executing",
                "type": action_type,
                "source_file": path.name,
                "reserved_at": isoformat_utc(),
            }
            save_receipts(receipts)
            reservation_written = True

            result = _execute_action(path, metadata, sender)
            completed_at = isoformat_utc()
            receipts[action_id] = {
                "state": "executed",
                "type": action_type,
                "source_file": path.name,
                "executed_at": completed_at,
                "result": result,
            }
            save_receipts(receipts)

            append_audit_event(
                _event(
                    action_id=action_id,
                    action_type=action_type,
                    status="executed",
                    source_file=path.name,
                    details=result,
                )
            )
            done_path = route_file(path, DONE_DIR)
            LOGGER.info("Executed %s and moved artifact to %s", action_id, done_path)
            return "executed"
        except Exception as exc:
            trace = traceback.format_exc()
            LOGGER.exception("Approval processing failed for %s", path)
            if reservation_written and action_id:
                try:
                    receipts = load_receipts()
                    receipt = receipts.get(action_id, {})
                    receipt.update(
                        {
                            "state": "failed",
                            "failed_at": isoformat_utc(),
                            "error": str(exc),
                        }
                    )
                    receipts[action_id] = receipt
                    save_receipts(receipts)
                except Exception:
                    LOGGER.exception("Could not update failed receipt for %s", action_id)
            try:
                append_audit_event(
                    _event(
                        action_id=action_id,
                        action_type=action_type,
                        status="failed",
                        source_file=path.name,
                        error=f"{type(exc).__name__}: {exc}",
                        traceback_text=trace,
                    )
                )
            except Exception:
                LOGGER.exception("Could not append failure audit event.")
            if path.exists():
                try:
                    route_file(path, FAILED_DIR)
                except OSError:
                    LOGGER.exception("Could not move failed artifact %s", path)
            return "failed"


class ApprovedEventHandler(FileSystemEventHandler):
    """Translate filesystem creation/move events into guarded executions."""

    def __init__(self, gmail_sender: EmailSender) -> None:
        super().__init__()
        self.gmail_sender = gmail_sender

    def _handle(self, raw_path: str, is_directory: bool) -> None:
        if is_directory:
            return
        path = Path(raw_path)
        if path.parent.resolve() != APPROVED_DIR.resolve():
            return
        process_approval(path, gmail_sender=self.gmail_sender)

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event.src_path, event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:
        destination = getattr(event, "dest_path", "")
        if destination:
            self._handle(destination, event.is_directory)


def process_existing(
    gmail_sender: EmailSender | None = None,
) -> dict[str, int]:
    """Process approvals that existed before the watcher started."""
    counts = {"executed": 0, "duplicate": 0, "failed": 0, "ignored": 0}
    sender = gmail_sender or GmailDirectSender()
    for path in sorted(APPROVED_DIR.glob("*.md")):
        result = process_approval(path, gmail_sender=sender)
        counts[result] = counts.get(result, 0) + 1
    return counts


def watch(stop_event: threading.Event | None = None) -> None:
    ensure_directories()
    initialize_execution_files()
    sender = GmailDirectSender()
    observer = Observer()
    observer.schedule(
        ApprovedEventHandler(sender),
        str(APPROVED_DIR),
        recursive=False,
    )
    observer.start()
    LOGGER.info("Watching %s for approved actions.", APPROVED_DIR)
    try:
        counts = process_existing(sender)
        LOGGER.info("Startup approval scan: %s", counts)
        while not (stop_event and stop_event.wait(1)):
            if stop_event is None:
                time.sleep(1)
        LOGGER.info("Approval watcher received a graceful shutdown request.")
    except KeyboardInterrupt:
        LOGGER.info("Approval watcher stopped by user.")
    finally:
        observer.stop()
        observer.join()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process current Approved/*.md files and exit.",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Create execution_receipts.json and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    ensure_directories()
    initialize_execution_files()
    if args.init:
        LOGGER.info("Execution receipts ready at %s", EXECUTION_RECEIPTS_FILE)
        return 0
    if args.once:
        LOGGER.info("Approval cycle completed: %s", process_existing())
        return 0
    watch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
