"""One-time Gmail OAuth setup for ChiefMind."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import (
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_TOKEN_FILE,
    GMAIL_SCOPES,
    ensure_directories,
    setup_logging,
)


LOGGER = setup_logging("chiefmind.gmail.auth")


class GmailAuthenticationError(RuntimeError):
    """Raised when Gmail credentials are missing, invalid, or expired."""


def _save_token(credentials: Credentials) -> None:
    """Write the OAuth token atomically with owner-only permissions."""
    ensure_directories()
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=GOOGLE_TOKEN_FILE.parent,
            prefix=f".{GOOGLE_TOKEN_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(json.loads(credentials.to_json()), temporary_file, indent=2)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(GOOGLE_TOKEN_FILE)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise GmailAuthenticationError(
            f"Could not save Gmail token to {GOOGLE_TOKEN_FILE}: {exc}"
        ) from exc


def load_gmail_credentials(*, interactive: bool = False) -> Credentials:
    """Load, refresh, or interactively create Gmail OAuth credentials."""
    credentials: Credentials | None = None

    if GOOGLE_TOKEN_FILE.is_file():
        try:
            credentials = Credentials.from_authorized_user_file(
                str(GOOGLE_TOKEN_FILE), list(GMAIL_SCOPES)
            )
        except (ValueError, OSError) as exc:
            raise GmailAuthenticationError(
                f"Invalid Gmail token file {GOOGLE_TOKEN_FILE}: {exc}. "
                "Delete it and run authenticate_gmail.py again."
            ) from exc

    if credentials and not credentials.has_scopes(GMAIL_SCOPES):
        raise GmailAuthenticationError(
            "The saved Gmail token does not grant all configured GMAIL_SCOPES. "
            "Delete token.json and run authenticate_gmail.py again."
        )

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            raise GmailAuthenticationError(
                "Gmail token refresh failed. Delete token.json and run "
                "authenticate_gmail.py again."
            ) from exc
        _save_token(credentials)
        LOGGER.info("Refreshed Gmail OAuth token.")
        return credentials

    if not interactive:
        raise GmailAuthenticationError(
            f"No usable Gmail token found at {GOOGLE_TOKEN_FILE}. "
            "Run `uv run python authenticate_gmail.py` first."
        )

    if not GOOGLE_CREDENTIALS_FILE.is_file():
        raise GmailAuthenticationError(
            f"Missing OAuth client file: {GOOGLE_CREDENTIALS_FILE}. "
            "Download a Desktop app OAuth client from Google Cloud Console "
            "and place it at that path."
        )

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(GOOGLE_CREDENTIALS_FILE), list(GMAIL_SCOPES)
        )
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            authorization_prompt_message=(
                "Open this URL in your browser to authorize ChiefMind:\n{url}"
            ),
            success_message=(
                "ChiefMind Gmail authorization succeeded. "
                "You may close this browser window."
            ),
            open_browser=True,
        )
    except (OSError, ValueError) as exc:
        raise GmailAuthenticationError(
            f"Could not start Gmail OAuth flow: {exc}"
        ) from exc

    _save_token(credentials)
    LOGGER.info("Saved Gmail OAuth token to %s", GOOGLE_TOKEN_FILE)
    return credentials


def main() -> int:
    """Run the one-time interactive authorization flow."""
    try:
        credentials = load_gmail_credentials(interactive=True)
    except GmailAuthenticationError as exc:
        LOGGER.error("%s", exc)
        return 1

    LOGGER.info(
        "Gmail authentication is ready for account %s.",
        getattr(credentials, "account", None) or "authorized user",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
