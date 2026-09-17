# ResumeBuild

AI-powered job application platform prototype. Parses resumes into structured JSON, builds a candidate knowledge base with embeddings, discovers jobs, scores matches, personalizes resumes for ATS, and tracks applications.

## Architecture (Prototype Scope)

```
Resume PDF → Parser → Candidate Profile (JSON)
                              ↓
                     FAISS Embeddings
                              ↓
Job Scraper (RemoteOK/Greenhouse) → PostgreSQL
                              ↓
                     Matching Engine (≥75% threshold)
                              ↓
              Resume Optimizer → HTML/PDF Generator
                              ↓
                   Application Tracker + Analytics
```

## What's Included

| Phase | Status |
|-------|--------|
| Resume PDF parsing (heuristic + optional OpenAI) | ✅ |
| Structured JSON profile | ✅ |
| FAISS embedding store | ✅ |
| Job scraping (RemoteOK API) | ✅ |
| PostgreSQL models + deduplication | ✅ |
| Multi-signal matching engine | ✅ |
| Resume personalization (reorder, no hallucination) | ✅ |
| ATS score estimator | ✅ |
| HTML → PDF generation (Playwright/WeasyPrint) | ✅ |
| Application tracking + analytics API | ✅ |
| Next.js frontend | ✅ |
| Browser automation adapters | 🔲 stubs |
| Celery workers | 🔲 stubs |
| Cover letter generator | 🔲 next |
| CAPTCHA / email agents | 🔲 next |

## Quick Start

### 1. Infrastructure (optional)

For PostgreSQL + Redis:

```bash
docker compose up -d
cp .env.example .env
```

By default the backend uses **SQLite** (`backend/resumebuild.db`) so you can run without Docker.

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload --app-dir .
```

API docs: http://localhost:8000/docs

Live discovery (no static pool — every run searches selected platforms fresh):

```bash
curl -X POST "http://localhost:8000/api/jobs/discover/1?sources=linkedin,indeed,foundit,naukri&fresh=true"
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI: http://localhost:3000

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/resumes/upload` | Upload PDF, parse, embed |
| GET | `/api/resumes/{id}` | Get candidate profile |
| POST | `/api/jobs` | Create job manually |
| GET | `/api/jobs` | List jobs |
| POST | `/api/jobs/scrape/remoteok` | Scrape RemoteOK jobs |
| POST | `/api/matching/{candidate_id}` | Score all jobs |
| POST | `/api/matching/{candidate_id}/personalize/{job_id}` | Optimize + PDF |
| POST | `/api/applications` | Track application |
| GET | `/api/applications/analytics/{candidate_id}` | Dashboard metrics |

## Configuration

Set `OPENAI_API_KEY` in `.env` for LLM-based resume parsing. Without it, the heuristic parser extracts skills, contact info, and links.

Matching rejects jobs below `MATCH_THRESHOLD` (default 75).

## Folder Structure

```
backend/
  app/
    agents/       # resume, matcher, scraper, browser
    api/routes/   # FastAPI routers
    database/     # SQLAlchemy models
    services/     # embeddings, deduplication
  scripts/
frontend/
worker/
templates/
storage/
```

## Next Steps

1. Wire Temporal/LangGraph for durable apply workflows
2. Add Greenhouse/Lever/Workday Playwright adapters
3. Cover letter agent
4. Clerk/Auth.js authentication
5. Qdrant migration for production vector search
6. Rate-limited scraping with Celery queues

## Notes

- Resume optimizer **never invents** experience — it only reorders and rewrites existing content.
- Duplicate jobs are blocked via SHA-256 hash of company + title + location.
- Respect site Terms of Service; prefer official APIs over browser automation.


okh first listen to me i give you api of which you wnat first remove job pool or stored static data i wnat you to search every selected platform for job and then tell me exect number of avalible jobs for that role and auto apply after that