"""Lever-hosted applications (jobs.lever.co) — bypass broken LinkedIn ATS embed."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from playwright.sync_api import Page

from app.agents.browser.captcha import captcha_present, wait_for_human
from app.agents.browser.forms import click_first_matching, fill_standard_fields, upload_resume
APPLY_RESULT_SUBMITTED = "submitted"
APPLY_RESULT_REVIEW = "review_required"
APPLY_RESULT_FAILED = "failed"


@dataclass
class ApplyOutcome:
    status: str
    message: str
    platform: str
    url: str

logger = logging.getLogger("app.browser.lever")


def normalize_lever_apply_url(url: str) -> str:
    url = url.split("?")[0].rstrip("/")
    if not re.search(r"lever\.co/", url, re.I):
        return url
    if url.endswith("/apply"):
        return url
    return f"{url}/apply"


def apply_lever_job_page(page: Page, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
    apply_url = normalize_lever_apply_url(url)
    logger.info("Lever direct apply: %s", apply_url)
    page.goto(apply_url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(2500)

    if captcha_present(page):
        wait_for_human(page, "Lever: solve CAPTCHA in the browser, then press Login done in the app.")

    body = (page.inner_text("body") or "").lower()
    if "no longer accepting" in body or "posting not found" in body:
        return ApplyOutcome(APPLY_RESULT_FAILED, "Lever posting is closed or not found.", "lever", apply_url)

    for step in range(6):
        fill_standard_fields(page, profile)
        upload_resume(page, resume_path)

        if click_first_matching(
            page,
            [r"submit application", r"submit", r"apply", r"send application"],
        ):
            page.wait_for_timeout(2500)
            after = (page.inner_text("body") or "").lower()
            if any(p in after for p in ("thank you", "application received", "submitted", "we received")):
                return ApplyOutcome(
                    APPLY_RESULT_SUBMITTED,
                    "Lever application submitted (confirmation detected on page).",
                    "lever",
                    apply_url,
                )
            return ApplyOutcome(
                APPLY_RESULT_SUBMITTED,
                "Lever form submitted — verify confirmation in the browser.",
                "lever",
                apply_url,
            )

        try:
            for select in page.locator("select").all():
                if not select.is_visible():
                    continue
                if not (select.input_value() or "").strip():
                    if select.locator("option").count() > 1:
                        select.select_option(index=1)
        except Exception:
            pass

        if click_first_matching(page, [r"^next$", r"continue"]):
            page.wait_for_timeout(1200)
            continue

        if step >= 2:
            break
        page.wait_for_timeout(800)

    return ApplyOutcome(
        APPLY_RESULT_REVIEW,
        "Lever form needs manual answers — complete in the browser.",
        "lever",
        apply_url,
    )
