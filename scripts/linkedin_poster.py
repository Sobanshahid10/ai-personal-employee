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
    AUTO_LINKEDIN_POSTS,
    LINKEDIN_ACCESS_TOKEN,
    LINKEDIN_API_VERSION,
    LINKEDIN_AUTHOR_URN,
    LINKEDIN_MODE,
    LINKEDIN_EMAIL,
    LINKEDIN_HEADLESS,
    LINKEDIN_PASSWORD,
    LINKEDIN_REQUEST_TIMEOUT,
    LINKEDIN_STORAGE_STATE_FILE,
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


def post_to_linkedin_playwright(
    content: str,
    *,
    email: str = LINKEDIN_EMAIL,
    password: str = LINKEDIN_PASSWORD,
    headless: bool = LINKEDIN_HEADLESS,
    storage_state_file: Path = LINKEDIN_STORAGE_STATE_FILE,
    max_retries: int = 2,
) -> bool:
    """Publish through LinkedIn's UI as an explicitly enabled fallback.

    LinkedIn can change this UI without notice. Official API mode is preferred.
    Login challenges intentionally fail rather than attempting to bypass them.
    Retries on transient network errors (ERR_NETWORK_CHANGED etc.).
    """
    if not storage_state_file.is_file() and (not email or not password):
        raise LinkedInPosterError(
            "No LinkedIn browser session is configured. Run "
            "`python linkedin_poster.py --setup-browser-session` for a "
            "one-time interactive login; storing a password is not required."
        )
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise LinkedInPosterError(
            "Playwright is not installed. Run `uv sync` and install Chromium."
        ) from exc

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context_options: dict[str, Any] = {
                    "user_agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "viewport": {"width": 1280, "height": 800},
                }
                if storage_state_file.is_file():
                    context_options["storage_state"] = str(storage_state_file)
                context = browser.new_context(**context_options)
                page = context.new_page()

                # Navigate with a longer timeout and networkidle for reliability.
                try:
                    page.goto(
                        "https://www.linkedin.com/feed/",
                        wait_until="networkidle",
                        timeout=60_000,
                    )
                except Exception:
                    # Fallback to domcontentloaded if networkidle times out.
                    page.goto(
                        "https://www.linkedin.com/feed/",
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )

                # Handle login if session expired or not present.
                # Only check URL — the DOM check is unreliable because LinkedIn
                # keeps a hidden #username element on the feed page.
                needs_login = (
                    "/login" in page.url
                    or "/uas/login" in page.url
                    or "/authwall" in page.url
                )
                if needs_login:
                    if not email or not password:
                        raise LinkedInPosterError(
                            "The saved LinkedIn session expired. Run "
                            "`python linkedin_poster.py --setup-browser-session` again."
                        )
                    LOGGER.info("LinkedIn session expired; logging in with credentials.")
                    page.goto(
                        "https://www.linkedin.com/login",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    try:
                        page.locator("#username").wait_for(state="visible", timeout=10_000)
                        page.locator("#username").fill(email)
                        page.locator("#password").fill(password)
                        page.locator('button[type="submit"]').click()
                        page.wait_for_url("**/feed/**", timeout=60_000)
                    except Exception as login_exc:
                        # If the page already navigated to feed the login succeeded.
                        if "/feed/" not in page.url:
                            raise LinkedInPosterError(
                                f"LinkedIn login failed: {login_exc}"
                            ) from login_exc
                        LOGGER.info("LinkedIn login redirect detected; continuing.")
                    # Save refreshed session so future runs skip login.
                    storage_state_file.parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(storage_state_file))
                    try:
                        storage_state_file.chmod(0o600)
                    except OSError:
                        pass
                    LOGGER.info("LinkedIn session refreshed and saved.")

                if any(marker in page.url for marker in ("checkpoint", "challenge")):
                    raise LinkedInPosterError(
                        "LinkedIn requires a manual security check; browser posting stopped."
                    )

                # Wait for the feed to be fully interactive.
                page.wait_for_load_state("domcontentloaded")

                # Try both exact and partial match for the "Start a post" button.
                start = page.get_by_role("button", name="Start a post", exact=True)
                if not start.count():
                    start = page.get_by_role("button", name="Start a post")
                start.wait_for(state="visible", timeout=30_000)
                start.click()

                dialog = page.get_by_role("dialog")
                editor = dialog.locator('[contenteditable="true"][role="textbox"]')
                editor.wait_for(state="visible", timeout=20_000)
                editor.click()
                editor.fill(content)

                # Small pause so LinkedIn registers the text before clicking Post.
                page.wait_for_timeout(500)

                post_button = dialog.get_by_role("button", name="Post", exact=True)
                post_button.wait_for(state="visible", timeout=10_000)
                post_button.click()
                dialog.wait_for(state="hidden", timeout=30_000)
                browser.close()
                return True

        except LinkedInPosterError:
            raise
        except PlaywrightTimeoutError as exc:
            last_exc = exc
            LOGGER.warning(
                "LinkedIn browser UI timeout on attempt %d/%d: %s",
                attempt, max_retries, exc,
            )
        except Exception as exc:
            last_exc = exc
            LOGGER.warning(
                "LinkedIn browser posting failed on attempt %d/%d: %s",
                attempt, max_retries, exc,
            )

    LOGGER.error(
        "LinkedIn browser posting failed after %d attempts: %s", max_retries, last_exc
    )
    return False


def setup_browser_session(
    *,
    email: str = LINKEDIN_EMAIL,
    storage_state_file: Path = LINKEDIN_STORAGE_STATE_FILE,
    timeout_ms: int = 600_000,
) -> Path:
    """Open LinkedIn for a human login and save reusable local session state."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise LinkedInPosterError(
            "Playwright is not installed. Run `uv sync` and install Chromium."
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(
                "https://www.linkedin.com/login",
                wait_until="domcontentloaded",
            )
            if email and page.locator("#username").count():
                page.locator("#username").fill(email)

            print(
                "Complete LinkedIn sign-in in the opened Chromium window. "
                "ChiefMind will save the session after the feed loads."
            )
            page.wait_for_url("**/feed/**", timeout=timeout_ms)
            if any(marker in page.url for marker in ("checkpoint", "challenge")):
                raise LinkedInPosterError(
                    "Complete LinkedIn's security check before saving the session."
                )
            page.get_by_role("button", name="Start a post", exact=True).wait_for(
                state="visible",
                timeout=30_000,
            )
            storage_state_file.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(storage_state_file))
            try:
                storage_state_file.chmod(0o600)
            except OSError:
                LOGGER.warning(
                    "Could not restrict permissions on %s.", storage_state_file
                )
            browser.close()
    except PlaywrightTimeoutError as exc:
        raise LinkedInPosterError(
            "LinkedIn login did not complete before the setup timeout."
        ) from exc

    LOGGER.info("Saved LinkedIn browser session to %s", storage_state_file)
    return storage_state_file


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
        if not AUTO_LINKEDIN_POSTS:
            raise LinkedInPosterError(
                "Live posting is disabled; set AUTO_LINKEDIN_POSTS=true explicitly."
            )
        result = publish_live(content)
        status = "linkedin_posted"
    elif mode == "browser":
        if not AUTO_LINKEDIN_POSTS:
            raise LinkedInPosterError(
                "Browser posting is disabled; set AUTO_LINKEDIN_POSTS=true explicitly."
            )
        if not post_to_linkedin_playwright(content):
            raise LinkedInPosterError("LinkedIn browser posting did not complete.")
        result = {
            "provider": "linkedin",
            "mode": "browser",
            "status_code": None,
            "post_id": None,
            "content_length": len(content),
        }
        status = "linkedin_posted"
    else:
        raise LinkedInPosterError("Mode must be `mock`, `live`, or `browser`.")

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
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument(
        "--setup-browser-session",
        action="store_true",
        help="Open a one-time manual login and save a local Playwright session.",
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "live", "browser"),
        help="Override LINKEDIN_MODE for this invocation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.setup_browser_session:
        try:
            path = setup_browser_session()
            print(f"LinkedIn browser session ready: {path}")
            return 0
        except Exception as exc:
            LOGGER.exception("LinkedIn browser session setup failed.")
            print(str(exc), file=sys.stderr)
            return 1
    if args.approval_file is None:
        print(
            "--approval-file is required unless --setup-browser-session is used.",
            file=sys.stderr,
        )
        return 2
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
