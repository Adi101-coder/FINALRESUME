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

        client_kwargs: dict = {
            "api_key": settings.openai_api_key,
            "timeout": settings.openai_timeout_seconds,
        }
        if settings.openai_base_url.strip():
            client_kwargs["base_url"] = settings.openai_base_url.strip()
            client_kwargs["default_headers"] = {
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": settings.app_name,
            }
        client = OpenAI(**client_kwargs)
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

        if not preferred_roles:
            derived = [
                exp.role.strip()
                for exp in experience
                if exp.role and 3 < len(exp.role.strip()) < 70
            ]
            preferred_roles = list(dict.fromkeys(derived))[:5]
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
        # Only the header block (first lines) — never glue name+city across lines.
        head = "\n".join(text.splitlines()[:6])
        for seg in re.split(r"[|\n]", head):
            seg = seg.strip()
            if (
                "," in seg
                and len(seg) <= 60
                and re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*\s*,\s*[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*", seg)
                and not re.search(r"\d|@|https?", seg)
            ):
                return seg
        m = re.search(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", head
        )
        if m:
            return m.group(1)
        m = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z]{2,})\b", head)
        if m:
            return m.group(1)
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

    @staticmethod
    def _header_variants() -> list[str]:
        out: list[str] = []
        for variants in ResumeParser.SECTION_HEADERS.values():
            out.extend(variants)
        return out

    def _is_section_header_line(self, line: str) -> bool:
        """True for section-header lines like 'Experience' or 'Achievements & Honors'."""
        norm = line.strip().rstrip(":：").lower()
        if not norm or len(norm) > 40:
            return False
        for h in self._header_variants():
            if norm == h or norm.startswith(h + " ") or norm.startswith(h + " &") or norm.startswith(h + " and"):
                return True
        return False

    def _split_sections(self, text: str) -> dict[str, str]:
        sections = {}
        header_positions = []

        for section_type, headers in self.SECTION_HEADERS.items():
            for header in headers:
                # Header line: variant at line start, optionally with a short
                # decorated tail ("Achievements & Honors", "Skills: …").
                pattern = rf"(?i)^\s*{re.escape(header)}[^\n]{{0,40}}$"
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
        while lines and self._is_section_header_line(lines[0]):
            lines = lines[1:]
        if not lines:
            return ExperienceItem()

        company = ""
        role = ""
        location = ""
        start_date = ""
        end_date = ""
        bullets: list[str] = []

        date_pattern = "|".join(self.DATE_PATTERNS)
        date_re = re.compile(date_pattern, re.I)

        first_line = lines[0]
        remote_m = re.match(r"^(.*?)\s+(Remote|Hybrid|On[- ]?site)\b(.*)$", first_line, re.I)
        if remote_m:
            company = remote_m.group(1).strip() or first_line
            location = (remote_m.group(2) + " " + (remote_m.group(3) or "")).strip()
        else:
            parts = re.split(r"\s{2,}|\s*\|\s*|\t", first_line)
            parts = [p.strip() for p in parts if p.strip()]
            if parts:
                company = parts[0].lstrip("•-* ")
            if len(parts) >= 2:
                location = parts[1]

        # Second non-bullet line = role line (title ± dates ± location)
        role_line = ""
        for line in lines[1:]:
            if self._BULLET_RE.match(line):
                continue
            role_line = line
            break
        if role_line:
            without_dates = date_re.sub("", role_line)
            role = re.sub(r"\s*[,;|–—-]\s*$", "", without_dates).strip(" -,–—|")
            role = re.sub(r"\s{2,}", " ", role)
            if not location:
                loc_parts = re.split(r"\s{2,}|\s*\|\s*", role_line)
                if len(loc_parts) >= 2 and not date_re.search(loc_parts[-1]):
                    location = loc_parts[-1].strip()

        dates = date_re.findall(entry)
        year_dates = [d for d in dates if re.search(r"(?:19|20)\d{2}", d)]
        if year_dates:
            start_date = year_dates[0]
            end_date = year_dates[1] if len(year_dates) > 1 else (dates[dates.index(year_dates[0]) + 1] if dates else "")
        elif dates:
            start_date = dates[0]

        for line in lines[1:]:
            if self._BULLET_RE.match(line):
                bullets.append(self._BULLET_RE.sub("", line).strip())
            elif line != role_line and not date_re.search(line):
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
        while lines and self._is_section_header_line(lines[0]):
            lines = lines[1:]
        if not lines:
            return ProjectItem()

        name = lines[0]
        description = ""
        technologies: list[str] = []
        bullets: list[str] = []
        url = ""

        url_match = self.URL_RE.search(entry)
        if url_match:
            url = url_match.group(0)

        if "|" in name:
            name, _, tech = name.partition("|")
            name = name.strip()
            technologies = [t.strip() for t in re.split(r"[,/]|&", tech) if t.strip()]

        tech_pattern = r"(?:tech|technologies|stack|tools|languages|frameworks)\s*[:\-]\s*([^\n]+)"
        tech_match = re.search(tech_pattern, entry, re.I)
        if tech_match and not technologies:
            tech_text = tech_match.group(1)
            technologies = [t.strip() for t in re.split(r"[,/|;]", tech_text) if t.strip()]

        for line in lines[1:]:
            if self._BULLET_RE.match(line):
                bullets.append(self._BULLET_RE.sub("", line).strip())
            elif line and not description:
                description = line

        return ProjectItem(
            name=name[:120],
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
        while lines and self._is_section_header_line(lines[0]):
            lines = lines[1:]
        if not lines:
            return EducationItem()

        institution = ""
        degree = ""
        field = ""
        start_date = ""
        end_date = ""
        cgpa = ""

        institution = lines[0]

        date_pattern = "|".join(self.DATE_PATTERNS)
        date_re = re.compile(date_pattern, re.I)

        for line in lines[1:]:
            if self._BULLET_RE.match(line):
                continue
            if not degree and re.search(r"(?i)b\.?tech|m\.?tech|b\.?e\.?|m\.?e\.?|bachelor|master|mba|bca|mca|b\.?sc|m\.?sc|ph\.?d|diploma|degree", line):
                degree = line.split(";")[0].split("CGPA")[0].split(" GPA")[0].strip()
                paren = re.search(r"\(([^)]+)\)", degree)
                if paren and not field:
                    field = paren.group(1).strip()
                if not field:
                    m = re.search(r"(?i)\bin\s+([A-Z][A-Za-z &]+?)(?:\s*[;(]|$)", degree)
                    if m:
                        field = m.group(1).strip()

        dates = date_re.findall(entry)
        year_dates = [d for d in dates if re.search(r"(?:19|20)\d{2}", d)]
        if year_dates:
            start_date = year_dates[0]
            if len(year_dates) > 1:
                end_date = year_dates[1]

        cgpa_match = re.search(r"(?:cgpa|gpa|grade|percentage)\s*[:\-]\s*([\d.]+)", entry, re.I)
        if cgpa_match:
            cgpa = cgpa_match.group(1)

        if not degree:
            for line in lines[1:]:
                if not self._BULLET_RE.match(line) and not date_re.fullmatch(line.strip()):
                    degree = line.split(";")[0].strip()
                    break

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
        certs: list[str] = []
        for raw in section_text.splitlines():
            line = raw.strip()
            if not line or self._is_section_header_line(line):
                continue
            line = self._BULLET_RE.sub("", line).strip() if self._BULLET_RE.match(line) else line
            if not line or len(line) <= 2:
                continue
            if certs and self._is_continuation(line, certs[-1]):
                certs[-1] = (certs[-1] + " " + line).strip()
            else:
                certs.append(line[:300])
        return [c[:200] for c in certs[:10]]

    def _extract_achievements(self, section_text: str) -> list[str]:
        if not section_text:
            return []
        achievements: list[str] = []
        for raw in section_text.splitlines():
            line = raw.strip()
            if not line or self._is_section_header_line(line):
                continue
            line = self._BULLET_RE.sub("", line).strip() if self._BULLET_RE.match(line) else line
            if not line or len(line) <= 5:
                continue
            if achievements and self._is_continuation(line, achievements[-1]):
                achievements[-1] = (achievements[-1] + " " + line).strip()
            else:
                achievements.append(line[:300])
        return achievements[:10]

    @staticmethod
    def _is_continuation(line: str, prev: str) -> bool:
        # A new item with its own year marker is never a wrap.
        if line[:1].isupper() and re.search(r"\((?:19|20)\d{2}\)", line):
            return False
        starts_lower = bool(line[:1].isalpha()) and line[0].islower()
        prev_finished = bool(re.search(r"[.!?][\"')\]]*$", prev.strip()))
        return starts_lower or not prev_finished

    _BULLET_RE = re.compile(r"^[\-\*•‣▪●○◆·]\s+")
    _NUMBERED_RE = re.compile(r"^\d{1,2}[.)]\s+")

    def _split_entries(self, text: str) -> list[str]:
        """Group section content into entries.

        Bullets always belong to the CURRENT entry (they never start a new one);
        section-header lines and blank lines flush the current entry.
        """
        if not text:
            return []
        entries: list[str] = []
        current: list[str] = []

        def flush() -> None:
            nonlocal current
            if current:
                body = "\n".join(current).strip()
                if len(body) > 20 and any(len(l.strip()) > 15 for l in current):
                    entries.append(body)
                current = []

        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                flush()
                continue
            if self._is_section_header_line(line):
                flush()
                continue
            if self._BULLET_RE.match(line):
                current.append(line)
                continue
            if not current:
                current = [line]
                continue
            if self._NUMBERED_RE.match(line) or self._looks_like_new_entry(line, current):
                flush()
                current = [line]
                continue
            if any(self._BULLET_RE.match(l) for l in current):
                # After a bullet: a lowercase line, or a line continuing a
                # sentence (previous line lacks terminal punctuation), is a
                # wrapped continuation — not a new entry.
                starts_lower = bool(line[:1].isalpha()) and line[0].islower()
                prev_finished = bool(re.search(r"[.!?][\"')\]]*$", current[-1].strip()))
                if starts_lower or not prev_finished:
                    if self._BULLET_RE.match(current[-1]):
                        current[-1] = current[-1] + " " + line
                    else:
                        current.append(line)
                else:
                    flush()
                    current = [line]
            else:
                current.append(line)
        flush()
        return entries

    def _looks_like_new_entry(self, line: str, current: list[str]) -> bool:
        """A non-bullet line carrying its own year range starts a new record
        when the current entry already has dated lines."""
        if re.search(r"(?:19|20)\d{2}\s*(?:[-–—]|to)\s*(?:present|(?:19|20)\d{2})", line, re.I):
            return any(re.search(r"(?:19|20)\d{2}", l) for l in current)
        return False