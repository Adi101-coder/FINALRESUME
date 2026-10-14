"""Applied-job deduplication helpers."""

from __future__ import annotations

import unittest

from app.agents.browser.linkedin import looks_like_internal_job_id
from app.services.applied_jobs import job_skip_keys, linkedin_job_id_from_url, normalize_apply_url


class TestAppliedJobs(unittest.TestCase):
    def test_linkedin_id_from_url(self) -> None:
        url = "https://www.linkedin.com/jobs/view/1234567890/?tracking=foo"
        self.assertEqual(linkedin_job_id_from_url(url), "1234567890")

    def test_normalize_strips_tracking(self) -> None:
        norm = normalize_apply_url("https://www.linkedin.com/jobs/view/99/?ref=1")
        self.assertEqual(norm, "https://www.linkedin.com/jobs/view/99/")

    def test_skip_keys_overlap(self) -> None:
        a = job_skip_keys(apply_url="https://www.linkedin.com/jobs/view/42/", linkedin_job_id="42")
        b = job_skip_keys(apply_url="https://www.linkedin.com/jobs/view/42/?x=1", linkedin_job_id="42")
        self.assertTrue(a & b)

    def test_internal_db_id_is_not_linkedin(self) -> None:
        self.assertTrue(looks_like_internal_job_id("https://www.linkedin.com/jobs/view/26/"))
        self.assertFalse(
            looks_like_internal_job_id("https://www.linkedin.com/jobs/view/4473056902/")
        )


if __name__ == "__main__":
    unittest.main()
