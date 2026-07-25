"""Central configuration for ChiefMind.

Every ChiefMind module must import paths and settings from this file.  Keeping
configuration here makes scripts portable, testable, and free of hardcoded
machine-specific paths.
"""

from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable

from dotenv import load_dotenv


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = Path(os.getenv("CHIEFMIND_ENV_FILE", SCRIPT_DIR / ".env")).expanduser()
load_dotenv(ENV_FILE)


def _path(variable: str, default: Path) -> Path:
    """Return an absolute, expanded path from an environment variable."""
    value = os.getenv(variable)
    path = Path(value).expanduser() if value else default
    return path.resolve()


def _int(variable: str, default: int, *, minimum: int = 0) -> int:
    """Read a bounded integer setting and fail early on invalid configuration."""
    raw_value = os.getenv(variable, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{variable} must be an integer, got {raw_value!r}") from exc
    if value < minimum:
        raise ValueError(f"{variable} must be at least {minimum}, got {value}")
    return value


def _bool(variable: str, default: bool = False) -> bool:
    """Read a conventional boolean environment variable."""
    raw_value = os.getenv(variable, str(default)).strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{variable} must be true or false, got {raw_value!r}")


# Base directories
PROJECT_ROOT = _path("CHIEFMIND_ROOT", SCRIPT_DIR.parent)
SCRIPTS_DIR = _path("SCRIPTS_DIR", PROJECT_ROOT / "scripts")
DASHBOARD_DIR = _path("DASHBOARD_DIR", PROJECT_ROOT / "dashboard")
DASHBOARD_STATIC_DIR = _path(
    "DASHBOARD_STATIC_DIR", DASHBOARD_DIR / "static"
)
DASHBOARD_TEMPLATES_DIR = _path(
    "DASHBOARD_TEMPLATES_DIR", DASHBOARD_DIR / "templates"
)
MCP_SERVERS_DIR = _path("MCP_SERVERS_DIR", PROJECT_ROOT / "mcp-servers")
GMAIL_MCP_SERVER_DIR = _path(
    "GMAIL_MCP_SERVER_DIR", MCP_SERVERS_DIR / "gmail-send"
)
DOCS_DIR = _path("DOCS_DIR", PROJECT_ROOT / "docs")
LAUNCHD_DIR = _path("LAUNCHD_DIR", PROJECT_ROOT / "launchd")
CREDENTIALS_DIR = _path("CREDENTIALS_DIR", PROJECT_ROOT / "credentials")

# File-system workflow state
INBOX_DIR = _path("INBOX_DIR", PROJECT_ROOT / "Inbox")
NEEDS_ACTION_DIR = _path("NEEDS_ACTION_DIR", PROJECT_ROOT / "Needs_Action")
PLANS_DIR = _path("PLANS_DIR", PROJECT_ROOT / "Plans")
PENDING_APPROVAL_DIR = _path(
    "PENDING_APPROVAL_DIR", PROJECT_ROOT / "Pending_Approval"
)
APPROVED_DIR = _path("APPROVED_DIR", PROJECT_ROOT / "Approved")
REJECTED_DIR = _path("REJECTED_DIR", PROJECT_ROOT / "Rejected")
DONE_DIR = _path("DONE_DIR", PROJECT_ROOT / "Done")
FAILED_DIR = _path("FAILED_DIR", PROJECT_ROOT / "Failed")
LOGS_DIR = _path("LOGS_DIR", PROJECT_ROOT / "Logs")

WORKFLOW_DIRS = (
    INBOX_DIR,
    NEEDS_ACTION_DIR,
    PLANS_DIR,
    PENDING_APPROVAL_DIR,
    APPROVED_DIR,
    REJECTED_DIR,
    DONE_DIR,
    FAILED_DIR,
    LOGS_DIR,
)

# Files
GOOGLE_CREDENTIALS_FILE = _path(
    "GOOGLE_CREDENTIALS_FILE", CREDENTIALS_DIR / "credentials.json"
)
GOOGLE_TOKEN_FILE = _path(
    "GOOGLE_TOKEN_FILE", CREDENTIALS_DIR / "token.json"
)
PROCESSED_IDS_FILE = _path(
    "PROCESSED_IDS_FILE", CREDENTIALS_DIR / "processed_ids.json"
)
REASONING_LOOP_FILE = _path(
    "REASONING_LOOP_FILE", SCRIPTS_DIR / "reasoning_loop.py"
)
KNOWLEDGE_BASE_FILE = _path(
    "KNOWLEDGE_BASE_FILE", DOCS_DIR / "KnowledgeBase.md"
)
LOG_FILE = _path("LOG_FILE", LOGS_DIR / "agent.log")

# External services and runtime behavior
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GMAIL_QUERY = os.getenv("GMAIL_QUERY", "is:unread").strip()
GMAIL_USER_ID = os.getenv("GMAIL_USER_ID", "me").strip()
GMAIL_SCOPES = tuple(
    scope.strip()
    for scope in os.getenv(
        "GMAIL_SCOPES",
        "https://www.googleapis.com/auth/gmail.modify",
    ).split(",")
    if scope.strip()
)
GMAIL_POLL_INTERVAL = _int("GMAIL_POLL_INTERVAL", 120, minimum=1)
GMAIL_RETRIES = _int("GMAIL_RETRIES", 3, minimum=0)
GMAIL_RETRY_DELAY = _int("GMAIL_RETRY_DELAY", 5, minimum=0)
GMAIL_MAX_RESULTS = _int("GMAIL_MAX_RESULTS", 100, minimum=1)
REASONING_TRIGGER_TIMEOUT = _int(
    "REASONING_TRIGGER_TIMEOUT", 300, minimum=1
)

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip()
DASHBOARD_PORT = _int("DASHBOARD_PORT", 5000, minimum=1)
DASHBOARD_DEBUG = _bool("DASHBOARD_DEBUG", False)
DASHBOARD_APPROVAL_TOKEN = os.getenv(
    "DASHBOARD_APPROVAL_TOKEN", ""
).strip()

AUTO_LINKEDIN_POSTS = _bool("AUTO_LINKEDIN_POSTS", False)
LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "").strip()
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "").strip()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
LOG_MAX_BYTES = _int("LOG_MAX_BYTES", 5_000_000, minimum=1)
LOG_BACKUP_COUNT = _int("LOG_BACKUP_COUNT", 5, minimum=0)


class ConfigError(RuntimeError):
    """Raised when required ChiefMind configuration is unavailable."""


def ensure_directories() -> None:
    """Create all configured application and workflow directories."""
    directories = (
        SCRIPTS_DIR,
        DASHBOARD_STATIC_DIR,
        DASHBOARD_TEMPLATES_DIR,
        GMAIL_MCP_SERVER_DIR,
        DOCS_DIR,
        LAUNCHD_DIR,
        CREDENTIALS_DIR,
        *WORKFLOW_DIRS,
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def validate_config(*, strict: bool = False) -> list[str]:
    """Validate secrets and credentials.

    Returns human-readable issues so dashboards and setup tools can display
    them.  With ``strict=True``, raises ``ConfigError`` when any issue exists.
    OAuth's generated token is intentionally not required on Day 1.
    """
    issues: list[str] = []

    if not GOOGLE_CREDENTIALS_FILE.is_file():
        issues.append(
            f"Missing Gmail OAuth client file: {GOOGLE_CREDENTIALS_FILE}"
        )
    if not GROQ_API_KEY:
        issues.append("GROQ_API_KEY is not set.")
    if not DASHBOARD_APPROVAL_TOKEN:
        issues.append("DASHBOARD_APPROVAL_TOKEN is not set.")
    if AUTO_LINKEDIN_POSTS:
        if not LINKEDIN_EMAIL:
            issues.append(
                "LINKEDIN_EMAIL is required when AUTO_LINKEDIN_POSTS=true."
            )
        if not LINKEDIN_PASSWORD:
            issues.append(
                "LINKEDIN_PASSWORD is required when AUTO_LINKEDIN_POSTS=true."
            )

    if strict and issues:
        formatted = "\n".join(f"- {issue}" for issue in issues)
        raise ConfigError(f"ChiefMind configuration is incomplete:\n{formatted}")
    return issues


def setup_logging(name: str = "chiefmind") -> logging.Logger:
    """Configure a named logger for console and rotating-file output."""
    ensure_directories()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, LOG_LEVEL, None)
    if not isinstance(level, int):
        raise ValueError(f"Unknown LOG_LEVEL: {LOG_LEVEL!r}")

    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def load_processed_ids() -> set[str]:
    """Load processed external IDs; return an empty set on first run."""
    if not PROCESSED_IDS_FILE.exists():
        return set()
    try:
        payload = json.loads(PROCESSED_IDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"Could not read {PROCESSED_IDS_FILE}: {exc}"
        ) from exc
    if not isinstance(payload, list) or not all(
        isinstance(item, str) for item in payload
    ):
        raise ConfigError(
            f"{PROCESSED_IDS_FILE} must contain a JSON list of strings."
        )
    return set(payload)


def save_processed_ids(processed_ids: Iterable[str]) -> None:
    """Atomically save processed IDs so an interrupted write cannot corrupt them."""
    ensure_directories()
    unique_ids = sorted({str(item) for item in processed_ids})
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=PROCESSED_IDS_FILE.parent,
            prefix=f".{PROCESSED_IDS_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(unique_ids, temporary_file, indent=2)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        temporary_path.replace(PROCESSED_IDS_FILE)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ConfigError(
            f"Could not write {PROCESSED_IDS_FILE}: {exc}"
        ) from exc


# Folder creation is safe and idempotent, so every importing process starts with
# a complete state machine.
ensure_directories()


if __name__ == "__main__":
    logger = setup_logging("chiefmind.config")
    issues = validate_config(strict=False)
    logger.info("Configuration loaded from %s", ENV_FILE)
    logger.info("ChiefMind root: %s", PROJECT_ROOT)
    if issues:
        for issue in issues:
            logger.warning(issue)
        logger.warning(
            "Configuration imports correctly, but private setup is incomplete."
        )
    else:
        logger.info("Configuration validation passed.")
