"""Unit tests for auto-apply form helpers (no browser required)."""

from __future__ import annotations

import unittest

from app.agents.browser.forms import (
    _answer_aliases,
    _extract_dates_from_merged_text,
    _normalize_experience_entry,
    _option_matches,
    _parse_resume_date,
    _screening_answer_for_hint,
    _years_option_index,
    phone_for_apply,
)


class TestYearsOptionIndex(unittest.TestCase):
    def test_zero_not_ten(self) -> None:
        labels = ["0 years", "1 year", "10+ years"]
        self.assertEqual(_years_option_index(labels, 0), 0)
        self.assertEqual(_years_option_index(labels, 10), 2)

    def test_less_than_one(self) -> None:
        labels = ["Select", "Less than 1 year", "1-2 years"]
        self.assertEqual(_years_option_index(labels, 0), 1)


class TestOptionMatching(unittest.TestCase):
    def test_month_abbreviation(self) -> None:
        self.assertTrue(_option_matches("January", "Jan"))
        self.assertTrue(_option_matches("Jun 2024", "June"))

    def test_year_boundary(self) -> None:
        self.assertTrue(_option_matches("2024", "2024"))
        self.assertFalse(_option_matches("2024", "202"))


class TestParseResumeDate(unittest.TestCase):
    def test_present(self) -> None:
        month, year, current = _parse_resume_date("Jun 2024 – Present")
        self.assertEqual(year, "2024")
        self.assertTrue(current)
        self.assertEqual(month, "June")

    def test_merged_company_dates(self) -> None:
        company, start, _ = _extract_dates_from_merged_text("The Hera Cubra Group Jun. 2026 – Present")
        self.assertIn("Hera", company)
        self.assertIn("2026", start)


class TestExperienceNormalize(unittest.TestCase):
    def test_splits_company_and_dates(self) -> None:
        raw = {
            "role": "Software Engineering Intern Remote",
            "company": "The Hera Cubra Group Jun. 2026 – Present",
            "start_date": "",
        }
        norm = _normalize_experience_entry(raw)
        self.assertNotIn("2026", norm["company"])
        self.assertTrue(norm.get("start_date"))


class TestScreeningAnswers(unittest.TestCase):
    def test_sponsorship_not_on_resume(self) -> None:
        self.assertEqual(
            _screening_answer_for_hint("Will you require visa sponsorship?", {}),
            "",
        )

    def test_skill_years_from_resume(self) -> None:
        profile = {"skills": ["Python"], "experience": [], "education": []}
        ans = _screening_answer_for_hint("How many years of Python experience?", profile)
        self.assertEqual(ans, "0")


class TestPhoneDefault(unittest.TestCase):
    def test_resume_phone_only(self) -> None:
        self.assertEqual(phone_for_apply({}), "")
        self.assertEqual(phone_for_apply({"phone": "+1 555 0100"}), "+1 555 0100")


class TestAnswerAliases(unittest.TestCase):
    def test_eeo_aliases(self) -> None:
        aliases = _answer_aliases("Prefer not to answer")
        self.assertTrue(any("Decline" in a for a in aliases))


if __name__ == "__main__":
    unittest.main()
