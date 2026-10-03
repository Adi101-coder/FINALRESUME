import json
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def parse_cors_origins(raw: str) -> list[str]:
    value = (raw or "").strip()
    if not value:
        return ["http://localhost:3000"]
    if value.startswith("["):
        parsed = json.loads(value)
        return [str(v).strip() for v in parsed if str(v).strip()]
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "ResumeBuild"
    debug: bool = True
    log_level: str = "DEBUG"
    sql_echo: bool = True

    database_url: str = "sqlite:///./resumebuild.db"
    redis_url: str = "redis://localhost:6379/0"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Set true on Render free/starter to avoid OOM from downloading the ~90MB model.
    embedding_fallback: bool = False
    openai_timeout_seconds: float = 25.0

    auto_apply_resumes_path: Path = BASE_DIR / "storage" / "auto_apply"

    storage_path: Path = BASE_DIR / "storage"
    # Resolve relative to this package (backend/), not the repo root — in the
    # Docker image BASE_DIR points at "/", where templates wouldn't exist.
    templates_path: Path = Path(__file__).resolve().parent.parent / "templates"
    vector_index_path: Path = BASE_DIR / "storage" / "faiss_index"

    match_threshold: float = 75.0
    top_jobs_limit: int = 200

    # Plain string env var — never use list[str] here (Render/pydantic JSON parsing breaks).
    allowed_origins: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("CORS_ORIGINS", "ALLOWED_ORIGINS"),
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return parse_cors_origins(self.allowed_origins)

    @property
    def use_embedding_fallback(self) -> bool:
        """Skip the ~90MB sentence-transformers model in production (Render free tier)."""
        if self.embedding_fallback:
            return True
        return not self.debug

    # Job boards — Indeed, ZipRecruiter, LinkedIn, Glassdoor + Greenhouse career pages
    # + Foundit + Naukri (India-first: LinkedIn, Indeed, Foundit, Naukri)
    indeed_domain: str = "www.indeed.com"
    indeed_session_cookie: str = ""
    greenhouse_boards: str = "stripe,figma,airbnb,netflix,datadog"
    linkedin_access_token: str = ""
    linkedin_session_cookie: str = ""
    glassdoor_session_cookie: str = ""
    foundit_session_cookie: str = ""
    naukri_session_cookie: str = ""
    naukri_nkparam: str = ""
    naukri_app_id: str = ""
    naukri_system_id: str = ""


settings = Settings()

# A literal default DATABASE_URL means "never configured" (set in the Render
# dashboard as a placeholder). If the persistent disk at /var/data is mounted,
# keep SQLite on the durable volume instead of the container's ephemeral FS.
if settings.database_url == "sqlite:///./resumebuild.db":
    try:
        _mounts = Path("/proc/mounts").read_text()
        if any(line.split()[1] == "/var/data" for line in _mounts.splitlines() if line.split()):
            settings.database_url = "sqlite:////var/data/resumebuild.db"
    except Exception:
        pass

settings.storage_path.mkdir(parents=True, exist_ok=True)
settings.auto_apply_resumes_path.mkdir(parents=True, exist_ok=True)
(settings.storage_path / "resumes").mkdir(parents=True, exist_ok=True)
(settings.storage_path / "generated").mkdir(parents=True, exist_ok=True)
settings.vector_index_path.mkdir(parents=True, exist_ok=True)
