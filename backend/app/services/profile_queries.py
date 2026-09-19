"""Derive job search queries from a candidate profile (any industry)."""

from __future__ import annotations

import re


GENERIC_ROLE_TERMS = (
    "analyst",
    "manager",
    "specialist",
    "coordinator",
    "associate",
    "executive",
    "consultant",
    "representative",
)


def _clean_term(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    return value[:80]


STOP_WORDS = {
    "with", "that", "this", "have", "years", "built", "full",
    "from", "into", "your", "will", "work", "team", "using",
    "role", "jobs", "year", "years", "experience",
}

GENERIC_DEGREE_WORDS = {"bachelor", "master", "btech", "mtech", "bca", "mca", "degree", "bachelor's", "master's"}


def _is_junk_term(term: str) -> bool:
    low = term.lower().strip()
    if len(low) < 3:
        return True
    words = low.split()
    # Reject sentence fragments like "Built a full"
    if len(words) <= 3 and all(w in STOP_WORDS or len(w) <= 4 for w in words):
        return True
    if low in STOP_WORDS or low in GENERIC_DEGREE_WORDS:
        return True
    # Reject terms with no alpha or mostly generic
    if not re.search(r"[a-z]{3,}", low):
        return True
    return False


def build_search_queries(profile: dict, max_queries: int = 10) -> list[str]:
    """Build diverse search terms from roles, experience, skills — not tech-only."""
    queries: list[str] = []

    for role in profile.get("preferred_roles") or []:
        term = _clean_term(str(role))
        if term and not _is_junk_term(term):
            queries.append(term)

    for exp in profile.get("experience") or []:
        role = _clean_term(str(exp.get("role", "")))
        if role and not _is_junk_term(role):
            queries.append(role)
        company = _clean_term(str(exp.get("company", "")))
        # company alone is not a good query, skip

    for skill in (profile.get("skills") or [])[:8]:
        term = _clean_term(str(skill))
        if term and not _is_junk_term(term):
            queries.append(term)

    for keyword in (profile.get("keywords") or [])[:8]:
        term = _clean_term(str(keyword))
        if term and not _is_junk_term(term):
            queries.append(term)

    for edu in profile.get("education") or []:
        field = _clean_term(str(edu.get("field", "")))
        if field and not _is_junk_term(field) and len(field) > 3:
            queries.append(field)

    if not queries:
        # Fallback to a safe default instead of sentence fragments
        queries.append("software engineer")

    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            unique.append(q)

    return unique[:max_queries]
