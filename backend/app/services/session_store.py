"""Persist Playwright login sessions (cookies) in the database — never passwords."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.models import PlatformSession

logger = logging.getLogger("app.session_store")

PLATFORMS = ("linkedin", "indeed", "foundit", "naukri", "ziprecruiter", "glassdoor")


def get_session(db: Session, candidate_id: int, platform: str) -> dict | None:
    row = (
        db.query(PlatformSession)
        .filter(
            PlatformSession.candidate_id == candidate_id,
            PlatformSession.platform == platform,
        )
        .first()
    )
    if not row or not row.storage_state:
        return None
    return row.storage_state


def save_session(db: Session, candidate_id: int, platform: str, storage_state: dict) -> None:
    row = (
        db.query(PlatformSession)
        .filter(
            PlatformSession.candidate_id == candidate_id,
            PlatformSession.platform == platform,
        )
        .first()
    )
    if row:
        row.storage_state = storage_state
        row.updated_at = datetime.utcnow()
    else:
        row = PlatformSession(
            candidate_id=candidate_id,
            platform=platform,
            storage_state=storage_state,
        )
        db.add(row)
    db.commit()
    logger.info("Saved %s session for candidate_id=%s", platform, candidate_id)


def save_session_from_file(db: Session, candidate_id: int, platform: str, path: Path) -> None:
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    save_session(db, candidate_id, platform, data)


def export_session_to_file(db: Session, candidate_id: int, platform: str, path: Path) -> bool:
    state = get_session(db, candidate_id, platform)
    if not state:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    return True


def list_sessions(db: Session, candidate_id: int) -> list[dict]:
    rows = db.query(PlatformSession).filter(PlatformSession.candidate_id == candidate_id).all()
    by_platform = {r.platform: r for r in rows}
    result = []
    for platform in PLATFORMS:
        row = by_platform.get(platform)
        result.append(
            {
                "platform": platform,
                "logged_in": row is not None and bool(row.storage_state),
                "updated_at": row.updated_at.isoformat() if row else None,
            }
        )
    return result


def carry_over_sessions(db: Session, new_candidate_id: int) -> int:
    """Copy the newest login session per platform onto a freshly uploaded candidate.

    Logins belong to the person (same browser/machine), not to one upload —
    without this every re-upload looks 'logged out' again.
    Returns the number of sessions carried over.
    """
    latest: dict[str, PlatformSession] = {}
    rows = (
        db.query(PlatformSession)
        .filter(PlatformSession.candidate_id != new_candidate_id)
        .order_by(PlatformSession.updated_at.desc())
        .all()
    )
    for row in rows:
        if row.platform not in latest and row.storage_state:
            latest[row.platform] = row
    carried = 0
    for platform, src in latest.items():
        exists = (
            db.query(PlatformSession)
            .filter(
                PlatformSession.candidate_id == new_candidate_id,
                PlatformSession.platform == platform,
            )
            .first()
        )
        if exists:
            continue
        db.add(
            PlatformSession(
                candidate_id=new_candidate_id,
                platform=platform,
                storage_state=dict(src.storage_state),
            )
        )
        carried += 1
    if carried:
        db.commit()
        logger.info("Carried over %d sessions to candidate_id=%s", carried, new_candidate_id)
    return carried
