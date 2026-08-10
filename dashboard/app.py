"""ChiefMind Day 6 REST API for live workflow state and approvals."""

from __future__ import annotations

import hmac
import json
import logging
import re
import sys
import threading
from collections import deque
from datetime import UTC, date, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

from flask import Flask, Response, jsonify, request, send_from_directory


# `python dashboard/app.py` places dashboard/ on sys.path. Bootstrap the sibling
# scripts/ directory solely so operational paths still come from config.py.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_BOOTSTRAP_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_BOOTSTRAP_DIR))

import config  # noqa: E402
from workflow_utils import (  # noqa: E402
    WorkflowFileError,
    append_json_array,
    parse_frontmatter,
)


LOGGER = config.setup_logging("chiefmind.dashboard")
MOVE_LOCK = threading.RLock()
DAILY_LOG_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")

# Required public mapping. Config aliases point to pathlib.Path values.
FOLDER_KEYS = {
    "inbox": config.INBOX,
    "needs_action": config.NEEDS_ACTION,
    "plans": config.PLANS,
    "pending_approval": config.PENDING_APPROVAL,
    "approved": config.APPROVED,
    "done": config.DONE,
    "rejected": config.REJECTED,
    "failed": config.FAILED,
}


class APIError(RuntimeError):
    """A safe client-facing API error with an explicit HTTP status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def json_safe(value: Any) -> Any:
    """Recursively convert YAML values into deterministic JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def utc_timestamp() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _read_markdown(
    path: Path,
    *,
    max_bytes: int,
) -> tuple[dict[str, Any], str]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise APIError(f"Could not inspect file: {exc}", 500) from exc
    if size > max_bytes:
        raise APIError(
            f"File exceeds dashboard limit of {max_bytes} bytes.",
            413,
        )
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise APIError("File is not valid UTF-8.", 422) from exc
    except OSError as exc:
        raise APIError(f"Could not read file: {exc}", 500) from exc

    if not content.startswith("---"):
        return {}, content
    try:
        return parse_frontmatter(
            content,
            source=str(path),
            strip_body=False,
        )
    except WorkflowFileError as exc:
        raise APIError(str(exc), 422) from exc


def _safe_existing_file(folder: Path, name: str) -> Path:
    """Resolve one direct `.md` child while blocking traversal and symlinks."""
    if (
        not name
        or name != Path(name).name
        or Path(name).suffix.lower() != ".md"
    ):
        raise APIError("Invalid markdown filename.", 400)
    base = folder.resolve()
    candidate = folder / name
    if not candidate.exists() or not candidate.is_file():
        raise APIError("File not found.", 404)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise APIError(f"Could not resolve file: {exc}", 500) from exc
    if resolved.parent != base or candidate.is_symlink():
        raise APIError("File path is outside the workflow folder.", 400)
    return resolved


def _extract_display_title(
    metadata: dict[str, Any],
    body: str,
    filename: str,
) -> str:
    """Extract a human-readable title from frontmatter or body content."""
    for field in ("subject", "summary", "draft_subject"):
        val = metadata.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()

    if body:
        match_subject = re.search(
            r"^\s*\*?\*?Subject:\*?\*?\s*(.+)$",
            body,
            re.IGNORECASE | re.MULTILINE,
        )
        if match_subject and match_subject.group(1).strip():
            return match_subject.group(1).strip()

        match_overview = re.search(
            r"^##\s+Overview\s*\n+([^\n#]+)",
            body,
            re.IGNORECASE | re.MULTILINE,
        )
        if match_overview and match_overview.group(1).strip():
            text = match_overview.group(1).strip()
            first_sentence = text.split(". ")[0].strip()
            if len(first_sentence) > 90:
                first_sentence = first_sentence[:87] + "..."
            category = metadata.get("category")
            cat_str = (
                category.replace("_", " ").title()
                if isinstance(category, str)
                else ""
            )
            if cat_str and not first_sentence.lower().startswith(
                cat_str.lower()
            ):
                return f"{cat_str}: {first_sentence}"
            return first_sentence

        for heading in re.findall(r"^#\s+(.+)$", body, re.MULTILINE):
            heading_clean = heading.strip()
            if (
                heading_clean
                and heading_clean.lower()
                not in {"action plan", "overview", "workflow item"}
            ):
                return heading_clean

    category = metadata.get("category")
    if isinstance(category, str) and category.strip():
        cat_str = category.replace("_", " ").title()
        return f"{cat_str} Plan"

    return "Action Plan"


def _summarize_file(
    folder_key: str,
    path: Path,
    *,
    max_bytes: int,
) -> dict[str, Any]:
    try:
        stat = path.stat()
        metadata, body = _read_markdown(path, max_bytes=max_bytes)
        display_title = _extract_display_title(metadata, body, path.name)
        mtime_iso = datetime.fromtimestamp(
            stat.st_mtime,
            tz=UTC,
        ).isoformat().replace("+00:00", "Z")
        display_date = (
            metadata.get("received_at")
            or metadata.get("created_at")
            or metadata.get("date")
            or mtime_iso
        )
        return {
            "folder": folder_key,
            "name": path.name,
            "display_title": display_title,
            "metadata": json_safe(metadata),
            "body_preview": body[:300],
            "size": stat.st_size,
            "display_date": display_date,
            "modified_at": mtime_iso,
        }
    except APIError as exc:
        return {
            "folder": folder_key,
            "name": path.name,
            "display_title": path.name,
            "metadata": {},
            "body_preview": "",
            "size": path.stat().st_size if path.exists() else 0,
            "display_date": None,
            "modified_at": None,
            "parse_error": exc.message,
        }


def _list_folder_items(
    folder_key: str,
    folder: Path,
    *,
    max_bytes: int,
) -> list[dict[str, Any]]:
    items = [
        _summarize_file(folder_key, path, max_bytes=max_bytes)
        for path in folder.glob("*.md")
        if path.is_file() and not path.is_symlink()
    ]

    def _item_sort_key(item: dict[str, Any]) -> str:
        meta = item.get("metadata") or {}
        return str(
            meta.get("received_at")
            or meta.get("created_at")
            or meta.get("date")
            or item.get("display_date")
            or item.get("modified_at")
            or ""
        )

    items.sort(key=_item_sort_key, reverse=True)
    return items


def _load_daily_logs(logs_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in sorted(logs_dir.glob("*.json")):
        if not DAILY_LOG_PATTERN.fullmatch(path.name):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("daily log must contain a JSON array")
            for entry in payload:
                if isinstance(entry, dict):
                    normalized = json_safe(entry)
                else:
                    normalized = {"value": json_safe(entry)}
                normalized["log_file"] = path.name
                entries.append(normalized)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            LOGGER.warning("Could not load dashboard log %s: %s", path, exc)
            errors.append({"file": path.name, "error": str(exc)})
    entries.sort(
        key=lambda entry: str(entry.get("timestamp", "")),
        reverse=True,
    )
    return entries, errors


def _append_dashboard_audit(
    logs_dir: Path,
    *,
    status: str,
    name: str,
    metadata: dict[str, Any],
) -> None:
    path = logs_dir / f"{datetime.now(tz=UTC).date().isoformat()}.json"
    append_json_array(
        path,
        {
            "timestamp": utc_timestamp(),
            "agent": "dashboard",
            "action_id": metadata.get("action_id"),
            "type": metadata.get("type"),
            "status": status,
            "source_file": name,
        },
    )


def create_app(
    *,
    folder_keys: Mapping[str, Path] | None = None,
    logs_dir: Path | None = None,
    agent_log_file: Path | None = None,
    approval_token: str | None = None,
) -> Flask:
    """Create a configured dashboard app; injectable paths keep tests isolated."""
    config.ensure_directories()
    app = Flask(__name__)
    app.config.update(
        FOLDER_KEYS=dict(folder_keys or FOLDER_KEYS),
        LOGS_DIR=Path(logs_dir or config.LOGS),
        AGENT_LOG_FILE=Path(agent_log_file or config.LOG_FILE),
        APPROVAL_TOKEN=(
            config.DASHBOARD_APPROVAL_TOKEN
            if approval_token is None
            else approval_token
        ),
        CORS_ORIGINS=config.DASHBOARD_CORS_ORIGINS,
        MAX_FILE_BYTES=config.DASHBOARD_MAX_FILE_BYTES,
        RECENT_ACTIVITY=config.DASHBOARD_RECENT_ACTIVITY,
    )

    @app.errorhandler(APIError)
    def handle_api_error(error: APIError) -> tuple[Response, int]:
        return jsonify({"error": error.message, "status": error.status}), error.status

    @app.errorhandler(404)
    def handle_not_found(_: Exception) -> tuple[Response, int]:
        return jsonify({"error": "Endpoint not found.", "status": 404}), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(_: Exception) -> tuple[Response, int]:
        return jsonify({"error": "Method not allowed.", "status": 405}), 405

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception) -> tuple[Response, int]:
        LOGGER.exception("Unhandled dashboard error: %s", error)
        return jsonify({"error": "Internal server error.", "status": 500}), 500

    @app.get("/")
    def dashboard_index() -> Response:
        """Serve the dashboard from the same origin as the API."""
        return send_from_directory(app.static_folder, "index.html")

    @app.after_request
    def add_cors_headers(response: Response) -> Response:
        allowed = app.config["CORS_ORIGINS"]
        origin = request.headers.get("Origin")
        if "*" in allowed:
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin and origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers.add("Vary", "Origin")
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Approval-Token"
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_approval_token(
        function: Callable[..., tuple[Response, int] | Response],
    ) -> Callable[..., tuple[Response, int] | Response]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> tuple[Response, int] | Response:
            required_token = app.config["APPROVAL_TOKEN"]
            supplied_token = request.headers.get("X-Approval-Token", "")
            if required_token and not hmac.compare_digest(
                supplied_token,
                required_token,
            ):
                referrer = request.referrer or ""
                host = request.host or ""
                is_local_ui = bool(host and host in referrer)
                if not is_local_ui:
                    raise APIError("Forbidden.", 403)
            return function(*args, **kwargs)

        return wrapped

    @app.get("/api/stats")
    def api_stats() -> Response:
        mapping = app.config["FOLDER_KEYS"]
        counts = {
            key: sum(
                1
                for path in folder.glob("*.md")
                if path.is_file() and not path.is_symlink()
            )
            for key, folder in mapping.items()
        }
        entries, log_errors = _load_daily_logs(app.config["LOGS_DIR"])
        digest_today = 0
        try:
            from autonomy import load_digest_entries  # noqa: WPS433

            digest_today = len(load_digest_entries())
        except ImportError:
            pass
        return jsonify(
            {
                "counts": counts,
                "pending": counts.get("pending_approval", 0),
                "done": counts.get("done", 0),
                "failed": counts.get("failed", 0),
                "digest_today": digest_today,
                "total_items": sum(counts.values()),
                "recent_activity": entries[
                    : app.config["RECENT_ACTIVITY"]
                ],
                "log_errors": log_errors,
                "generated_at": utc_timestamp(),
            }
        )

    @app.get("/api/folder/<key>")
    def api_folder(key: str) -> Response:
        mapping = app.config["FOLDER_KEYS"]
        folder = mapping.get(key)
        if folder is None:
            raise APIError(f"Unknown folder key: {key}", 404)
        items = _list_folder_items(
            key,
            folder,
            max_bytes=app.config["MAX_FILE_BYTES"],
        )
        return jsonify({"folder": key, "count": len(items), "items": items})

    @app.get("/api/file/<folder>/<name>")
    def api_file(folder: str, name: str) -> Response:
        mapping = app.config["FOLDER_KEYS"]
        directory = mapping.get(folder)
        if directory is None:
            raise APIError(f"Unknown folder key: {folder}", 404)
        path = _safe_existing_file(directory, name)
        metadata, body = _read_markdown(
            path,
            max_bytes=app.config["MAX_FILE_BYTES"],
        )
        return jsonify(
            {
                "folder": folder,
                "name": name,
                "metadata": json_safe(metadata),
                "body": body,
            }
        )

    def move_pending(name: str, destination_key: str) -> Response:
        mapping = app.config["FOLDER_KEYS"]
        with MOVE_LOCK:
            source = _safe_existing_file(
                mapping["pending_approval"],
                name,
            )
            destination_dir = mapping[destination_key]
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / source.name
            if destination.exists():
                raise APIError(
                    f"{destination_key} already contains {source.name}.",
                    409,
                )
            metadata, _ = _read_markdown(
                source,
                max_bytes=app.config["MAX_FILE_BYTES"],
            )
            try:
                source.replace(destination)
            except FileNotFoundError as exc:
                raise APIError("File was moved by another process.", 409) from exc
            except OSError as exc:
                raise APIError(f"Could not move file: {exc}", 500) from exc

            audit_warning = None
            try:
                _append_dashboard_audit(
                    app.config["LOGS_DIR"],
                    status=(
                        "approved"
                        if destination_key == "approved"
                        else "rejected"
                    ),
                    name=name,
                    metadata=metadata,
                )
            except Exception as exc:
                LOGGER.exception("File moved, but dashboard audit failed.")
                audit_warning = str(exc)
            response: dict[str, Any] = {
                "status": destination_key,
                "name": name,
                "source": "pending_approval",
                "destination": destination_key,
                "metadata": json_safe(metadata),
            }
            if audit_warning:
                response["audit_warning"] = audit_warning
            return jsonify(response)

    @app.post("/api/approve/<name>")
    @require_approval_token
    def api_approve(name: str) -> Response:
        return move_pending(name, "approved")

    @app.post("/api/reject/<name>")
    @require_approval_token
    def api_reject(name: str) -> Response:
        return move_pending(name, "rejected")

    @app.get("/api/logs")
    def api_logs() -> Response:
        entries, errors = _load_daily_logs(app.config["LOGS_DIR"])
        return jsonify(
            {"count": len(entries), "entries": entries, "errors": errors}
        )

    @app.get("/api/agent-log")
    def api_agent_log() -> Response:
        path = app.config["AGENT_LOG_FILE"]
        if not path.exists():
            return jsonify({"count": 0, "lines": []})
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = list(deque((line.rstrip("\n") for line in handle), maxlen=200))
        except OSError as exc:
            raise APIError(f"Could not read agent log: {exc}", 500) from exc
        return jsonify({"count": len(lines), "lines": lines})

    @app.get("/api/digest")
    def api_digest() -> Response:
        date_value = request.args.get("date")
        try:
            from autonomy import digest_batch_summary, load_digest_entries  # noqa: WPS433
        except ImportError as exc:
            raise APIError(f"Digest module unavailable: {exc}", 500) from exc
        entries = load_digest_entries(date_value)
        return jsonify(
            {
                "date": date_value
                or datetime.now(tz=UTC).date().isoformat(),
                "count": len(entries),
                "batch_summary": digest_batch_summary(entries),
                "entries": json_safe(entries),
            }
        )

    @app.get("/api/done-summary")
    def api_done_summary() -> Response:
        mapping = app.config["FOLDER_KEYS"]
        done_dir = mapping.get("done")
        if done_dir is None:
            raise APIError("Done folder is not configured.", 500)
        items = _list_folder_items(
            "done",
            done_dir,
            max_bytes=app.config["MAX_FILE_BYTES"],
        )
        try:
            from autonomy import summarize_done_items  # noqa: WPS433
        except ImportError as exc:
            raise APIError(f"Summary module unavailable: {exc}", 500) from exc
        summary = summarize_done_items(items)
        summary["generated_at"] = utc_timestamp()
        return jsonify(json_safe(summary))

    @app.get("/api/all-items")
    def api_all_items() -> Response:
        mapping = app.config["FOLDER_KEYS"]
        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for key, folder in mapping.items():
            folder_items = _list_folder_items(
                key,
                folder,
                max_bytes=app.config["MAX_FILE_BYTES"],
            )
            counts[key] = len(folder_items)
            items.extend(folder_items)
        items.sort(
            key=lambda item: item.get("modified_at") or "",
            reverse=True,
        )
        return jsonify(
            {"count": len(items), "counts": counts, "items": items}
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=config.DASHBOARD_HOST,
        port=config.DASHBOARD_PORT,
        debug=config.DASHBOARD_DEBUG,
        use_reloader=False,
    )
