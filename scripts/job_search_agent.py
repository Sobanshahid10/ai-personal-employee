"""Discover field-relevant remote/Pakistan jobs and stage auditable leads."""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import (
    CANDIDATE_PROFILE_FILE,
    JOB_MAX_RESULTS,
    JOB_MIN_SCORE,
    JOB_SEARCH_INTERVAL,
    JOB_SEEN_IDS_FILE,
    NEEDS_ACTION_DIR,
    setup_logging,
)
from workflow_utils import atomic_write_json, write_frontmatter


LOGGER = setup_logging("chiefmind.jobs")
REMOTIVE_URL = "https://remotive.com/api/remote-jobs?limit=100"
ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Job:
    source: str
    source_id: str
    title: str
    company: str
    location: str
    remote: bool
    employment_type: str
    url: str
    description: str
    tags: tuple[str, ...] = ()


def utc_timestamp() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_text(value: Any) -> str:
    text = html.unescape(TAG_RE.sub(" ", str(value or "")))
    return SPACE_RE.sub(" ", text).strip()


def load_profile(path: Path = CANDIDATE_PROFILE_FILE) -> dict[str, Any]:
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load candidate profile {path}: {exc}") from exc
    required = ("target_roles", "skills", "excluded_title_terms", "resume_path")
    missing = [key for key in required if not profile.get(key)]
    if missing:
        raise RuntimeError(f"Candidate profile is missing: {', '.join(missing)}")
    return profile


def fetch_json(url: str, *, timeout: int = 30) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ChiefMind-Job-Agent/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_remotive() -> list[Job]:
    payload = fetch_json(REMOTIVE_URL)
    jobs: list[Job] = []
    for item in payload.get("jobs", []):
        jobs.append(
            Job(
                source="remotive",
                source_id=str(item.get("id", "")),
                title=clean_text(item.get("title")),
                company=clean_text(item.get("company_name")),
                location=clean_text(item.get("candidate_required_location") or "Remote"),
                remote=True,
                employment_type=clean_text(item.get("job_type")),
                url=str(item.get("url", "")).strip(),
                description=clean_text(item.get("description")),
                tags=tuple(clean_text(tag) for tag in item.get("tags", []) if tag),
            )
        )
    return jobs


def fetch_arbeitnow() -> list[Job]:
    payload = fetch_json(ARBEITNOW_URL)
    jobs: list[Job] = []
    for item in payload.get("data", []):
        job_types = item.get("job_types") or []
        jobs.append(
            Job(
                source="arbeitnow",
                source_id=str(item.get("slug", "")),
                title=clean_text(item.get("title")),
                company=clean_text(item.get("company_name")),
                location=clean_text(item.get("location")),
                remote=bool(item.get("remote")),
                employment_type=clean_text(", ".join(job_types)),
                url=str(item.get("url", "")).strip(),
                description=clean_text(item.get("description")),
                tags=tuple(clean_text(tag) for tag in item.get("tags", []) if tag),
            )
        )
    return jobs


def score_job(job: Job, profile: dict[str, Any]) -> tuple[int, list[str]]:
    title = job.title.lower()
    haystack = " ".join((job.title, job.description, " ".join(job.tags))).lower()
    excluded = [str(term).lower() for term in profile["excluded_title_terms"]]
    if any(term in title for term in excluded):
        return 0, ["rejected title"]
    if not job.remote and "pakistan" not in job.location.lower():
        return 0, ["not remote or Pakistan-based"]

    score = 0
    reasons: list[str] = []
    role_tokens = {
        token.lower()
        for role in profile["target_roles"]
        for token in re.findall(r"[a-zA-Z+#.]+", str(role))
        if len(token) >= 3
    }
    title_hits = sorted(token for token in role_tokens if token in title)
    if not title_hits:
        return 0, ["title does not match a target technical role"]
    score += min(35, 12 + 6 * len(title_hits))
    reasons.append(f"target title: {', '.join(title_hits[:4])}")

    skill_hits = sorted(
        str(skill) for skill in profile["skills"] if str(skill).lower() in haystack
    )
    if skill_hits:
        score += min(40, 5 * len(skill_hits))
        reasons.append(f"skills: {', '.join(skill_hits[:7])}")

    if job.remote:
        score += 12
        reasons.append("remote")
    junior_markers = ("junior", "entry", "intern", "graduate", "associate")
    if any(marker in haystack for marker in junior_markers):
        score += 12
        reasons.append("junior/entry signal")
    if any(term in job.employment_type.lower() for term in ("full", "intern", "contract")):
        score += 5

    return min(score, 100), reasons


def stable_id(job: Job) -> str:
    identity = f"{job.source}:{job.source_id or job.url}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def load_seen(path: Path = JOB_SEEN_IDS_FILE) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read job ledger {path}: {exc}") from exc
    return {str(item) for item in payload if isinstance(item, str)}


def stage_job(job: Job, score: int, reasons: list[str]) -> Path:
    job_id = stable_id(job)
    destination = NEEDS_ACTION_DIR / f"job_{job_id}.md"
    metadata = {
        "id": f"job_{job_id}",
        "action_id": f"job_{job_id}",
        "type": "job_opportunity",
        "status": "needs_action",
        "source": job.source,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "remote": job.remote,
        "employment_type": job.employment_type or "unspecified",
        "match_score": score,
        "apply_url": job.url,
        "discovered_at": utc_timestamp(),
    }
    description = job.description[:4000]
    body = (
        f"# {job.title}\n\n"
        f"**Company:** {job.company}\n\n"
        f"**Match reasons:** {'; '.join(reasons)}\n\n"
        f"**Application:** {job.url}\n\n"
        f"## Description\n\n{description}\n"
    )
    write_frontmatter(destination, metadata, body)
    return destination


def discover_once() -> int:
    profile = load_profile()
    seen = load_seen()
    collected: list[Job] = []
    for source in (fetch_remotive, fetch_arbeitnow):
        try:
            collected.extend(source())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            LOGGER.exception("Job source %s failed; continuing.", source.__name__)

    ranked: list[tuple[int, Job, list[str]]] = []
    for job in collected:
        job_id = stable_id(job)
        if job_id in seen or not job.url or not job.title:
            continue
        score, reasons = score_job(job, profile)
        if score >= JOB_MIN_SCORE:
            ranked.append((score, job, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1].title.lower()))

    staged = 0
    for score, job, reasons in ranked[:JOB_MAX_RESULTS]:
        job_id = stable_id(job)
        try:
            destination = stage_job(job, score, reasons)
            seen.add(job_id)
            atomic_write_json(JOB_SEEN_IDS_FILE, sorted(seen))
            staged += 1
            LOGGER.info("Staged job score=%s at %s", score, destination)
        except Exception:
            LOGGER.exception("Could not stage job %s", job.url)
    LOGGER.info("Job discovery staged %s new matching role(s).", staged)
    return staged


def run_job_search(stop_event: threading.Event | None = None, *, once: bool = False) -> int:
    while not (stop_event and stop_event.is_set()):
        try:
            discover_once()
        except Exception:
            LOGGER.exception("Job discovery cycle failed.")
            if once:
                return 1
        if once:
            return 0
        if stop_event:
            stop_event.wait(JOB_SEARCH_INTERVAL)
        else:
            time.sleep(JOB_SEARCH_INTERVAL)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_job_search(once=True))
