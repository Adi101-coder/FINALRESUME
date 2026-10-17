"""Jobs already listed to the candidate — prefer unseen next time; unapplied may return."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.database.models import ShownJob
from app.services.applied_jobs import job_skip_keys


def unseen_first(items: list[tuple[set[str], object]], shown: set[str]) -> list:
    """Put never-shown items first; previously shown (unapplied) stay available at the end."""
    new: list = []
    again: list = []
    for keys, item in items:
        if keys & shown:
            again.append(item)
        else:
            new.append(item)
    return new + again


def shown_skip_keys(db: Session, candidate_id: int) -> set[str]:
    rows = db.query(ShownJob.skip_key).filter(ShownJob.candidate_id == candidate_id).all()
    return {row[0] for row in rows if row[0]}


def mark_jobs_shown(
    db: Session,
    candidate_id: int,
    jobs: list,
) -> int:
    """Record that these jobs were displayed. Does not hide unapplied jobs forever."""
    added = 0
    existing = shown_skip_keys(db, candidate_id)
    for job in jobs:
        apply_url = getattr(job, "apply_url", "") or ""
        job_id = getattr(job, "id", None)
        keys = job_skip_keys(apply_url=apply_url, db_job_id=job_id)
        if not keys:
            continue
        skip_key = next(iter(sorted(keys)))
        if skip_key in existing or any(k in existing for k in keys):
            continue
        db.add(
            ShownJob(
                candidate_id=candidate_id,
                job_id=job_id,
                apply_url=apply_url,
                skip_key=skip_key,
                shown_at=datetime.utcnow(),
            )
        )
        existing.add(skip_key)
        existing |= keys
        added += 1
    if added:
        db.commit()
    return added
