from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy.orm import Session

from app.agents.scraper.registry import SOURCE_CATALOG, build_scrapers
from app.database.models import Candidate, CandidateJob, Job
from app.schemas.profile import CandidateProfile
from app.services.deduplication import job_dedup_hash
from app.services.job_normalize import prepare_job_row
from app.services.pipeline_log import step, step_done, step_fail
from app.services.profile_queries import build_search_queries

logger = logging.getLogger("app.discovery")

SCRAPER_TIMEOUT_SEC = 180.0
JOBS_PER_SOURCE = 50
JOBS_PER_SOURCE_ALL = 150  # worldwide searches pull a bigger raw batch
JOBS_AFTER_FILTER = 35
# Sources that expose the whole feed: fetch broadly, then select locally by profile queries.
BOARD_SOURCES = frozenset({"greenhouse"})
BULK_SOURCES = frozenset({"remotive", "remoteok", "arbeitnow"})


def _query_select(jobs: list[dict], queries: list[str]) -> list[dict]:
    """Rank jobs by resume-query hits (matches first), keeping every job.

    Ranking, not dropping: guest search results have no descriptions yet, so a
    title-only hard filter threw away jobs the MatchingEngine would pass once
    descriptions are enriched. Original order is preserved within hit counts.
    """
    if not jobs:
        return []
    if not queries:
        return jobs
    low_queries = [q.lower() for q in queries]
    scored: list[tuple[int, dict]] = []
    for job in jobs:
        haystack = " ".join(
            [
                str(job.get("title", "")),
                str(job.get("description", "")),
                str(job.get("company", "")),
                " ".join(str(s) for s in (job.get("skills") or [])),
            ]
        ).lower()
        hits = sum(1 for q in low_queries if q in haystack)
        scored.append((hits, job))
    scored.sort(key=lambda pair: -pair[0])
    return [job for _, job in scored]


def _experience_select(matched: list[dict], profile: dict | None) -> list[dict]:
    """Drop jobs that clearly demand far more experience than the resume has.

    Applied only when that would not leave almost nothing (e.g. a fresher
    searching worldwide would otherwise get a gutted list) — in that case
    experience-suited jobs are ranked first instead.
    """
    if not profile:
        return matched
    from app.services.experience_level import estimate_experience_years, is_overqualified

    years = estimate_experience_years(profile)
    if years is None:
        return matched
    before = len(matched)
    suited = [j for j in matched if not is_overqualified(j, years)]
    if not suited:
        return matched
    if len(suited) >= min(20, len(matched)):
        result = suited
        mode = "filtered"
    else:
        rest = [j for j in matched if j not in suited]
        result = suited + rest
        mode = "ranked suited first"
    logger.info(
        "Experience %s: %d jobs kept/%d (candidate years=%s, suited=%d)",
        mode, len(result), before, years, len(suited),
    )
    return result


def _select_jobs_for_profile(
    jobs: list[dict], queries: list[str], limit: int, profile: dict | None = None
) -> list[dict]:
    """Rank by resume-query hits, drop clearly over-leveled jobs, cap at limit."""
    matched = _query_select(jobs, queries)
    matched = _experience_select(matched, profile)
    return matched[:limit]


_REMOTE_LOCATION = re.compile(r"\bremote\b|work[\s-]?from[\s-]?home|\bwfh\b", re.I)
_REMOTE_DESC = re.compile(
    r"fully remote|remote[- ]first|100% remote|work(?:ing)? remotely|remote (?:position|role|job|schedule)",
    re.I,
)
_HYBRID_PAT = re.compile(r"\bhybrid\b", re.I)
_ONSITE_PAT = re.compile(r"on[\s-]?site|in[\s-]?office", re.I)


def _work_type_match(remote_type: str, location: str, description: str, title: str = "") -> bool:
    """Classify a job against the requested work type (any/remote/onsite/hybrid).

    LinkedIn's guest search endpoint ignores the f_WT filter, so Job type is
    applied here from the job's title + location + description text.
    """
    rt = (remote_type or "").lower()
    if rt not in ("remote", "onsite", "hybrid"):
        return True
    loc = location or ""
    head = f"{title or ''} {loc}"
    desc = (description or "")[:3000]
    if rt == "remote":
        return bool(
            _REMOTE_LOCATION.search(head)
            or _REMOTE_DESC.search(desc)
            or _REMOTE_DESC.search(head)
        )
    if rt == "hybrid":
        return bool(_HYBRID_PAT.search(head) or _HYBRID_PAT.search(desc))
    # onsite: explicit on-site wording, or a physical (non-remote) location
    return bool(_ONSITE_PAT.search(desc)) or not _REMOTE_LOCATION.search(head)


def _filter_by_work_type(jobs: list[dict], remote_type: str, min_keep: int = 1) -> list[dict]:
    """Apply the work-type filter; fall back to the raw batch only when
    classification matched nothing (e.g. enrichment throttled to zero)."""
    rt = (remote_type or "").lower()
    if rt not in ("remote", "onsite", "hybrid") or not jobs:
        return jobs
    kept = [
        j
        for j in jobs
        if _work_type_match(
            rt,
            str(j.get("location") or ""),
            str(j.get("description") or ""),
            str(j.get("title") or ""),
        )
    ]
    if len(kept) < min_keep:
        logger.info(
            "Work-type %s: only %d/%d matched — keeping unfiltered batch",
            rt, len(kept), len(jobs),
        )
        return jobs
    logger.info("Work-type %s: kept %d/%d jobs", rt, len(kept), len(jobs))
    return kept


def _pick_queries(queries: list[str], n: int = 3) -> list[str]:
    """Top queries for server-side search — diverse, no substring duplicates."""
    picked: list[str] = []
    for q in queries:
        low = q.lower()
        if any(low == p.lower() or low in p.lower() or p.lower() in low for p in picked):
            continue
        picked.append(q)
        if len(picked) >= n:
            break
    return picked or queries[:1]


async def _search_board_fetch(
    scraper,
    queries: list[str],
    location: str,
    fetch_limit: int,
) -> tuple[list[dict], int | None]:
    """Search a board with several queries, merging and deduping by URL."""
    picked = _pick_queries(queries, 3)
    per_query = max(15, fetch_limit // len(picked)) if len(picked) > 1 else fetch_limit
    merged: list[dict] = []
    seen: set[str] = set()
    best_total: int | None = None
    for qi, q in enumerate(picked):
        if qi:
            await asyncio.sleep(2.5)  # guest search throttles bursts
        if hasattr(scraper, "fetch_jobs_with_count"):
            part, total = await scraper.fetch_jobs_with_count(
                query=q, location=location, limit=per_query
            )
        else:
            part = await scraper.fetch_jobs(query=q, location=location, limit=per_query)
            total = None
        for job in part:
            url = str(job.get("apply_url") or job.get("url") or "")
            if url:
                if url in seen:
                    continue
                seen.add(url)
            merged.append(job)
        if total is not None and (best_total is None or total > best_total):
            best_total = total
    return merged, best_total


async def _fetch_from_scraper(
    scraper,
    queries: list[str],
    location_hint: str,
    query_index: int = 0,
    profile: dict | None = None,
    remote_type: str = "",
) -> tuple[list[dict], int | None, int]:
    """Live search per platform for the candidate's role.

    Every selected platform is searched live (no stored pool). Search boards
    run several short queries and merge; when a city-locked search comes back
    thin (<8 jobs) it is retried country-wide. Bulk / board sources fetch the
    full feed and are filtered locally against every resume query.
    Returns (jobs_kept_for_profile, site_total_available, raw_fetched_count).
    """
    source = scraper.source_name
    if source in BOARD_SOURCES or source in BULK_SOURCES:
        if hasattr(scraper, "fetch_jobs_with_count"):
            raw, _total = await scraper.fetch_jobs_with_count(
                query="", location="", limit=JOBS_PER_SOURCE
            )
        else:
            raw = await scraper.fetch_jobs(query="", location="", limit=JOBS_PER_SOURCE)
            _total = None
        if remote_type:
            raw = _filter_by_work_type(raw, remote_type)
        kept = _select_jobs_for_profile(raw, queries, limit=JOBS_AFTER_FILTER, profile=profile)
        # For bulk feeds the "available for role" is the matched subset.
        total = len(kept) if _total is None else _total
        return kept, total, len(raw or [])
    fetch_limit = JOBS_PER_SOURCE if location_hint else JOBS_PER_SOURCE_ALL
    raw, total = await _search_board_fetch(scraper, queries, location_hint, fetch_limit)
    # City search too thin → retry the country part (e.g. "Jaipur, India" → "India").
    if len(raw) < 8 and "," in (location_hint or ""):
        parts = [p.strip() for p in location_hint.split(",") if p.strip()]
        if len(parts) >= 2 and len(parts[-1]) >= 2:
            wide = parts[-1]
            wide_raw, wide_total = await _search_board_fetch(scraper, queries, wide, fetch_limit)
            if len(wide_raw) > len(raw):
                logger.info(
                    "Location %r returned %d jobs — widened to %r → %d",
                    location_hint, len(raw), wide, len(wide_raw),
                )
                raw, total = wide_raw, wide_total
    if remote_type:
        # Guest search returns no work-type filter (f_WT ignored) and no
        # descriptions — classify from location + enriched description over
        # the query-matched set, BEFORE experience selection drops jobs.
        matched = _query_select(raw, queries)
        if source == "linkedin":
            from app.agents.scraper.sources import enrich_linkedin_descriptions

            await enrich_linkedin_descriptions(matched, max_fetch=30)
        matched = _filter_by_work_type(matched, remote_type)
        kept = _experience_select(matched, profile)[:JOBS_AFTER_FILTER]
        if total is None:
            total = len(kept)
        return kept, total, len(raw or [])
    kept = _select_jobs_for_profile(raw, queries, limit=JOBS_AFTER_FILTER, profile=profile)
    if total is None:
        total = len(kept)
    # Return both kept and raw count for accurate platform stats
    return kept, total, len(raw)


async def _scrape_one(
    scraper,
    queries: list[str],
    location_hint: str,
    query_index: int,
    profile: dict | None = None,
    remote_type: str = "",
) -> tuple[str, list[dict], int | None, int, str | None]:
    source = scraper.source_name
    label = (
        getattr(scraper, "company_label", None)
        or getattr(scraper, "board_token", None)
        or getattr(scraper, "company_slug", None)
        or source
    )
    step(f"SCRAPE_{source.upper()}", target=label)
    try:
        batch, total, raw_count = await asyncio.wait_for(
            _fetch_from_scraper(
                scraper, queries, location_hint, query_index,
                profile=profile, remote_type=remote_type,
            ),
            timeout=SCRAPER_TIMEOUT_SEC,
        )
        if source == "linkedin" and batch:
            from app.agents.scraper.sources import enrich_linkedin_descriptions

            enriched = await enrich_linkedin_descriptions(batch, max_fetch=min(40, len(batch)))
            logger.info("LinkedIn enriched %d/%d job descriptions", enriched, len(batch))
        scraper_error = getattr(scraper, "last_error", None)
        if not batch and scraper_error:
            step_fail(f"SCRAPE_{source.upper()}", scraper_error)
            return source, [], total, raw_count, scraper_error
        step_done(f"SCRAPE_{source.upper()}", target=label, jobs=len(batch), available=total, fetched_raw=raw_count)
        return source, batch, total, raw_count, None
    except asyncio.TimeoutError:
        msg = f"timed out after {SCRAPER_TIMEOUT_SEC}s"
        step_fail(f"SCRAPE_{source.upper()}", msg)
        logger.warning("Source %s (%s) timed out", source, label)
        return source, [], None, 0, msg
    except Exception as exc:
        msg = str(exc) or repr(exc)
        step_fail(f"SCRAPE_{source.upper()}", msg)
        logger.exception("Source %s (%s) failed", source, label)
        return source, [], None, 0, msg


async def discover_jobs_for_candidate(
    db: Session,
    candidate_id: int,
    sources: list[str] | None = None,
    remote_type: str | None = None,
    fresh: bool = True,
    location: str | None = None,
    all_locations: bool = False,
) -> dict:
    """Live discovery for the candidate's role — no stored/static pool reuse.

    Location resolution (search only): all_locations=True → worldwide;
    a non-empty `location` → search in that preferred location instead of
    the resume's location; otherwise fall back to the resume location.

    When fresh=True (default), previous CandidateJob links for this candidate
    are cleared first so `candidate_jobs` and `platform_counts` reflect ONLY
    this live run across the selected platforms. Job rows themselves are kept
    (Application history FK) but never reused as a pool.
    """
    step("DISCOVERY_START", candidate_id=candidate_id)

    candidate = db.get(Candidate, candidate_id)
    if not candidate:
        step_fail("DISCOVERY_START", "candidate not found")
        raise ValueError("Candidate not found")

    profile = CandidateProfile.model_validate(candidate.profile_json)
    queries = build_search_queries(profile.model_dump())

    if all_locations:
        location_hint = ""
        location_mode = "all"
    elif location and location.strip():
        location_hint = " ".join(location.replace("\r", " ").split())[:80]
        location_mode = "preferred"
    else:
        # Location often comes back as "Name\nCity" from bad parsing — keep only last line / city part
        raw_loc = (profile.location or "").replace("\r", "\n")
        if "\n" in raw_loc:
            raw_loc = [p.strip() for p in raw_loc.split("\n") if p.strip()][-1]
        location_hint = " ".join(raw_loc.split())[:80]
        location_mode = "resume"

    step(
        "BUILD_QUERIES",
        count=len(queries),
        queries=",".join(queries[:8]),
        location=location_hint or "all locations",
        location_mode=location_mode,
    )
    scrapers = build_scrapers()

    if sources:
        available = {s.source_name for s in scrapers}
        requested = [s for s in sources if s in available]
        if requested:
            scrapers = [s for s in scrapers if s.source_name in requested]
        step(
            "FILTER_SOURCES",
            filtered=len(scrapers),
            selected=",".join(requested) or "linkedin (default)",
        )
    step(
        "INIT_SOURCES",
        total_sources=len(scrapers),
        mode="parallel",
        max_wait_sec=SCRAPER_TIMEOUT_SEC,
    )

    results = await asyncio.gather(
        *[
            _scrape_one(
                scraper, queries, location_hint, idx,
                profile=profile.model_dump(), remote_type=remote_type or "",
            )
            for idx, scraper in enumerate(scrapers)
        ]
    )

    scraped: list[dict] = []
    source_stats: dict[str, dict] = {}
    platform_counts: dict[str, dict] = {}

    for source, batch, site_total, raw_count, error in results:
        stats = source_stats.setdefault(source, {"fetched": 0, "errors": 0})
        stats["fetched"] += len(batch)
        if error:
            stats["errors"] += 1
            stats["error"] = error
        scraped.extend(batch)
        # Exact available number for this role on this platform:
        # site-reported total when the platform exposes it, else live fetched.
        available = site_total if site_total is not None else len(batch)
        entry = platform_counts.setdefault(
            source, {"fetched": 0, "available": 0, "errors": 0}
        )
        entry["fetched"] += len(batch)
        # Keep the max site total seen across scrapers sharing a source key
        # (e.g. multiple Greenhouse boards roll up under "greenhouse").
        if site_total is not None:
            entry["available"] = max(int(entry["available"] or 0), int(site_total))
        else:
            entry["available"] += len(batch)
        if error:
            entry["errors"] += 1
            entry["error"] = error

    if fresh:
        # Remove stale pool links for the selected sources only so counts = this live run only.
        # Jobs from other sources not searched this time are preserved.
        if sources:
            source_job_ids = (
                db.query(Job.id)
                .filter(Job.source.in_(sources))
                .subquery()
            )
            db.query(CandidateJob).filter(
                CandidateJob.candidate_id == candidate_id,
                CandidateJob.job_id.in_(source_job_ids),
            ).delete(synchronize_session=False)
        else:
            db.query(CandidateJob).filter(CandidateJob.candidate_id == candidate_id).delete()
        db.flush()

    step("DEDUPLICATE_AND_STORE", raw_jobs=len(scraped))
    created = 0
    skipped = 0
    seen_hashes: set[str] = set()
    candidates: list[tuple[dict, str]] = []

    for item in scraped:
        dedup_hash = job_dedup_hash(item["company"], item["title"], item["location"])
        if dedup_hash in seen_hashes:
            skipped += 1
            continue
        seen_hashes.add(dedup_hash)
        candidates.append((item, dedup_hash))

    existing_hashes: set[str] = set()
    if candidates:
        hash_list = [dedup_hash for _, dedup_hash in candidates]
        existing_hashes = {
            row[0] for row in db.query(Job.dedup_hash).filter(Job.dedup_hash.in_(hash_list)).all()
        }

    selected_hashes: list[str] = []
    rows_to_add: list[Job] = []
    backfill: list[tuple[str, dict]] = []
    for item, dedup_hash in candidates:
        if dedup_hash in existing_hashes:
            if remote_type:
                stored = (
                    db.query(Job.remote_type).filter(Job.dedup_hash == dedup_hash).first()
                )
                if stored and stored[0] and stored[0].lower() != remote_type.lower():
                    skipped += 1
                    continue
            if item.get("description"):
                backfill.append((dedup_hash, item))
            selected_hashes.append(dedup_hash)
            continue
        try:
            job_row = prepare_job_row(item, dedup_hash)
            if remote_type and _work_type_match(
                remote_type,
                job_row.location or "",
                job_row.description or "",
                job_row.title or "",
            ):
                if not job_row.remote_type:
                    job_row.remote_type = remote_type
            if remote_type and job_row.remote_type and job_row.remote_type.lower() != remote_type.lower():
                skipped += 1
                continue
            rows_to_add.append(job_row)
            created += 1
        except Exception as exc:
            logger.warning("Skipped job %s @ %s: %s", item.get("title"), item.get("company"), exc)
            skipped += 1

    db.add_all(rows_to_add)
    db.flush()

    if backfill:
        stored_rows = {
            row.dedup_hash: row
            for row in db.query(Job)
            .filter(Job.dedup_hash.in_([h for h, _ in backfill]))
            .all()
        }
        updated = 0
        for dedup_hash, item in backfill:
            row = stored_rows.get(dedup_hash)
            if row and not (row.description or "").strip():
                row.description = item["description"][:50_000]
                if item.get("easy_apply"):
                    row.easy_apply = True
                updated += 1
        if updated:
            logger.info("Backfilled descriptions for %d existing jobs", updated)

    candidate_job_ids: list[int] = []
    if selected_hashes:
        candidate_job_ids = [
            row[0] for row in db.query(Job.id).filter(Job.dedup_hash.in_(selected_hashes)).all()
        ]

    if rows_to_add:
        for row in rows_to_add:
            candidate_job_ids.append(row.id)

    if candidate_job_ids:
        existing_links = {
            row[0]
            for row in db.query(CandidateJob.job_id)
            .filter(
                CandidateJob.candidate_id == candidate_id,
                CandidateJob.job_id.in_(candidate_job_ids),
            )
            .all()
        }
        db.add_all(
            [
                CandidateJob(candidate_id=candidate_id, job_id=job_id)
                for job_id in candidate_job_ids
                if job_id not in existing_links
            ]
        )

    db.commit()
    candidate_job_count = (
        db.query(CandidateJob).filter(CandidateJob.candidate_id == candidate_id).count()
    )
    total_jobs = db.query(Job).count()

    step_done(
        "DISCOVERY_COMPLETE",
        created=created,
        skipped=skipped,
        candidate_jobs=candidate_job_count,
        total_jobs=total_jobs,
        sources=len(source_stats),
    )

    role = queries[0] if queries else "software engineer"
    total_available = sum(int(v.get("available", 0) or 0) for v in platform_counts.values())

    return {
        "candidate_id": candidate_id,
        "role": role,
        "search_queries": queries,
        "search_location": location_hint or "All locations",
        "location_mode": location_mode,
        "scraped": len(scraped),
        "created": created,
        "skipped_duplicates": skipped,
        "candidate_jobs": candidate_job_count,
        "total_jobs_in_db": total_jobs,
        "sources": source_stats,
        "platform_counts": platform_counts,
        "total_available": total_available,
        "fresh": fresh,
        "source_catalog": SOURCE_CATALOG,
    }
