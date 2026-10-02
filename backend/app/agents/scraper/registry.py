from __future__ import annotations

import logging

from app.agents.scraper.sources import LinkedInScraper

logger = logging.getLogger("app.scraper.registry")

SOURCE_CATALOG = {
    "linkedin": {
        "name": "LinkedIn",
        "auth": "optional session cookie",
        "status": "active",
    },
}


def build_scrapers() -> list:
    scrapers = [LinkedInScraper()]
    logger.info("Initialized %d job adapter (LinkedIn only)", len(scrapers))
    return scrapers


def get_source_status() -> list[dict]:
    scrapers = build_scrapers()
    active = {s.source_name for s in scrapers}
    return [
        {**meta, "key": key, "enabled": key in active}
        for key, meta in SOURCE_CATALOG.items()
    ]
