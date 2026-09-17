"""Detect CAPTCHA / bot checks and pause for human intervention."""

from __future__ import annotations

import logging
import threading
import time

from playwright.sync_api import Page

logger = logging.getLogger("app.browser.captcha")

CAPTCHA_IFRAME_SELECTORS = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[title*="captcha" i]',
    'iframe[title*="challenge" i]',
)

CAPTCHA_TEXT_HINTS = (
    "captcha",
    "verify you are human",
    "verify you're human",
    "security check",
    "unusual traffic",
    "robot",
    "challenge",
)

_human_event = threading.Event()


def captcha_present(page: Page) -> bool:
    for selector in CAPTCHA_IFRAME_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue

    try:
        body = (page.inner_text("body") or "").lower()
        if any(hint in body for hint in CAPTCHA_TEXT_HINTS):
            if "recaptcha" in body or "captcha" in body or "unusual traffic" in body:
                return True
    except Exception:
        pass
    return False


def signal_human_ready() -> None:
    """Signal that the human has completed the required action."""
    _human_event.set()


def human_signaled() -> bool:
    """Return True once if a human signal was sent since the last check."""
    if _human_event.is_set():
        _human_event.clear()
        return True
    return False


def wait_for_human(page: Page, reason: str, *, timeout_sec: int = 600) -> None:
    """Pause automation until human signals ready (after solving CAPTCHA).

    In daemon threads (auto-apply bot), ``input()`` is not available.
    Instead we poll for CAPTCHA clearance and respect a timeout.
    The caller can also set ``signal_human_ready()`` from an API endpoint.
    """
    print("\n" + "=" * 60)
    print("HUMAN ACTION REQUIRED")
    print(reason)
    print("Complete the step in the browser window.")
    print("The bot will resume automatically once the CAPTCHA is cleared.")
    print("Press Ctrl+C to abort this application.")
    print("=" * 60)

    _human_event.clear()
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _human_event.is_set():
            _human_event.clear()
            return
        if not captcha_present(page):
            # CAPTCHA cleared — give user a moment to finish filling fields
            time.sleep(2)
            if not captcha_present(page):
                return
        time.sleep(2)

    raise TimeoutError(f"Timed out waiting for human action: {reason}")
