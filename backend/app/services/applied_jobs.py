"""Track jobs already applied to so the bot never duplicates applications."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.database.models import Application, Job

logger = logging.getLogger("app.applied_jobs")

APPLIED_STATUSES = frozenset({"applied"})


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
