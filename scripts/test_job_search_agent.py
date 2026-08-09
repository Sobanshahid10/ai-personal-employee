"""Offline matching tests for the field-specific job agent."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from job_search_agent import Job, score_job  # noqa: E402


PROFILE = {
    "target_roles": ["AI Engineer", "Python Developer", "React Developer"],
    "skills": ["Python", "FastAPI", "React", "OpenCV"],
    "excluded_title_terms": ["senior", "sales", "manager"],
    "resume_path": "resume.pdf",
}


class JobSearchAgentTests(unittest.TestCase):
    def test_relevant_remote_junior_job_scores_high(self) -> None:
        job = Job(
            source="test",
            source_id="1",
            title="Junior AI Engineer",
            company="Example",
            location="Remote",
            remote=True,
            employment_type="full_time",
            url="https://example.test/job/1",
            description="Build Python FastAPI and OpenCV services.",
        )
        score, reasons = score_job(job, PROFILE)
        self.assertGreaterEqual(score, 45)
        self.assertIn("remote", reasons)

    def test_senior_or_unrelated_job_is_rejected(self) -> None:
        senior = Job(
            "test", "2", "Senior Sales Manager", "Example", "Remote", True,
            "full_time", "https://example.test/job/2", "Sales role",
        )
        self.assertEqual(score_job(senior, PROFILE)[0], 0)

    def test_foreign_onsite_job_is_rejected(self) -> None:
        onsite = Job(
            "test", "3", "Junior Python Developer", "Example", "Berlin", False,
            "full_time", "https://example.test/job/3", "Python role",
        )
        self.assertEqual(score_job(onsite, PROFILE)[0], 0)

    def test_skill_mentions_do_not_rescue_unrelated_title(self) -> None:
        unrelated = Job(
            "test", "4", "Remote Office Assistant", "Example", "Remote", True,
            "full_time", "https://example.test/job/4",
            "Uses Python, FastAPI, React, and OpenCV.",
        )
        self.assertEqual(score_job(unrelated, PROFILE)[0], 0)


if __name__ == "__main__":
    unittest.main()
