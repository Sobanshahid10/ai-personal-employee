"""Publish one human-approved text post through LinkedIn's official Posts API.

Mock mode is the safe default. Live mode requires LINKEDIN_ACCESS_TOKEN and
LINKEDIN_AUTHOR_URN and must only be reached through the approval watcher.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import (
    LINKEDIN_ACCESS_TOKEN,
    LINKEDIN_API_VERSION,
    LINKEDIN_AUTHOR_URN,
    LINKEDIN_MODE,
    LINKEDIN_REQUEST_TIMEOUT,
    LOGS_DIR,
    setup_logging,
)
from workflow_utils import WorkflowFileError, append_json_array, load_frontmatter_file


LOGGER = setup_logging("chiefmind.linkedin")
POSTS_ENDPOINT = "https://api.linkedin.com/rest/posts"
MAX_TEXT_LENGTH = 3000


class LinkedInPosterError(RuntimeError):
    """Raised when an artifact is invalid or LinkedIn rejects a post."""


def utc_timestamp() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def daily_log_path(logs_dir: Path = LOGS_DIR) -> Path:
    return logs_dir / f"{datetime.now(tz=UTC).date().isoformat()}.json"


def audit(event: dict[str, Any], *, logs_dir: Path = LOGS_DIR) -> None:
    append_json_array(daily_log_path(logs_dir), event)


def read_approved_post(path: Path) -> tuple[dict[str, Any], str]:
    """Load and validate an immutable, human-approved LinkedIn artifact."""
    try:
        metadata, body = load_frontmatter_file(path)
    except (OSError, WorkflowFileError) as exc:
        raise LinkedInPosterError(f"Could not parse approval artifact: {exc}") from exc

    if metadata.get("type") != "linkedin_post":
        raise LinkedInPosterError("Approval artifact type must be `linkedin_post`.")
    action_id = metadata.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        raise LinkedInPosterError("Approval artifact requires a non-empty `action_id`.")

    # post_body is canonical; aliases keep older Day 4 artifacts compatible.
    candidates = (
        metadata.get("post_body"),
        metadata.get("content"),
        metadata.get("draft_body"),
        body,
    )
    content = next(
        (value for value in candidates if isinstance(value, str) and value.strip()),
        "",
    )
    if not content:
        raise LinkedInPosterError("LinkedIn approval requires non-empty `post_body`.")
    if len(content) > MAX_TEXT_LENGTH:
        raise LinkedInPosterError(
            f"LinkedIn post exceeds the {MAX_TEXT_LENGTH}-character safety limit."
        )
    return metadata, content


def linkedin_payload(*, author_urn: str, content: str) -> dict[str, Any]:
    return {
        "author": author_urn,
        "commentary": content,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def publish_live(
    content: str,
    *,
    access_token: str = LINKEDIN_ACCESS_TOKEN,
    author_urn: str = LINKEDIN_AUTHOR_URN,
    api_version: str = LINKEDIN_API_VERSION,
    timeout: int = LINKEDIN_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """Send the exact approved content; never rewrite or call an LLM."""
    if not access_token:
        raise LinkedInPosterError("LINKEDIN_ACCESS_TOKEN is missing.")
    if not author_urn.startswith(("urn:li:person:", "urn:li:organization:")):
        raise LinkedInPosterError("LINKEDIN_AUTHOR_URN is missing or invalid.")

    encoded = json.dumps(
        linkedin_payload(author_urn=author_urn, content=content)
    ).encode("utf-8")
    request = urllib.request.Request(
        POSTS_ENDPOINT,
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Linkedin-Version": api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return {
                "provider": "linkedin",
                "mode": "live",
                "status_code": response.status,
                "post_id": response.headers.get("x-restli-id"),
                "response": response_body or None,
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise LinkedInPosterError(
            f"LinkedIn API returned HTTP {exc.code}: {detail or exc.reason}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LinkedInPosterError(f"LinkedIn API request failed: {exc}") from exc


def post_approved_artifact(
    path: Path,
    *,
    mode: str = LINKEDIN_MODE,
    logs_dir: Path = LOGS_DIR,
) -> dict[str, Any]:
    metadata, content = read_approved_post(path)
    action_id = str(metadata["action_id"])
    if mode == "mock":
        result: dict[str, Any] = {
            "provider": "linkedin",
            "mode": "mock",
            "status_code": None,
            "post_id": f"mock:{action_id}",
            "content_length": len(content),
        }
        status = "linkedin_post_mocked"
    elif mode == "live":
        result = publish_live(content)
        status = "linkedin_posted"
    else:
        raise LinkedInPosterError("Mode must be `mock` or `live`.")

    audit(
        {
            "timestamp": utc_timestamp(),
            "agent": "linkedin_poster",
            "action_id": action_id,
            "type": "linkedin_post",
            "status": status,
            "source_file": path.name,
            "details": result,
        },
        logs_dir=logs_dir,
    )
    LOGGER.info("LinkedIn action %s completed in %s mode.", action_id, mode)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-file", required=True, type=Path)
    parser.add_argument(
        "--mode",
        choices=("mock", "live"),
        help="Override LINKEDIN_MODE for this invocation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mode = args.mode or LINKEDIN_MODE
    try:
        result = post_approved_artifact(args.approval_file, mode=mode)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        trace = traceback.format_exc()
        LOGGER.exception("LinkedIn posting failed for %s", args.approval_file)
        try:
            audit(
                {
                    "timestamp": utc_timestamp(),
                    "agent": "linkedin_poster",
                    "action_id": None,
                    "type": "linkedin_post",
                    "status": "failed",
                    "source_file": args.approval_file.name,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": trace,
                }
            )
        except Exception:
            LOGGER.exception("Could not append LinkedIn failure audit event.")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
