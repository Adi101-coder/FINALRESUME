import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.models import Candidate
from app.database.session import get_db
from app.services.auto_apply_config import (
    config_to_dict,
    get_or_create_config,
    save_auto_apply_resume,
)
from app.services.session_store import list_sessions, save_session

logger = logging.getLogger("app.auto_apply")

router = APIRouter(prefix="/auto-apply", tags=["auto-apply"])


class AutoApplyConfigUpdate(BaseModel):
    job_titles: list[str] = Field(default_factory=lambda: ["Software Developer", "Frontend Developer"])
    location: str = "Remote"
    remote_type: str | None = None


@router.get("/{candidate_id}/config")
def get_config(candidate_id: int, db: Session = Depends(get_db)):
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    config = get_or_create_config(db, candidate_id)
    sessions = list_sessions(db, candidate_id)
    return {
        **config_to_dict(config, candidate_id),
        "sessions": sessions,
        "run_command": (
            f"python scripts/auto_apply.py search-apply --candidate-id {candidate_id} --limit 5"
        ),
    }


@router.put("/{candidate_id}/config")
def update_config(candidate_id: int, payload: AutoApplyConfigUpdate, db: Session = Depends(get_db)):
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    config = get_or_create_config(db, candidate_id)
    config.job_titles = [t.strip() for t in payload.job_titles if t.strip()]
    config.location = payload.location.strip() or "Remote"
    if payload.remote_type is not None:
        config.remote_type = payload.remote_type.strip()
    db.commit()
    db.refresh(config)
    return config_to_dict(config, candidate_id)


@router.post("/{candidate_id}/resume")
async def upload_auto_apply_resume(
    candidate_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are supported.")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Resume must be under 10 MB.")

    path = save_auto_apply_resume(db, candidate_id, content, file.filename)
    logger.info("Auto-apply resume uploaded: candidate_id=%s", candidate_id)
    return {"candidate_id": candidate_id, "resume_path": path, "filename": file.filename}


@router.get("/{candidate_id}/sessions")
def get_sessions(candidate_id: int, db: Session = Depends(get_db)):
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return {"candidate_id": candidate_id, "sessions": list_sessions(db, candidate_id)}


class SessionUpload(BaseModel):
    storage_state: dict


@router.put("/{candidate_id}/sessions/{platform}")
def upload_session(candidate_id: int, platform: str, payload: SessionUpload, db: Session = Depends(get_db)):
    """Save a Playwright storage_state (e.g. LinkedIn cookies captured on the
    local machine) so a headless/cloud deployment can reuse the login."""
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    state = payload.storage_state
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not state["cookies"]:
        raise HTTPException(
            status_code=422,
            detail="storage_state must be a Playwright storage_state dict with a non-empty cookies list.",
        )
    save_session(db, candidate_id, platform, state)
    logger.info("Session uploaded: candidate_id=%s platform=%s (%d cookies)", candidate_id, platform, len(state["cookies"]))
    return {"candidate_id": candidate_id, "platform": platform, "saved": True, "cookies": len(state["cookies"])}
