"""Headed Playwright runner for auto-apply with DB-backed login sessions."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import Browser, BrowserContext, sync_playwright

from app.agents.browser.board_common import (
    GLASSDOOR_CONFIG,
    INDEED_CONFIG,
    ZIPRECRUITER_CONFIG,
    apply_from_search as board_apply_from_search,
)
from app.agents.browser.job_search import search_jobs_on_platform
from app.agents.browser.linkedin import apply_linkedin_from_search
from app.agents.browser.platform_handlers import ApplyOutcome, apply_for_platform, detect_platform

logger = logging.getLogger("app.browser.runner")

DEFAULT_SESSION_DIR = Path("storage/browser_sessions")


def default_headless() -> bool:
    """Headless mode on servers (Linux without a display, e.g. Render).

    Desktop platforms keep the visible browser so the user can watch/solve CAPTCHAs.
    """
    import os

    return sys.platform.startswith("linux") and not os.environ.get("DISPLAY")

BOARD_CONFIGS = {
    "indeed": INDEED_CONFIG,
    "ziprecruiter": ZIPRECRUITER_CONFIG,
    "glassdoor": GLASSDOOR_CONFIG,
}


def system_chrome_user_data_dir() -> Path | None:
    """User's real Chrome profile dir (where they are already logged in).

    Override with CHROME_USER_DATA_DIR env. Returns None when Chrome is absent.
    """
    import os
    import sys

    override = os.environ.get("CHROME_USER_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.exists() else None
    home = Path.home()
    if sys.platform == "darwin":
        path = home / "Library" / "Application Support" / "Google" / "Chrome"
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        path = Path(local) / "Google" / "Chrome" / "User Data" if local else home
    else:
        path = home / ".config" / "google-chrome"
    return path if path.exists() else None


def system_safari_user_data_dir() -> Path | None:
    """User's Safari profile dir on macOS (if available via Playwright)."""
    import sys

    if sys.platform != "darwin":
        return None
    home = Path.home()
    # Safari doesn't have a traditional profile dir like Chrome
    # Playwright uses a different approach for Safari
    path = home / "Library" / "Safari"
    return path if path.exists() else None


def system_firefox_user_data_dir() -> Path | None:
    """User's Firefox profile dir."""
    import os
    import sys

    override = os.environ.get("FIREFOX_USER_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.exists() else None
    home = Path.home()
    if sys.platform == "darwin":
        path = home / "Library" / "Application Support" / "Firefox"
    elif sys.platform == "win32":
        local = os.environ.get("APPDATA", "")
        path = Path(local) / "Mozilla" / "Firefox" if local else home
    else:
        path = home / ".mozilla" / "firefox"
    return path if path.exists() else None


def get_available_browser_contexts(pw) -> list[tuple[str, callable]]:
    """Get list of available browser contexts to try in order of preference."""
    contexts = []

    # 1. Try user's Chrome profile
    chrome_dir = system_chrome_user_data_dir()
    if chrome_dir:
        def chrome_context(vp=None):
            import os
            profile = os.environ.get("CHROME_PROFILE", "Default").strip() or "Default"
            return pw.chromium.launch_persistent_context(
                str(chrome_dir),
                channel="chrome",
                headless=default_headless(),
                slow_mo=80,
                viewport=vp or {"width": 1400, "height": 900},
                ignore_default_args=["--enable-automation"],
                args=[f"--profile-directory={profile}", "--disable-blink-features=AutomationControlled"],
            )
        contexts.append(("Chrome (your profile)", chrome_context))

    # 2. Try Chrome without persistent context (fresh)
    def chrome_fresh(vp=None):
        return pw.chromium.launch(
            headless=default_headless(),
            slow_mo=80,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
        )
    contexts.append(("Chrome (fresh)", lambda vp: chrome_fresh(vp).new_context(viewport=vp or {"width": 1400, "height": 900})))

    # 3. Try Firefox if available
    firefox_dir = system_firefox_user_data_dir()
    if firefox_dir:
        def firefox_context(vp=None):
            import os
            # Firefox profile selection is more complex, use first available
            profiles_ini = firefox_dir / "profiles.ini"
            profile_dir = firefox_dir
            if profiles_ini.exists():
                # Parse profiles.ini to find default profile
                import configparser
                config = configparser.ConfigParser()
                config.read(profiles_ini)
                for section in config.sections():
                    if config[section].get("Default") == "1":
                        profile_dir = firefox_dir / config[section].get("Path", "")
                        break
            return pw.firefox.launch_persistent_context(
                str(profile_dir),
                headless=default_headless(),
                slow_mo=80,
                viewport=vp or {"width": 1400, "height": 900},
            )
        contexts.append(("Firefox (your profile)", firefox_context))

    # 4. Try Safari on macOS (Playwright support is limited)
    if sys.platform == "darwin":
        try:
            def safari_context(vp=None):
                return pw.webkit.launch(
                    headless=default_headless(),
                    slow_mo=80,
                    viewport=vp or {"width": 1400, "height": 900},
                )
            contexts.append(("Safari (WebKit)", lambda vp: safari_context(vp).new_context(viewport=vp or {"width": 1400, "height": 900})))
        except Exception:
            pass

    return contexts


def open_system_chrome_context(pw, *, viewport: dict | None = None):
    """Open the user's own browser (persistent profile) via Playwright.

    This reuses the browser where the user is already logged in — no fresh
    bot browser, no logging in again. Tries Chrome, Firefox, Safari in order.
    Raises RuntimeError with a human-readable reason when unusable.
    """
    import os

    # Try to get available browser contexts
    available_contexts = get_available_browser_contexts(pw)

    if not available_contexts:
        raise RuntimeError(
            "No supported browser found. Install Chrome, Firefox, or Safari "
            "and make sure you have a profile with your logins."
        )

    last_error = None
    for name, context_factory in available_contexts:
        try:
            logger.info(f"Trying to open {name}...")
            context = context_factory(viewport)
            logger.info(f"Successfully opened {name}")
            return context
        except Exception as exc:
            last_error = exc
            msg = str(exc).lower()
            if "profile" in msg or "in use" in msg or "lock" in msg:
                logger.warning(f"{name} profile locked: {exc}")
                continue
            logger.warning(f"{name} failed: {exc}")
            continue

    # If we get here, all browsers failed
    if last_error:
        msg = str(last_error)
        if "profile" in msg.lower() or "in use" in msg.lower() or "lock" in msg.lower():
            raise RuntimeError(
                "Your browser is open and locking its profile. Close all browser windows completely, "
                "then click Log in again — it will reuse your logged-in sessions."
            ) from last_error
        raise RuntimeError(f"Could not open any browser profile ({msg}).") from last_error
    raise RuntimeError("No supported browser found. Install Chrome, Firefox, or Safari.")


class AutoApplyRunner:
    """Runs job applications in a visible browser; pauses for CAPTCHA / login."""

    def __init__(
        self,
        *,
        session_dir: Path | None = None,
        headless: bool = False,
        slow_mo_ms: int = 100,
        candidate_id: int | None = None,
        db=None,
    ) -> None:
        self.session_dir = session_dir or DEFAULT_SESSION_DIR
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.candidate_id = candidate_id
        self.db = db
        self.on_apply_result: Callable[[dict], None] | None = None
        self._playwright = None
        self._browser: Browser | None = None

    def _emit(self, result: dict) -> None:
        if self.on_apply_result:
            self.on_apply_result(result)

    def __enter__(self) -> "AutoApplyRunner":
        self._playwright = sync_playwright().start()
        self._ensure_browser()
        return self

    def _ensure_browser(self) -> None:
        """Launch chromium, or relaunch it if the user closed the window /
        the process crashed — one dead job must not fail the whole batch."""
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    return
            except Exception:
                pass
            logger.warning("Browser no longer connected — relaunching")
            try:
                self._browser.close()
            except Exception:
                pass
        args = ["--disable-dev-shm-usage", "--disable-gpu"]
        if sys.platform.startswith("linux"):
            # Containers (Render/Docker) have no user-namespace sandbox and a
            # tiny /dev/shm — without these Chromium dies at launch/first tab.
            args.append("--no-sandbox")
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
            args=args,
        )

    def __exit__(self, *args) -> None:
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass

    def _session_file(self, platform: str) -> Path:
        return self.session_dir / f"{platform}_session.json"

    def _applied_ids_file(self, platform: str) -> Path:
        suffix = f"_{self.candidate_id}" if self.candidate_id else ""
        return self.session_dir / f"{platform}_applied{suffix}.json"

    def load_applied_ids(self, platform: str) -> set[str]:
        path = self._applied_ids_file(platform)
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return set(str(x) for x in data)
        except Exception:
            return set()

    def save_applied_ids(self, platform: str, applied_ids: set[str]) -> None:
        path = self._applied_ids_file(platform)
        path.write_text(json.dumps(sorted(applied_ids), indent=2), encoding="utf-8")

    def _load_storage_state(self, platform: str) -> dict | str | None:
        if self.db and self.candidate_id:
            from app.services.session_store import get_session

            state = get_session(self.db, self.candidate_id, platform)
            if state:
                return state
        path = self._session_file(platform)
        if path.exists():
            return str(path)
        return None

    def save_session(self, context: BrowserContext, platform: str) -> None:
        path = self._session_file(platform)
        try:
            context.storage_state(path=path)
            logger.info("Saved %s session → %s", platform, path)
        except Exception as exc:
            logger.warning("Could not save session file: %s", exc)

        if self.db and self.candidate_id:
            try:
                from app.services.session_store import save_session_from_file

                save_session_from_file(self.db, self.candidate_id, platform, path)
            except Exception as exc:
                logger.warning("Could not save session to DB: %s", exc)

    def _new_context(self, platform: str | None) -> BrowserContext:
        self._ensure_browser()
        kwargs: dict = {"viewport": {"width": 1400, "height": 900}}
        if platform:
            state = self._load_storage_state(platform)
            if state is not None:
                kwargs["storage_state"] = state
        return self._browser.new_context(**kwargs)

    def login_once(self, platform: str, start_url: str) -> None:
        self._ensure_browser()
        context = self._browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.goto(start_url, wait_until="domcontentloaded")
        print(f"\nLog in to {platform} in the browser window.")
        print("(We save session cookies — never your password.)")
        input("Press ENTER here after you are fully logged in...")
        self.save_session(context, platform)
        context.close()

    def apply(self, url: str, profile: dict, resume_path: str) -> ApplyOutcome:
        self._ensure_browser()
        platform = detect_platform(url) or "generic"
        context = self._new_context(platform if platform != "generic" else None)
        page = context.new_page()
        try:
            outcome = apply_for_platform(page, url, profile, resume_path)
            if platform != "generic":
                self.save_session(context, platform)
            return outcome
        finally:
            context.close()

    def linkedin_run_until_stop(
        self,
        profile: dict,
        resume_path: str,
        job_titles: list[str],
        location: str,
        *,
        batch_size: int = 5,
        delay_sec: float = 4.0,
        max_rounds: int | None = None,
        report_path: Path | None = None,
        debug: bool = False,
        stop_check: Callable[[], bool] | None = None,
        max_jobs: int | None = None,
        apply_mode: str | None = "top",
    ) -> list[dict]:
        self._ensure_browser()
        context = self._new_context("linkedin")
        page = context.new_page()

        applied_ids = self.load_applied_ids("linkedin")
        all_results: list[dict] = []
        report_path = report_path or self.session_dir / "linkedin_apply_report.json"
        page_offset = 0
        round_num = 0

        try:
            while True:
                if stop_check and stop_check():
                    break
                if max_jobs is not None and len(all_results) >= max_jobs:
                    break
                round_num += 1
                if max_rounds is not None and round_num > max_rounds:
                    break

                for title in job_titles:
                    if stop_check and stop_check():
                        break
                    if max_jobs is not None and len(all_results) >= max_jobs:
                        break
                    batch = apply_linkedin_from_search(
                        page,
                        title,
                        location,
                        profile,
                        resume_path,
                        limit=batch_size,
                        applied_ids=applied_ids,
                        start_offset=page_offset,
                        delay_sec=delay_sec,
                        debug=debug,
                        stop_check=stop_check,
                        on_result=self._emit,
                    )
                    all_results.extend(batch)
                    self.save_applied_ids("linkedin", applied_ids)
                    self.write_report(all_results, report_path)

                self.save_session(context, "linkedin")
                page_offset += 25

                if max_rounds is not None or (stop_check and stop_check()):
                    break
                if page_offset > 200:
                    page_offset = 0
                time.sleep(2)

        except KeyboardInterrupt:
            pass
        finally:
            self.save_applied_ids("linkedin", applied_ids)
            self.write_report(all_results, report_path)
            try:
                self.save_session(context, "linkedin")
            except Exception:
                pass
            context.close()

        return all_results

    def platform_run_until_stop(
        self,
        platform: str,
        profile: dict,
        resume_path: str,
        job_titles: list[str],
        location: str,
        *,
        batch_size: int = 5,
        delay_sec: float = 4.0,
        max_rounds: int | None = None,
        stop_check: Callable[[], bool] | None = None,
        max_jobs: int | None = None,
        apply_mode: str | None = "top",
    ) -> list[dict]:
        config = BOARD_CONFIGS.get(platform)
        if not config:
            raise ValueError(f"Unsupported platform: {platform}")

        self._ensure_browser()
        context = self._new_context(platform)
        page = context.new_page()
        applied_urls = self.load_applied_ids(platform)
        all_results: list[dict] = []
        page_offset = 0
        round_num = 0

        try:
            while True:
                if stop_check and stop_check():
                    break
                if max_jobs is not None and len(all_results) >= max_jobs:
                    break
                round_num += 1
                if max_rounds is not None and round_num > max_rounds:
                    break

                for title in job_titles:
                    if stop_check and stop_check():
                        break
                    if max_jobs is not None and len(all_results) >= max_jobs:
                        break
                    batch = board_apply_from_search(
                        page,
                        config,
                        title,
                        location,
                        profile,
                        resume_path,
                        limit=batch_size,
                        applied_urls=applied_urls,
                        start_offset=page_offset,
                        delay_sec=delay_sec,
                        stop_check=stop_check,
                        on_result=self._emit,
                    )
                    all_results.extend(batch)

                self.save_applied_ids(platform, applied_urls)
                self.save_session(context, platform)
                page_offset += 25

                if max_rounds is not None or (stop_check and stop_check()):
                    break
                if page_offset > 200:
                    page_offset = 0
                time.sleep(2)
        finally:
            self.save_applied_ids(platform, applied_urls)
            try:
                self.save_session(context, platform)
            except Exception:
                pass
            context.close()

        return all_results

    def search_and_apply(
        self,
        platform: str,
        profile: dict,
        resume_path: str,
        job_titles: list[str],
        location: str,
        *,
        limit_per_title: int = 3,
        delay_sec: float = 4.0,
        max_jobs: int | None = None,
        apply_mode: str | None = "top",
    ) -> list[dict]:
        if platform == "linkedin":
            return self.linkedin_run_until_stop(
                profile,
                resume_path,
                job_titles,
                location,
                batch_size=limit_per_title,
                delay_sec=delay_sec,
                max_rounds=1,
                max_jobs=max_jobs,
                apply_mode=apply_mode,
            )
        if platform in BOARD_CONFIGS:
            return self.platform_run_until_stop(
                platform,
                profile,
                resume_path,
                job_titles,
                location,
                batch_size=limit_per_title,
                delay_sec=delay_sec,
                max_rounds=1,
                max_jobs=max_jobs,
                apply_mode=apply_mode,
            )

        self._ensure_browser()
        context = self._new_context(platform)
        page = context.new_page()
        collected: list[dict] = []
        seen_urls: set[str] = set()
        try:
            for title in job_titles:
                batch = search_jobs_on_platform(page, platform, title, location, limit_per_title)
                for job in batch:
                    url = job.get("apply_url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        collected.append(job)
        finally:
            self.save_session(context, platform)
            context.close()

        if not collected:
            return []
        return self.apply_many(collected, profile, resume_path, delay_sec=delay_sec)

    def apply_many(
        self,
        jobs: list[dict],
        profile: dict,
        resume_path: str,
        *,
        delay_sec: float = 3.0,
    ) -> list[dict]:
        results: list[dict] = []
        for i, job in enumerate(jobs, start=1):
            url = job.get("apply_url") or job.get("url", "")
            title = job.get("title", "?")
            company = job.get("company", "?")
            if not url:
                row = {"title": title, "company": company, "status": "skipped", "message": "No apply URL"}
                results.append(row)
                self._emit(row)
                continue

            try:
                outcome = self.apply(url, profile, resume_path)
                row = {
                    "title": title,
                    "company": company,
                    "url": url,
                    "platform": outcome.platform,
                    "status": outcome.status,
                    "message": outcome.message,
                    "job_id": job.get("job_id"),
                }
                results.append(row)
                self._emit(row)
            except Exception as exc:
                row = {
                    "title": title,
                    "company": company,
                    "url": url,
                    "status": "failed",
                    "message": str(exc),
                    "job_id": job.get("job_id"),
                }
                results.append(row)
                self._emit(row)

            if i < len(jobs):
                time.sleep(delay_sec)

        return results

    @staticmethod
    def write_report(results: list[dict], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
