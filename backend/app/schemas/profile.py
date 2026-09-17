from pydantic import BaseModel, Field, field_validator


class ExperienceItem(BaseModel):
    company: str = ""
    role: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str = ""
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    url: str = ""


class EducationItem(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str = ""
    start_date: str = ""
    end_date: str = ""
    cgpa: str = ""


class CandidateProfile(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    notice_period: str = ""
    preferred_roles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    github: str = ""
    portfolio: str = ""
    linkedin: str = ""
    summary: str = ""
    raw_text: str = ""

    @field_validator("raw_text", mode="before")
    @classmethod
    def sanitize_raw_text(cls, v: str) -> str:
        if isinstance(v, str):
            return v.encode("utf-8", errors="replace").decode("utf-8")
        return v
