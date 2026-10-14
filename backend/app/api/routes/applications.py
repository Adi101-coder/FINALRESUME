from datetime import datetime

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.models import Application, Candidate, Job
from app.database.session import get_db

logger = logging.getLogger("app.applications")

router = APIRouter(prefix="/applications", tags=["applications"])


class ApplicationCreate(BaseModel):
    candidate_id: int
    job_id: int
    resume_id: int | None = None
    status: str = "applied"
    notes: str = ""
    ats_score: float | None = None


class ApplicationResponse(BaseModel):
    id: int
    candidate_id: int
    job_id: int
    resume_id: int | None
    status: str
    applied_at: datetime | None
    ats_score: float | None
    notes: str
    created_at: datetime
    job_title: str
    job_company: str
    job_location: str
    apply_url: str
    match_score: float | None = None

    model_config = {"from_attributes": True}


def _to_response(app: Application, job: Job, match_score: float | None = None) -> ApplicationResponse:
    return ApplicationResponse(
        id=app.id,
        candidate_id=app.candidate_id,
        job_id=app.job_id,
        resume_id=app.resume_id,
        status=app.status,
        applied_at=app.applied_at,
        ats_score=app.ats_score,
        notes=app.notes,
        created_at=app.created_at,
        job_title=job.title,
        job_company=job.company,
        job_location=job.location,
        apply_url=job.apply_url,
        match_score=match_score,
    )


@router.get("/candidate/{candidate_id}", response_model=list[ApplicationResponse])
def list_applications(candidate_id: int, db: Session = Depends(get_db)):
    logger.info("List applications: candidate_id=%s", candidate_id)
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")

    rows = (
        db.query(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .filter(Application.candidate_id == candidate_id)
        .order_by(Application.created_at.desc())
        .all()
    )

    from app.database.models import JobMatch

    results: list[ApplicationResponse] = []
    for app, job in rows:
        match = (
            db.query(JobMatch)
            .filter(JobMatch.candidate_id == candidate_id, JobMatch.job_id == job.id)
            .order_by(JobMatch.created_at.desc())
            .first()
        )
        results.append(_to_response(app, job, match.score if match else None))

    return results


@router.post("")
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    logger.info(
        "Create application: candidate_id=%s job_id=%s status=%s",
        payload.candidate_id,
        payload.job_id,
        payload.status,
    )
    if not db.get(Candidate, payload.candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    job = db.get(Job, payload.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    duplicate = (
        db.query(Application)
        .filter(
            Application.candidate_id == payload.candidate_id,
            Application.job_id == payload.job_id,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Application already exists for this job.")

    application = Application(
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        resume_id=payload.resume_id,
        status=payload.status,
        notes=payload.notes,
        ats_score=payload.ats_score,
        applied_at=datetime.utcnow() if payload.status == "applied" else None,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    logger.info("Application created: id=%s", application.id)
    from app.services.pipeline_log import step_done

    step_done("APPLICATION_SAVED", id=application.id, job_id=payload.job_id, status=payload.status)
    if payload.status in {"applied", "submitted"}:
        from app.services.applied_jobs import record_applied_job

        record_applied_job(
            db,
            payload.candidate_id,
            title=job.title,
            company=job.company,
            location=job.location or "",
            apply_url=job.apply_url or "",
            job_id=job.id,
            platform=job.source or "linkedin",
            notes=payload.notes,
        )
    return _to_response(application, job)


@router.patch("/{application_id}/status")
def update_application_status(application_id: int, status: str, db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found.")

    job = db.get(Job, application.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    logger.info("Update application id=%s status=%s", application_id, status)
    application.status = status
    if status == "applied" and not application.applied_at:
        application.applied_at = datetime.utcnow()
    db.commit()
    db.refresh(application)
    return _to_response(application, job)


@router.get("/analytics/{candidate_id}")
def get_analytics(candidate_id: int, db: Session = Depends(get_db)):
    logger.info("Analytics requested: candidate_id=%s", candidate_id)
    applications = db.query(Application).filter(Application.candidate_id == candidate_id).all()
    if not applications:
        return {
            "total_applications": 0,
            "interview_rate": 0.0,
            "status_breakdown": {},
            "average_ats_score": None,
        }

    status_breakdown: dict[str, int] = {}
    ats_scores: list[float] = []
    interviews = 0

    for app in applications:
        status_breakdown[app.status] = status_breakdown.get(app.status, 0) + 1
        if app.status in {"interview", "offer"}:
            interviews += 1
        if app.ats_score is not None:
            ats_scores.append(app.ats_score)

    total = len(applications)
    return {
        "total_applications": total,
        "interview_rate": round((interviews / total) * 100, 1),
        "status_breakdown": status_breakdown,
        "average_ats_score": round(sum(ats_scores) / len(ats_scores), 1) if ats_scores else None,
    }
