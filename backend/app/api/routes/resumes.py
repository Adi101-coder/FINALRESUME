from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.resume.parser import ResumeParser
from app.database.models import Candidate, Resume
from app.database.session import get_db
from app.schemas.profile import CandidateProfile
from app.services.embeddings import EmbeddingService, build_profile_document

import logging

logger = logging.getLogger("app.resumes")

router = APIRouter(prefix="/resumes", tags=["resumes"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/upload", response_model=dict)
async def upload_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are supported.")

    logger.info("Upload received: %s (%d bytes)", file.filename, file.size or 0)
    from uuid import uuid4

    from app.config import settings

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Resume PDF must be under 10 MB.")
    if len(content) < 64:
        raise HTTPException(status_code=400, detail="Uploaded file is empty or invalid.")

    file_id = uuid4().hex
    dest = settings.storage_path / "resumes" / f"{file_id}.pdf"
    dest.write_bytes(content)

    try:
        parser = ResumeParser()
        logger.info("Parsing resume: %s", dest.name)
        from app.services.pipeline_log import step, step_done

        step("PARSE_RESUME", file=file.filename)
        profile = parser.parse(dest)
        step_done("PARSE_RESUME", candidate_name=profile.name, skills=len(profile.skills))
        logger.info(
            "Parsed profile: name=%r skills=%d experience=%d projects=%d",
            profile.name,
            len(profile.skills),
            len(profile.experience),
            len(profile.projects),
        )

        candidate = Candidate(
            name=profile.name or "Unknown",
            email=profile.email or "",
            profile_json=profile.model_dump(),
            raw_text=profile.raw_text,
        )
        db.add(candidate)
        db.flush()

        # Same person, new upload — keep their platform logins so the page
        # shows logged-in immediately instead of asking again.
        try:
            from app.services.session_store import carry_over_sessions

            carry_over_sessions(db, candidate.id)
        except Exception as exc:
            logger.warning("Session carry-over skipped for candidate_id=%s: %s", candidate.id, exc)

        resume = Resume(
            candidate_id=candidate.id,
            original_filename=file.filename,
            file_path=str(dest),
            profile_json=profile.model_dump(),
            version_label="original",
        )
        db.add(resume)

        embedding_id = None
        try:
            embedder = EmbeddingService()
            logger.info("Creating embedding for candidate_id=%s", candidate.id)
            embedding_id = embedder.upsert_candidate(
                candidate.id,
                build_profile_document(profile.model_dump()),
            )
            candidate.embedding_id = embedding_id
        except Exception as exc:
            logger.warning("Embedding skipped for candidate_id=%s: %s", candidate.id, exc)

        db.commit()
        db.refresh(candidate)
        db.refresh(resume)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        logger.exception("Database integrity error during upload")
        raise HTTPException(status_code=409, detail="Could not save candidate. Please retry.") from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Upload failed for %s", file.filename)
        raise HTTPException(status_code=500, detail=f"Resume upload failed: {exc}") from exc

    logger.info(
        "Resume stored: candidate_id=%s resume_id=%s embedding_id=%s",
        candidate.id,
        resume.id,
        embedding_id,
    )
    step_done("STORE_CANDIDATE", candidate_id=candidate.id, resume_id=resume.id)
    profile_dict = profile.model_dump()
    if "raw_text" in profile_dict:
        profile_dict["raw_text"] = profile_dict["raw_text"].encode("utf-8", errors="replace").decode("utf-8")
    return {
        "candidate_id": candidate.id,
        "resume_id": resume.id,
        "profile": profile_dict,
    }


@router.get("/{candidate_id}", response_model=CandidateProfile)
def get_candidate_profile(candidate_id: int, db: Session = Depends(get_db)):
    candidate = db.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return CandidateProfile.model_validate(candidate.profile_json)
