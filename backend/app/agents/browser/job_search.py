"""Search job boards in-browser after login and collect apply URLs."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

from playwright.sync_api import Page

logger = logging.getLogger("app.browser.job_search")


def _collect_unique_links(page: Page, selector: str, limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for el in page.locator(selector).all()[: limit * 3]:
        try:
            href = el.get_attribute("href") or ""
            if not href or href in seen:
                continue
            if href.startswith("/"):
                href = page.url.split("/")[0] + "//" + page.url.split("/")[2] + href
            seen.add(href)
            urls.append(href.split("?")[0] if "linkedin.com" in href else href)
            if len(urls) >= limit:
                break
        except Exception:
            continue
    return urls


def search_linkedin_jobs(page: Page, query: str, location: str, limit: int = 10) -> list[dict]:
    keywords = quote_plus(query)
    loc = quote_plus(location or "Remote")
    url = f"https://www.linkedin.com/jobs/search/?keywords={keywords}&location={loc}&f_AL=true"
    logger.info("LinkedIn job search: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(2500)

    for _ in range(3):
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(800)

    links = _collect_unique_links(page, 'a[href*="/jobs/view/"]', limit)
    jobs = [{"title": query, "company": "LinkedIn", "apply_url": link, "source": "linkedin"} for link in links]
    logger.info("LinkedIn search found %d jobs for %r", len(jobs), query)
    return jobs


def search_indeed_jobs(page: Page, query: str, location: str, limit: int = 10) -> list[dict]:
    q = quote_plus(query)
    loc = quote_plus(location or "remote")
    url = f"https://www.indeed.com/jobs?q={q}&l={loc}"
    logger.info("Indeed job search: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(2500)

    jobs: list[dict] = []
    cards = page.locator("a.jcs-JobTitle, a[data-jk], h2.jobTitle a").all()
    for card in cards[: limit * 2]:
        try:
            href = card.get_attribute("href") or ""
            title = card.inner_text().strip() or query
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.indeed.com" + href
            jobs.append({"title": title, "company": "Indeed", "apply_url": href, "source": "indeed"})
            if len(jobs) >= limit:
                break
        except Exception:
            continue
    logger.info("Indeed search found %d jobs for %r", len(jobs), query)
    return jobs


def search_ziprecruiter_jobs(page: Page, query: str, location: str, limit: int = 10) -> list[dict]:
    q = quote_plus(query)
    loc = quote_plus(location or "remote")
    url = f"https://www.ziprecruiter.com/jobs-search?search={q}&location={loc}"
    logger.info("ZipRecruiter job search: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(2500)

    jobs: list[dict] = []
    for link in page.locator("a[href*='/Job/']").all()[: limit * 2]:
        try:
            href = link.get_attribute("href") or ""
            title = link.inner_text().strip() or query
            if not href.startswith("http"):
                href = "https://www.ziprecruiter.com" + href
            jobs.append({"title": title, "company": "ZipRecruiter", "apply_url": href, "source": "ziprecruiter"})
            if len(jobs) >= limit:
                break
        except Exception:
            continue
    logger.info("ZipRecruiter search found %d jobs for %r", len(jobs), query)
    return jobs


def search_glassdoor_jobs(page: Page, query: str, location: str, limit: int = 10) -> list[dict]:
    q = quote_plus(query)
    loc = quote_plus(location or "remote")
    url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}&locT=C&locKeyword={loc}"
    logger.info("Glassdoor job search: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=300_000)
    page.wait_for_timeout(2500)

    jobs: list[dict] = []
    for link in page.locator("a[href*='/job-listing/'], a.jobLink").all()[: limit * 2]:
        try:
            href = link.get_attribute("href") or ""
            title = link.inner_text().strip() or query
            if not href:
                continue
            if not href.startswith("http"):
                href = "https://www.glassdoor.com" + href
            jobs.append({"title": title, "company": "Glassdoor", "apply_url": href, "source": "glassdoor"})
            if len(jobs) >= limit:
                break
        except Exception:
            continue
    logger.info("Glassdoor search found %d jobs for %r", len(jobs), query)
    return jobs


SEARCH_HANDLERS = {
    "linkedin": search_linkedin_jobs,
    "indeed": search_indeed_jobs,
    "ziprecruiter": search_ziprecruiter_jobs,
    "glassdoor": search_glassdoor_jobs,
}


def search_jobs_on_platform(page: Page, platform: str, query: str, location: str, limit: int) -> list[dict]:
    handler = SEARCH_HANDLERS.get(platform)
    if not handler:
        return []
    return handler(page, query, location, limit)
