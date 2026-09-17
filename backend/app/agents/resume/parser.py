from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pdfplumber

from app.config import settings
from app.schemas.profile import CandidateProfile

logger = logging.getLogger("app.parser")


class ResumeParser:
    """Extract structured candidate profile from PDF resume."""

    EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    PHONE_RE = re.compile(r"(\+?\d[\d\s\-().]{7,}\d)")
    LINKEDIN_RE = re.compile(r"(https?://(?:www\.)?linkedin\.com/in/[\w\-_/]+)", re.I)
    GITHUB_RE = re.compile(r"(https?://(?:www\.)?github\.com/[\w\-_/]+)", re.I)
    URL_RE = re.compile(r"https?://[\w\-./?=&%#]+", re.I)

    COMMON_SKILLS = {
        "python", "javascript", "typescript", "java", "c++", "c#", "go", "rust",
        "react", "next.js", "nextjs", "node", "nodejs", "docker", "kubernetes",
        "aws", "azure", "gcp", "postgresql", "postgres", "mysql", "mongodb",
        "redis", "fastapi", "django", "flask", "nestjs", "graphql", "rest",
        "tailwind", "html", "css", "sql", "git", "linux", "playwright",
        "selenium", "machine learning", "deep learning", "pytorch", "tensorflow",
        "nlp", "llm", "openai", "langchain", "faiss", "qdrant", "celery",
    }

    def extract_text(self, file_path: Path) -> str:
        logger.info("Extracting text from PDF: %s", file_path.name)
        chunks: list[str] = []
        with pdfplumber.open(file_path) as pdf:
            logger.debug("PDF has %d page(s)", len(pdf.pages))
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    chunks.append(text)
                    logger.debug("Page %d: extracted %d chars", i, len(text))
        total = "\n".join(chunks).strip()
        logger.info("Extracted %d total characters", len(total))
        return total

    def parse(self, file_path: Path) -> CandidateProfile:
        raw_text = self.extract_text(file_path)
        raw_text = raw_text.encode("utf-8", errors="replace").decode("utf-8")
        if settings.openai_api_key:
            try:
                logger.info("Using LLM parser (%s)", settings.openai_model)
                profile = self._parse_with_llm(raw_text)
            except Exception as exc:
                logger.warning("LLM parse failed, using heuristic fallback: %s", exc)
                profile = self._parse_heuristic(raw_text)
        else:
            logger.info("Using heuristic parser (no OPENAI_API_KEY)")
            profile = self._parse_heuristic(raw_text)
        profile.raw_text = raw_text
        return profile

    def _parse_with_llm(self, raw_text: str) -> CandidateProfile:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
        )
        schema = CandidateProfile.model_json_schema()
        prompt = (
            "Extract resume information into JSON matching this schema exactly. "
            "Do not invent facts. Use empty strings/lists for missing fields.\n\n"
            f"Schema:\n{json.dumps(schema, indent=2)}\n\nResume:\n{raw_text}"
        )
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a resume parser. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        return CandidateProfile.model_validate(data)

    def _parse_heuristic(self, raw_text: str) -> CandidateProfile:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        name = lines[0] if lines else ""

        email_match = self.EMAIL_RE.search(raw_text)
        phone_match = self.PHONE_RE.search(raw_text)
        linkedin_match = self.LINKEDIN_RE.search(raw_text)
        github_match = self.GITHUB_RE.search(raw_text)

        lower_text = raw_text.lower()
        found_skills = sorted(
            skill for skill in self.COMMON_SKILLS if skill in lower_text
        )

        portfolio = ""
        for url in self.URL_RE.findall(raw_text):
            if "linkedin" not in url.lower() and "github" not in url.lower():
                portfolio = url
                break

        keywords = list({*found_skills})
        if "cgpa" in lower_text or "gpa" in lower_text:
            keywords.append("gpa")

        return CandidateProfile(
            name=name,
            email=email_match.group(0) if email_match else "",
            phone=phone_match.group(0).strip() if phone_match else "",
            linkedin=linkedin_match.group(1) if linkedin_match else "",
            github=github_match.group(1) if github_match else "",
            portfolio=portfolio,
            skills=found_skills,
            keywords=keywords,
            summary=self._extract_section(raw_text, ["summary", "objective", "about"]),
        )

    def _extract_section(self, text: str, headers: list[str]) -> str:
        pattern = rf"(?i)(?:{'|'.join(headers)})\s*[:\n]\s*(.+?)(?:\n\s*\n|\Z)"
        match = re.search(pattern, text, re.S)
        if not match:
            return ""
        return " ".join(match.group(1).split())[:500]
