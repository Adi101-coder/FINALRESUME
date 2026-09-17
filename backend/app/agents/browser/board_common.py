"""Shared helpers for Indeed / ZipRecruiter / Glassdoor apply flows."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote_plus

from playwright.sync_api import Locator, Page

from app.agents.browser.captcha import captcha_present, wait_for_human
from app.agents.browser.forms import fill_custom_questions, fill_standard_fields, upload_resume

logger = logging.getLogger("app.browser.board_common")

APPLY_RESULT_SUBMITTED = "submitted"
APPLY_RESULT_REVIEW = "review_required"
APPLY_RESULT_UNSUPPORTED = "unsupported"
APPLY_RESULT_FAILED = "failed"

MODAL_NEXT = (r"continue", r"^next$", r"review", r"save and continue")
MODAL_SUBMIT = (r"submit application", r"submit your application", r"^submit$", r"send application")
MODAL_DONE = (r"^done$", r"^close$", r"not now")


@dataclass
class BoardConfig:
    platform: str
    search_url: Callable[[str, str, int], str]
    card_selector: str
    title_selector: str
    company_selector: str
    apply_selectors: tuple[str, ...]
    apply_role_patterns: tuple[str, ...] = ("apply", "quick apply", "easy apply")


def _click_locator(loc: Locator, *, timeout_ms: int = 8000) -> bool:
    try:
        if loc.count() == 0:
            return False
        target = loc.first
        target.wait_for(state="visible", timeout=timeout_ms)
        target.scroll_into_view_if_needed(timeout=timeout_ms)
        try:
            target.click(timeout=timeout_ms)
        except Exception:
            target.click(force=True, timeout=timeout_ms)
        return True
    except Exception:
        return False


def _click_patterns(scope: Locator | Page, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        try:
            btn = scope.get_by_role("button", name=re.compile(pattern, re.I))
            if _click_locator(btn):
                return True
        except Exception:
            pass
    return False


def click_apply_button(page: Page, config: BoardConfig) -> bool:
    root = page.locator("main, body").first
    for selector in config.apply_selectors:
        if _click_locator(root.locator(selector)):
            page.wait_for_timeout(1200)
            return True
    for pattern in config.apply_role_patterns:
        try:
            btn = page.get_by_role("button", name=re.compile(pattern, re.I))
            if _click_locator(btn):
                page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    # JS fallback
    try:
        clicked = page.evaluate(
            """(selectors) => {
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) { el.click(); return sel; }
                }
                const buttons = [...document.querySelectorAll('button')];
                for (const b of buttons) {
                    const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                    if (t.includes('apply') && !t.includes('company website')) { b.click(); return t; }
                }
                return null;
            }""",
            list(config.apply_selectors),
        )
        if clicked:
            page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    return False


def complete_apply_wizard(page: Page, profile: dict, resume_path: str, platform: str, *, max_steps: int = 10) -> str:
    modal = page.locator('div[role="dialog"], .ia-Modal, .artdeco-modal, .modal').first
    for step in range(max_steps):
        if captcha_present(page):
            wait_for_human(page, f"{platform}: solve CAPTCHA, then press ENTER.")
        fill_standard_fields(page, profile)
        fill_custom_questions(page, profile)
        upload_resume(page, resume_path)

        scope = modal if modal.count() > 0 and modal.is_visible() else page
        if _click_patterns(scope, MODAL_SUBMIT):
            page.wait_for_timeout(2000)
            return APPLY_RESULT_SUBMITTED
        if _click_patterns(scope, MODAL_NEXT):
            page.wait_for_timeout(1200)
            continue
        if _click_patterns(scope, MODAL_DONE):
            return APPLY_RESULT_SUBMITTED

        try:
            body = (scope.inner_text() or "").lower()
            if any(p in body for p in ("application submitted", "application sent", "thank you for applying")):
                return APPLY_RESULT_SUBMITTED
        except Exception:
            pass
        page.wait_for_timeout(800)

    return APPLY_RESULT_REVIEW


def apply_job_url(page: Page, url: str, profile: dict, resume_path: str, config: BoardConfig) -> dict:
    title = profile.get("_job_title", "Job")
    company = profile.get("_job_company", "")
    page.goto(url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(2500)

    if captcha_present(page):
        wait_for_human(page, f"{config.platform}: security check — solve then press ENTER.")
    if "/login" in page.url or "auth" in page.url.lower():
        wait_for_human(page, f"{config.platform}: log in in browser, then press ENTER.")
        page.goto(url, wait_until="domcontentloaded", timeout=300_000)
        page.wait_for_timeout(2000)

    if not click_apply_button(page, config):
        return {
            "title": title,
            "company": company,
            "url": url,
            "platform": config.platform,
            "status": APPLY_RESULT_UNSUPPORTED,
            "message": "No apply button found on job page.",
        }

    if len(page.context.pages) > 1:
        page = page.context.pages[-1]

    status = complete_apply_wizard(page, profile, resume_path, config.platform)
    return {
        "title": title,
        "company": company,
        "url": page.url,
        "platform": config.platform,
        "status": status,
        "message": f"{config.platform} apply finished: {status}",
    }


def apply_from_search(
    page: Page,
    config: BoardConfig,
    query: str,
    location: str,
    profile: dict,
    resume_path: str,
    *,
    limit: int = 5,
    applied_urls: set[str] | None = None,
    start_offset: int = 0,
    delay_sec: float = 4.0,
    stop_check: Callable[[], bool] | None = None,
    on_result: Callable[[dict], None] | None = None,
    debug: bool = False,
) -> list[dict]:
    applied_urls = applied_urls or set()
    results: list[dict] = []
    search_url = config.search_url(query, location, start_offset)
    page.goto(search_url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(3000)

    cards = page.locator(config.card_selector)
    count = cards.count()
    logger.info("%s search: %d cards", config.platform, count)

    applied = 0
    for idx in range(count):
        if stop_check and stop_check():
            break
        if applied >= limit:
            break

        card = cards.nth(idx)
        title = query
        company = ""
        try:
            title = card.locator(config.title_selector).first.inner_text().strip() or query
            company = card.locator(config.company_selector).first.inner_text().strip()
        except Exception:
            pass

        link = card.locator("a[href]").first
        try:
            href = link.get_attribute("href") or ""
            if href.startswith("/"):
                origin = page.url.split("/")[0] + "//" + page.url.split("/")[2]
                href = origin + href
            if not href or href in applied_urls:
                continue
        except Exception:
            continue

        print(f"  [{config.platform}] Opening: {title} @ {company or '?'}")
        job_profile = {**profile, "_job_title": title, "_job_company": company}
        try:
            result = apply_job_url(page, href, job_profile, resume_path, config)
        except Exception as exc:
            result = {
                "title": title,
                "company": company,
                "url": href,
                "platform": config.platform,
                "status": APPLY_RESULT_FAILED,
                "message": str(exc),
            }

        applied_urls.add(href)
        results.append(result)
        if on_result:
            on_result(result)
        print(f"  → {result.get('status')}: {title}")
        if result.get("status") == APPLY_RESULT_SUBMITTED:
            applied += 1
        time.sleep(delay_sec)

    return results


INDEED_CONFIG = BoardConfig(
    platform="indeed",
    search_url=lambda q, loc, start: (
        f"https://www.indeed.com/jobs?q={quote_plus(q)}&l={quote_plus(loc or 'remote')}"
        + (f"&start={start}" if start else "")
    ),
    card_selector="div.job_seen_beacon, div.cardOutline, td.resultContent",
    title_selector="h2.jobTitle span, a.jcs-JobTitle, h2 a",
    company_selector="span[data-testid='company-name'], .companyName",
    apply_selectors=(
        "#indeedApplyButton",
        "button.indeed-apply-button",
        "[data-testid='indeedApplyButton-test']",
        "button[aria-label*='Apply now' i]",
    ),
    apply_role_patterns=("apply now", "apply on indeed", "^apply$"),
)

ZIPRECRUITER_CONFIG = BoardConfig(
    platform="ziprecruiter",
    search_url=lambda q, loc, start: (
        f"https://www.ziprecruiter.com/jobs-search?search={quote_plus(q)}&location={quote_plus(loc or 'remote')}"
        f"&page={max(1, start // 20 + 1)}"
    ),
    card_selector="article.job_result, div.job_content",
    title_selector="a.job_title, h2 a",
    company_selector="a.t_org_link, span.company_name",
    apply_selectors=(
        "button.zr-btn-quick-apply",
        "a.zr-btn-quick-apply",
        "button[aria-label*='Quick Apply' i]",
    ),
    apply_role_patterns=("quick apply", "^apply$"),
)

GLASSDOOR_CONFIG = BoardConfig(
    platform="glassdoor",
    search_url=lambda q, loc, start: (
        f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={quote_plus(q)}&locT=C&locKeyword={quote_plus(loc or 'remote')}"
        + (f"&p={start // 30 + 1}" if start else "")
    ),
    card_selector="li.react-job-listing, div.jobCard",
    title_selector="a.jobLink, a[data-test='job-link']",
    company_selector="div.jobHeader div.employerName, span.employerName",
    apply_selectors=(
        "button[data-test='applyButton']",
        "button.easyApplyButton",
        "button[aria-label*='Easy Apply' i]",
    ),
    apply_role_patterns=("easy apply", "^apply$"),
)
