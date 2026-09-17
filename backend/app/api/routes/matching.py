import logging
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents.matcher.engine import MatchingEngine
from app.agents.resume.optimizer import ResumeOptimizer
from app.agents.resume.pdf_generator import ResumePDFGenerator
from app.config import settings
from app.database.models import Candidate, CandidateJob, Job, JobMatch, Resume
from app.database.session import get_db
from app.schemas.job import MatchResult
from app.schemas.profile import CandidateProfile

logger = logging.getLogger("app.matching")

router = APIRouter(prefix="/matching", tags=["matching"])

MATCH_POOL_LIMIT = 1000


@router.post("/{candidate_id}", response_model=list[MatchResult])
def match_candidate_jobs(candidate_id: int, db: Session = Depends(get_db)):
    candidate = db.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found.")

    profile = CandidateProfile.model_validate(candidate.profile_json)
    engine = MatchingEngine(threshold=settings.match_threshold)

    candidate_job_ids = [
        row[0]
        for row in db.query(CandidateJob.job_id)
        .filter(CandidateJob.candidate_id == candidate.id)
        .order_by(CandidateJob.created_at.desc())
        .limit(MATCH_POOL_LIMIT)
        .all()
    ]
    from app.services.pipeline_log import step, step_done

    if not candidate_job_ids:
        logger.info("No platform-discovered jobs for candidate_id=%s — run discovery first.", candidate_id)
        step("MATCH_JOBS", candidate_id=candidate_id, job_pool=0)
        step_done("MATCH_JOBS", returned=0, passed=0)
        return []

    jobs = db.query(Job).filter(Job.id.in_(candidate_job_ids)).all()
    logger.info(
        "Matching candidate_id=%s against %d live platform jobs (no shared pool)",
        candidate_id,
        len(jobs),
    )
    step("MATCH_JOBS", candidate_id=candidate_id, job_pool=len(jobs))

    db.query(JobMatch).filter(JobMatch.candidate_id == candidate.id).delete()
    db.flush()

    job_payloads = [
        {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "description": job.description,
            "skills": job.skills or [],
        }
        for job in jobs
    ]
    score_rows = engine.score_many(profile, job_payloads)

    results: list[MatchResult] = []
    for job, scores in zip(jobs, score_rows):
        db.add(
            JobMatch(
                candidate_id=candidate.id,
                job_id=job.id,
                score=scores["score"],
                skills_score=scores["skills_score"],
                experience_score=scores["experience_score"],
                embedding_score=scores["embedding_score"],
                location_score=scores["location_score"],
                passed_threshold=scores["passed_threshold"],
            )
        )
        results.append(
            MatchResult(
                job_id=job.id,
                title=job.title,
                company=job.company,
                location=job.location,
                apply_url=job.apply_url,
                **scores,
            )
        )

    db.commit()
    results.sort(key=lambda item: item.score, reverse=True)
    top = results[: settings.top_jobs_limit]
    passed = sum(1 for item in top if item.passed_threshold)
    logger.info(
        "Match complete: candidate_id=%s top=%d passed_threshold=%d best_score=%.1f",
        candidate_id,
        len(top),
        passed,
        top[0].score if top else 0,
    )
    step_done("MATCH_JOBS", returned=len(top), passed=passed)
    return top

@router.post("/{candidate_id}/personalize/{job_id}")
def personalize_resume(candidate_id: int, job_id: int, db: Session = Depends(get_db)):
    candidate = db.get(Candidate, candidate_id)
    job = db.get(Job, job_id)
    if not candidate or not job:
        raise HTTPException(status_code=404, detail="Candidate or job not found.")

    profile = CandidateProfile.model_validate(candidate.profile_json)
    optimizer = ResumeOptimizer()
    logger.info("Personalizing resume: candidate_id=%s job_id=%s", candidate_id, job_id)
    optimized = optimizer.optimize(profile, job.description, job.skills or [])
    ats = optimizer.estimate_ats_score(optimized, job.description, job.skills or [])
    logger.info("ATS score estimate: %.1f%%", ats["overall_score"])
    output_path = settings.storage_path / "generated" / f"{candidate_id}_{job_id}_{uuid4().hex}.pdf"
    ResumePDFGenerator().generate(optimized, output_path)
    logger.info("Generated PDF: %s", output_path)
    resume = Resume(
        candidate_id=candidate.id,
        original_filename=output_path.name,
        file_path=str(output_path),
        profile_json=optimized.model_dump(),
        version_label=f"optimized-{job_id}",
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    return {
        "resume_id": resume.id,
        "pdf_path": str(output_path),
        "optimized_profile": optimized.model_dump(),
        "ats_report": ats,
    }
