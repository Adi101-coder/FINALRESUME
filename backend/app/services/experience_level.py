"""Estimate candidate experience level and job seniority requirements.

Used to surface jobs that match the resume's actual experience (e.g. a final-year
student should not be shown "Senior/Lead" roles requiring 5+ years).
"""

from __future__ import annotations

import re
from datetime import date

_SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|head of|director|manager|architect|chief|vp|vice president)\b",
    re.I,
)
_ENTRY_TITLE_RE = re.compile(
    r"\b(junior|jr\.?|intern(ship)?|trainee|entry[\s-]?level|graduate|fresher|assistant)\b",
    re.I,
)
_EXPLICIT_YEARS_RE = re.compile(
    r"(?:minimum of |at least )?(\d+)\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(?:\d+)?\s*years?(?:\s+of)?(?:\s+(?:relevant\s+)?experience)?",
    re.I,
)


def _parse_year(value) -> int | None:
    s = str(value or "").strip()
    if not s:
        return None
    m = re.search(r"(19|20)\d{2}", s)
    return int(m.group(0)) if m else None


def _is_present(value) -> bool:
    return str(value or "").strip().lower() in {
        "present",
        "current",
        "now",
        "ongoing",
        "till date",
        "till now",
        "present day",
        "continue",
    }


def estimate_experience_years(profile: dict) -> float | None:
    """Years of professional experience inferred from the resume.

    Returns None when the resume gives no usable signal (treated as neutral).
    Students (education end year in the future) return 0.0.
    """
    today = date.today()
    spans: list[float] = []
    saw_experience = False

    for exp in profile.get("experience") or []:
        title = str(exp.get("role") or exp.get("company") or "")
        bullets = exp.get("bullets") or []
        if not title and not bullets:
            continue
        # Ignore parsed noise: bullet fragments stored as company/role entries.
        company_l = str(exp.get("company") or "").strip().lower().rstrip(":").lstrip("•- *")
        if company_l.startswith(("conducted", "audit", "engineer", "develop", "implement", "utiliz", "built", "created", "designed", "deployed", "trained")):
            continue
        # Section headers ("EXPERIENCE") often carry the first role's dates — keep
        # the dates but never treat the header itself as a role.
        is_header = company_l in {
            "experience",
            "work experience",
            "employment",
            "work history",
            "professional experience",
            "career",
            "projects",
            "personal projects",
        }
        start = _parse_year(exp.get("start_date"))
        end_present = _is_present(exp.get("end_date")) or _is_present(exp.get("start_date"))
        end = _parse_year(exp.get("end_date"))
        if start and not is_header:
            saw_experience = True
        if start:
            if end and end >= start:
                span = end - start + (1 if end >= today.year else 0)
                spans.append(max(1, span))
            elif end_present:
                spans.append(max(1, today.year - start + (1 if today.month >= 7 else 0)))

    if spans:
        return float(max(spans))

    # Education signal: enrolled with a future graduation year → student/fresher.
    for edu in profile.get("education") or []:
        end = _parse_year(edu.get("end_date"))
        if end and end > today.year:
            return 0.0
        if end and end == today.year and _is_present(edu.get("end_date")):
            return 0.0

    if saw_experience:
        return 1.0
    return None


def title_seniority(title: str) -> str:
    """'entry' | 'mid' | 'senior' based on the job title wording."""
    t = str(title or "")
    if _SENIOR_TITLE_RE.search(t):
        return "senior"
    if _ENTRY_TITLE_RE.search(t):
        return "entry"
    return "mid"


def job_required_years(job: dict) -> float | None:
    """Minimum years the posting asks for, if it says so."""
    fields = [
        str(job.get("experience_required") or ""),
        str(job.get("title") or ""),
    ]
    for text in fields:
        m = _EXPLICIT_YEARS_RE.search(text)
        if m:
            return float(m.group(1))
    desc = str(job.get("description") or "")[:1200]
    m = re.search(
        r"(\d+)\s*(?:\+|plus)?\s*years(?:\s+of)?(?:\s+(?:relevant|proven|hands[- ]on))?\s*(?:work\s*)?experience",
        desc,
        re.I,
    )
    if m:
        return float(m.group(1))
    return None


def is_overqualified(job: dict, years: float | None) -> bool:
    """True when the job clearly asks for far more experience than the resume has."""
    if years is None:
        return False
    title = str(job.get("title") or "")
    req = job_required_years(job)
    if years < 2:
        # Fresher / student: hide roles that ask for real experience or seniority.
        if req is not None and req >= 2:
            return True
        if title_seniority(title) == "senior":
            return True
        if re.search(r"\b([2-9]|\d{2})\+?\s*years?\b", title, re.I):
            return True
    if req is not None and req > years + 2:
        return True
    level = title_seniority(title)
    if level == "senior":
        if years < 0.5:
            return True
        if years < 2 and re.search(r"\b(manager|director|head|chief|vp|principal)\b", title, re.I):
            return True
    # Experienced candidates should not be shown fresher/intern roles.
    if years >= 4 and re.search(
        r"\b(intern(ship)?|trainee|fresher|entry[\s-]?level|junior|jr\.?|graduate)\b",
        title,
        re.I,
    ):
        return True
    # Academic / PhD-gated roles are not open to freshers.
    if years < 2 and re.search(r"\b(professor|lecturer|faculty|ph\.?d)\b", title, re.I):
        return True
    return False


def experience_fit(job: dict, years: float | None) -> float:
    """0-100 fit between the candidate's experience and the job's requirement."""
    if years is None:
        return 70.0  # unknown → neutral
    req = job_required_years(job)
    level = title_seniority(str(job.get("title") or ""))
    if req is not None:
        if req <= years + 1:
            return 95.0
        if req <= years + 3:
            return 60.0
        return 20.0
    if level == "entry":
        return 95.0 if years <= 2 else 75.0
    if level == "senior":
        if years >= 5:
            return 90.0
        if years >= 3:
            return 60.0
        return 30.0
    # mid-level
    if years >= 2:
        return 85.0
    if years >= 1:
        return 70.0
    return 75.0  # freshers can still apply to typical "2-4 yrs" mid jobs? no req given → mild
