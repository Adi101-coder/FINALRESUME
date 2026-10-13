"""Auto-apply resume folder + job title preferences per candidate."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import AutoApplyConfig, Resume

logger = logging.getLogger("app.auto_apply_config")

DEFAULT_JOB_TITLES = ["Software Developer", "Frontend Developer"]


def auto_apply_dir(candidate_id: int) -> Path:
    path = settings.auto_apply_resumes_path / str(candidate_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resume_path_for(candidate_id: int) -> Path:
    return auto_apply_dir(candidate_id) / "resume.pdf"


def get_or_create_config(db: Session, candidate_id: int) -> AutoApplyConfig:
    row = db.get(AutoApplyConfig, candidate_id)
    if row:
        return row
    row = AutoApplyConfig(
        candidate_id=candidate_id,
        job_titles=DEFAULT_JOB_TITLES.copy(),
        location="Remote",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _named_resume(*directories: Path) -> Path | None:
    """Find Adit_Katiyar.pdf (any casing) in the given folders."""
    wanted = "adit_katiyar.pdf"
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.name.lower() == wanted:
                return path.resolve()
    return None


def resolve_resume_path(db: Session, candidate_id: int) -> str:
    """Use the resume parsed at the start, then fall back to named copies."""
    original = (
        db.query(Resume)
        .filter(Resume.candidate_id == candidate_id, Resume.version_label == "original")
        .order_by(Resume.created_at.asc())
        .first()
    )
    if original and Path(original.file_path).exists():
        return str(Path(original.file_path).resolve())

    named = _named_resume(settings.storage_path / "resumes", settings.storage_path)
    if named:
        return str(named)

    dedicated = resume_path_for(candidate_id)
    if dedicated.exists():
        return str(dedicated.resolve())

    downloads = _named_resume(Path.home() / "Downloads")
    if downloads:
        return str(downloads)

    config = db.get(AutoApplyConfig, candidate_id)
    if config and config.resume_path and Path(config.resume_path).exists():
        return config.resume_path

    auto_resume = (
        db.query(Resume)
        .filter(Resume.candidate_id == candidate_id, Resume.version_label == "auto_apply")
        .order_by(Resume.created_at.desc())
        .first()
    )
    if auto_resume and Path(auto_resume.file_path).exists():
        return auto_resume.file_path

    raise FileNotFoundError(
        f"No resume for candidate {candidate_id}. Upload via Auto-Apply section or main resume upload."
    )


def save_auto_apply_resume(db: Session, candidate_id: int, content: bytes, filename: str) -> str:
    dest = resume_path_for(candidate_id)
    dest.write_bytes(content)

    config = get_or_create_config(db, candidate_id)
    config.resume_path = str(dest.resolve())
    config.resume_filename = filename
    config.updated_at = datetime.utcnow()

    existing = (
        db.query(Resume)
        .filter(Resume.candidate_id == candidate_id, Resume.version_label == "auto_apply")
        .first()
    )
    if existing:
        existing.original_filename = filename
        existing.file_path = str(dest.resolve())
    else:
        db.add(
            Resume(
                candidate_id=candidate_id,
                original_filename=filename,
                file_path=str(dest.resolve()),
                profile_json={},
                version_label="auto_apply",
            )
        )
    db.commit()
    logger.info("Auto-apply resume saved: candidate_id=%s path=%s", candidate_id, dest)
    return str(dest.resolve())


def config_to_dict(config: AutoApplyConfig, candidate_id: int) -> dict:
    path = resume_path_for(candidate_id)
    return {
        "candidate_id": candidate_id,
        "job_titles": config.job_titles or DEFAULT_JOB_TITLES,
        "location": config.location or "Remote",
        "remote_type": config.remote_type or "",
        "resume_uploaded": path.exists(),
        "resume_filename": config.resume_filename or (path.name if path.exists() else ""),
        "resume_path": str(path) if path.exists() else config.resume_path,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }
