#!/usr/bin/env bash
# Start frontend, backend, and Celery worker with one command.
# Usage (from repo root):  ./dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
VENV="$BACKEND_DIR/.venv"
PIDS=()

log() { printf '\n==> %s\n' "$*"; }

cleanup() {
  trap - INT TERM EXIT
  log "Stopping frontend, backend, and worker…"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

pick_python() {
  if [[ -x "$VENV/bin/python" ]]; then
    echo "$VENV/bin/python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  echo "Need python3 on PATH. Install Python 3.11+ and retry." >&2
  exit 1
}

if [[ ! -f "$ROOT/.env" && -f "$ROOT/.env.example" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  log "Created .env from .env.example — add API keys if you need LLM parsing."
fi
if [[ ! -f "$FRONTEND_DIR/.env.local" ]]; then
  printf 'NEXT_PUBLIC_API_URL=http://localhost:8000\n' > "$FRONTEND_DIR/.env.local"
  log "Created frontend/.env.local"
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  log "Creating backend virtualenv…"
  SYS_PY="$(command -v python3 || command -v python)"
  "$SYS_PY" -m venv "$VENV"
fi
PY="$VENV/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(pick_python)"
fi

log "Installing backend dependencies…"
"$PY" -m pip install -q -r "$BACKEND_DIR/requirements.txt"
"$PY" -m playwright install chromium >/dev/null 2>&1 || true

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  log "Installing frontend packages…"
  (cd "$FRONTEND_DIR" && npm install)
fi

if command -v docker >/dev/null 2>&1; then
  log "Starting Redis (worker broker)…"
  (cd "$ROOT" && docker compose up -d redis) >/dev/null 2>&1 \
    || log "Docker Redis skipped — worker may fail until Redis is on :6379."
else
  log "Docker not found — worker needs Redis at localhost:6379."
fi

export PYTHONPATH="$BACKEND_DIR:$ROOT"

log "Backend  → http://localhost:8000  (docs: /docs)"
(cd "$BACKEND_DIR" && "$PY" -m uvicorn app.main:app --reload --app-dir . --host 0.0.0.0 --port 8000) &
PIDS+=($!)

log "Frontend → http://localhost:3000"
(cd "$FRONTEND_DIR" && npm run dev) &
PIDS+=($!)

log "Worker   → Celery (scraping + matching queues)"
(cd "$ROOT" && "$PY" -m celery -A worker.tasks:celery_app worker --loglevel=info) &
PIDS+=($!)

log "All three running. Ctrl+C stops everything."
wait
