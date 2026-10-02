"""Persist bot apply results to jobs + applications tables."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.database.models import Application, Job, Resume
from app.services.deduplication import job_dedup_hash

logger = logging.getLogger("app.bot_sync")

STATUS_MAP = {
    "submitted": "applied",
    "review_required": "pending",
    "unsupported": "skipped",
    "failed": "failed",
    "skipped": "skipped",
}


def _find_job(db: Session, *, title: str, company: str, apply_url: str, location: str = "") -> Job | None:
    if apply_url:
        row = db.query(Job).filter(Job.apply_url == apply_url).first()
        if row:
            return row
    dedup = job_dedup_hash(company or "unknown", title or "unknown", location or "")
    return db.query(Job).filter(Job.dedup_hash == dedup).first()


def ensure_job(
    db: Session,
    *,
    title: str,
    company: str,
    apply_url: str,
    source: str,
    location: str = "",
    job_id: int | None = None,
) -> Job:
    if job_id:
        existing = db.get(Job, job_id)
        if existing:
            return existing

    found = _find_job(db, title=title, company=company, apply_url=apply_url, location=location)
    if found:
        return found

    dedup = job_dedup_hash(company or "unknown", title or "unknown", location or "")
    job = Job(
        title=title or "Unknown role",
        company=company or "Unknown",
        location=location or "",
        apply_url=apply_url or "",
        source=source,
        application_type="easy_apply" if source == "linkedin" else "external",
        easy_apply=source == "linkedin",
        dedup_hash=dedup,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("Created job id=%s for bot apply: %s @ %s", job.id, title, company)
    return job


def _auto_apply_resume_id(db: Session, candidate_id: int) -> int | None:
    row = (
        db.query(Resume)
        .filter(Resume.candidate_id == candidate_id, Resume.version_label == "auto_apply")
        .order_by(Resume.created_at.desc())
        .first()
    )
    return row.id if row else None


def record_bot_result(
    db: Session,
    *,
    candidate_id: int,
    result: dict,
    job_id: int | None = None,
) -> Application | None:
    """Upsert job + application from a bot apply result dict."""
    bot_status = result.get("status", "failed")
    app_status = STATUS_MAP.get(bot_status, "pending")
    title = result.get("title") or "Unknown"
    company = result.get("company") or ""
    apply_url = result.get("url") or result.get("apply_url") or ""
    platform = result.get("platform") or "bot"
    message = result.get("message") or ""

    job = ensure_job(
        db,
        title=title,
        company=company,
        apply_url=apply_url,
        source=platform,
        job_id=job_id,
    )

    existing = (
        db.query(Application)
        .filter(Application.candidate_id == candidate_id, Application.job_id == job.id)
        .first()
    )
    resume_id = _auto_apply_resume_id(db, candidate_id)
    notes = f"[bot:{platform}] {bot_status} — {message}"[:2000]

    if existing:
        if existing.status != "applied":
            existing.status = app_status
        existing.notes = notes
        if app_status == "applied" and not existing.applied_at:
            existing.applied_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    application = Application(
        candidate_id=candidate_id,
        job_id=job.id,
        resume_id=resume_id,
        status=app_status,
        notes=notes,
        applied_at=datetime.utcnow() if app_status == "applied" else None,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    logger.info(
        "Bot application recorded: app_id=%s job_id=%s status=%s",
        application.id,
        job.id,
        app_status,
    )
    return application
