from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    embedding_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    resumes: Mapped[list["Resume"]] = relationship(back_populates="candidate")
    applications: Mapped[list["Application"]] = relationship(back_populates="candidate")
    platform_sessions: Mapped[list["PlatformSession"]] = relationship(back_populates="candidate")
    auto_apply_config: Mapped["AutoApplyConfig | None"] = relationship(back_populates="candidate", uselist=False)


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(String(1024))
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
    version_label: Mapped[str] = mapped_column(String(128), default="original")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    candidate: Mapped["Candidate"] = relationship(back_populates="resumes")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    company: Mapped[str] = mapped_column(String(512), index=True)
    location: Mapped[str] = mapped_column(String(255), default="")
    salary: Mapped[str] = mapped_column(String(128), default="")
    experience_required: Mapped[str] = mapped_column(String(128), default="")
    skills: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    apply_url: Mapped[str] = mapped_column(String(1024), default="")
    application_type: Mapped[str] = mapped_column(String(64), default="external")
    easy_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_type: Mapped[str] = mapped_column(String(64), default="")
    visa_required: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    dedup_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JobMatch(Base):
    __tablename__ = "job_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    skills_score: Mapped[float] = mapped_column(Float, default=0)
    experience_score: Mapped[float] = mapped_column(Float, default=0)
    embedding_score: Mapped[float] = mapped_column(Float, default=0)
    location_score: Mapped[float] = mapped_column(Float, default=0)
    passed_threshold: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CandidateJob(Base):
    """Jobs discovered/selected for a specific candidate (per-resume job pool)."""

    __tablename__ = "candidate_jobs"
    __table_args__ = (UniqueConstraint("candidate_id", "job_id", name="uq_candidate_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("candidate_id", "job_id", name="uq_application_candidate_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), nullable=True)
    cover_letter_path: Mapped[str] = mapped_column(String(1024), default="")
    generated_resume_path: Mapped[str] = mapped_column(String(1024), default="")
    ats_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    candidate: Mapped["Candidate"] = relationship(back_populates="applications")


class AppliedJob(Base):
    """Local ledger of jobs this candidate already applied to (for skip + verify)."""

    __tablename__ = "applied_jobs"
    __table_args__ = (UniqueConstraint("candidate_id", "skip_key", name="uq_applied_job_skip"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    company: Mapped[str] = mapped_column(String(512), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    apply_url: Mapped[str] = mapped_column(String(1024), default="")
    linkedin_job_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    platform: Mapped[str] = mapped_column(String(64), default="linkedin")
    skip_key: Mapped[str] = mapped_column(String(256), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ShownJob(Base):
    """Jobs this candidate has already been shown in Discover (may reappear if unapplied)."""

    __tablename__ = "shown_jobs"
    __table_args__ = (UniqueConstraint("candidate_id", "skip_key", name="uq_shown_job_skip"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    apply_url: Mapped[str] = mapped_column(String(1024), default="")
    skip_key: Mapped[str] = mapped_column(String(256), default="")
    shown_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PlatformSession(Base):
    """Playwright cookie/session state — not passwords."""

    __tablename__ = "platform_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    platform: Mapped[str] = mapped_column(String(64), index=True)
    storage_state: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    candidate: Mapped["Candidate"] = relationship(back_populates="platform_sessions")


class AutoApplyConfig(Base):
    __tablename__ = "auto_apply_configs"

    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), primary_key=True)
    job_titles: Mapped[list] = mapped_column(JSON, default=list)
    location: Mapped[str] = mapped_column(String(255), default="Remote")
    remote_type: Mapped[str] = mapped_column(String(64), default="")
    resume_filename: Mapped[str] = mapped_column(String(512), default="")
    resume_path: Mapped[str] = mapped_column(String(1024), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    candidate: Mapped["Candidate"] = relationship(back_populates="auto_apply_config")
