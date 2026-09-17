"""One-time copy of local SQLite data into Postgres (Neon)."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.database.models import Application, Candidate, CandidateJob, Job, JobMatch, Resume  # noqa: E402
from app.database.postgres import reset_postgres_sequences  # noqa: E402

SQLITE_PATH = BACKEND_DIR / "resumebuild.db"
TABLES = (Candidate, Resume, Job, JobMatch, CandidateJob, Application)


def main() -> None:
    if not SQLITE_PATH.exists():
        print(f"No SQLite file at {SQLITE_PATH}")
        return

    if settings.database_url.startswith("sqlite"):
        print("Set DATABASE_URL to Postgres in root .env before running this script.")
        sys.exit(1)

    src_engine = create_engine(f"sqlite:///{SQLITE_PATH}")
    dst_engine = create_engine(settings.database_url, pool_pre_ping=True)

    Src = sessionmaker(bind=src_engine)
    Dst = sessionmaker(bind=dst_engine)

    src = Src()
    dst = Dst()

    try:
        for model in TABLES:
            name = model.__tablename__
            rows = src.query(model).order_by(model.id).all()
            existing = dst.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
            if existing:
                print(f"Skip {name}: already has {existing} rows in Postgres")
                continue

            for row in rows:
                data = {c.name: getattr(row, c.name) for c in model.__table__.columns}
                dst.add(model(**data))

            dst.commit()
            print(f"Copied {len(rows)} rows -> {name}")

        reset_postgres_sequences(dst_engine)
        print("Reset Postgres ID sequences.")
        print("Migration complete.")
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
