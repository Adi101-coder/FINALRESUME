from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

import pdfplumber

from app.config import settings
from app.schemas.profile import (
    CandidateProfile,
    EducationItem,
    ExperienceItem,
    ProjectItem,
)

logger = logging.getLogger("app.parser")


class ResumeParser:
    """Extract structured candidate profile from PDF resume."""

    EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    PHONE_RE = re.compile(r"(\+?\d[\d\s\-().]{7,}\d)")
    LINKEDIN_RE = re.compile(r"(https?://(?:www\.)?linkedin\.com/in/[\w\-_/]+)", re.I)
    GITHUB_RE = re.compile(r"(https?://(?:www\.)?github\.com/[\w\-_/]+)", re.I)
    URL_RE = re.compile(r"https?://[\w\-./?=&%#]+", re.I)

    SECTION_HEADERS = {
        "experience": [
            "experience", "work experience", "employment", "work history",
            "professional experience", "career", "positions", "roles"
        ],
        "education": [
            "education", "academic", "qualifications", "degrees", "university",
            "college", "school", "academic background"
        ],
        "projects": [
            "projects", "personal projects", "key projects", "notable projects",
            "project experience", "side projects", "portfolio"
        ],
        "skills": [
            "skills", "technical skills", "technologies", "tech stack",
            "programming languages", "tools", "frameworks", "competencies"
        ],
        "summary": [
            "summary", "objective", "about", "profile", "professional summary",
            "career objective", "summary of qualifications", "overview"
        ],
        "certifications": [
            "certifications", "certificates", "licenses", "credentials",
            "professional certifications", "certs"
        ],
        "achievements": [
            "achievements", "awards", "honors", "accomplishments",
            "recognitions", "publications", "patents"
        ],
    }

    COMMON_SKILLS = {
        "python", "javascript", "typescript", "java", "c++", "c#", "go", "rust",
        "react", "next.js", "nextjs", "node", "nodejs", "docker", "kubernetes",
        "aws", "azure", "gcp", "postgresql", "postgres", "mysql", "mongodb",
        "redis", "fastapi", "django", "flask", "nestjs", "graphql", "rest",
        "tailwind", "html", "css", "sql", "git", "linux", "playwright",
        "selenium", "machine learning", "deep learning", "pytorch", "tensorflow",
        "nlp", "llm", "openai", "langchain", "faiss", "qdrant", "celery",
        "spring", "spring boot", "hibernate", "microservices", "kafka", "rabbitmq",
        "elasticsearch", "terraform", "ansible", "ci/cd", "jenkins", "github actions",
        "gitlab", "bitbucket", "jira", "confluence", "agile", "scrum", "kanban",
        "vue", "angular", "svelte", "express", "koa", "deno", "bun", "rust",
        "sqlalchemy", "prisma", "typeorm", "mongoose", "sequelize", "knex",
        "testing", "jest", "pytest", "cypress", "playwright", "vitest", "mocha",
        "webpack", "vite", "esbuild", "rollup", "parcel", "pnpm", "yarn", "npm",
        "postcss", "sass", "less", "styled-components", "emotion", "mui", "chakra",
        "ant design", "bootstrap", "material ui", "tailwindcss", "css modules",
        "graphql", "apollo", "relay", "urql", "react query", "swr", "redux",
        "zustand", "recoil", "jotai", "xstate", "framer motion", "gsap",
        "three.js", "webgl", "canvas", "svg", "webassembly", "wasm",
        "kubernetes", "docker", "helm", "kubectl", "istio", "linkerd", "argo",
        "prometheus", "grafana", "datadog", "new relic", "elk", "splunk",
        "kafka", "pulsar", "nats", "redis", "memcached", "cassandra", "dynamodb",
        "firebase", "supabase", "planetscale", "neon", "vercel", "netlify",
        "cloudflare", "aws lambda", "gcp functions", "azure functions",
    }

    DATE_PATTERNS = [
        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}",
        r"\d{1,2}/\d{4}",
        r"\d{4}-\d{2}",
        r"\d{4}",
        r"(?:present|current|now|ongoing)",
    ]

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
        if not lines:
            return CandidateProfile()

        name = self._extract_name(lines, raw_text)

        email_match = self.EMAIL_RE.search(raw_text)
        phone_match = self.PHONE_RE.search(raw_text)
        linkedin_match = self.LINKEDIN_RE.search(raw_text)
        github_match = self.GITHUB_RE.search(raw_text)

        location = self._extract_location(raw_text)
        notice_period = self._extract_notice_period(raw_text)
        preferred_roles = self._extract_preferred_roles(raw_text)

        lower_text = raw_text.lower()
        found_skills = sorted(
            skill for skill in self.COMMON_SKILLS if skill in lower_text
        )

        portfolio = ""
        for url in self.URL_RE.findall(raw_text):
            if "linkedin" not in url.lower() and "github" not in url.lower():
                portfolio = url
                break

        sections = self._split_sections(raw_text)

        experience = self._extract_experience(sections.get("experience", ""))
        projects = self._extract_projects(sections.get("projects", ""))
        education = self._extract_education(sections.get("education", ""))
        certifications = self._extract_certifications(sections.get("certifications", ""))
        achievements = self._extract_achievements(sections.get("achievements", ""))
        summary = self._extract_section(raw_text, self.SECTION_HEADERS["summary"])

        keywords = list({*found_skills, *preferred_roles})

        return CandidateProfile(
            name=name,
            email=email_match.group(0) if email_match else "",
            phone=phone_match.group(0).strip() if phone_match else "",
            linkedin=linkedin_match.group(1) if linkedin_match else "",
            github=github_match.group(1) if github_match else "",
            portfolio=portfolio,
            location=location,
            notice_period=notice_period,
            preferred_roles=preferred_roles,
            skills=found_skills,
            experience=experience,
            projects=projects,
            education=education,
            certifications=certifications,
            achievements=achievements,
            keywords=keywords,
            summary=summary,
        )

    def _extract_name(self, lines: list[str], raw_text: str) -> str:
        if not lines:
            return ""
        first_line = lines[0]
        if len(first_line) < 80 and not any(c in first_line for c in "@|"):
            return first_line
        name_match = re.search(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})", raw_text, re.M)
        if name_match:
            return name_match.group(1)
        return first_line[:50]

    def _extract_location(self, text: str) -> str:
        location_patterns = [
            r"(?:location|based in|located in|city|address)\s*[:\-]\s*([^\n]+)",
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2,})",
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][a-z]+)",
        ]
        for pattern in location_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).strip()
        return ""

    def _extract_notice_period(self, text: str) -> str:
        patterns = [
            r"(?:notice period|notice|availability)\s*[:\-]\s*([^\n]+)",
            r"(\d+\s*(?:week|month|day)s?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).strip()
        return ""

    def _extract_preferred_roles(self, text: str) -> list[str]:
        roles = []
        patterns = [
            r"(?:preferred roles?|desired roles?|target roles?|looking for|seeking)\s*[:\-]\s*([^\n]+)",
            r"(?:role|position|title)\s*[:\-]\s*([^\n]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                role_text = match.group(1)
                roles.extend([r.strip() for r in re.split(r"[,/|;]", role_text) if r.strip()])
        return list(dict.fromkeys(roles))[:5]

    def _split_sections(self, text: str) -> dict[str, str]:
        sections = {}
        lower_text = text.lower()
        header_positions = []

        for section_type, headers in self.SECTION_HEADERS.items():
            for header in headers:
                pattern = rf"(?i)^\s*{re.escape(header)}\s*[:\n]"
                for match in re.finditer(pattern, text, re.M):
                    header_positions.append((match.start(), section_type, header))

        header_positions.sort(key=lambda x: x[0])

        for i, (pos, section_type, header) in enumerate(header_positions):
            start = pos
            end = header_positions[i + 1][0] if i + 1 < len(header_positions) else len(text)
            section_text = text[start:end].strip()
            if section_type not in sections or len(section_text) > len(sections[section_type]):
                sections[section_type] = section_text

        return sections

    def _extract_section(self, text: str, headers: list[str]) -> str:
        for header in headers:
            pattern = rf"(?i)(?:{re.escape(header)})\s*[:\n]\s*(.+?)(?:\n\s*(?:\n|[A-Z][A-Z\s]+:)|$)"
            match = re.search(pattern, text, re.S)
            if match:
                return " ".join(match.group(1).split())[:500]
        return ""

    def _extract_experience(self, section_text: str) -> list[ExperienceItem]:
        if not section_text:
            return []
        experiences = []
        entries = self._split_entries(section_text)
        for entry in entries:
            exp = self._parse_experience_entry(entry)
            if exp.company or exp.role:
                experiences.append(exp)
        return experiences[:10]

    def _parse_experience_entry(self, entry: str) -> ExperienceItem:
        lines = [l.strip() for l in entry.splitlines() if l.strip()]
        if not lines:
            return ExperienceItem()

        company = ""
        role = ""
        location = ""
        start_date = ""
        end_date = ""
        bullets = []

        first_line = lines[0]
        parts = re.split(r"\s*[-|•]\s*|\s{2,}|\t", first_line)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) >= 2:
            role = parts[0]
            company = parts[1]
            if len(parts) >= 3:
                location = parts[2]
        else:
            company = first_line

        date_pattern = "|".join(self.DATE_PATTERNS)
        date_re = re.compile(date_pattern, re.I)
        dates = date_re.findall(entry)
        if dates:
            start_date = dates[0] if len(dates) > 0 else ""
            end_date = dates[1] if len(dates) > 1 else ""

        for line in lines[1:]:
            if re.match(r"^[\-\*•]\s+", line):
                bullets.append(re.sub(r"^[\-\*•]\s+", "", line))
            elif line and not re.search(date_pattern, line, re.I):
                bullets.append(line)

        return ExperienceItem(
            company=company,
            role=role,
            location=location,
            start_date=start_date,
            end_date=end_date,
            bullets=bullets[:10],
        )

    def _extract_projects(self, section_text: str) -> list[ProjectItem]:
        if not section_text:
            return []
        projects = []
        entries = self._split_entries(section_text)
        for entry in entries:
            proj = self._parse_project_entry(entry)
            if proj.name:
                projects.append(proj)
        return projects[:10]

    def _parse_project_entry(self, entry: str) -> ProjectItem:
        lines = [l.strip() for l in entry.splitlines() if l.strip()]
        if not lines:
            return ProjectItem()

        name = lines[0]
        description = ""
        technologies = []
        bullets = []
        url = ""

        url_match = self.URL_RE.search(entry)
        if url_match:
            url = url_match.group(0)

        tech_pattern = r"(?:tech|technologies|stack|tools|languages|frameworks)\s*[:\-]\s*([^\n]+)"
        tech_match = re.search(tech_pattern, entry, re.I)
        if tech_match:
            tech_text = tech_match.group(1)
            technologies = [t.strip() for t in re.split(r"[,/|;]", tech_text) if t.strip()]

        for line in lines[1:]:
            if re.match(r"^[\-\*•]\s+", line):
                bullets.append(re.sub(r"^[\-\*•]\s+", "", line))
            elif line and not description:
                description = line

        return ProjectItem(
            name=name,
            description=description[:300],
            technologies=technologies[:15],
            bullets=bullets[:10],
            url=url,
        )

    def _extract_education(self, section_text: str) -> list[EducationItem]:
        if not section_text:
            return []
        education = []
        entries = self._split_entries(section_text)
        for entry in entries:
            edu = self._parse_education_entry(entry)
            if edu.institution or edu.degree:
                education.append(edu)
        return education[:5]

    def _parse_education_entry(self, entry: str) -> EducationItem:
        lines = [l.strip() for l in entry.splitlines() if l.strip()]
        if not lines:
            return EducationItem()

        institution = ""
        degree = ""
        field = ""
        start_date = ""
        end_date = ""
        cgpa = ""

        first_line = lines[0]
        parts = re.split(r"\s*[-|•]\s*|\s{2,}|\t", first_line)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) >= 2:
            degree = parts[0]
            institution = parts[1]
        elif len(parts) == 1:
            institution = parts[0]

        date_pattern = "|".join(self.DATE_PATTERNS)
        date_re = re.compile(date_pattern, re.I)
        dates = date_re.findall(entry)
        if dates:
            start_date = dates[0] if len(dates) > 0 else ""
            end_date = dates[1] if len(dates) > 1 else ""

        cgpa_match = re.search(r"(?:cgpa|gpa|grade|percentage)\s*[:\-]\s*([\d.]+)", entry, re.I)
        if cgpa_match:
            cgpa = cgpa_match.group(1)

        if len(parts) >= 3:
            field = parts[2]

        return EducationItem(
            institution=institution,
            degree=degree,
            field=field,
            start_date=start_date,
            end_date=end_date,
            cgpa=cgpa,
        )

    def _extract_certifications(self, section_text: str) -> list[str]:
        if not section_text:
            return []
        certs = []
        lines = [l.strip() for l in section_text.splitlines() if l.strip()]
        for line in lines:
            if re.match(r"^[\-\*•]\s+", line):
                cert = re.sub(r"^[\-\*•]\s+", "", line)
            else:
                cert = line
            if cert and len(cert) > 2:
                certs.append(cert[:200])
        return certs[:10]

    def _extract_achievements(self, section_text: str) -> list[str]:
        if not section_text:
            return []
        achievements = []
        lines = [l.strip() for l in section_text.splitlines() if l.strip()]
        for line in lines:
            if re.match(r"^[\-\*•]\s+", line):
                ach = re.sub(r"^[\-\*•]\s+", "", line)
            else:
                ach = line
            if ach and len(ach) > 5:
                achievements.append(ach[:300])
        return achievements[:10]

    def _split_entries(self, text: str) -> list[str]:
        if not text:
            return []
        lines = text.splitlines()
        entries = []
        current = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"^[\-\*•\d]\.?\s+", stripped) and current:
                entries.append("\n".join(current))
                current = [stripped]
            elif re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+[-|•]\s+[A-Z]", stripped) and current:
                entries.append("\n".join(current))
                current = [stripped]
            else:
                current.append(stripped)
        if current:
            entries.append("\n".join(current))
        return [e for e in entries if len(e.strip()) > 20]