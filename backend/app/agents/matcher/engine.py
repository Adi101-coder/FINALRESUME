from __future__ import annotations

import logging
import re

from app.schemas.profile import CandidateProfile

logger = logging.getLogger("app.matcher")

_HTML_TAG_RE = re.compile(r"<[^>]+>")

class MatchingEngine:
    """Score resume vs job description on multiple dimensions."""

    def __init__(self, threshold: float = 60.0) -> None:
        self.threshold = threshold
        self._embedder = None

    @property
    def embedder(self):
        if self._embedder is None:
            from app.services.embeddings import EmbeddingService

            self._embedder = EmbeddingService()
        return self._embedder

    def score(self, profile: CandidateProfile | dict, job: dict, *, profile_vec=None) -> dict:
        if isinstance(profile, CandidateProfile):
            profile_data = profile.model_dump()
        else:
            profile_data = profile

        job_skills = [s.lower() for s in job.get("skills", [])]
        profile_skills = [s.lower() for s in profile_data.get("skills", [])]
        profile_text = str(profile_data).lower()
        description = job.get("description", "").lower()

        terms = self._profile_terms(profile_data)
        relevance_score = min(100.0, self._relevance_score(terms, job, job_skills))
        skills_score = min(100.0, self._skills_score(profile_skills, job_skills, profile_text, description))
        experience_score = min(100.0, self._experience_score(profile_data, job))
        location_score = min(100.0, self._location_score(profile_data.get("location", ""), job.get("location", "")))
        embedding_score = min(100.0, self._embedding_score(profile_data, job, profile_vec=profile_vec))

        final = round(
            min(
                100.0,
                relevance_score * 0.50
                + skills_score * 0.15
                + experience_score * 0.15
                + embedding_score * 0.15
                + location_score * 0.05,
            ),
            1,
        )

        logger.debug(
            "Score %s @ %s → total=%.1f relevance=%.1f skills=%.1f exp=%.1f embed=%.1f loc=%.1f pass=%s",
            job.get("title", "?"),
            job.get("company", "?"),
            final,
            relevance_score,
            skills_score,
            experience_score,
            embedding_score,
            location_score,
            final >= self.threshold,
        )

        return {
            "score": final,
            "relevance_score": round(relevance_score, 1),
            "skills_score": round(skills_score, 1),
            "experience_score": round(experience_score, 1),
            "embedding_score": round(embedding_score, 1),
            "location_score": round(location_score, 1),
            "passed_threshold": final >= self.threshold,
        }

    def profile_vector(self, profile: CandidateProfile | dict):
        from app.services.embeddings import build_profile_document

        profile_data = profile.model_dump() if isinstance(profile, CandidateProfile) else profile
        return self.embedder.embed_text(build_profile_document(profile_data))

    def score_many(self, profile: CandidateProfile | dict, jobs: list[dict]) -> list[dict]:
        profile_vec = self.profile_vector(profile)
        return [self.score(profile, job, profile_vec=profile_vec) for job in jobs]

    def _profile_terms(self, profile: dict) -> list[str]:
        """Role/field/skill phrases that signal relevance to this resume."""
        terms: set[str] = set()
        for role in profile.get("preferred_roles") or []:
            role = str(role).lower().strip()
            if role:
                terms.add(role)
        for exp in profile.get("experience") or []:
            role = str(exp.get("role", "")).lower().strip()
            if role:
                terms.add(role)
        for edu in profile.get("education") or []:
            for key in ("field", "degree"):
                value = str(edu.get(key, "")).lower().strip()
                if len(value) >= 3:
                    terms.add(value)
        for skill in profile.get("skills") or []:
            skill = str(skill).lower().strip()
            if skill:
                terms.add(skill)
        for keyword in profile.get("keywords") or []:
            keyword = str(keyword).lower().strip()
            if keyword:
                terms.add(keyword)
        return sorted(t for t in terms if len(t) >= 3)

    def _relevance_score(self, terms: list[str], job: dict, job_skills: list[str]) -> float:
        """How well the job title/description matches the resume's role + skill vocabulary."""
        if not terms:
            return 30.0
        title_text = " ".join([str(job.get("title", "")), str(job.get("company", ""))]).lower()
        description = _HTML_TAG_RE.sub(" ", str(job.get("description", ""))).lower()

        title_hits = sum(1 for term in terms if term in title_text)
        title_pct = min(100.0, (title_hits / max(len(terms), 1)) * 300)

        desc_hits = sum(1 for term in terms if term in description)
        desc_pct = min(100.0, (desc_hits / max(len(terms), 1)) * 400)

        skills_hits = sum(1 for skill in job_skills if skill in terms)
        skills_pct = (skills_hits / max(len(job_skills), 1)) * 100 if job_skills else 0.0

        return round(0.5 * title_pct + 0.3 * desc_pct + 0.2 * skills_pct, 1)

    def _skills_score(
        self,
        profile_skills: list[str],
        job_skills: list[str],
        profile_text: str,
        description: str,
    ) -> float:
        if not job_skills:
            overlap = sum(1 for token in description.split() if token in profile_text)
            return min(100.0, min(100.0, overlap / max(len(set(description.split())), 1) * 150) + 20)

        matched = 0
        for skill in job_skills:
            if skill in profile_skills or skill in profile_text:
                matched += 1
        return min(100.0, (matched / max(len(job_skills), 1)) * 100 + 15)

    def _experience_score(self, profile: dict, job: dict) -> float:
        """Blend resume-experience relevance with the job's seniority requirement."""
        from app.services.experience_level import estimate_experience_years, experience_fit

        description = str(job.get("description", "") or "").lower()
        bullets = []
        for exp in profile.get("experience", []):
            bullets.extend(exp.get("bullets", []))
        if bullets and description:
            hits = sum(
                1
                for bullet in bullets
                if any(token in bullet.lower() for token in description.split()[:80])
            )
            base = min(100.0, (hits / max(len(bullets), 1)) * 120 + 25)
        else:
            base = 40.0 if profile.get("skills") else 25.0

        years = estimate_experience_years(profile)
        fit = experience_fit(job, years)
        return round(0.4 * base + 0.6 * fit, 1)

    def _location_score(self, candidate_location: str, job_location: str) -> float:
        if not job_location:
            return 85.0
        if not candidate_location:
            return 60.0
        c = candidate_location.lower()
        j = job_location.lower()
        if "remote" in j:
            return 100.0
        if c in j or j in c:
            return 100.0
        # Partial match for country/region
        c_parts = c.replace(",", " ").split()
        j_parts = j.replace(",", " ").split()
        if any(p in j_parts for p in c_parts if len(p) > 2):
            return 80.0
        return 50.0

    def _embedding_score(self, profile: dict, job: dict, *, profile_vec=None) -> float:
        job_doc = " ".join(
            [
                job.get("title", ""),
                job.get("company", ""),
                str(job.get("description", ""))[:1500],
                ", ".join(job.get("skills", [])),
            ]
        )
        if profile_vec is None:
            from app.services.embeddings import build_profile_document

            profile_doc = build_profile_document(profile)
            profile_vec = self.embedder.embed_text(profile_doc)
        job_vec = self.embedder.embed_text(job_doc)
        similarity = float((profile_vec @ job_vec.T)[0][0])
        # Boost similarity to 0-100 with minimum floor
        return max(20.0, min(100.0, similarity * 120))
