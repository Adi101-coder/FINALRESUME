from __future__ import annotations

import logging

from app.agents.scraper.sources import (
    ArbeitnowScraper,
    FounditScraper,
    GlassdoorScraper,
    GreenhouseBoardScraper,
    IndeedScraper,
    LinkedInScraper,
    NaukriScraper,
    RemoteOKScraper,
    RemotiveScraper,
    ZipRecruiterScraper,
)
from app.config import settings

logger = logging.getLogger("app.scraper.registry")

SOURCE_CATALOG = {
    "linkedin": {
        "name": "LinkedIn",
        "auth": "optional session cookie",
        "status": "active",
    },
    "indeed": {"name": "Indeed", "auth": "none (RSS)", "status": "active"},
    "foundit": {
        "name": "Foundit",
        "auth": "optional session cookie (blocks datacenter IPs)",
        "status": "active",
    },
    "naukri": {
        "name": "Naukri.com",
        "auth": "optional nkparam + session cookie (login recommended)",
        "status": "active",
    },
    "ziprecruiter": {"name": "ZipRecruiter", "auth": "none", "status": "active"},
    "glassdoor": {
        "name": "Glassdoor",
        "auth": "optional session cookie",
        "status": "active",
    },
    "greenhouse": {
        "name": "Greenhouse",
        "auth": "board slugs",
        "status": "configurable",
    },
    "remotive": {
        "name": "Remotive",
        "auth": "none (free API)",
        "status": "active",
    },
    "remoteok": {
        "name": "RemoteOK",
        "auth": "none (free API)",
        "status": "active",
    },
    "arbeitnow": {
        "name": "Arbeitnow",
        "auth": "none (free API)",
        "status": "active",
    },
}


def _parse_boards(raw: str) -> list[tuple[str, str]]:
    """Parse 'stripe,figma' or 'Stripe:stripe,Acme:acme-board'."""
    pairs: list[tuple[str, str]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            label, slug = chunk.split(":", 1)
            pairs.append((slug.strip(), label.strip()))
        else:
            pairs.append((chunk, chunk.replace("-", " ").title()))
    return pairs


def build_scrapers() -> list:
    scrapers = [
        LinkedInScraper(),
        IndeedScraper(),
        FounditScraper(),
        NaukriScraper(),
        ZipRecruiterScraper(),
        GlassdoorScraper(),
        RemotiveScraper(),
        RemoteOKScraper(),
        ArbeitnowScraper(),
    ]

    for slug, label in _parse_boards(settings.greenhouse_boards):
        scrapers.append(GreenhouseBoardScraper(slug, label))

    logger.info(
        "Initialized %d job adapters (LinkedIn/Indeed/Foundit/Naukri + 5 free boards + %d Greenhouse companies)",
        len(scrapers),
        len(scrapers) - 9,
    )
    return scrapers


def get_source_status() -> list[dict]:
    scrapers = build_scrapers()
    active = {s.source_name for s in scrapers}
    greenhouse_count = sum(1 for s in scrapers if s.source_name == "greenhouse")
    result = []
    for key, meta in SOURCE_CATALOG.items():
        entry = {**meta, "key": key, "enabled": key in active}
        if key == "greenhouse" and greenhouse_count:
            entry["status"] = f"active ({greenhouse_count} boards)"
        result.append(entry)
    return result
