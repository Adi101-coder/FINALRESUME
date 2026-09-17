"""Platform-specific Playwright apply flows."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from playwright.sync_api import Page

from app.agents.browser.captcha import captcha_present, wait_for_human
from app.agents.browser.forms import (
    click_apply_entrypoint,
    click_first_matching,
    fill_greenhouse_fields,
    fill_standard_fields,
    upload_resume,
)
from app.agents.browser.linkedin import apply_linkedin_job_page

logger = logging.getLogger("app.browser.platforms")

APPLY_RESULT_SUBMITTED = "submitted"
APPLY_RESULT_REVIEW = "review_required"
APPLY_RESULT_UNSUPPORTED = "unsupported"
APPLY_RESULT_FAILED = "failed"


@dataclass
class ApplyOutcome:
    status: str
    message: str
    platform: str
    url: str


def _guard_captcha(page: Page, platform: str) -> None:
    if captcha_present(page):
        wait_for_human(page, f"{platform}: CAPTCHA or security check detected.")


def _wizard_continue(
    page: Page, profile: dict, resume_path: str, platform: str, *, steps: int = 5
) -> str:
    """Generic multi-step apply wizard used by several boards."""
    for step in range(steps):
        _guard_captcha(page, platform)
        fill_standard_fields(page, profile)
        upload_resume(page, resume_path)

        if click_first_matching(page, [r"submit application", r"submit", r"send application"]):
            page.wait_for_timeout(1500)
            return APPLY_RESULT_SUBMITTED

        if click_first_matching(page, [r"^next$", r"continue", r"review", r"save and continue"]):
            page.wait_for_timeout(1200)
            continue

        # No obvious button — may need human for custom questions
        wait_for_human(
            page,
            f"Step {step + 1}: answer any custom questions, then press ENTER.",
        )
        if click_first_matching(page, [r"submit", r"next", r"continue"]):
            page.wait_for_timeout(1200)

    return APPLY_RESULT_REVIEW


def apply_linkedin(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    return apply_linkedin_job_page(page, url, profile, resume_path)


def apply_indeed(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    from app.agents.browser.board_common import INDEED_CONFIG, apply_job_url

    logger.info("Indeed apply: %s", url)
    result = apply_job_url(page, url, {**profile, "_job_title": "Job"}, resume_path, INDEED_CONFIG)
    return ApplyOutcome(result["status"], result["message"], "indeed", url)


def apply_ziprecruiter(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    from app.agents.browser.board_common import ZIPRECRUITER_CONFIG, apply_job_url

    logger.info("ZipRecruiter apply: %s", url)
    result = apply_job_url(page, url, {**profile, "_job_title": "Job"}, resume_path, ZIPRECRUITER_CONFIG)
    return ApplyOutcome(result["status"], result["message"], "ziprecruiter", url)


def apply_glassdoor(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    from app.agents.browser.board_common import GLASSDOOR_CONFIG, apply_job_url

    logger.info("Glassdoor apply: %s", url)
    result = apply_job_url(page, url, {**profile, "_job_title": "Job"}, resume_path, GLASSDOOR_CONFIG)
    return ApplyOutcome(result["status"], result["message"], "glassdoor", url)


def apply_greenhouse(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    """Greenhouse applications — structured forms on boards.greenhouse.io."""
    logger.info("Greenhouse apply: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(2000)
    _guard_captcha(page, "greenhouse")

    # Job description page → click through to application form
    if "greenhouse.io" in page.url and "#app" not in page.url:
        click_apply_entrypoint(page)
        page.wait_for_timeout(1500)

    fill_greenhouse_fields(page, profile)
    if not upload_resume(page, resume_path):
        logger.warning("Greenhouse: resume upload field not found yet")

    # Custom questions (dropdowns, short text)
    for _ in range(4):
        _guard_captcha(page, "greenhouse")
        fill_greenhouse_fields(page, profile)

        if click_first_matching(
            page,
            [r"submit application", r"submit your application", r"^submit$"],
        ):
            page.wait_for_timeout(2000)
            return ApplyOutcome(
                APPLY_RESULT_SUBMITTED,
                "Greenhouse application submitted (verify confirmation in browser).",
                "greenhouse",
                url,
            )

        # Required select fields — pick first non-empty option if blank
        try:
            for select in page.locator("select").all():
                if not select.is_visible():
                    continue
                value = select.input_value()
                if not value:
                    options = select.locator("option")
                    if options.count() > 1:
                        select.select_option(index=1)
        except Exception:
            pass

        if click_first_matching(page, [r"^next$", r"continue"]):
            page.wait_for_timeout(1200)
            continue

        wait_for_human(
            page,
            "Greenhouse: answer any required custom questions, then press ENTER.",
        )

    return ApplyOutcome(
        APPLY_RESULT_REVIEW,
        "Greenhouse form incomplete — review browser and submit manually.",
        "greenhouse",
        url,
    )


def apply_naukri(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    logger.info("Naukri apply (generic wizard): %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(2000)
    _guard_captcha(page, "naukri")
    if click_apply_entrypoint(page):
        page.wait_for_timeout(1500)
    status = _wizard_continue(page, profile, resume_path, "naukri")
    return ApplyOutcome(status, f"Naukri apply finished with status={status}.", "naukri", url)


def apply_foundit(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    logger.info("Foundit apply (generic wizard): %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(2000)
    _guard_captcha(page, "foundit")
    if click_apply_entrypoint(page):
        page.wait_for_timeout(1500)
    status = _wizard_continue(page, profile, resume_path, "foundit")
    return ApplyOutcome(status, f"Foundit apply finished with status={status}.", "foundit", url)


def detect_platform(url: str) -> str | None:
    url_lower = url.lower()
    if "greenhouse.io" in url_lower:
        return "greenhouse"
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "indeed.com" in url_lower:
        return "indeed"
    if "naukri.com" in url_lower:
        return "naukri"
    if "foundit.in" in url_lower or "monsterindia" in url_lower:
        return "foundit"
    if "ziprecruiter.com" in url_lower:
        return "ziprecruiter"
    if "glassdoor.com" in url_lower:
        return "glassdoor"
    return None


def apply_for_platform(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    platform = detect_platform(url)
    if platform == "greenhouse":
        return apply_greenhouse(page, url, profile, resume_path)
    if platform == "linkedin":
        return apply_linkedin(page, url, profile, resume_path)
    if platform == "indeed":
        return apply_indeed(page, url, profile, resume_path)
    if platform == "naukri":
        return apply_naukri(page, url, profile, resume_path)
    if platform == "foundit":
        return apply_foundit(page, url, profile, resume_path)
    if platform == "ziprecruiter":
        return apply_ziprecruiter(page, url, profile, resume_path)
    if platform == "glassdoor":
        return apply_glassdoor(page, url, profile, resume_path)
    return ApplyOutcome(APPLY_RESULT_UNSUPPORTED, f"No handler for URL: {url}", platform or "unknown", url)
