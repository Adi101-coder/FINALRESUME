import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.models import Candidate, CandidateJob, Job
from app.database.session import get_db
from app.schemas.job import JobCreate, JobResponse, MatchResult
from app.services.applied_jobs import linkedin_job_id_from_url, should_skip_applied
from app.services.deduplication import job_dedup_hash
from app.services.job_discovery import discover_jobs_for_candidate

logger = logging.getLogger("app.jobs")
router = APIRouter(prefix="/jobs", tags=["jobs"])

@router.post("", response_model=JobResponse)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    dedup_hash = job_dedup_hash(payload.company, payload.title, payload.location)
    existing = db.query(Job).filter(Job.dedup_hash == dedup_hash).first()
    if existing:
        logger.info("Duplicate job skipped: %s @ %s", payload.title, payload.company)
        return existing
    job = Job(
        **payload.model_dump(),
        dedup_hash=dedup_hash,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("Job created: id=%s %s @ %s", job.id, job.title, job.company)
    return job

@router.get("", response_model=list[JobResponse])
def list_jobs(
    db: Session = Depends(get_db),
    limit: int = Query(default=200, le=500),
    source: str | None = None,
):
    query = db.query(Job).order_by(Job.created_at.desc())
    if source:
        query = query.filter(Job.source == source)
    return query.limit(limit).all()


@router.get("/sources")
def list_job_sources():
    from app.agents.scraper.registry import get_source_status

    return {"sources": get_source_status()}


@router.post("/discover/{candidate_id}")
async def discover_jobs(
    candidate_id: int,
    sources: str | None = Query(default=None, description="Comma-separated list of sources to scrape"),
    remote_type: str | None = Query(default=None, description="Filter: remote, onsite, hybrid"),
    fresh: bool = Query(default=True, description="Clear previous pool links so counts are live-only for this run"),
    location: str | None = Query(default=None, description="Preferred search location (overrides resume location)"),
    all_locations: bool = Query(default=False, description="Search all locations (worldwide)"),
    db: Session = Depends(get_db),
):
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    try:
        source_list = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
        result = await discover_jobs_for_candidate(
            db,
            candidate_id,
            sources=source_list,
            remote_type=remote_type,
            fresh=fresh,
            location=location,
            all_locations=all_locations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Discovery failed for candidate_id=%s", candidate_id)
        raise HTTPException(status_code=500, detail=f"Job discovery failed: {exc}") from exc
    logger.info("Discovery complete: %s", result)
    return result


@router.get("/applied/{candidate_id}")
def list_applied_jobs_for_candidate(candidate_id: int, db: Session = Depends(get_db)):
    """Local ledger of jobs already applied to (SQLite + JSON dump)."""
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    from app.services.applied_jobs import list_applied_jobs

    rows = list_applied_jobs(db, candidate_id)
    return [
        {
            "id": r.id,
            "job_id": r.job_id,
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


@router.get("/discovered/{candidate_id}", response_model=list[MatchResult])
def list_discovered_jobs_for_candidate(candidate_id: int, db: Session = Depends(get_db)):
    """Jobs linked to this candidate from discovery, in list order (oldest link first)."""
    if not db.get(Candidate, candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")

    candidate_job_ids = [
        row[0]
        for row in db.query(CandidateJob.job_id)
        .filter(CandidateJob.candidate_id == candidate_id)
        .order_by(CandidateJob.created_at.asc())
        .limit(1000)
        .all()
    ]
    if not candidate_job_ids:
        return []

    jobs = (
        db.query(Job)
        .filter(Job.id.in_(candidate_job_ids))
        .filter(Job.apply_url.like("%linkedin.com%"))
        .all()
    )
    job_by_id = {j.id: j for j in jobs}

    results: list[MatchResult] = []
    for job_id in candidate_job_ids:
        job = job_by_id.get(job_id)
        if not job:
            continue
        if should_skip_applied(
            db,
            candidate_id,
            apply_url=job.apply_url or "",
            linkedin_job_id=linkedin_job_id_from_url(job.apply_url or ""),
            db_job_id=job.id,
        ):
            continue
        results.append(
            MatchResult(
                job_id=job.id,
                title=job.title,
                company=job.company,
                location=job.location,
                apply_url=job.apply_url,
                score=0,
                relevance_score=0,
                skills_score=0,
                experience_score=0,
                embedding_score=0,
                location_score=0,
                passed_threshold=True,
            )
        )
    return results


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job
