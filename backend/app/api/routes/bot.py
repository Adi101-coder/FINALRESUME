"""Bot runner API — start/stop auto-apply from the website (local backend)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.bot_runner import bot_runner

logger = logging.getLogger("app.bot")

router = APIRouter(prefix="/bot", tags=["bot"])


class BotStartRequest(BaseModel):
    candidate_id: int
    platform: str = "linkedin"
    continuous: bool = True
    batch_size: int = Field(default=5, ge=1, le=20)
    delay_sec: float = Field(default=5.0, ge=1.0, le=30.0)
    job_url: str | None = None
    job_id: int | None = None
    job_title: str | None = None
    job_company: str | None = None
    max_jobs: int | None = Field(default=None, ge=1, le=100, description="Maximum jobs to apply to in continuous mode")


class BotLoginRequest(BaseModel):
    candidate_id: int
    platform: str = "linkedin"


@router.get("/availability")
def bot_availability():
    return bot_runner.availability()


@router.get("/status/{candidate_id}")
def bot_status(candidate_id: int):
    return bot_runner.get_status(candidate_id)


@router.post("/start")
def bot_start(payload: BotStartRequest):
    try:
        return bot_runner.start(
            payload.candidate_id,
            platform=payload.platform,
            continuous=payload.continuous,
            batch_size=payload.batch_size,
            delay_sec=payload.delay_sec,
            job_url=payload.job_url,
            job_id=payload.job_id,
            job_title=payload.job_title,
            job_company=payload.job_company,
            max_jobs=payload.max_jobs,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Bot start failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/stop/{candidate_id}")
def bot_stop(candidate_id: int):
    return bot_runner.stop(candidate_id)


@router.post("/login")
def bot_login(payload: BotLoginRequest):
    """Open the platform login page for a fresh user; saves session on 'Login done'."""
    try:
        return bot_runner.start_login(payload.candidate_id, platform=payload.platform)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Bot login failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/human-ready")
def signal_human_ready():
    """Signal that the human has completed CAPTCHA/login in the browser."""
    from app.agents.browser.captcha import signal_human_ready as _signal
    _signal()
    return {"status": "ok", "message": "Human action signal sent. Bot will resume."}
