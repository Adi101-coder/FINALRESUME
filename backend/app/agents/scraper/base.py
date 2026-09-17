from __future__ import annotations

from abc import ABC, abstractmethod

import logging

logger = logging.getLogger("app.scraper")


def _matches_query(text: str, query: str) -> bool:
    if not query:
        return True
    return query.lower() in text.lower()


class BaseJobScraper(ABC):
    source_name: str = "base"
    last_total_available: int | None = None
    last_error: str | None = None

    @abstractmethod
    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        raise NotImplementedError

    async def fetch_jobs_with_count(
        self, query: str = "", location: str = "", limit: int = 50
    ) -> tuple[list[dict], int | None]:
        """Return (jobs, total_available_on_platform).

        total_available is the site-reported total for this query when the
        platform exposes it, else None (caller falls back to len(jobs)).
        """
        jobs = await self.fetch_jobs(query=query, location=location, limit=limit)
        total = getattr(self, "last_total_available", None)
        if isinstance(total, int) and total >= 0:
            return jobs, total
        return jobs, None
