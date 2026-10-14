# ResumeBuild

AI-powered job application platform prototype. Parses resumes into structured JSON, builds a candidate knowledge base with embeddings, discovers jobs, scores matches, personalizes resumes for ATS, and tracks applications.

## Architecture (Prototype Scope)

```
Resume PDF → Parser → Candidate Profile (JSON)
                              ↓
                     FAISS Embeddings
                              ↓
Job Scraper (LinkedIn) → SQLite/PostgreSQL
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
| Job scraping (LinkedIn live search) | ✅ |
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

One command from the repo root (installs deps if needed, then starts **frontend + backend + worker**):

```bash
./dev.sh
```

Same thing: `make dev` or `npm start`.

Windows:

```powershell
.\dev.ps1
```

Then open:

| Service | URL |
|---------|-----|
| App | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |

Ctrl+C stops all three. Copy `.env.example` to `.env` (the script does this once) and add `OPENAI_API_KEY` if you want LLM resume parsing.

The worker uses Redis. If Docker is installed, `./dev.sh` starts the Redis service from `docker-compose.yml`. Without Docker, run Redis on port 6379 yourself, or ignore worker errors — the UI and API still work on SQLite.

### Run pieces separately (optional)

```bash
# Backend
cd backend && source .venv/bin/activate
python -m uvicorn app.main:app --reload --app-dir .

# Frontend
cd frontend && npm run dev

# Worker
PYTHONPATH=backend:. .venv/bin/python -m celery -A worker.tasks:celery_app worker --loglevel=info
```

```bash
docker compose up -d   # optional Postgres + Redis
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/resumes/upload` | Upload PDF, parse, embed |
| GET | `/api/resumes/{id}` | Get candidate profile |
| POST | `/api/jobs` | Create job manually |
| GET | `/api/jobs` | List jobs |
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
