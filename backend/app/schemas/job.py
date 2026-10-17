from datetime import datetime

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    title: str
    company: str
    location: str = ""
    salary: str = ""
    experience_required: str = ""
    skills: list[str] = Field(default_factory=list)
    description: str = ""
    apply_url: str = ""
    application_type: str = "external"
    easy_apply: bool = False
    remote_type: str = ""
    visa_required: bool = False
    source: str = "manual"
    posted_at: datetime | None = None


class JobResponse(JobCreate):
    id: int
    dedup_hash: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}


class MatchResult(BaseModel):
    job_id: int
    title: str
    company: str
    location: str
    apply_url: str
    score: float
    relevance_score: float = 0.0
    skills_score: float
    experience_score: float
    embedding_score: float
    location_score: float
    passed_threshold: bool
    apply_status: str = "queued"
    apply_message: str = ""
