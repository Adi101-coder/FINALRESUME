"""LinkedIn Easy Apply automation — search results page + modal wizard."""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote_plus
from pathlib import Path

from typing import Callable

from playwright.sync_api import Locator, Page

from app.agents.browser.captcha import captcha_present, wait_for_human
from app.agents.browser.forms import fill_custom_questions, fill_standard_fields, upload_resume

logger = logging.getLogger("app.browser.linkedin")

APPLY_RESULT_SUBMITTED = "submitted"
APPLY_RESULT_REVIEW = "review_required"
APPLY_RESULT_UNSUPPORTED = "unsupported"
APPLY_RESULT_FAILED = "failed"

MODAL_NEXT = (
    r"continue to next step",
    r"^next$",
    r"continue",
    r"review",
    r"review your application",
    r"save and continue",
    r"save & continue",
)
MODAL_SUBMIT = (r"submit application", r"submit", r"send application")
MODAL_DONE = (r"^done$", r"^close$", r"not now", r"no thanks", r"dismiss")

# Apply button selectors inside the job details panel (priority order)
APPLY_BUTTON_SELECTORS = (
    "div.jobs-apply-button--top-card button",
    "div.jobs-apply-button--top-card",
    "div.jobs-s-apply button",
    "div.jobs-s-apply",
    "button.jobs-apply-button",
    "#jobs-apply-button-id",
    ".jobs-unified-top-card button.jobs-apply-button",
    ".job-details-jobs-unified-top-card button.jobs-apply-button",
    'button[aria-label*="Easy Apply" i]',
    'button[aria-label*="Easy Apply to" i]',
    'button[aria-label*="Apply to" i]',
    'button[aria-label*="Apply for" i]',
    'a[href*="openSDUIApplyFlow=true"]',
)

DETAILS_PANEL_SELECTORS = (
    ".jobs-search__job-details",
    ".jobs-details",
    ".jobs-details__main-content",
    ".job-view-layout",
    ".scaffold-layout__detail",
    "main.scaffold-layout__main",
)


def _job_id_from_url(url: str) -> str | None:
    match = re.search(r"/jobs/view/(\d+)", url)
    return match.group(1) if match else None


def _job_id_from_card(card: Locator) -> str | None:
    try:
        job_id = card.get_attribute("data-job-id") or card.get_attribute("data-occludable-job-id")
        if job_id:
            return job_id
        urn = card.get_attribute("data-entity-urn") or ""
        match = re.search(r"jobPosting:(\d+)", urn)
        if match:
            return match.group(1)
        link = card.locator('a[href*="/jobs/view/"]').first
        if link.count() > 0:
            href = link.get_attribute("href") or ""
            return _job_id_from_url(href)
    except Exception:
        pass
    return None


def _job_details_panel(page: Page) -> Locator:
    for selector in DETAILS_PANEL_SELECTORS:
        loc = page.locator(selector).first
        try:
            if loc.count() > 0 and loc.is_visible():
                return loc
        except Exception:
            continue
    return page.locator("body")


def _debug_buttons(details: Locator) -> str:
    """List visible apply-related buttons for troubleshooting."""
    lines: list[str] = []
    try:
        for btn in details.locator("button, a[role='button'], a.jobs-apply-button").all()[:20]:
            if not btn.is_visible():
                continue
            label = btn.get_attribute("aria-label") or btn.inner_text() or ""
            label = " ".join(label.split())
            if label and re.search(r"apply|submit|continue", label, re.I):
                lines.append(label[:120])
    except Exception as exc:
        lines.append(f"(debug error: {exc})")
    return "; ".join(lines) if lines else "(no apply-like buttons visible in job panel)"


def _click_locator(loc: Locator, *, timeout_ms: int = 8000) -> bool:
    try:
        if loc.count() == 0:
            return False
        target = loc.first
        target.wait_for(state="visible", timeout=timeout_ms)
        target.scroll_into_view_if_needed(timeout=timeout_ms)
        try:
            target.click(timeout=timeout_ms)
            return True
        except Exception:
            target.click(timeout=timeout_ms, force=True)
            return True
    except Exception as exc:
        logger.debug("Click failed: %s", exc)
        return False


def _click_by_patterns(page: Page, patterns: tuple[str, ...], *, scope: Page | Locator | None = None) -> bool:
    root = scope or page
    for pattern in patterns:
        try:
            btn = root.get_by_role("button", name=re.compile(pattern, re.I))
            if _click_locator(btn):
                logger.info("Clicked button matching %r", pattern)
                return True
        except Exception:
            pass
        try:
            btn = root.locator(f'button[aria-label*="{pattern}" i]')
            if _click_locator(btn):
                logger.info("Clicked aria-label matching %r", pattern)
                return True
        except Exception:
            pass
    return False


def already_applied(page: Page) -> bool:
    """Detect jobs the user already applied to (scoped to details panel)."""
    details = _job_details_panel(page)
    hints = (
        details.get_by_role("button", name=re.compile(r"^applied$", re.I)),
        details.locator('button[aria-label*="Applied" i]'),
        details.locator(".jobs-apply-button--applied"),
    )
    for loc in hints:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def _js_click_easy_apply(details: Locator) -> bool:
    """DOM click fallback — works when Playwright thinks element is obscured."""
    try:
        handle = details.element_handle()
        if not handle:
            return False
        clicked = handle.evaluate(
            """(root) => {
                const selectors = [
                    'div.jobs-apply-button--top-card button',
                    'div.jobs-apply-button--top-card',
                    'div.jobs-s-apply button',
                    'div.jobs-s-apply',
                    'button.jobs-apply-button',
                    '#jobs-apply-button-id',
                    'button[aria-label*="Easy Apply"]',
                    'button[aria-label*="Apply to"]',
                    'button[aria-label*="Apply for"]',
                    'a[href*="openSDUIApplyFlow=true"]',
                ];
                for (const sel of selectors) {
                    const el = root.querySelector(sel);
                    if (!el) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 2 || rect.height < 2) continue;
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    if (typeof el.click === 'function') el.click();
                    return sel;
                }
                return null;
            }"""
        )
        if clicked:
            logger.info("Easy Apply clicked via JS: %s", clicked)
            return True
    except Exception as exc:
        logger.debug("JS click failed: %s", exc)
    return False


def _wait_for_apply_button(details: Locator, *, timeout_ms: int = 15000) -> Locator | None:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for selector in APPLY_BUTTON_SELECTORS:
            loc = details.locator(selector)
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first
            except Exception:
                continue
        details.page.wait_for_timeout(400)
    return None


def click_easy_apply(page: Page, *, debug: bool = False) -> bool:
    """Find and click Easy Apply in the job details panel."""
    if already_applied(page):
        logger.info("Job already applied — skipping")
        return False

    details = _job_details_panel(page)
    page.wait_for_timeout(800)

    apply_btn = _wait_for_apply_button(details, timeout_ms=15000)
    if apply_btn is None:
        if debug:
            print(f"  [debug] Buttons in panel: {_debug_buttons(details)}")
        apply_btn = _wait_for_apply_button(page.locator("body"), timeout_ms=3000)

    if apply_btn is not None:
        try:
            apply_btn.scroll_into_view_if_needed(timeout=8000)
            apply_btn.click(timeout=8000)
            page.wait_for_timeout(1500)
            if _modal_visible(page):
                logger.info("Easy Apply opened after wait")
                return True
        except Exception:
            try:
                apply_btn.click(force=True, timeout=8000)
                page.wait_for_timeout(1500)
                if _modal_visible(page):
                    return True
            except Exception:
                pass

    # Playwright role-based attempts inside details panel
    role_attempts = (
        details.locator("button").filter(has_text=re.compile(r"easy apply", re.I)),
        page.locator("button").filter(has_text=re.compile(r"easy apply", re.I)),
        details.get_by_role("button", name=re.compile(r"easy apply", re.I)),
        details.get_by_role("button", name=re.compile(r"^apply$", re.I)),
        details.get_by_role("button", name=re.compile(r"apply to", re.I)),
    )
    for loc in role_attempts:
        if _click_locator(loc):
            page.wait_for_timeout(1500)
            if _modal_visible(page):
                logger.info("Easy Apply opened via role locator")
                return True

    # CSS selector sweep in details panel
    for selector in APPLY_BUTTON_SELECTORS:
        loc = details.locator(selector)
        if _click_locator(loc):
            page.wait_for_timeout(1500)
            if _modal_visible(page):
                logger.info("Easy Apply clicked via %s", selector)
                return True

    # JavaScript click
    if _js_click_easy_apply(details):
        page.wait_for_timeout(1500)
        if _modal_visible(page):
            return True
    if _js_click_easy_apply(page.locator("body")):
        page.wait_for_timeout(1500)
        if _modal_visible(page):
            return True

    if debug:
        print(f"  [debug] Could not click Easy Apply. Visible: {_debug_buttons(details)}")
    return False


def _easy_apply_modal(page: Page) -> Locator:
    return page.locator(
        'div[role="dialog"], .jobs-easy-apply-modal, .artdeco-modal, div[data-test-modal-id]'
    ).first


def _modal_visible(page: Page) -> bool:
    try:
        modal = _easy_apply_modal(page)
        return modal.count() > 0 and modal.is_visible()
    except Exception:
        return False


def _dismiss_blocking_overlays(page: Page) -> None:
    _click_by_patterns(page, (r"^dismiss$", r"^close$", r"^not now$", r"^no thanks$"))


def complete_easy_apply_modal(page: Page, profile: dict, resume_path: str, *, max_steps: int = 12) -> str:
    """Walk through LinkedIn Easy Apply modal — Next → Review → Submit."""
    for _ in range(30):
        if _modal_visible(page):
            break
        page.wait_for_timeout(300)
    if not _modal_visible(page):
        return APPLY_RESULT_UNSUPPORTED

    modal = _easy_apply_modal(page)
    last_step_sig = ""
    same_step_count = 0

    for step in range(max_steps):
        if captcha_present(page):
            wait_for_human(page, "LinkedIn: solve CAPTCHA in the Easy Apply modal, then press ENTER.")

        fill_standard_fields(page, profile)
        fill_custom_questions(page, profile)
        upload_resume(page, resume_path)

        body = ""
        try:
            body = (modal.inner_text() or "").lower()
        except Exception:
            pass

        if any(
            phrase in body
            for phrase in ("application sent", "application submitted", "your application was sent")
        ):
            _click_by_patterns(page, MODAL_DONE, scope=modal)
            return APPLY_RESULT_SUBMITTED

        # LinkedIn often uses aria-label on modal buttons
        for aria in ("Submit application", "Submit", "Continue to next step", "Review your application"):
            btn = modal.locator(f'button[aria-label="{aria}" i]')
            if _click_locator(btn):
                page.wait_for_timeout(2000)
                break
        else:
            if _click_by_patterns(page, MODAL_SUBMIT, scope=modal):
                page.wait_for_timeout(2000)
            elif _click_by_patterns(page, MODAL_DONE, scope=modal):
                return APPLY_RESULT_SUBMITTED
            elif _click_by_patterns(page, MODAL_NEXT, scope=modal):
                page.wait_for_timeout(1200)
            else:
                step_sig = body[:200]
                if step_sig == last_step_sig:
                    same_step_count += 1
                    if same_step_count >= 2:
                        wait_for_human(
                            page,
                            f"LinkedIn step {step + 1}: answer required questions in the modal, then press ENTER.",
                        )
                        same_step_count = 0
                else:
                    same_step_count = 0
                last_step_sig = step_sig

        try:
            body = (modal.inner_text() or "").lower()
            if any(
                phrase in body
                for phrase in ("application sent", "application submitted", "your application was sent")
            ):
                _click_by_patterns(page, MODAL_DONE, scope=modal)
                return APPLY_RESULT_SUBMITTED
        except Exception:
            pass

        _dismiss_blocking_overlays(page)
        page.wait_for_timeout(800)

    return APPLY_RESULT_REVIEW


def close_easy_apply_modal(page: Page) -> None:
    modal = _easy_apply_modal(page)
    if modal.count() > 0 and modal.is_visible():
        _click_by_patterns(page, MODAL_DONE, scope=modal)
        _click_by_patterns(page, (r"^dismiss$", r"^close$", r"^cancel$"), scope=modal)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(600)


def _open_job_card(page: Page, card: Locator) -> bool:
    """Click job title link and wait for details panel + apply area to load."""
    link = card.locator(
        "a.job-card-list__title, a.base-card__full-link, a[href*='/jobs/view/'], a[href*='/jobs/collections/']"
    ).first
    try:
        if link.count() > 0:
            link.scroll_into_view_if_needed(timeout=5000)
            link.click(timeout=8000)
        else:
            card.scroll_into_view_if_needed(timeout=5000)
            card.click(timeout=8000)
    except Exception as exc:
        logger.warning("Job card click failed: %s", exc)
        return False

    # Wait for details panel
    for selector in DETAILS_PANEL_SELECTORS:
        try:
            page.locator(selector).first.wait_for(state="visible", timeout=10000)
            break
        except Exception:
            continue

    page.wait_for_timeout(1200)
    details = _job_details_panel(page)
    btn = _wait_for_apply_button(details, timeout_ms=12000)
    return btn is not None or details.locator("button, a").count() > 0


def _search_url(query: str, location: str, *, start: int = 0) -> str:
    keywords = quote_plus(query)
    loc = quote_plus(location or "Remote")
    url = f"https://www.linkedin.com/jobs/search/?keywords={keywords}&location={loc}&f_AL=true"
    if start > 0:
        url += f"&start={start}"
    return url


def _job_cards(page: Page) -> Locator:
    return page.locator(
        "li.scaffold-layout__list-item[data-occludable-job-id], "
        "li.jobs-search-results__list-item, "
        "div.job-card-container, "
        "ul.scaffold-layout__list-container > li"
    )


def card_shows_easy_apply(card: Locator) -> bool:
    try:
        text = (card.inner_text() or "").lower()
        if "easy apply" in text:
            return True
        if re.search(r"\bapplied\b", text):
            return False
    except Exception:
        pass
    return True  # f_AL filter is on — assume eligible unless marked applied


def apply_linkedin_from_search(
    page: Page,
    query: str,
    location: str,
    profile: dict,
    resume_path: str,
    *,
    limit: int = 10,
    applied_ids: set[str] | None = None,
    start_offset: int = 0,
    delay_sec: float = 4.0,
    debug: bool = False,
    stop_check: Callable[[], bool] | None = None,
    on_result: Callable[[dict], None] | None = None,
) -> list[dict]:
    applied_ids = applied_ids or set()
    results: list[dict] = []
    url = _search_url(query, location, start=start_offset)

    logger.info("LinkedIn search batch: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(3000)

    if "/login" in page.url or "authwall" in page.url:
        wait_for_human(page, "LinkedIn: log in in the browser, then press ENTER.")
        page.goto(url, wait_until="domcontentloaded", timeout=300_000)
        page.wait_for_timeout(2500)

    if captcha_present(page):
        wait_for_human(page, "LinkedIn: solve security check, then press ENTER.")

    cards = _job_cards(page)
    count = cards.count()
    logger.info("Found %d job cards on page (start=%d)", count, start_offset)

    if count == 0:
        print("  No job cards found — check login or try different search terms.")
        return results

    applied_this_batch = 0
    for idx in range(count):
        if stop_check and stop_check():
            print("  Stop requested — exiting batch.")
            break
        if applied_this_batch >= limit:
            break

        card = cards.nth(idx)
        job_id = _job_id_from_card(card)
        if job_id and job_id in applied_ids:
            continue

        if not card_shows_easy_apply(card):
            if job_id:
                applied_ids.add(job_id)
            continue

        title = query
        company = ""
        try:
            title = (
                card.locator(".job-card-list__title, a.job-card-list__title, strong").first.inner_text().strip()
                or query
            )
            company = card.locator(
                ".job-card-container__company-name, .artdeco-entity-lockup__subtitle"
            ).first.inner_text().strip()
        except Exception:
            pass

        print(f"  Opening: {title} @ {company or '?'}")

        if not _open_job_card(page, card):
            logger.warning("Details panel did not load for job %s", job_id)
            if debug:
                print(f"  [debug] Panel buttons: {_debug_buttons(_job_details_panel(page))}")
            continue

        if already_applied(page):
            if job_id:
                applied_ids.add(job_id)
            print(f"  → skipped (already applied)")
            continue

        if not click_easy_apply(page, debug=debug):
            msg = f"No Easy Apply clickable — {_debug_buttons(_job_details_panel(page))}"
            logger.info("No Easy Apply for: %s @ %s", title, company)
            if debug and job_id:
                debug_dir = Path("storage/browser_sessions/debug")
                debug_dir.mkdir(parents=True, exist_ok=True)
                shot = debug_dir / f"linkedin_{job_id}.png"
                try:
                    page.screenshot(path=str(shot), full_page=True)
                    print(f"  [debug] Screenshot saved: {shot}")
                except Exception:
                    pass
            if job_id:
                applied_ids.add(job_id)
            results.append(
                {
                    "title": title,
                    "company": company,
                    "url": page.url,
                    "job_id": job_id,
                    "platform": "linkedin",
                    "status": APPLY_RESULT_UNSUPPORTED,
                    "message": msg,
                }
            )
            if on_result:
                on_result(results[-1])
            print(f"  → unsupported: {msg[:100]}")
            continue

        try:
            status = complete_easy_apply_modal(page, profile, resume_path)
            close_easy_apply_modal(page)
            results.append(
                {
                    "title": title,
                    "company": company,
                    "url": page.url,
                    "job_id": job_id,
                    "platform": "linkedin",
                    "status": status,
                    "message": f"LinkedIn Easy Apply finished: {status}",
                }
            )
            if on_result:
                on_result(results[-1])
            print(f"  → {status}: {title} @ {company or 'LinkedIn'}")
            if status == APPLY_RESULT_SUBMITTED:
                applied_this_batch += 1
        except Exception as exc:
            logger.exception("Easy Apply failed for job %s", job_id)
            close_easy_apply_modal(page)
            results.append(
                {
                    "title": title,
                    "company": company,
                    "url": page.url,
                    "job_id": job_id,
                    "platform": "linkedin",
                    "status": APPLY_RESULT_FAILED,
                    "message": str(exc),
                }
            )
            if on_result:
                on_result(results[-1])
            print(f"  → failed: {title} — {exc}")

        if job_id:
            applied_ids.add(job_id)

        time.sleep(delay_sec)

    return results


def apply_linkedin_job_page(page: Page, url: str, profile: dict, resume_path: str, *, debug: bool = False):
    from app.agents.browser.platform_handlers import ApplyOutcome

    logger.info("LinkedIn job page apply: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(3500)

    if captcha_present(page):
        wait_for_human(page, "LinkedIn: CAPTCHA detected — solve it, then press ENTER.")

    if "/login" in page.url or "authwall" in page.url:
        wait_for_human(page, "LinkedIn: log in manually, then press ENTER.")
        page.goto(url, wait_until="domcontentloaded", timeout=300_000)
        page.wait_for_timeout(3000)

    if already_applied(page):
        return ApplyOutcome(APPLY_RESULT_UNSUPPORTED, "Already applied to this job.", "linkedin", url)

    if not click_easy_apply(page, debug=debug):
        details = _job_details_panel(page)
        return ApplyOutcome(
            APPLY_RESULT_UNSUPPORTED,
            f"No Easy Apply button found. {_debug_buttons(details)}",
            "linkedin",
            url,
        )

    status = complete_easy_apply_modal(page, profile, resume_path)
    close_easy_apply_modal(page)
    return ApplyOutcome(status, f"LinkedIn apply finished with status={status}", "linkedin", url)
