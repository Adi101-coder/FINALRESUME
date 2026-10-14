"""Track jobs already applied to so the bot never duplicates applications."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.models import Application, AppliedJob, Job

logger = logging.getLogger("app.applied_jobs")

APPLIED_STATUSES = frozenset({"applied", "submitted"})


def linkedin_job_id_from_url(url: str) -> str | None:
    match = re.search(r"/jobs/view/(\d+)", url or "")
    return match.group(1) if match else None


def normalize_apply_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    lid = linkedin_job_id_from_url(raw)
    if lid:
        return f"https://www.linkedin.com/jobs/view/{lid}/"
    parsed = urlparse(raw)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def job_skip_keys(
    *,
    apply_url: str = "",
    linkedin_job_id: str | None = None,
    db_job_id: int | None = None,
) -> set[str]:
    keys: set[str] = set()
    if linkedin_job_id:
        keys.add(str(linkedin_job_id))
    norm = normalize_apply_url(apply_url)
    if norm:
        keys.add(f"url:{norm}")
    if db_job_id is not None:
        keys.add(f"dbjob:{db_job_id}")
    return keys


def _ledger_path(candidate_id: int) -> Path:
    from app.config import settings

    folder = settings.storage_path / "applied_jobs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"candidate_{candidate_id}.json"


def _write_verify_file(db: Session, candidate_id: int) -> None:
    """Human-readable local dump so you can open the file and confirm applies."""
    import json

    rows = (
        db.query(AppliedJob)
        .filter(AppliedJob.candidate_id == candidate_id)
        .order_by(AppliedJob.applied_at.desc())
        .all()
    )
    payload = [
        {
            "id": r.id,
            "title": r.title,
            "company": r.company,
            "location": r.location,
            "apply_url": r.apply_url,
            "linkedin_job_id": r.linkedin_job_id,
            "platform": r.platform,
            "applied_at": r.applied_at.isoformat() if r.applied_at else None,
            "notes": r.notes,
        }
        for r in rows
    ]
    path = _ledger_path(candidate_id)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def record_applied_job(
    db: Session,
    candidate_id: int,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    apply_url: str = "",
    job_id: int | None = None,
    platform: str = "linkedin",
    notes: str = "",
) -> AppliedJob | None:
    """Upsert one applied job into the local SQLite ledger."""
    lid = linkedin_job_id_from_url(apply_url) or ""
    norm = normalize_apply_url(apply_url)
    skip_key = lid or (f"url:{norm}" if norm else (f"dbjob:{job_id}" if job_id else ""))
    if not skip_key:
        return None

    existing = (
        db.query(AppliedJob)
        .filter(AppliedJob.candidate_id == candidate_id, AppliedJob.skip_key == skip_key)
        .first()
    )
    if existing:
        if notes and notes != existing.notes:
            existing.notes = notes[:2000]
            db.commit()
        return existing

    row = AppliedJob(
        candidate_id=candidate_id,
        job_id=job_id,
        title=title or "",
        company=company or "",
        location=location or "",
        apply_url=norm or apply_url or "",
        linkedin_job_id=lid,
        platform=platform or "linkedin",
        skip_key=skip_key,
        notes=(notes or "")[:2000],
        applied_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        _write_verify_file(db, candidate_id)
    except Exception:
        logger.exception("Could not write applied-jobs verify file")
    logger.info(
        "Applied job stored locally: candidate_id=%s skip_key=%s %s @ %s",
        candidate_id,
        skip_key,
        title,
        company,
    )
    return row


def backfill_applied_ledger(db: Session, candidate_id: int | None = None) -> int:
    """Copy existing application rows into applied_jobs if the ledger is empty for them."""
    q = (
        db.query(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .filter(Application.status.in_(APPLIED_STATUSES))
    )
    if candidate_id is not None:
        q = q.filter(Application.candidate_id == candidate_id)
    added = 0
    for app, job in q.all():
        before = db.query(AppliedJob).filter(AppliedJob.candidate_id == app.candidate_id).count()
        record_applied_job(
            db,
            app.candidate_id,
            title=job.title,
            company=job.company,
            location=job.location or "",
            apply_url=job.apply_url or "",
            job_id=job.id,
            platform=job.source or "linkedin",
            notes=app.notes or "",
        )
        after = db.query(AppliedJob).filter(AppliedJob.candidate_id == app.candidate_id).count()
        if after > before:
            added += 1
    return added


def list_applied_jobs(db: Session, candidate_id: int) -> list[AppliedJob]:
    backfill_applied_ledger(db, candidate_id)
    return (
        db.query(AppliedJob)
        .filter(AppliedJob.candidate_id == candidate_id)
        .order_by(AppliedJob.applied_at.desc())
        .all()
    )


def file_applied_keys(candidate_id: int, platform: str = "linkedin") -> set[str]:
    """Skip keys persisted by the bot (survives if a DB write was missed)."""
    try:
        from app.config import settings

        path = settings.storage_path / "browser_sessions" / f"{platform}_applied_{candidate_id}.json"
        if not path.exists():
            return set()
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(x) for x in data}
    except Exception:
        return set()


def applied_skip_keys(db: Session, candidate_id: int, platform: str | None = None) -> set[str]:
    """All skip keys for jobs this candidate has successfully applied to."""
    keys: set[str] = set()
    q = (
        db.query(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .filter(
            Application.candidate_id == candidate_id,
            Application.status.in_(APPLIED_STATUSES),
        )
    )
    if platform:
        q = q.filter(Job.source == platform)
    for _app, job in q.all():
        keys.update(
            job_skip_keys(
                apply_url=job.apply_url or "",
                linkedin_job_id=linkedin_job_id_from_url(job.apply_url or ""),
                db_job_id=job.id,
            )
        )
    ledger = db.query(AppliedJob).filter(AppliedJob.candidate_id == candidate_id)
    if platform:
        ledger = ledger.filter(AppliedJob.platform == platform)
    for row in ledger.all():
        keys.update(
            job_skip_keys(
                apply_url=row.apply_url or "",
                linkedin_job_id=row.linkedin_job_id or None,
                db_job_id=row.job_id,
            )
        )
        if row.skip_key:
            keys.add(row.skip_key)
    keys |= file_applied_keys(candidate_id, platform or "linkedin")
    if keys:
        logger.debug("Loaded %d applied skip keys for candidate_id=%s", len(keys), candidate_id)
    return keys


def should_skip_applied(
    db: Session,
    candidate_id: int,
    *,
    apply_url: str = "",
    linkedin_job_id: str | None = None,
    db_job_id: int | None = None,
    extra_keys: set[str] | None = None,
) -> bool:
    keys = job_skip_keys(
        apply_url=apply_url,
        linkedin_job_id=linkedin_job_id or linkedin_job_id_from_url(apply_url),
        db_job_id=db_job_id,
    )
    if extra_keys:
        keys |= extra_keys
    if not keys:
        return False
    skip = applied_skip_keys(db, candidate_id)
    if keys & skip:
        return True
    return False


def merge_applied_sets(file_ids: set[str], db: Session | None, candidate_id: int | None, platform: str) -> set[str]:
    """Merge JSON file history with durable DB applied records."""
    merged = set(file_ids)
    if db is not None and candidate_id is not None:
        merged |= applied_skip_keys(db, candidate_id, platform)
    return merged
