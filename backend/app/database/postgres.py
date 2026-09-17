"""Keep Postgres SERIAL sequences aligned after manual ID inserts / SQLite migration."""

from __future__ import annotations

from sqlalchemy import Engine, text

from app.database.models import Application, Candidate, CandidateJob, Job, JobMatch, Resume

TABLES_WITH_SERIAL_ID = (
    Candidate.__tablename__,
    Resume.__tablename__,
    Job.__tablename__,
    JobMatch.__tablename__,
    CandidateJob.__tablename__,
    Application.__tablename__,
)


def reset_postgres_sequences(engine: Engine) -> None:
    if not engine.dialect.name.startswith("postgres"):
        return

    with engine.begin() as conn:
        for table in TABLES_WITH_SERIAL_ID:
            conn.execute(
                text(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 0) + 1,
                        false
                    )
                    """
                )
            )
