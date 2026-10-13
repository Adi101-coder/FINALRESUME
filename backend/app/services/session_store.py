"""Persist Playwright login sessions (cookies) in the database — never passwords."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import PlatformSession

logger = logging.getLogger("app.session_store")

PLATFORMS = ("linkedin",)

_LINKEDIN_AUTH_COOKIES = frozenset({"li_at"})


def linkedin_session_logged_in(storage_state: dict | None) -> bool:
    """True when Playwright state includes LinkedIn's auth cookie (not guest-only)."""
    if not storage_state:
        return False
    cookies = storage_state.get("cookies") or []
    names = {c.get("name") for c in cookies if isinstance(c, dict)}
    return bool(names & _LINKEDIN_AUTH_COOKIES)


def global_session_path(platform: str) -> Path:
    return settings.storage_path / "browser_sessions" / f"{platform}_session.json"


def _read_session_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_best_linkedin_session(db: Session) -> dict | None:
    """Newest LinkedIn storage_state that includes li_at (any candidate)."""
    rows = (
        db.query(PlatformSession)
        .filter(PlatformSession.platform == "linkedin")
        .order_by(PlatformSession.updated_at.desc())
        .all()
    )
    for row in rows:
        if linkedin_session_logged_in(row.storage_state):
            return row.storage_state
    file_state = _read_session_file(global_session_path("linkedin"))
    if linkedin_session_logged_in(file_state):
        return file_state
    return None


def get_effective_session(db: Session, candidate_id: int, platform: str) -> dict | None:
    """Session for this candidate, else the best saved login on this machine (login once)."""
    own = get_session(db, candidate_id, platform)
    if platform == "linkedin":
        if linkedin_session_logged_in(own):
            return own
        best = get_best_linkedin_session(db)
        if best:
            return best
        return own
    return own


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
    if platform == "linkedin" and not linkedin_session_logged_in(storage_state):
        existing = get_session(db, candidate_id, platform)
        if linkedin_session_logged_in(existing):
            logger.info(
                "Skipped saving guest LinkedIn cookies for candidate_id=%s (keeping existing login)",
                candidate_id,
            )
            return
        logger.warning(
            "Refusing to save LinkedIn session without li_at for candidate_id=%s",
            candidate_id,
        )
        return

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

    if platform == "linkedin":
        path = global_session_path(platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(storage_state), encoding="utf-8")
        _attach_linkedin_session_to_all_candidates(db, storage_state, source_candidate_id=candidate_id)


def _attach_linkedin_session_to_all_candidates(
    db: Session, storage_state: dict, *, source_candidate_id: int
) -> None:
    """One LinkedIn login applies to every profile on this install."""
    from app.database.models import Candidate

    if not linkedin_session_logged_in(storage_state):
        return
    state_copy = dict(storage_state)
    for (cid,) in db.query(Candidate.id).all():
        row = (
            db.query(PlatformSession)
            .filter(
                PlatformSession.candidate_id == cid,
                PlatformSession.platform == "linkedin",
            )
            .first()
        )
        if row:
            if not linkedin_session_logged_in(row.storage_state) or cid == source_candidate_id:
                row.storage_state = state_copy
                row.updated_at = datetime.utcnow()
        else:
            db.add(
                PlatformSession(
                    candidate_id=cid,
                    platform="linkedin",
                    storage_state=state_copy,
                )
            )
    db.commit()


def save_session_from_file(db: Session, candidate_id: int, platform: str, path: Path) -> None:
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    save_session(db, candidate_id, platform, data)


def export_session_to_file(db: Session, candidate_id: int, platform: str, path: Path) -> bool:
    state = get_effective_session(db, candidate_id, platform)
    if not state:
        return False
    if platform == "linkedin" and not linkedin_session_logged_in(state):
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
        effective = get_effective_session(db, candidate_id, platform)
        logged_in = (
            linkedin_session_logged_in(effective)
            if platform == "linkedin"
            else bool(effective)
        )
        updated = row.updated_at.isoformat() if row and row.updated_at else None
        result.append(
            {
                "platform": platform,
                "logged_in": logged_in,
                "updated_at": updated,
            }
        )
    return result


def carry_over_sessions(db: Session, new_candidate_id: int) -> int:
    """Copy the newest valid login per platform onto a freshly uploaded candidate."""
    latest: dict[str, PlatformSession] = {}
    rows = (
        db.query(PlatformSession)
        .filter(PlatformSession.candidate_id != new_candidate_id)
        .order_by(PlatformSession.updated_at.desc())
        .all()
    )
    for row in rows:
        if row.platform in latest:
            continue
        if row.platform == "linkedin" and not linkedin_session_logged_in(row.storage_state):
            continue
        if row.storage_state:
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
        if exists and linkedin_session_logged_in(exists.storage_state):
            continue
        if exists:
            exists.storage_state = dict(src.storage_state)
            exists.updated_at = datetime.utcnow()
        else:
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
    else:
        best = get_best_linkedin_session(db)
        if best:
            save_session(db, new_candidate_id, "linkedin", dict(best))
            carried = 1
    return carried
