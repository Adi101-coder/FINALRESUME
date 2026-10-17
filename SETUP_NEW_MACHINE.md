# Running ResumeBuild on a New Machine

Use this guide when setting up the project on someone else's computer from scratch. It lists what to install, what secrets to gather, and how to verify everything works.

## What you are running

| Piece | Purpose | Default URL |
|-------|---------|-------------|
| **Next.js frontend** | Web UI | http://localhost:3000 |
| **FastAPI backend** | API, parsing, matching, PDFs | http://localhost:8000 |
| **API docs** | Interactive OpenAPI UI | http://localhost:8000/docs |
| **Celery worker** | Background jobs (scraping queues) | No browser URL |
| **Redis** | Message broker for the worker | `localhost:6379` |
| **PostgreSQL** (optional) | Primary DB if configured | `localhost:5432` |

The repo root script `./dev.sh` (or `npm start` / `make dev`) starts frontend, backend, and worker together. On Windows use `.\dev.ps1`.

---

## 1. Prerequisites (install on the new machine)

### Required

| Tool | Version | Why |
|------|---------|-----|
| **Git** | Any recent | Clone the repository |
| **Python** | **3.11+** | Backend API, worker, Playwright automation |
| **Node.js** | **18.18+** (20 LTS recommended) | Next.js 15 frontend |
| **npm** | Comes with Node | Frontend dependencies |

Check versions:

```bash
git --version
python3 --version   # or: python --version
node --version
npm --version
```

### Strongly recommended

| Tool | Why |
|------|-----|
| **Docker Desktop** (or Docker Engine + Compose) | Starts **Redis** automatically via `dev.sh`; easy **PostgreSQL** if you use the default `.env.example` database URL |

`dev.sh` runs `docker compose up -d redis` when Docker is available. It does **not** start Postgres for you.

### Optional (feature-specific)

| Tool / library | When you need it |
|----------------|------------------|
| **Playwright Chromium** | PDF generation paths, browser auto-apply — installed by `dev.sh` via `playwright install chromium` |
| **WeasyPrint system libraries** | HTML→PDF on Linux if WeasyPrint fails — on Debian/Ubuntu often: `libpango-1.0-0`, `libgdk-pixbuf-2.0-0`, `libcairo2`, etc. (see [WeasyPrint docs](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)) |
| **Google Chrome** | Some deployment/Docker setups use system Chrome; local dev usually uses Playwright’s Chromium |

---

## 2. Get the code

```bash
git clone <your-repo-url> ResumeBuild
cd ResumeBuild
```

Use whichever remote you were given (for example `origin` on your team’s Git host). This project does not require a specific remote name.

---

## 3. Environment files

### Root `.env`

On first run, `./dev.sh` copies `.env.example` → `.env` if `.env` is missing. You should still review and edit it.

```bash
cp .env.example .env   # if you prefer to create it before dev.sh
```

### Frontend `frontend/.env.local`

`dev.sh` creates this if missing:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

The browser must reach the backend at this URL. If the API runs on another host or port, update both `.env` (`NEXT_PUBLIC_API_URL`) and `frontend/.env.local`.

### Never commit secrets

Keep `.env` and `frontend/.env.local` local. Do not push API keys or session cookies to Git.

---

## 4. Secrets and API keys (what to prepare)

### Tier 1 — Best experience (not strictly required to boot)

| Variable | Required? | Purpose |
|----------|-----------|---------|
| `OPENAI_API_KEY` | Optional | LLM resume parsing (deeper than heuristics) |
| `OPENAI_BASE_URL` | Optional | Leave empty for OpenAI; set to `https://openrouter.ai/api/v1` for OpenRouter |
| `OPENAI_MODEL` | Optional | e.g. `gpt-4o-mini` or an OpenRouter model id |

Without `OPENAI_API_KEY`, upload still works using the **heuristic** PDF parser.

### Tier 2 — Matching / embeddings

| Variable | Notes |
|----------|--------|
| `EMBEDDING_MODEL` | Default `sentence-transformers/all-MiniLM-L6-v2` — **first run downloads ~90MB** |
| `EMBEDDING_FALLBACK` | Set `true` on low-memory hosts to skip the heavy model |

### Tier 3 — Job discovery (optional)

| Variable | Purpose |
|----------|---------|
| `INDEED_DOMAIN` | e.g. `www.indeed.com` or `www.indeed.co.in` |
| `GREENHOUSE_BOARDS` | Comma-separated Greenhouse board slugs |
| `LINKEDIN_ACCESS_TOKEN` / `LINKEDIN_SESSION_COOKIE` | When LinkedIn blocks anonymous scraping |
| `GLASSDOOR_SESSION_COOKIE` | Glassdoor access |
| `FOUNDIT_SESSION_COOKIE`, `NAUKRI_*` | India-focused boards when configured |

Many flows work with public RSS/API paths; cookies are for when boards rate-limit or block datacenter IPs.

### Tier 4 — Database

Pick **one** approach before starting the backend.

**Option A — PostgreSQL via Docker (matches `.env.example`)**

```bash
docker compose up -d postgres redis
```

Ensure `.env` contains:

```env
DATABASE_URL=postgresql://resumebuild:resumebuild@localhost:5432/resumebuild
```

**Option B — SQLite (simplest for a quick local demo)**

In `.env`:

```env
DATABASE_URL=sqlite:///./resumebuild.db
```

Tables are created automatically on API startup (`create_all`). You still want Redis if you care about the Celery worker.

**Option C — Cloud Postgres (Neon, Supabase, Railway)**

Paste the provider’s connection string into `DATABASE_URL` (keep `?sslmode=require` if the provider requires SSL).

### Tier 5 — Tuning

| Variable | Default (example) | Meaning |
|----------|-------------------|---------|
| `MATCH_THRESHOLD` | `75` | Minimum match % to treat a job as a good fit |
| `TOP_JOBS_LIMIT` | `50` | Cap on jobs returned per discovery |
| `DEBUG` | `true` | Verbose app behavior |
| `LOG_LEVEL` / `SQL_ECHO` | `DEBUG` / `true` | Logging; turn down after debugging |

### Auto-apply (browser automation)

If you use auto-apply features, the backend stores resumes under `storage/auto_apply/` and may open real job sites in a browser. You need:

- Playwright Chromium installed (`playwright install chromium`)
- Valid session cookies for the boards you automate (see Tier 3)
- Awareness of each site’s Terms of Service

Config keys such as `AUTO_APPLY_*` live in `backend/app/config.py` with defaults; override via env if your deployment documents them.

---

## 5. First-time install and run

From the **repository root**:

```bash
chmod +x dev.sh    # macOS/Linux, once
./dev.sh
```

Equivalent:

```bash
npm start
# or
make dev
```

**Windows (PowerShell):**

```powershell
.\dev.ps1
```

The script will:

1. Create `backend/.venv` and `pip install -r backend/requirements.txt`
2. Install Playwright Chromium (best effort)
3. Run `npm install` in `frontend/` if needed
4. Start Redis via Docker if Docker is installed
5. Start backend (port **8000**), frontend (**3000**), and Celery worker

Stop everything with **Ctrl+C** in the terminal running `dev.sh`.

---

## 6. Verify it works

1. Open http://localhost:3000 — UI loads without console errors about API connection.
2. Open http://localhost:8000/docs — Swagger UI loads.
3. Upload a resume PDF in the app (or `POST /api/resumes/upload` via docs).
4. If the worker fails but UI works: check Redis (`docker compose ps` or `redis-cli ping`). Core API can run on SQLite without the worker for many UI flows.

---

## 7. Run services separately (optional)

```bash
# Terminal 1 — backend
cd backend && source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m uvicorn app.main:app --reload --app-dir . --host 0.0.0.0 --port 8000

# Terminal 2 — frontend
cd frontend && npm run dev

# Terminal 3 — worker (from repo root)
export PYTHONPATH=backend:.              # Windows: set PYTHONPATH=backend;.
python -m celery -A worker.tasks:celery_app worker --loglevel=info
```

Infrastructure only:

```bash
docker compose up -d postgres redis
```

---

## 8. Disk and generated data

These paths are created at runtime (do not need to be in Git):

| Path | Contents |
|------|----------|
| `storage/` | Uploaded resumes, generated PDFs, FAISS index, auto-apply assets |
| `backend/.venv/` | Python virtual environment |
| `frontend/node_modules/` | Node dependencies |
| `resumebuild.db` | SQLite file (if using SQLite; location depends on cwd) |
| Docker volume `postgres_data` | Postgres data when using Compose |

---

## 9. Common problems

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Backend exits on startup, Postgres errors | `.env` points at Postgres but DB not running | `docker compose up -d postgres` or switch to SQLite |
| Worker errors about Redis | Redis not running | `docker compose up -d redis` or install Redis on `:6379` |
| Frontend cannot reach API | Wrong API URL | Match `NEXT_PUBLIC_API_URL` in `frontend/.env.local` to backend port |
| Slow first match/upload | Embedding model download | Wait once, or set `EMBEDDING_FALLBACK=true` |
| Playwright / PDF failures | Browser not installed | `backend/.venv/bin/python -m playwright install chromium` |
| `pip install` fails on Apple Silicon | Old Python or missing build tools | Use Python 3.11+ from python.org or Homebrew; upgrade pip |
| Port already in use | Another app on 3000/8000 | Stop the other process or change ports in dev commands |

---

## 10. Minimal checklist for a new teammate

Copy this list when handing off a machine setup:

- [ ] Git, Python 3.11+, Node 18+ installed
- [ ] Repository cloned
- [ ] `.env` created from `.env.example` and database choice made (Postgres **or** SQLite)
- [ ] If using Postgres: `docker compose up -d postgres` (and Redis for worker)
- [ ] `OPENAI_API_KEY` added (optional but recommended for parsing quality)
- [ ] `./dev.sh` or `.\dev.ps1` runs without errors
- [ ] http://localhost:3000 and http://localhost:8000/docs open successfully
- [ ] Test resume upload works

For day-to-day development details and API overview, see [README.md](./README.md).
