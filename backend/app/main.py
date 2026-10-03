import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import applications, auto_apply, bot, events, jobs, matching, resumes
from app.config import settings
from app.database.models import Base
from app.database.postgres import reset_postgres_sequences
from app.database.session import engine
from app.logging_config import setup_logging
from app.middleware.request_logging import RequestLoggingMiddleware

setup_logging(settings.log_level)
logger = logging.getLogger("app.main")

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    logger.info("Starting %s", settings.app_name)
    logger.info("Database: %s", settings.database_url.split("@")[-1])
    logger.info("Storage: %s", settings.storage_path)
    logger.info("CORS origins: %s", settings.cors_origin_list)
    logger.info("Embedding fallback: %s", settings.use_embedding_fallback)
    Base.metadata.create_all(bind=engine)
    try:
        reset_postgres_sequences(engine)
    except Exception as exc:
        logger.warning("Sequence reset skipped: %s", exc)
    logger.info("Database tables ready")


@app.on_event("shutdown")
def on_shutdown() -> None:
    logger.info("Shutting down %s", settings.app_name)


@app.get("/")
def root() -> dict:
    return {
        "app": settings.app_name,
        "status": "ok",
        "health": "/health",
        "docs": "/docs",
        "api_prefix": "/api",
    }


def _disk_info() -> dict:
    """Non-secret diagnostics: is the Render persistent disk mounted, and where do we write?"""
    mounted = False
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "/var/data":
                mounted = True
                break
    except Exception:
        pass
    db = settings.database_url
    if db.startswith("sqlite"):
        db = f"sqlite:{db.split('///')[-1]}" if "///" in db else "sqlite:(relative)"
    else:
        db = db.split("@")[-1]
    return {"var_data_mounted": mounted, "storage": str(settings.storage_path), "db": db}


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "api_version": 2,
        "build": "2026-10-03.8",
        "embedding_fallback": settings.use_embedding_fallback,
        "cors_origins": settings.cors_origin_list,
        "disk": _disk_info(),
        "routes": [
            "/api/resumes/upload",
            "/api/jobs/discover/{candidate_id}",
            "/api/applications/candidate/{candidate_id}",
            "/api/events/client",
        ],
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origin_list:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


app.include_router(events.router, prefix="/api")
app.include_router(resumes.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(matching.router, prefix="/api")
app.include_router(applications.router, prefix="/api")
app.include_router(auto_apply.router, prefix="/api")
app.include_router(bot.router, prefix="/api")
