"""LinkedIn Easy Apply automation — search results page + modal wizard."""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote_plus
from pathlib import Path

from typing import Callable

from playwright.sync_api import Locator, Page

from app.agents.browser.captcha import (
    BotAborted,
    SkipJob,
    captcha_present,
    go_back_pending,
    go_back_signaled,
    human_signaled,
    pause_for_manual_fill,
    raise_if_stopped,
    wait_for_human,
)
from app.config import settings
from app.agents.browser.forms import (
    easy_apply_section_complete,
    fill_from_resume_only,
    is_resume_document_step,
    needs_user_input_for_resume_gaps,
    repair_invalid_fields,
    upload_resume,
    wait_for_dropdown_review,
    wait_for_user_if_resume_gaps,
)

logger = logging.getLogger("app.browser.linkedin")

APPLY_RESULT_SUBMITTED = "submitted"
APPLY_RESULT_REVIEW = "review_required"
APPLY_RESULT_UNSUPPORTED = "unsupported"
APPLY_RESULT_FAILED = "failed"
APPLY_RESULT_SKIPPED = "skipped"

MODAL_NEXT = (
    r"continue to next step",
    r"^next$",
    r"^continue$",
    r"^review$",
    r"review your application",
    r"save and continue",
    r"save & continue",
)
MODAL_SUBMIT = (r"submit application", r"submit", r"send application")
MODAL_DONE = (r"^done$", r"^close$", r"not now", r"no thanks", r"dismiss")
MODAL_BACK = (r"^back$", r"go back", r"previous")


def _goto_with_retry(page: Page, url: str, *, timeout: int = 300_000, attempts: int = 3) -> None:
    """Navigate with retries — LinkedIn intermittently aborts navigations (ERR_ABORTED)."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return
        except Exception as exc:
            last = exc
            msg = str(exc)
            retryable = "ERR_ABORTED" in msg or "Timeout" in msg or "net::" in msg
            if not retryable:
                raise
            logger.warning("Navigation attempt %d/%d failed: %s", attempt, attempts, msg.splitlines()[0])
            if attempt < attempts:
                page.wait_for_timeout(2500)
    raise last  # type: ignore[misc]

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
    'a[aria-label*="Easy Apply" i]',
    'a[href*="openSDUIApplyFlow=true"]',
    'button:has-text("Easy Apply")',
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


def _canonical_linkedin_job_url(url: str) -> str:
    """Strip tracking/ATS query params — they often break Easy Apply (see LinkedIn community reports)."""
    job_id = _job_id_from_url(url)
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}/"
    return url.split("?")[0]


def _external_ats_from_url(url: str) -> str | None:
    match = re.search(r"applicantTrackingSystemName=([^&]+)", url, re.I)
    if not match:
        return None
    return match.group(1).replace("+", " ").strip()


_EXTERNAL_URL_PATTERNS = (
    re.compile(r"https?://jobs\.lever\.co/[a-zA-Z0-9_\-./]+", re.I),
    re.compile(r"https?://jobs\.eu\.lever\.co/[a-zA-Z0-9_\-./]+", re.I),
    re.compile(r"https?://boards\.greenhouse\.io/[a-zA-Z0-9_\-./]+", re.I),
)


def discover_external_apply_url(page: Page) -> str | None:
    """Find Lever/Greenhouse apply URLs embedded in the LinkedIn job page HTML."""
    try:
        html = page.content()
        for pattern in _EXTERNAL_URL_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(0).rstrip("\"'\\),;>")
                if "lever.co" in found.lower():
                    from app.agents.browser.lever import normalize_lever_apply_url

                    return normalize_lever_apply_url(found)
                return found.split("?")[0]
    except Exception:
        pass
    for selector in (
        'a[href*="jobs.lever.co"]',
        'a[href*="jobs.eu.lever.co"]',
        'a[href*="boards.greenhouse.io"]',
    ):
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                href = loc.first.get_attribute("href") or ""
                if href.startswith("http"):
                    if "lever.co" in href.lower():
                        from app.agents.browser.lever import normalize_lever_apply_url

                        return normalize_lever_apply_url(href)
                    return href.split("?")[0]
        except Exception:
            continue
    return None


def _try_direct_ats_apply(
    page: Page, profile: dict, resume_path: str, external_url: str, ats_name: str | None
) -> "ApplyOutcome | None":
    from app.agents.browser.platform_handlers import ApplyOutcome, apply_for_platform

    platform = "lever" if "lever.co" in external_url.lower() else None
    if not platform and "greenhouse.io" in external_url.lower():
        platform = "greenhouse"
    if not platform:
        return None
    logger.info("Bypassing LinkedIn ATS embed — applying directly on %s (%s)", platform, external_url)
    outcome = apply_for_platform(page, external_url, profile, resume_path)
    if outcome.status != "failed":
        outcome = ApplyOutcome(
            outcome.status,
            f"Applied via {platform} (bypassed LinkedIn embed"
            + (f" for {ats_name}" if ats_name else "")
            + f"): {outcome.message}",
            outcome.platform,
            external_url,
        )
    return outcome


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


def _no_apply_reason(page: Page, details: Locator) -> str:
    """Explain why no Easy Apply button was found: closed job / external apply / unknown."""
    panel_text = ""
    try:
        panel_text = details.inner_text() or ""
    except Exception:
        try:
            panel_text = page.inner_text("body") or ""
        except Exception:
            pass
    low = panel_text.lower()
    if "no longer accepting applications" in low or "not currently accepting applications" in low:
        return "Job is no longer accepting applications — skipped."
    external = page.locator(
        'a[aria-label*="company website" i], a[aria-label*="External Apply" i], '
        'button[aria-label*="company website" i]'
    )
    try:
        if external.count() > 0 and external.first.is_visible():
            return "External apply only (Apply on company website) — not an Easy Apply job, skipped."
    except Exception:
        pass
    return f"No Easy Apply button found. {_debug_buttons(details)}"


def _click_locator(loc: Locator, *, timeout_ms: int = 8000, force: bool = True) -> bool:
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
            if not force:
                return False
            target.click(timeout=timeout_ms, force=True)
            return True
    except Exception as exc:
        logger.debug("Click failed: %s", exc)
        return False


def _click_by_patterns(
    page: Page,
    patterns: tuple[str, ...],
    *,
    scope: Page | Locator | None = None,
    force: bool = True,
) -> bool:
    root = scope or page
    for pattern in patterns:
        try:
            btn = root.get_by_role("button", name=re.compile(pattern, re.I))
            if _click_locator(btn, force=force):
                logger.info("Clicked button matching %r", pattern)
                return True
        except Exception:
            pass
        try:
            btn = root.locator(f'button[aria-label*="{pattern}" i]')
            if _click_locator(btn, force=force):
                logger.info("Clicked aria-label matching %r", pattern)
                return True
        except Exception:
            pass
    return False


def already_applied(page: Page) -> bool:
    """Detect jobs the user already applied to (Applied button / confirmation)."""
    details = _job_details_panel(page)
    hints = (
        details.get_by_role("button", name=re.compile(r"^applied$", re.I)),
        details.locator('button[aria-label*="Applied" i]'),
        details.locator(".jobs-apply-button--applied"),
        page.get_by_role("button", name=re.compile(r"^applied$", re.I)),
        page.locator(".jobs-apply-button--applied"),
    )
    for loc in hints:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:
            continue
    try:
        panel_text = (details.inner_text() or "").lower()
    except Exception:
        panel_text = _page_text_lower(page)
    return bool(
        re.search(
            r"already applied|you applied|applied now|application (submitted|sent)",
            panel_text,
        )
    )


def applications_closed(page: Page) -> bool:
    """True when LinkedIn shows the job is no longer accepting applications."""
    text = _page_text_lower(page)
    if "no longer accepting" in text or "not currently accepting applications" in text:
        return True
    try:
        badge = page.locator("text=/no longer accepting applications/i")
        if badge.count() > 0 and badge.first.is_visible():
            return True
    except Exception:
        pass
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
        raise_if_stopped()
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
        # Saved draft: LinkedIn swaps the apply button for "Continue".
        details.locator("button").filter(has_text=re.compile(r"^continue$", re.I)),
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

    # Last chance: one of the clicks above may have opened a slow-rendering modal.
    if _wait_for_modal(page, 5000):
        logger.info("Easy Apply modal appeared after delayed render")
        return True

    if debug:
        print(f"  [debug] Could not click Easy Apply. Visible: {_debug_buttons(details)}")
    return False


_MODAL_CONTROL_RE = re.compile(r"next|submit|review|continue|done", re.I)


def _dialog_is_safety_reminder(dialog: Locator) -> bool:
    try:
        text = (dialog.inner_text() or "").lower()
    except Exception:
        return False
    return "job search safety reminder" in text or "report suspicious jobs" in text


def _dismiss_job_safety_reminder(page: Page) -> bool:
    """LinkedIn often inserts this overlay instead of the Easy Apply form.

    It contains the word “review” (review the employer’s website) — a loose
    Next/Review matcher used to click that and stall on the job page.
    """
    dismissed = False
    try:
        dialogs = page.locator("dialog, .jobs-easy-apply-modal, div[role='dialog']")
        for i in range(min(dialogs.count(), 8)):
            d = dialogs.nth(i)
            try:
                if not d.is_visible() or not _dialog_is_safety_reminder(d):
                    continue
            except Exception:
                continue
            logger.info("Dismissing LinkedIn job-search safety reminder")
            if _click_by_patterns(
                page,
                (r"^got it$", r"^ok$", r"^okay$", r"^continue$", r"^i understand$", r"^dismiss$"),
                scope=d,
            ):
                dismissed = True
            else:
                try:
                    page.keyboard.press("Escape")
                    dismissed = True
                except Exception:
                    pass
            page.wait_for_timeout(400)
    except Exception:
        pass
    return dismissed


def _easy_apply_modal(page: Page) -> Locator:
    """Locate the Easy Apply application modal.

    LinkedIn currently wraps it in a native <dialog>, while a sibling
    div[role=dialog] overlay carries no controls — and the save-draft prompt
    is yet another dialog. Pick the visible dialog that actually has buttons.
    """
    try:
        dialogs = page.locator("dialog, .jobs-easy-apply-modal, div[role='dialog']")
        count = dialogs.count()
        visible: list[Locator] = []
        for i in range(min(count, 6)):
            d = dialogs.nth(i)
            try:
                if d.is_visible() and not _dialog_is_safety_reminder(d):
                    visible.append(d)
            except Exception:
                continue
        for d in visible:
            try:
                labels = " ".join(d.locator("button").all_inner_texts())
                if _MODAL_CONTROL_RE.search(labels):
                    return d
            except Exception:
                continue
        for d in visible:
            try:
                if d.locator("button").count() > 0:
                    return d
            except Exception:
                continue
        if visible:
            return visible[0]
    except Exception:
        pass
    return page.locator(".jobs-easy-apply-content, .jobs-easy-apply-modal").first


def _modal_visible(page: Page) -> bool:
    try:
        modal = _easy_apply_modal(page)
        if modal.count() == 0 or not modal.is_visible():
            return False
        return not _dialog_is_safety_reminder(modal)
    except Exception:
        return False


def _wait_for_modal(page: Page, timeout_ms: int = 6000) -> bool:
    """Poll until the Easy Apply modal appears (page renders slowly under load)."""
    waited = 0
    while waited < timeout_ms:
        if _modal_visible(page):
            return True
        page.wait_for_timeout(400)
        waited += 400
    return _modal_visible(page)


_SUBMISSION_PHRASES = (
    "application sent",
    "application submitted",
    "your application was sent",
    "applied now",
    "has been submitted",
)


def _application_modal_open(page: Page) -> bool:
    """True only when a live application modal (Next/Review/Submit controls)
    is open. Post-submit popups (job alert, 'not now', …) must NOT count —
    they used to short-circuit submission detection."""
    try:
        dialogs = page.locator("dialog, .jobs-easy-apply-modal, div[role='dialog']")
        for i in range(min(dialogs.count(), 6)):
            d = dialogs.nth(i)
            try:
                if d.is_visible():
                    labels = " ".join(d.locator("button").all_inner_texts())
                    if _MODAL_CONTROL_RE.search(labels):
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _submission_confirmed(page: Page, modal_text: str = "", *, settle_sec: float = 5.0) -> bool:
    """True when LinkedIn confirms the application — checked in the modal first,
    then on the page itself (after Submit the modal closes and the job page
    shows 'Application status: Application submitted').

    Polls for up to settle_sec: right after Submit the page navigates/reloads
    (inner_text raises or the section renders late), and a one-shot check
    raced the render — real submissions were misreported as review_required."""
    if any(p in (modal_text or "").lower() for p in _SUBMISSION_PHRASES):
        return True
    deadline = time.monotonic() + settle_sec
    while True:
        try:
            if _application_modal_open(page):
                return False
            page_text = (page.inner_text("body") or "").lower()
            if any(p in page_text for p in _SUBMISSION_PHRASES):
                return True
        except Exception:
            pass  # navigation / re-render in progress — keep polling
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(600)


def _dismiss_blocking_overlays(page: Page) -> None:
    # NOTE: never match dismiss/close here — that is the Easy Apply modal's own X,
    # which opens LinkedIn's "Save this application?" dialog and breaks the run.
    _dismiss_job_safety_reminder(page)
    _click_by_patterns(page, (r"^not now$", r"^no thanks$"))


def _save_dialog_visible(page: Page) -> bool:
    try:
        return "save this application" in (page.inner_text("body") or "").lower()
    except Exception:
        return False


_APPLY_LOAD_ERROR_PHRASES = (
    "error loading this application",
    "experienced an error loading",
)

_JOB_GONE_PHRASES = (
    "unable to load the page",
    "job id provided may not be valid",
    "job posting has been removed",
    "this job is no longer available",
    "this job is no longer accepting applications",
    "no longer accepting applications",
    "not currently accepting applications",
    "no longer accepting",
    "this posting is no longer available",
    "we couldn't find this job",
    "couldn't find the job",
    "page not found",
)


def _page_text_lower(page: Page) -> str:
    try:
        return (page.inner_text("body") or "").lower()
    except Exception:
        return ""


def job_posting_unavailable(page: Page) -> bool:
    """True when LinkedIn shows a removed / invalid / closed job page — skip immediately."""
    if applications_closed(page):
        return True
    text = _page_text_lower(page)
    if any(phrase in text for phrase in _JOB_GONE_PHRASES):
        return True
    try:
        heading = (page.locator("h1, h2").first.inner_text(timeout=800) or "").lower()
        if "unable to load" in heading or "no longer available" in heading:
            return True
    except Exception:
        pass
    return False


def looks_like_internal_job_id(url: str) -> bool:
    """LinkedIn posting ids are long; a 1–7 digit /jobs/view/N/ is our DB id, not LinkedIn's."""
    job_id = _job_id_from_url(url)
    return bool(job_id) and len(job_id) < 8


def _apply_load_failed(page: Page) -> bool:
    """Detect LinkedIn's in-modal 'error loading this application' (not job description text)."""
    try:
        if _modal_visible(page):
            modal = _easy_apply_modal(page)
            text = (modal.inner_text() or "").lower()
        else:
            text = ""
        if not text:
            return False
        return any(phrase in text for phrase in _APPLY_LOAD_ERROR_PHRASES)
    except Exception:
        return False


def _dismiss_save_application_dialog(page: Page) -> bool:
    if not _save_dialog_visible(page):
        return False
    if _click_by_patterns(page, (r"^discard$",)):
        page.wait_for_timeout(500)
        return True
    return False


def _review_with_screenshot(page: Page) -> str:
    """Bail out of a stuck modal, keeping a debug screenshot for diagnosis."""
    debug_dir = settings.storage_path / "browser_sessions" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    shot = debug_dir / f"modal_review_{int(time.time())}.png"
    try:
        page.screenshot(path=str(shot), full_page=False)
        logger.info("Review-required screenshot: %s", shot)
    except Exception:
        pass
    return APPLY_RESULT_REVIEW


def _read_dialog_text(page: Page) -> str:
    """Read the current dialog's text without going blind.

    The walk's dialog locator goes stale after LinkedIn re-renders it
    (inner_text then raises or returns ''), which used to reset the step
    signature to '' and bail out of otherwise-healthy applications."""
    try:
        modal = _easy_apply_modal(page)
        text = (modal.inner_text() or "").strip().lower()
        if text:
            return text
    except Exception:
        pass
    try:
        for sel in ("dialog", "div[role='dialog']", ".jobs-easy-apply-modal"):
            loc = page.locator(sel)
            for i in range(min(loc.count(), 5)):
                try:
                    if loc.nth(i).is_visible():
                        text = (loc.nth(i).inner_text() or "").strip().lower()
                        if text:
                            return text
                except Exception:
                    continue
    except Exception:
        pass
    return ""


_BACK_HOLD_JS = """() => {
  if (window.__rbHoldInstalled) return true;
  window.__rbHoldInstalled = true;
  window.__rbHoldMs = window.__rbHoldMs || 30000;
  window.__rbHoldUntil = window.__rbHoldUntil || 0;
  window.__rbUserIdleMs = window.__rbUserIdleMs || 12000;
  window.__rbUserBusyUntil = window.__rbUserBusyUntil || 0;
  window.__rbBotBusy = window.__rbBotBusy || false;
  const labelOf = (el) => {
    if (!el) return "";
    return ((el.innerText || "") + " " + (el.getAttribute("aria-label") || "")).trim().toLowerCase();
  };
  const isBack = (el) => {
    const t = labelOf(el);
    return t === "back" || t.startsWith("back ") || t.includes("previous step");
  };
  const isAdvance = (el) => {
    const t = labelOf(el);
    if (isBack(el)) return false;
    return /^(next|continue|review|submit)/.test(t) || t.includes("next") || t.includes("submit application") || t.includes("review your application");
  };
  const isField = (el) => {
    if (!el || !el.tagName) return false;
    const tag = el.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
  };
  const arm = () => { window.__rbHoldUntil = Date.now() + window.__rbHoldMs; };
  const clear = () => { window.__rbHoldUntil = 0; };
  const bumpUser = () => {
    if (window.__rbBotBusy) return;
    window.__rbUserBusyUntil = Date.now() + window.__rbUserIdleMs;
  };
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest && e.target.closest("button, [role='button']");
    if (btn) {
      if (isBack(btn)) { arm(); return; }
      if (isAdvance(btn)) {
        if (!window.__rbBotAdvancing) {
          clear();
          window.__rbUserBusyUntil = 0;
        }
        return;
      }
    }
    if (e.isTrusted && !window.__rbBotBusy) bumpUser();
  }, true);
  ["keydown", "keyup", "input", "paste", "change", "compositionupdate"].forEach((ev) => {
    document.addEventListener(ev, (e) => {
      if (!e.isTrusted || window.__rbBotBusy) return;
      bumpUser();
    }, true);
  });
  document.addEventListener("focusin", (e) => {
    if (window.__rbBotBusy) return;
    if (isField(e.target)) bumpUser();
  }, true);
  return true;
}"""


def _install_back_hold(page: Page) -> None:
    hold_ms = max(30, int(settings.auto_apply_back_review_sec or 30)) * 1000
    idle_ms = max(5, int(settings.auto_apply_user_idle_sec or 12)) * 1000
    try:
        page.evaluate(f"window.__rbHoldMs = {hold_ms}; window.__rbUserIdleMs = {idle_ms}")
        page.evaluate(_BACK_HOLD_JS)
    except Exception as exc:
        logger.debug("Could not install Back-hold script: %s", exc)


def _arm_back_hold(page: Page) -> None:
    try:
        page.evaluate(
            "window.__rbHoldUntil = Date.now() + (window.__rbHoldMs || 30000)"
        )
    except Exception:
        pass


def _hold_remaining_ms(page: Page) -> int:
    try:
        return int(
            page.evaluate(
                """() => {
                  const now = Date.now();
                  const back = Math.max(0, (window.__rbHoldUntil || 0) - now);
                  const typed = Math.max(0, (window.__rbUserBusyUntil || 0) - now);
                  const el = document.activeElement;
                  const inModal = el && el.closest && el.closest("dialog, [role='dialog'], .jobs-easy-apply-modal");
                  const tag = el && el.tagName ? el.tagName.toLowerCase() : "";
                  const focused = Boolean(
                    inModal && (tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable)
                  );
                  if (focused && !window.__rbBotBusy) return Math.max(back, typed, 800);
                  return Math.max(back, typed);
                }"""
            )
            or 0
        )
    except Exception:
        return 0


def _release_user_edit_hold(page: Page) -> None:
    try:
        page.evaluate("window.__rbUserBusyUntil = 0; window.__rbHoldUntil = 0;")
    except Exception:
        pass


def _bot_fill_begin(page: Page) -> None:
    try:
        page.evaluate("window.__rbBotBusy = true")
    except Exception:
        pass


def _bot_fill_end(page: Page) -> None:
    try:
        page.evaluate(
            """() => {
              window.__rbBotBusy = false;
              const el = document.activeElement;
              if (el && el.blur && el.closest && el.closest("dialog, [role='dialog'], .jobs-easy-apply-modal")) {
                el.blur();
              }
            }"""
        )
    except Exception:
        pass


def _click_easy_apply_back(page: Page, modal: Locator) -> bool:
    """Click LinkedIn's Easy Apply Back control (footer / aria-label / role)."""
    for aria in ("Back to previous step", "Back", "Previous"):
        try:
            btn = modal.locator(f'button[aria-label="{aria}" i]')
            if _click_locator(btn):
                logger.info("User go-back: clicked aria-label %r", aria)
                return True
        except Exception:
            continue
    if _click_by_patterns(page, MODAL_BACK, scope=modal):
        logger.info("User go-back: clicked Back via pattern")
        return True
    try:
        footer = modal.locator(
            "button:has-text('Back'), footer button, .jobs-easy-apply-footer button"
        )
        for i in range(min(footer.count(), 8)):
            loc = footer.nth(i)
            label = (loc.inner_text() or loc.get_attribute("aria-label") or "").strip().lower()
            if label in {"back", "previous"} or label.startswith("back"):
                if _click_locator(loc):
                    logger.info("User go-back: clicked footer Back %r", label)
                    return True
    except Exception:
        pass
    return False


def _wait_slice(page: Page, total_ms: int, *, slice_ms: int = 250) -> None:
    """Wait in short slices so Stop / Go-back is noticed quickly."""
    remaining = max(0, total_ms)
    while remaining > 0:
        raise_if_stopped()
        if go_back_pending():
            return
        step = min(slice_ms, remaining)
        page.wait_for_timeout(step)
        remaining -= step


def _modal_page_index(page: Page) -> int | None:
    """Parse LinkedIn's '3/13 pages' indicator so we can detect Back."""
    blob = _read_dialog_text(page)
    match = re.search(r"(\d+)\s*/\s*(\d+)", blob)
    if match:
        return int(match.group(1))
    try:
        modal = _easy_apply_modal(page)
        text = (modal.inner_text() or "")
        match = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def _freeze_on_previous_step(page: Page, *, reason: str = "", profile: dict | None = None) -> None:
    """Stay on this Easy Apply page. Ends early if you click Next or finish every field."""
    _install_back_hold(page)
    _arm_back_hold(page)
    remaining = _hold_remaining_ms(page)
    hold_sec = max(30, int(remaining / 1000) if remaining else int(settings.auto_apply_back_review_sec or 30))
    start_idx = _modal_page_index(page)
    logger.info(
        "Holding %ss on previous Easy Apply page (%s). Completing the section or clicking Next ends the wait.",
        hold_sec,
        reason or "review",
    )
    print(
        f"\n*** Back — reviewing this page for {hold_sec}s. "
        "If every box/field is filled, the bot continues; otherwise click Next or wait out the timer. ***\n"
    )
    deadline = time.time() + hold_sec
    while time.time() < deadline:
        raise_if_stopped()
        if profile is not None:
            try:
                modal = _easy_apply_modal(page)
                if easy_apply_section_complete(page, profile, modal):
                    logger.info("Hold ended early — section is complete")
                    try:
                        page.evaluate("window.__rbHoldUntil = 0")
                    except Exception:
                        pass
                    return
            except Exception:
                pass
        if _hold_remaining_ms(page) < 250:
            logger.info("Hold ended early — you clicked Next")
            return
        idx = _modal_page_index(page)
        if start_idx is not None and idx is not None and idx > start_idx:
            logger.info("Hold ended early — page moved %s→%s", start_idx, idx)
            try:
                page.evaluate("window.__rbHoldUntil = 0")
            except Exception:
                pass
            return
        page.wait_for_timeout(250)


def _user_requested_step_back(page: Page, modal: Locator, profile: dict | None = None) -> bool:
    """Click Back when the user asks, then stay on that previous step (no Next)."""
    if not go_back_signaled():
        return False
    clicked = _click_easy_apply_back(page, modal)
    if not clicked:
        page.wait_for_timeout(400)
        modal = _easy_apply_modal(page)
        clicked = _click_easy_apply_back(page, modal)
    if clicked:
        page.wait_for_timeout(900)
        logger.info("Clicked LinkedIn Back — now freezing on the previous section")
    else:
        logger.warning("Go-back requested but Back button was not found — freezing on current step")
    _freeze_on_previous_step(page, reason="app Go back", profile=profile)
    return True


def complete_easy_apply_modal(page: Page, profile: dict, resume_path: str, *, max_steps: int = 16) -> str:
    """Walk through LinkedIn Easy Apply modal — Next → Review → Submit."""
    _dismiss_job_safety_reminder(page)
    for _ in range(30):
        _dismiss_job_safety_reminder(page)
        if _modal_visible(page):
            break
        page.wait_for_timeout(300)
    if not _modal_visible(page):
        return APPLY_RESULT_UNSUPPORTED

    _install_back_hold(page)
    modal = _easy_apply_modal(page)
    try:
        preview = (modal.inner_text() or "")[:240].replace("\n", " | ")
        logger.info(
            "Modal walk start: visible=%s buttons=[%s] body=%r",
            _modal_visible(page),
            _debug_buttons(modal),
            preview,
        )
    except Exception as exc:
        logger.info("Modal walk start: cannot read modal (%s)", exc)
    last_step_sig = ""
    same_step_count = 0
    stall_manual_pauses = 0
    last_page_idx = _modal_page_index(page)
    for step in range(max_steps):
        raise_if_stopped()
        # Dialog is replaced by LinkedIn between steps — always re-resolve.
        modal = _easy_apply_modal(page)
        _install_back_hold(page)
        held = _hold_remaining_ms(page)
        if held >= 250:
            if human_signaled():
                _release_user_edit_hold(page)
                logger.info("User signaled done — bot may continue")
            else:
                logger.info("Waiting — you are editing the form (%sms). Bot will not click Next/Submit.", held)
                page.wait_for_timeout(min(held, 800))
            continue
        page_idx = _modal_page_index(page)
        if last_page_idx is not None and page_idx is not None and page_idx < last_page_idx:
            _freeze_on_previous_step(page, reason=f"Back {last_page_idx}→{page_idx}", profile=profile)
            last_page_idx = page_idx
            continue
        if _user_requested_step_back(page, modal, profile):
            last_page_idx = _modal_page_index(page) or last_page_idx
            continue
        if captcha_present(page):
            try:
                wait_for_human(page, "LinkedIn: solve CAPTCHA in the Easy Apply modal, then press ENTER.", timeout_sec=150)
            except TimeoutError:
                return _review_with_screenshot(page)

        modal = _easy_apply_modal(page)
        resume_step = is_resume_document_step(modal)
        _bot_fill_begin(page)
        try:
            if resume_step:
                upload_resume(page, resume_path)
                logger.info("Resume document step — using a listed PDF, then Next (max ~10s)")
            else:
                fill_from_resume_only(page, profile)
                page_idx = _modal_page_index(page)
                if last_page_idx is not None and page_idx is not None and page_idx < last_page_idx:
                    _freeze_on_previous_step(page, reason=f"Back {last_page_idx}→{page_idx}", profile=profile)
                    last_page_idx = page_idx
                    continue
                if _user_requested_step_back(page, _easy_apply_modal(page), profile):
                    last_page_idx = _modal_page_index(page) or last_page_idx
                    continue
                upload_resume(page, resume_path)
                from app.agents.browser.forms import _fill_phone_country_code

                _fill_phone_country_code(page, profile)
                wait_for_dropdown_review(page, profile, modal)
                fixed, invalid = repair_invalid_fields(page, profile, modal)
                if fixed:
                    logger.info("Repaired %d invalid modal fields from resume", fixed)
                    page.wait_for_timeout(400)
                modal = _easy_apply_modal(page)
                if needs_user_input_for_resume_gaps(page, profile, modal):
                    logger.info("Pausing — some required fields are not on the uploaded resume")
                    _bot_fill_end(page)
                    wait_for_user_if_resume_gaps(page, profile, modal)
                    _bot_fill_begin(page)
                    modal = _easy_apply_modal(page)
                    if needs_user_input_for_resume_gaps(page, profile, modal):
                        continue
        finally:
            _bot_fill_end(page)

        body = _read_dialog_text(page)

        if _apply_load_failed(page):
            logger.warning("LinkedIn Easy Apply form failed to load (session/browser mismatch or LinkedIn glitch)")
            _dismiss_save_application_dialog(page)
            close_easy_apply_modal(page)
            return APPLY_RESULT_FAILED

        if _save_dialog_visible(page):
            # The modal X was hit at some point — clean up and let the run continue.
            logger.info("Save-draft dialog detected — discarding")
            _dismiss_save_application_dialog(page)
            return _review_with_screenshot(page)

        if _submission_confirmed(page, body):
            _click_by_patterns(page, MODAL_DONE, scope=modal)
            return APPLY_RESULT_SUBMITTED

        modal = _easy_apply_modal(page)
        page_idx = _modal_page_index(page)
        if last_page_idx is not None and page_idx is not None and page_idx < last_page_idx:
            _freeze_on_previous_step(page, reason=f"Back {last_page_idx}→{page_idx}", profile=profile)
            last_page_idx = page_idx
            continue
        if _user_requested_step_back(page, modal, profile):
            last_page_idx = _modal_page_index(page) or last_page_idx
            continue
        if _hold_remaining_ms(page) >= 250:
            continue

        # Advance first (Next/Review), then Submit, and only close via DONE after
        # submission — clicking the modal's X opens LinkedIn's save-draft dialog.
        advanced = False
        if _hold_remaining_ms(page) >= 250:
            continue
        try:
            page.evaluate("window.__rbBotAdvancing = true")
        except Exception:
            pass
        for aria in ("Continue to next step", "Review your application", "Submit application", "Submit"):
            btn = modal.locator(f'button[aria-label="{aria}" i]')
            if _click_locator(btn, force=False):
                logger.info("Clicked aria-label %r", aria)
                _wait_slice(page, 2000)
                advanced = True
                break
        if not advanced:
            if _click_by_patterns(page, MODAL_NEXT, scope=modal, force=False):
                _wait_slice(page, 1200)
                advanced = True
            elif _click_by_patterns(page, MODAL_SUBMIT, scope=modal):
                _wait_slice(page, 2000)
                advanced = True
            elif _click_by_patterns(page, MODAL_DONE, scope=modal):
                try:
                    page.evaluate("window.__rbBotAdvancing = false")
                except Exception:
                    pass
                return APPLY_RESULT_SUBMITTED
            else:
                if not body and _modal_visible(page):
                    # A dialog exists but its text isn't readable yet (mid
                    # re-render) — wait instead of counting a blind identical
                    # step (that used to bail out of healthy applications).
                    same_step_count = 0
                    last_step_sig = ""
                    page.wait_for_timeout(600)
                    continue
                step_sig = body[:200]
                if step_sig == last_step_sig:
                    same_step_count += 1
                    if same_step_count >= 2:
                        if _submission_confirmed(page, body):
                            return APPLY_RESULT_SUBMITTED
                        if stall_manual_pauses >= 1:
                            logger.info(
                                "No progress after manual fill pause (body_empty=%s) — skipping job",
                                not body,
                            )
                            return _review_with_screenshot(page)
                        stall_manual_pauses += 1
                        wait_for_user_if_resume_gaps(
                            page,
                            profile,
                            modal,
                            reason=(
                                f"LinkedIn step {step + 1}: fill fields that are not on your resume. "
                                f"The bot will not click for {settings.auto_apply_manual_fill_sec} seconds."
                            ),
                        )
                        same_step_count = 0
                        continue
                else:
                    same_step_count = 0
                last_step_sig = step_sig
        try:
            page.evaluate("window.__rbBotAdvancing = false")
        except Exception:
            pass

        body = _read_dialog_text(page)
        if _submission_confirmed(page, body):
            _click_by_patterns(page, MODAL_DONE, scope=modal)
            return APPLY_RESULT_SUBMITTED

        _dismiss_blocking_overlays(page)
        _wait_slice(page, 800)
        after_idx = _modal_page_index(page)
        if last_page_idx is not None and after_idx is not None and after_idx < last_page_idx:
            _freeze_on_previous_step(page, reason=f"Back {last_page_idx}→{after_idx}", profile=profile)
            last_page_idx = after_idx
            continue
        if after_idx is not None:
            last_page_idx = after_idx

    return _review_with_screenshot(page)


def close_easy_apply_modal(page: Page) -> None:
    modal = _easy_apply_modal(page)
    if modal.count() > 0 and modal.is_visible():
        _click_by_patterns(page, MODAL_DONE, scope=modal)
        _click_by_patterns(page, (r"^dismiss$", r"^close$", r"^cancel$"), scope=modal)
        page.wait_for_timeout(400)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(400)
    if _apply_load_failed(page):
        _dismiss_save_application_dialog(page)
    elif _save_dialog_visible(page):
        # Closing mid-application asked to save a draft — discard it so the
        # next job starts with a clean page (LinkedIn rejects stale drafts).
        _dismiss_save_application_dialog(page)
        page.wait_for_timeout(400)
    page.wait_for_timeout(300)


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
    _goto_with_retry(page, url)
    page.wait_for_timeout(3000)

    if "/login" in page.url or "authwall" in page.url:
        wait_for_human(page, "LinkedIn: log in in the browser, then press ENTER.")
        _goto_with_retry(page, url)
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
                debug_dir = settings.storage_path / "browser_sessions" / "debug"
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
        except SkipJob:
            try:
                close_easy_apply_modal(page)
            except Exception:
                pass
            results.append(
                {
                    "title": title,
                    "company": company,
                    "url": page.url,
                    "job_id": job_id,
                    "platform": "linkedin",
                    "status": APPLY_RESULT_SKIPPED,
                    "message": "Skipped by you — moving to the next job.",
                }
            )
            if on_result:
                on_result(results[-1])
            print(f"  → skipped: {title}")
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

    apply_url = _canonical_linkedin_job_url(url)
    try:
        return _apply_linkedin_job_page_inner(page, url, profile, resume_path, apply_url=apply_url, debug=debug)
    except SkipJob:
        logger.info("User skipped this job: %s", apply_url)
        try:
            close_easy_apply_modal(page)
        except Exception:
            pass
        return ApplyOutcome(
            APPLY_RESULT_SKIPPED,
            "Skipped by you — moving to the next job.",
            "linkedin",
            apply_url,
        )


def _apply_linkedin_job_page_inner(
    page: Page, url: str, profile: dict, resume_path: str, *, apply_url: str, debug: bool = False
):
    from app.agents.browser.platform_handlers import ApplyOutcome

    external_ats = _external_ats_from_url(url)
    if external_ats:
        logger.info("Job uses external ATS in LinkedIn Easy Apply: %s", external_ats)
    if apply_url != url:
        logger.info("Canonical job URL: %s (was %s)", apply_url, url[:120])
    logger.info("LinkedIn job page apply: %s", apply_url)
    raise_if_stopped()
    if looks_like_internal_job_id(apply_url):
        logger.warning("Skip invalid LinkedIn job URL (internal id): %s", apply_url)
        return ApplyOutcome(
            APPLY_RESULT_UNSUPPORTED,
            "Invalid LinkedIn job id — posting cannot be loaded.",
            "linkedin",
            apply_url,
        )
    _goto_with_retry(page, apply_url)
    _wait_slice(page, 1800)
    raise_if_stopped()
    if applications_closed(page) or job_posting_unavailable(page):
        logger.warning("Skip closed/removed job page: %s", apply_url)
        return ApplyOutcome(
            APPLY_RESULT_UNSUPPORTED,
            "No longer accepting applications — skipped.",
            "linkedin",
            apply_url,
        )

    if captcha_present(page):
        wait_for_human(page, "LinkedIn: CAPTCHA detected — solve it, then press ENTER.")

    if "/login" in page.url or "authwall" in page.url:
        wait_for_human(page, "LinkedIn: log in manually, then press ENTER.")
        _goto_with_retry(page, apply_url)
        page.wait_for_timeout(3000)
        if applications_closed(page) or job_posting_unavailable(page):
            return ApplyOutcome(
                APPLY_RESULT_UNSUPPORTED,
                "No longer accepting applications — skipped.",
                "linkedin",
                apply_url,
            )

    if already_applied(page):
        return ApplyOutcome(APPLY_RESULT_UNSUPPORTED, "Already applied to this job.", "linkedin", apply_url)

    external_direct = discover_external_apply_url(page)
    ats_lower = (external_ats or "").lower()
    bypass_embed = bool(
        external_direct
        and (
            "lever" in ats_lower
            or "greenhouse" in ats_lower
            or "lever.co" in external_direct.lower()
            or "greenhouse.io" in external_direct.lower()
        )
    )
    if bypass_embed:
        direct = _try_direct_ats_apply(page, profile, resume_path, external_direct, external_ats)
        if direct and direct.status in (APPLY_RESULT_SUBMITTED, APPLY_RESULT_REVIEW):
            return direct

    if not click_easy_apply(page, debug=debug):
        details = _job_details_panel(page)
        debug_dir = settings.storage_path / "browser_sessions" / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        shot = debug_dir / f"no_easy_apply_{int(time.time())}.png"
        try:
            page.screenshot(path=str(shot), full_page=False)
            logger.info("No-Easy-Apply screenshot: %s (url=%s)", shot, page.url)
        except Exception:
            pass
        if external_direct:
            direct = _try_direct_ats_apply(page, profile, resume_path, external_direct, external_ats)
            if direct and direct.status in (APPLY_RESULT_SUBMITTED, APPLY_RESULT_REVIEW):
                return direct
        return ApplyOutcome(
            APPLY_RESULT_UNSUPPORTED,
            _no_apply_reason(page, details),
            "linkedin",
            apply_url,
        )

    status = complete_easy_apply_modal(page, profile, resume_path)
    close_easy_apply_modal(page)
    if status == APPLY_RESULT_FAILED:
        fallback_url = external_direct or discover_external_apply_url(page)
        if fallback_url:
            direct = _try_direct_ats_apply(page, profile, resume_path, fallback_url, external_ats)
            if direct and direct.status in (APPLY_RESULT_SUBMITTED, APPLY_RESULT_REVIEW):
                return direct
            if direct and direct.status == APPLY_RESULT_FAILED:
                return direct
        if external_ats:
            msg = (
                f"LinkedIn could not load the embedded {external_ats} application and direct ATS apply "
                f"{'was attempted' if fallback_url else 'URL was not found on the page'}."
            )
        else:
            msg = (
                "LinkedIn could not load the Easy Apply form "
                "(\"error loading this application\"). Often account, ATS, or URL-tracking related — "
                "try the job in regular Chrome, verify LinkedIn email/phone, or skip."
            )
    elif status == APPLY_RESULT_REVIEW:
        msg = "Easy Apply needs manual answers — check storage/browser_sessions/debug/ for a screenshot."
    elif status == APPLY_RESULT_UNSUPPORTED:
        msg = "This posting is not Easy Apply on LinkedIn."
    elif status == APPLY_RESULT_SUBMITTED:
        msg = "Application submitted on LinkedIn."
    elif status == APPLY_RESULT_SKIPPED:
        msg = "Skipped by you — moving to the next job."
    else:
        msg = f"LinkedIn apply finished with status={status}"
    return ApplyOutcome(status, msg, "linkedin", apply_url)
