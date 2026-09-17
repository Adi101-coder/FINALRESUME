"""In-process Playwright bot runner — local backend only."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

from app.database.session import SessionLocal
from app.services.auto_apply_config import get_or_create_config, resolve_resume_path
from app.services.bot_application_sync import record_bot_result
from app.services.session_store import export_session_to_file, PLATFORMS

logger = logging.getLogger("app.bot_runner")

ApplyCallback = Callable[[dict], None]


class BotState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class BotEvent:
    ts: str
    level: str
    message: str
    result: dict | None = None

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "level": self.level,
            "message": self.message,
            "result": self.result,
        }


@dataclass
class BotRun:
    run_id: str
    candidate_id: int
    platform: str
    state: BotState = BotState.IDLE
    started_at: str | None = None
    stopped_at: str | None = None
    submitted: int = 0
    failed: int = 0
    skipped: int = 0
    pending: int = 0
    events: list[BotEvent] = field(default_factory=list)
    stop_requested: bool = False
    error: str | None = None
    _thread: threading.Thread | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "platform": self.platform,
            "state": self.state.value,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "submitted": self.submitted,
            "failed": self.failed,
            "skipped": self.skipped,
            "pending": self.pending,
            "error": self.error,
            "recent_events": [e.to_dict() for e in self.events[-30:]],
        }


class BotRunnerService:
    """Manages background auto-apply threads (one active run per candidate)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[int, BotRun] = {}
        self._session_dir = Path("storage/browser_sessions")

    def _playwright_available(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401

            return True
        except ImportError:
            return False

    def availability(self) -> dict:
        return {
            "available": self._playwright_available(),
            "message": (
                "Bot runner ready (local Playwright)."
                if self._playwright_available()
                else "Install Playwright in backend venv."
            ),
            "platforms": list(PLATFORMS),
        }

    def get_status(self, candidate_id: int) -> dict:
        with self._lock:
            run = self._runs.get(candidate_id)
            if not run:
                return {
                    "candidate_id": candidate_id,
                    "state": BotState.IDLE.value,
                    "available": self._playwright_available(),
                }
            return {"candidate_id": candidate_id, **run.to_dict(), "available": self._playwright_available()}

    def _log(self, run: BotRun, message: str, *, level: str = "info", result: dict | None = None) -> None:
        event = BotEvent(ts=datetime.utcnow().isoformat(), level=level, message=message, result=result)
        with self._lock:
            run.events.append(event)
            if len(run.events) > 200:
                run.events = run.events[-200:]
        logger.info("[bot:%s] %s", run.candidate_id, message)

    def _sync_result(self, run: BotRun, result: dict, job_id: int | None = None) -> None:
        db = SessionLocal()
        try:
            record_bot_result(db, candidate_id=run.candidate_id, result=result, job_id=job_id)
        except Exception as exc:
            logger.exception("Failed to sync bot result to DB")
            self._log(run, f"DB sync failed: {exc}", level="error")
        finally:
            db.close()

        status = result.get("status", "")
        with self._lock:
            if status == "submitted":
                run.submitted += 1
            elif status in ("unsupported", "skipped"):
                run.skipped += 1
            elif status == "review_required":
                run.pending += 1
            else:
                run.failed += 1

    def stop(self, candidate_id: int) -> dict:
        with self._lock:
            run = self._runs.get(candidate_id)
            if not run or run.state != BotState.RUNNING:
                return self.get_status(candidate_id)
            run.stop_requested = True
            run.state = BotState.STOPPING
        self._log(run, "Stop requested — finishing current job…")
        return self.get_status(candidate_id)

    LOGIN_URLS = {
        "linkedin": "https://www.linkedin.com/login",
        "indeed": "https://secure.indeed.com/auth/login",
        "foundit": "https://www.foundit.in/",
        "naukri": "https://www.naukri.com/nlogin/login",
        "ziprecruiter": "https://www.ziprecruiter.com/auth/login",
        "glassdoor": "https://www.glassdoor.com/profile/login_input.htm",
    }

    def start_login(self, candidate_id: int, *, platform: str) -> dict:
        """Open the platform login page so a new user can log in from the website.

        The run ends when the human presses 'Login done' (or login is detected);
        the session cookies are then saved for this candidate's future applies.
        """
        if not self._playwright_available():
            raise RuntimeError("Playwright not installed. Run: playwright install chromium")

        if platform not in self.LOGIN_URLS:
            raise RuntimeError(f"Unsupported platform: {platform}")

        with self._lock:
            existing = self._runs.get(candidate_id)
            if existing and existing.state == BotState.RUNNING:
                raise RuntimeError("Bot already running for this candidate. Stop it first.")

            run = BotRun(
                run_id=str(uuid.uuid4())[:8],
                candidate_id=candidate_id,
                platform=platform,
                state=BotState.RUNNING,
                started_at=datetime.utcnow().isoformat(),
            )
            self._runs[candidate_id] = run

        thread = threading.Thread(
            target=self._run_login,
            args=(run, platform),
            daemon=True,
            name=f"bot-login-{candidate_id}",
        )
        run._thread = thread
        thread.start()
        return self.get_status(candidate_id)

    @staticmethod
    def _looks_logged_in(page, platform: str) -> bool:
        try:
            url = (page.url or "").lower()
            body = (page.inner_text("body") or "").lower()
        except Exception:
            return False
        if platform == "linkedin":
            return "/feed" in url or "sign out" in body
        if platform == "indeed":
            return "sign out" in body or "/profile" in url or "my jobs" in body
        if platform == "naukri":
            return "/mnjuser/" in url or "my naukri" in body
        if platform == "foundit":
            return "sign out" in body or "logout" in body
        return "sign out" in body or "log out" in body

    def _run_login(self, run: BotRun, platform: str) -> None:
        from playwright.sync_api import sync_playwright

        from app.agents.browser.captcha import human_signaled
        from app.agents.browser.runner import open_system_chrome_context

        login_url = self.LOGIN_URLS[platform]
        self._log(
            run,
            f"Opening your Chrome on {platform} (reuses your existing logins).",
        )
        try:
            with sync_playwright() as pw:
                try:
                    context = open_system_chrome_context(pw)
                    self._log(run, "Your Chrome profile opened — checking login status…")
                except RuntimeError as exc:
                    # Fallback: fresh window with manual login (old behavior).
                    self._log(run, f"{exc} Opening a separate login window instead.", level="warning")
                    browser = pw.chromium.launch(
                        headless=False,
                        slow_mo=80,
                        ignore_default_args=["--enable-automation"],
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                    context = browser.new_context(viewport={"width": 1400, "height": 900})
                    context._owned_browser = browser  # type: ignore[attr-defined]
                try:
                    pages = context.pages
                    page = pages[0] if pages else context.new_page()
                    try:
                        page.goto(login_url, wait_until="domcontentloaded", timeout=120_000)
                    except Exception:
                        pass
                    deadline = time.time() + 300
                    logged_in = self._looks_logged_in(page, platform)
                    if logged_in:
                        self._log(run, f"Already logged in to {platform} — saving session.")
                    else:
                        self._log(
                            run,
                            f"Log in to {platform} in the opened Chrome window, "
                            "then press 'Login done' here.",
                        )
                    while not logged_in and time.time() < deadline:
                        if run.stop_requested:
                            self._log(run, "Login cancelled.")
                            break
                        if human_signaled():
                            logged_in = True
                            break
                        if self._looks_logged_in(page, platform):
                            logged_in = True
                            self._log(run, f"{platform} login detected.")
                            break
                        time.sleep(3)
                    if not logged_in and not run.stop_requested:
                        raise TimeoutError("Login timed out after 5 minutes.")

                    if logged_in:
                        path = self._session_dir / f"{platform}_session.json"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        context.storage_state(path=path)
                        db = SessionLocal()
                        try:
                            from app.services.session_store import save_session_from_file

                            save_session_from_file(db, run.candidate_id, platform, path)
                        finally:
                            db.close()
                        with self._lock:
                            run.submitted += 1
                        self._log(run, f"{platform} session saved. You can now auto-apply.")
                    try:
                        context.close()
                    except Exception:
                        pass
                    owned = getattr(context, "_owned_browser", None)
                    if owned is not None:
                        try:
                            owned.close()
                        except Exception:
                            pass
                except Exception:
                    try:
                        context.close()
                    except Exception:
                        pass
                    owned = getattr(context, "_owned_browser", None)
                    if owned is not None:
                        try:
                            owned.close()
                        except Exception:
                            pass
                    raise

            with self._lock:
                run.state = BotState.IDLE
                run.stopped_at = datetime.utcnow().isoformat()
        except Exception as exc:
            logger.exception("Bot login failed")
            with self._lock:
                run.state = BotState.ERROR
                run.error = str(exc)
                run.stopped_at = datetime.utcnow().isoformat()
            self._log(run, f"Login error: {exc}", level="error")

    def start(
        self,
        candidate_id: int,
        *,
        platform: str = "linkedin",
        continuous: bool = True,
        batch_size: int = 5,
        delay_sec: float = 5.0,
        job_url: str | None = None,
        job_id: int | None = None,
        job_title: str | None = None,
        job_company: str | None = None,
    ) -> dict:
        if not self._playwright_available():
            raise RuntimeError("Playwright not installed. Run: playwright install chromium")

        if platform not in ("linkedin", "indeed", "foundit", "naukri", "ziprecruiter", "glassdoor"):
            raise RuntimeError(f"Unsupported platform: {platform}")

        with self._lock:
            existing = self._runs.get(candidate_id)
            if existing and existing.state == BotState.RUNNING:
                raise RuntimeError("Bot already running for this candidate. Stop it first.")

            run = BotRun(
                run_id=str(uuid.uuid4())[:8],
                candidate_id=candidate_id,
                platform=platform,
                state=BotState.RUNNING,
                started_at=datetime.utcnow().isoformat(),
            )
            self._runs[candidate_id] = run

        thread = threading.Thread(
            target=self._run,
            args=(run, platform, continuous, batch_size, delay_sec, job_url, job_id, job_title, job_company),
            daemon=True,
            name=f"bot-{candidate_id}",
        )
        run._thread = thread
        thread.start()
        return self.get_status(candidate_id)

    def _check_session_valid(self, candidate_id: int, platform: str) -> bool:
        """Check if a valid session exists for the platform."""
        session_file = self._session_dir / f"{platform}_session.json"
        if session_file.exists():
            return True
        db = SessionLocal()
        try:
            from app.services.session_store import get_session
            state = get_session(db, candidate_id, platform)
            return state is not None
        except Exception:
            return False
        finally:
            db.close()

    def _prepare_session(self, candidate_id: int) -> None:
        db = SessionLocal()
        try:
            for p in PLATFORMS:
                export_session_to_file(db, candidate_id, p, self._session_dir / f"{p}_session.json")
        finally:
            db.close()

    def _run(
        self,
        run: BotRun,
        platform: str,
        continuous: bool,
        batch_size: int,
        delay_sec: float,
        job_url: str | None,
        job_id: int | None,
        job_title: str | None,
        job_company: str | None,
    ) -> None:
        from app.agents.browser.runner import AutoApplyRunner
        from app.database.models import Candidate

        db = SessionLocal()
        try:
            candidate = db.get(Candidate, run.candidate_id)
            if not candidate:
                raise RuntimeError(f"Candidate {run.candidate_id} not found")

            profile = candidate.profile_json or {}
            resume_path = resolve_resume_path(db, run.candidate_id)
            config = get_or_create_config(db, run.candidate_id)
            job_titles = config.job_titles or ["Software Developer", "Frontend Developer"]
            location = config.location or profile.get("location") or "Remote"
        finally:
            db.close()

        self._prepare_session(run.candidate_id)

        if not self._check_session_valid(run.candidate_id, platform):
            self._log(
                run,
                f"No valid {platform} session found. The bot will open the browser for login. "
                "Complete login in the browser window, then the bot will resume.",
                level="warning",
            )

        self._log(run, f"Starting {platform} bot (continuous={continuous})")

        def on_result(result: dict) -> None:
            if job_id and not result.get("job_id"):
                result = {**result, "job_id": job_id}
            self._sync_result(run, result, job_id=job_id)
            self._log(
                run,
                f"{result.get('status')}: {result.get('title')} @ {result.get('company', '?')}",
                result=result,
            )

        try:
            with AutoApplyRunner(
                session_dir=self._session_dir,
                headless=False,
                slow_mo_ms=80,
                candidate_id=run.candidate_id,
                db=SessionLocal(),
            ) as runner:
                runner.on_apply_result = on_result

                if job_url:
                    self._log(run, f"Single apply: {job_url}")
                    job = {
                        "title": job_title or "Job",
                        "company": job_company or "",
                        "apply_url": job_url,
                        "job_id": job_id,
                    }
                    runner.apply_many([job], profile, resume_path, delay_sec=delay_sec)
                elif platform == "linkedin":
                    runner.linkedin_run_until_stop(
                        profile,
                        resume_path,
                        job_titles,
                        location,
                        batch_size=batch_size,
                        delay_sec=delay_sec,
                        max_rounds=None if continuous else 1,
                        debug=True,
                        stop_check=lambda: run.stop_requested,
                    )
                else:
                    runner.platform_run_until_stop(
                        platform,
                        profile,
                        resume_path,
                        job_titles,
                        location,
                        batch_size=batch_size,
                        delay_sec=delay_sec,
                        max_rounds=None if continuous else 1,
                        stop_check=lambda: run.stop_requested,
                    )

            with self._lock:
                run.state = BotState.IDLE
                run.stopped_at = datetime.utcnow().isoformat()
            self._log(run, "Bot finished.")

        except Exception as exc:
            logger.exception("Bot run failed")
            with self._lock:
                run.state = BotState.ERROR
                run.error = str(exc)
                run.stopped_at = datetime.utcnow().isoformat()
            self._log(run, f"Bot error: {exc}", level="error")
        finally:
            db = SessionLocal()
            try:
                from app.services.session_store import save_session_from_file

                for p in PLATFORMS:
                    path = self._session_dir / f"{p}_session.json"
                    if path.exists():
                        save_session_from_file(db, run.candidate_id, p, path)
            except Exception:
                pass
            finally:
                db.close()


bot_runner = BotRunnerService()
