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
    return str(value or "").strip().lower() in {"present", "current", "now", "ongoing"}


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
        # Ignore parsed noise rows (bullet fragments stored as company/role)
        if str(exp.get("company") or "").lstrip("•- ").lower().startswith(("conducted", "audit", "engineer", "develop", "implement", "utiliz")):
            continue
        start = _parse_year(exp.get("start_date"))
        end_present = _is_present(exp.get("end_date")) or _is_present(exp.get("start_date"))
        end = _parse_year(exp.get("end_date"))
        if start:
            saw_experience = True
            if end and end >= start:
                spans.append(end - start + (1 if end == today.year else 0))
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
    req = job_required_years(job)
    if req is not None and req > years + 2:
        return True
    level = title_seniority(str(job.get("title") or ""))
    if level == "senior":
        if years < 0.5:
            return True
        if years < 2 and re.search(r"\b(manager|director|head|chief|vp|principal)\b", str(job.get("title") or ""), re.I):
            return True
    # Academic / PhD-gated roles are not open to freshers.
    if years < 2 and re.search(r"\b(professor|lecturer|faculty|ph\.?d)\b", str(job.get("title") or ""), re.I):
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
