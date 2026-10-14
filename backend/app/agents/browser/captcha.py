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
_go_back_event = threading.Event()
_stop_event = threading.Event()


class BotAborted(Exception):
    """User clicked Stop — abandon the current job immediately."""


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


def signal_bot_stop() -> None:
    _stop_event.set()


def clear_bot_stop() -> None:
    _stop_event.clear()


def stop_signaled() -> bool:
    return _stop_event.is_set()


def raise_if_stopped() -> None:
    if _stop_event.is_set():
        raise BotAborted("Stop requested")


def signal_human_ready() -> None:
    """Signal that the human has completed the required action."""
    _human_event.set()


def signal_go_back() -> None:
    """User wants the bot to click Back in Easy Apply and pause for review."""
    _go_back_event.set()


def go_back_pending() -> bool:
    """True while the user has asked to step back (does not consume the signal)."""
    return _go_back_event.is_set()


def go_back_signaled() -> bool:
    """Return True once if the user requested a step back."""
    if _go_back_event.is_set():
        _go_back_event.clear()
        return True
    return False


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
        if stop_signaled():
            raise BotAborted("Stop requested")
        if not captcha_present(page):
            # CAPTCHA cleared — give user a moment to finish filling fields
            time.sleep(2)
            if not captcha_present(page):
                return
        time.sleep(2)

    raise TimeoutError(f"Timed out waiting for human action: {reason}")


def pause_for_manual_fill(page: Page, reason: str, *, timeout_sec: int = 60) -> None:
    """Wait up to ``timeout_sec`` so the user can complete fields in the browser.

    Unlike ``wait_for_human`` (CAPTCHA / ENTER), this is a fixed window for
    employment dates, phone, and other fields automation often misses.
    Ends early if the user signals ready via the app API.
    """
    logger.info("Manual fill pause (%ss): %s", timeout_sec, reason)
    print("\n" + "=" * 60)
    print("MANUAL FILL — up to %s seconds" % timeout_sec)
    print(reason)
    print("The bot will NOT click or type for %s seconds — fill fields yourself." % timeout_sec)
    print("Or click “Login Done / CAPTCHA Solved” in the app when finished early.")
    print("=" * 60)

    _human_event.clear()
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _human_event.is_set():
            _human_event.clear()
            return
        if go_back_pending():
            logger.info("Manual fill pause ended early — user requested Go back")
            return
        if stop_signaled():
            raise BotAborted("Stop requested")
        time.sleep(0.25)
