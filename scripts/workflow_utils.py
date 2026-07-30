"""Safe file primitives shared by ChiefMind workflow agents."""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterator, TextIO

import yaml

try:
    import fcntl
except ImportError:  # pragma: no cover - fallback for non-POSIX systems
    fcntl = None  # type: ignore[assignment]


FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*"
    r"(?:\r?\n|$)(?P<body>.*)\Z",
    re.DOTALL,
)
JSON_APPEND_LOCK = threading.RLock()


class WorkflowFileError(ValueError):
    """Raised when a workflow artifact is malformed or unsafe."""


def parse_frontmatter(
    text: str,
    *,
    source: str = "<memory>",
    strip_body: bool = True,
) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter with SafeLoader and return metadata plus body."""
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        raise WorkflowFileError(
            f"{source} must begin with YAML frontmatter delimited by `---`."
        )
    try:
        metadata = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise WorkflowFileError(f"Invalid YAML in {source}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise WorkflowFileError(
            f"Frontmatter in {source} must be a YAML mapping."
        )
    body = match.group("body")
    return metadata, body.strip() if strip_body else body


def load_frontmatter_file(path: Path) -> tuple[dict[str, Any], str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowFileError(f"Could not read {path}: {exc}") from exc
    return parse_frontmatter(content, source=str(path))


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace a UTF-8 text file."""
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


def atomic_write_json(path: Path, payload: Any) -> None:
    """Atomically write human-readable JSON."""
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
        + "\n",
    )


def load_json_object(path: Path) -> dict[str, Any]:
    """Load a JSON object, returning an empty object when absent."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowFileError(f"Could not load JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowFileError(f"{path} must contain a JSON object.")
    return payload


def load_json_array(path: Path) -> list[Any]:
    """Load a JSON array, returning an empty list when absent."""
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowFileError(f"Could not load JSON from {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise WorkflowFileError(f"{path} must contain a JSON array.")
    return payload


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[TextIO]:
    """Lock a sidecar file so separate ChiefMind services can append safely."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield lock_file
    finally:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def append_json_array(path: Path, item: Any) -> None:
    """Atomically append to a JSON array across threads and POSIX processes."""
    lock_path = path.with_name(f".{path.name}.lock")
    with JSON_APPEND_LOCK:
        with _exclusive_lock(lock_path):
            payload = load_json_array(path)
            payload.append(item)
            atomic_write_json(path, payload)
