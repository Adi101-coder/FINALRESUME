"""Job board scrapers — Indeed, ZipRecruiter, LinkedIn, Glassdoor + Greenhouse company boards."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from app.agents.scraper.base import BaseJobScraper, _matches_query
from app.config import settings

logger = logging.getLogger("app.scraper")

_INDEED_BROWSER_CACHE: dict[str, tuple[list[dict], int | None]] = {}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _job(
    *,
    title: str,
    company: str,
    location: str = "",
    description: str = "",
    apply_url: str = "",
    skills: list | None = None,
    source: str,
    application_type: str = "external",
    remote_type: str = "",
    salary: str = "",
    experience_required: str = "",
    easy_apply: bool = False,
) -> dict:
    return {
        "title": title,
        "company": company,
        "location": location or "Remote",
        "salary": salary,
        "experience_required": experience_required,
        "skills": skills or [],
        "description": description,
        "apply_url": apply_url,
        "application_type": application_type,
        "easy_apply": easy_apply,
        "remote_type": remote_type,
        "visa_required": False,
        "source": source,
    }


def _split_title_company(raw_title: str) -> tuple[str, str]:
    """Parse 'Role - Company' titles from RSS/listings."""
    title = unescape(raw_title.strip())
    if " - " in title:
        role, company = title.rsplit(" - ", 1)
        return role.strip(), company.strip()
    return title, ""


async def _get_text(client: httpx.AsyncClient, url: str, *, headers: dict | None = None) -> str:
    merged = {**DEFAULT_HEADERS, **(headers or {})}
    response = await client.get(url, headers=merged, follow_redirects=True)
    response.raise_for_status()
    return response.text


_NAUKRI_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBALrlQ+djR0RjJwBF1xuisHmdFv334MIm
K6LgzJhmLhN7B5yuEyaKoasgXQk3+OQglsOaBxEJ0j5PcTL3nbOvt80CAwEAAQ==
-----END PUBLIC KEY-----"""


def _parse_total_count(text: str) -> int | None:
    """Best-effort parse of a site-reported total like '12,345 Jobs'."""
    import re as _re

    patterns = [
        r"([0-9][0-9,]*)\s+Jobs?\b",
        r"Showing\s+[0-9][0-9,]*\s*[-–]\s*[0-9][0-9,]*\s+of\s+([0-9][0-9,]+)",
        r"of\s+([0-9][0-9,]+)\s+jobs",
        r'"totaljobs"\s*:\s*([0-9]+)',
        r'"total"\s*:\s*([0-9]+)',
        r'"noOfJobs"\s*:\s*"?([0-9][0-9,]*)"?',
    ]
    for pat in patterns:
        m = _re.search(pat, text, _re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except (ValueError, IndexError):
                continue
    return None


def _find_total_in_json(obj, _depth: int = 0) -> int | None:
    """Recursively find a site-reported total in a JSON payload."""
    if _depth > 4 or obj is None:
        return None
    if isinstance(obj, dict):
        for key in ("totalJobs", "noOfJobs", "totalCount", "totalResults", "numJobs", "total"):
            if key in obj:
                try:
                    val = int(str(obj[key]).replace(",", ""))
                    if val >= 0:
                        return val
                except (ValueError, TypeError):
                    continue
        for value in obj.values():
            found = _find_total_in_json(value, _depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(obj, list):
        for item in obj[:5]:
            found = _find_total_in_json(item, _depth + 1)
            if found is not None:
                return found
    return None


class IndeedScraper(BaseJobScraper):
    """Indeed job discovery via public RSS feeds."""

    source_name = "indeed"
    last_total_available: int | None = None

    async def fetch_jobs_with_count(
        self, query: str = "", location: str = "", limit: int = 50
    ) -> tuple[list[dict], int | None]:
        jobs = await self.fetch_jobs(query=query, location=location, limit=limit)
        return jobs, self.last_total_available

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        q = quote_plus(query or "software engineer")
        loc = quote_plus(location or "remote")
        domain = settings.indeed_domain.strip() or "www.indeed.com"
        if "india" in (location or "").lower() and domain == "www.indeed.com":
            domain = "in.indeed.com"
        url = f"https://{domain}/rss?q={q}&l={loc}"
        logger.info("Indeed RSS fetch: query=%r location=%r domain=%s", query or "(default)", location or "(default)", domain)

        cookie_headers = dict(DEFAULT_HEADERS)
        if settings.indeed_session_cookie:
            cookie_headers["Cookie"] = settings.indeed_session_cookie
            cookie_headers["Referer"] = f"https://{domain}/"

        jobs: list[dict] = []
        self.last_total_available = None
        self.last_error = None
        rss_ok = False
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                text = await _get_text(client, url, headers=cookie_headers)
            root = ET.fromstring(text)
            channel = root.find("channel")
            if channel is None:
                self.last_error = "Indeed RSS returned no channel (feed deprecated for this query)."
                return jobs
            rss_ok = True
            for item in channel.findall("item")[: limit * 2]:
                raw_title = item.findtext("title", "")
                link = item.findtext("link", "")
                description = item.findtext("description", "") or ""
                company = item.findtext("source", "") or ""
                title, parsed_company = _split_title_company(raw_title)
                if not company:
                    company = parsed_company or "Indeed Employer"
                if query and not _matches_query(f"{title} {company} {description}", query):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=company,
                        location=location or "Remote",
                        description=description[:2000],
                        apply_url=link,
                        source=self.source_name,
                        application_type="indeed",
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            self.last_error = f"Indeed RSS failed ({exc}). RSS is deprecated; HTML fallback attempted."
            logger.warning("Indeed fetch failed: %s", exc)

        # Best-effort site-reported total + HTML fallback when RSS is empty/404.
        # RSS alone never carries a total, so without this the "exact number"
        # would just be len(jobs). Failure here must not break job listing.
        html = ""
        try:
            html_url = f"https://{domain}/jobs?q={q}&l={loc}"
            async with httpx.AsyncClient(timeout=20.0) as client:
                html = await _get_text(client, html_url, headers=cookie_headers)
            total = _parse_total_count(html)
            if total is not None:
                self.last_total_available = total
        except Exception:
            pass

        if not jobs and html:
            try:
                soup = BeautifulSoup(html, "lxml")
                for card in soup.select("td.resultContent, div.jobsearch-ResultsList li")[: limit * 2]:
                    title_el = card.select_one("h2.jobTitle a, h2.jobTitle, a.jcs-JobTitle")
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    href = title_el.get("href", "") if title_el.name == "a" else ""
                    link_el = card.select_one("a[href*='/rc/clk'], a[href*='/pagead/'], h2.jobTitle a")
                    if link_el and link_el.get("href"):
                        href = link_el.get("href", "")
                    apply_url = (
                        href
                        if href.startswith("http")
                        else (f"https://{domain}{href}" if href.startswith("/") else "")
                    )
                    company_el = card.select_one("[data-testid='company-name'], span.companyName")
                    company = company_el.get_text(strip=True) if company_el else "Indeed Employer"
                    loc_el = card.select_one("[data-testid='text-location'], div.companyLocation")
                    loc_text = loc_el.get_text(strip=True) if loc_el else (location or "Remote")
                    if query and not _matches_query(f"{title} {company}", query):
                        continue
                    if not title:
                        continue
                    jobs.append(
                        _job(
                            title=title,
                            company=company,
                            location=loc_text,
                            apply_url=apply_url,
                            source=self.source_name,
                            application_type="indeed",
                        )
                    )
                    if len(jobs) >= limit:
                        break
            except Exception as exc:
                self.last_error = f"Indeed HTML fallback failed ({exc})."
                logger.warning("Indeed HTML fallback failed: %s", exc)

        if not jobs and not rss_ok and not self.last_total_available:
            hint = "" if settings.indeed_session_cookie else " Set INDEED_SESSION_COOKIE (logged-in browser cookie) to unlock Indeed."
            self.last_error = (
                (self.last_error or "Indeed blocked this request (RSS deprecated + HTML challenge).")
                + hint
            )
        elif jobs:
            self.last_error = None

        # Browser fallback using Playwright when RSS and HTML both fail
        if not jobs and not rss_ok:
            try:
                browser_jobs, browser_total = await self._fetch_jobs_browser_fallback(query, location, limit, domain)
                if browser_jobs:
                    jobs = browser_jobs
                    self.last_total_available = browser_total or len(jobs)
                    self.last_error = None
                    logger.info("Indeed browser fallback succeeded: %d jobs", len(jobs))
            except Exception as exc:
                logger.warning("Indeed browser fallback failed: %s", exc)

        logger.info("Indeed kept %d jobs (site total=%s)", len(jobs), self.last_total_available)
        return jobs

    async def _fetch_jobs_browser_fallback(
        self, query: str, location: str, limit: int, domain: str
    ) -> tuple[list[dict], int | None]:
        """Fetch Indeed jobs using Playwright browser automation as last resort."""
        from playwright.async_api import async_playwright

        q = quote_plus(query or "software engineer")
        loc = quote_plus(location or "remote")
        cache_key = f"{q}:{loc}:{limit}"

        if cache_key in _INDEED_BROWSER_CACHE:
            return _INDEED_BROWSER_CACHE[cache_key]

        jobs: list[dict] = []
        total: int | None = None

        async with async_playwright() as pw:
            # Try to use user's Chrome profile first
            browser = None
            context = None
            try:
                from app.agents.browser.runner import system_chrome_user_data_dir
                import os

                user_data_dir = system_chrome_user_data_dir()
                if user_data_dir:
                    profile = os.environ.get("CHROME_PROFILE", "Default").strip() or "Default"
                    browser = await pw.chromium.launch_persistent_context(
                        str(user_data_dir),
                        channel="chrome",
                        headless=True,
                        slow_mo=0,
                        viewport={"width": 1400, "height": 900},
                        ignore_default_args=["--enable-automation"],
                        args=[f"--profile-directory={profile}", "--disable-blink-features=AutomationControlled"],
                    )
                    context = browser
                else:
                    browser = await pw.chromium.launch(headless=True)
                    context = await browser.new_context(viewport={"width": 1400, "height": 900})
            except Exception:
                # Fallback to basic browser
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1400, "height": 900})

            try:
                page = await context.new_page()
                url = f"https://{domain}/jobs?q={q}&l={loc}"
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Wait for job cards to load - try multiple selectors
                job_card_selectors = [
                    "td.resultContent",
                    "div.jobsearch-ResultsList li",
                    "div.job_seen_beacon",
                    "div[data-jk]",
                    "div.result",
                    "li[data-testid='result']",
                    "article.jobResult",
                    "div.mosaic-provider-jobcards li",
                ]
                for selector in job_card_selectors:
                    try:
                        await page.wait_for_selector(selector, timeout=5000)
                        break
                    except Exception:
                        continue

                # Try to get total count
                body_text = await page.inner_text("body")
                total = _parse_total_count(body_text)

                # Extract job cards - try multiple selectors
                cards = []
                for selector in job_card_selectors:
                    cards = await page.query_selector_all(selector)
                    if cards:
                        break

                for card in cards[: limit * 2]:
                    if len(jobs) >= limit:
                        break
                    # Try multiple title selectors
                    title_el = None
                    for title_selector in [
                        "h2.jobTitle a",
                        "h2.jobTitle",
                        "a.jcs-JobTitle",
                        "a[data-testid='job-title']",
                        "h2 a",
                        "h3 a",
                        "a.title",
                    ]:
                        title_el = await card.query_selector(title_selector)
                        if title_el:
                            break
                    if not title_el:
                        continue
                    title = await title_el.inner_text()
                    title = title.strip()
                    href = await title_el.get_attribute("href")
                    if not href:
                        for link_selector in [
                            "a[href*='/rc/clk']",
                            "a[href*='/pagead/']",
                            "h2.jobTitle a",
                            "a[data-testid='job-link']",
                            "h2 a",
                        ]:
                            link_el = await card.query_selector(link_selector)
                            if link_el:
                                href = await link_el.get_attribute("href")
                                break
                    apply_url = (
                        href
                        if href and href.startswith("http")
                        else (f"https://{domain}{href}" if href and href.startswith("/") else "")
                    )
                    # Try multiple company selectors
                    company_el = None
                    for company_selector in [
                        "[data-testid='company-name']",
                        "span.companyName",
                        "div.companyName",
                        "a[data-testid='company-link']",
                        "span.company",
                    ]:
                        company_el = await card.query_selector(company_selector)
                        if company_el:
                            break
                    company = (await company_el.inner_text()).strip() if company_el else "Indeed Employer"
                    # Try multiple location selectors
                    loc_el = None
                    for loc_selector in [
                        "[data-testid='text-location']",
                        "div.companyLocation",
                        "span.location",
                        "div.location",
                        "[data-testid='job-location']",
                    ]:
                        loc_el = await card.query_selector(loc_selector)
                        if loc_el:
                            break
                    loc_text = (await loc_el.inner_text()).strip() if loc_el else (location or "Remote")

                    if query and not _matches_query(f"{title} {company}", query):
                        continue
                    if not title:
                        continue
                    jobs.append(
                        _job(
                            title=title,
                            company=company,
                            location=loc_text,
                            apply_url=apply_url,
                            source=self.source_name,
                            application_type="indeed",
                        )
                    )
            finally:
                if context:
                    await context.close()
                if browser and hasattr(browser, 'close'):
                    try:
                        await browser.close()
                    except Exception:
                        pass

        _INDEED_BROWSER_CACHE[cache_key] = (jobs, total)
        return jobs, total


class ZipRecruiterScraper(BaseJobScraper):
    """ZipRecruiter search results."""

    source_name = "ziprecruiter"

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        q = quote_plus(query or "software engineer")
        loc = quote_plus(location or "remote")
        url = f"https://www.ziprecruiter.com/jobs-search?search={q}&location={loc}"
        logger.info("ZipRecruiter fetch: query=%r location=%r", query or "(default)", location or "(default)")

        headers = dict(DEFAULT_HEADERS)
        jobs: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                html = await _get_text(client, url, headers=headers)
            # Search pages sometimes embed a total; parse best-effort.
            total = _parse_total_count(html)
            if total is not None:
                self.last_total_available = total
            soup = BeautifulSoup(html, "lxml")
            for article in soup.select("article.job_result, div.job_content")[: limit * 2]:
                title_el = article.select_one("h2, a.job_title")
                company_el = article.select_one("a.company_name, span.company_name")
                loc_el = article.select_one("a.job_location, span.job_location")
                link_el = article.select_one("a[href*='/Job/'], a.job_link")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                company = company_el.get_text(strip=True) if company_el else "ZipRecruiter Employer"
                loc_text = loc_el.get_text(strip=True) if loc_el else (location or "Remote")
                href = link_el.get("href", "") if link_el else ""
                apply_url = href if href.startswith("http") else f"https://www.ziprecruiter.com{href}"
                if query and not _matches_query(f"{title} {company}", query):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=company,
                        location=loc_text,
                        apply_url=apply_url,
                        source=self.source_name,
                        application_type="ziprecruiter",
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            logger.warning("ZipRecruiter fetch failed: %s", exc)

        logger.info("ZipRecruiter kept %d jobs", len(jobs))
        return jobs


class LinkedInScraper(BaseJobScraper):
    """LinkedIn Jobs via guest search API (optional session cookie for reliability)."""

    source_name = "linkedin"
    last_total_available: int | None = None

    async def fetch_jobs_with_count(
        self, query: str = "", location: str = "", limit: int = 50
    ) -> tuple[list[dict], int | None]:
        jobs = await self.fetch_jobs(query=query, location=location, limit=limit)
        return jobs, self.last_total_available

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        keywords = quote_plus(query or "software engineer")
        loc = quote_plus(location or "Remote")
        url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={keywords}&location={loc}&start=0"
        )
        logger.info("LinkedIn fetch: query=%r location=%r", query or "(default)", location or "(default)")

        headers = dict(DEFAULT_HEADERS)
        if settings.linkedin_session_cookie:
            headers["Cookie"] = settings.linkedin_session_cookie
        elif settings.linkedin_access_token:
            headers["Authorization"] = f"Bearer {settings.linkedin_access_token}"

        jobs: list[dict] = []
        self.last_total_available = None
        self.last_error = None
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                html = await _get_text(client, url, headers=headers)
            # Guest API pages sometimes embed a total; parse best-effort.
            total = _parse_total_count(html)
            if total is not None:
                self.last_total_available = total
            soup = BeautifulSoup(html, "lxml")
            seen_urls: set[str] = set()
            for card in soup.select("li, div.base-card")[: limit * 4]:
                title_el = card.select_one("h3, span.sr-only")
                company_el = card.select_one("h4, a.hidden-nested-link")
                loc_el = card.select_one("span.job-search-card__location")
                link_el = card.select_one("a[href*='/jobs/view/']")
                if not title_el or not link_el:
                    continue
                title = title_el.get_text(strip=True)
                company = company_el.get_text(strip=True) if company_el else "LinkedIn Employer"
                loc_text = loc_el.get_text(strip=True) if loc_el else (location or "Remote")
                href = link_el.get("href", "")
                apply_url = href.split("?")[0] if href.startswith("http") else f"https://www.linkedin.com{href}"
                if apply_url in seen_urls:
                    continue
                seen_urls.add(apply_url)
                if query and not _matches_query(f"{title} {company}", query):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=company,
                        location=loc_text,
                        apply_url=apply_url,
                        source=self.source_name,
                        application_type="linkedin",
                        easy_apply="easy" in href.lower(),
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            self.last_error = f"LinkedIn fetch failed ({exc}). Set LINKEDIN_SESSION_COOKIE for reliability."
            logger.warning("LinkedIn fetch failed (set LINKEDIN_SESSION_COOKIE for better results): %s", exc)

        logger.info("LinkedIn kept %d jobs (site total=%s)", len(jobs), self.last_total_available)
        if jobs:
            self.last_error = None
        return jobs


class GlassdoorScraper(BaseJobScraper):
    """Glassdoor job search listings."""

    source_name = "glassdoor"

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        q = quote_plus(query or "software engineer")
        loc = quote_plus(location or "remote")
        url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}&locT=C&locKeyword={loc}"
        logger.info("Glassdoor fetch: query=%r location=%r", query or "(default)", location or "(default)")

        headers = dict(DEFAULT_HEADERS)
        if settings.glassdoor_session_cookie:
            headers["Cookie"] = settings.glassdoor_session_cookie

        jobs: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                html = await _get_text(client, url, headers=headers)
            soup = BeautifulSoup(html, "lxml")
            for row in soup.select("li.react-job-listing, div.jobContainer, article")[: limit * 3]:
                title_el = row.select_one("a.jobLink, a[data-test='job-link'], a[href*='/job-listing/']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                apply_url = href if href.startswith("http") else f"https://www.glassdoor.com{href}"
                company_el = row.select_one("div.jobHeader div, span.employerName, div.jobEmpolyerName")
                loc_el = row.select_one("span.loc, div.location")
                company = company_el.get_text(strip=True) if company_el else "Glassdoor Employer"
                loc_text = loc_el.get_text(strip=True) if loc_el else (location or "Remote")
                if query and not _matches_query(f"{title} {company}", query):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=company,
                        location=loc_text,
                        apply_url=apply_url,
                        source=self.source_name,
                        application_type="glassdoor",
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            logger.warning("Glassdoor fetch failed (set GLASSDOOR_SESSION_COOKIE for better results): %s", exc)

        logger.info("Glassdoor kept %d jobs", len(jobs))
        return jobs


class GreenhouseBoardScraper(BaseJobScraper):
    """Public Greenhouse job boards (Stripe, Figma, Airbnb, etc.)."""

    source_name = "greenhouse"

    def __init__(self, board_token: str, company_label: str = "") -> None:
        self.board_token = board_token
        self.company_label = company_label or board_token.replace("-", " ").title()

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs?content=true"
        logger.info("Greenhouse fetch: board=%s", self.board_token)

        jobs: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    logger.warning("Greenhouse board not found: %s", self.board_token)
                    return []
                response.raise_for_status()
                payload = response.json()

            for item in payload.get("jobs", [])[: limit * 2]:
                title = item.get("title", "")
                loc_data = item.get("location") or {}
                loc = loc_data.get("name", "") if isinstance(loc_data, dict) else str(loc_data)
                content = item.get("content", "") or ""
                haystack = f"{title} {content} {self.company_label}"
                if query and not _matches_query(haystack, query):
                    continue
                if location and location.lower() not in loc.lower() and "remote" not in loc.lower():
                    if location.lower() not in ("remote", ""):
                        continue
                jobs.append(
                    _job(
                        title=title,
                        company=self.company_label,
                        location=loc or "Remote",
                        description=content[:5000],
                        apply_url=item.get("absolute_url", ""),
                        source=self.source_name,
                        application_type="greenhouse",
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            logger.warning("Greenhouse board %s failed: %s", self.board_token, exc)

        logger.info("Greenhouse %s kept %d jobs", self.board_token, len(jobs))
        return jobs


class RemotiveScraper(BaseJobScraper):
    """Remotive.com — free remote job board API (no auth required)."""

    source_name = "remotive"

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        url = "https://remotive.com/api/remote-jobs"
        logger.info("Remotive fetch: query=%r", query or "(all)")

        jobs: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=DEFAULT_HEADERS)
                response.raise_for_status()
                data = response.json()

            for item in data.get("jobs", [])[: limit * 3]:
                title = item.get("title", "")
                company = item.get("company_name", "Remotive")
                category = item.get("category", "")
                desc = item.get("description", "") or ""
                url_val = item.get("url", "")
                tags = item.get("tags", []) or []
                remote_type = "remote"
                haystack = f"{title} {company} {category} {desc}"
                if query and not _matches_query(haystack, query):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=company,
                        location="Remote",
                        description=desc[:2000],
                        apply_url=url_val,
                        skills=tags,
                        source=self.source_name,
                        remote_type=remote_type,
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            logger.warning("Remotive fetch failed: %s", exc)

        logger.info("Remotive kept %d jobs", len(jobs))
        return jobs


class RemoteOKScraper(BaseJobScraper):
    """RemoteOK.com — free remote job board API (no auth required)."""

    source_name = "remoteok"

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        url = "https://remoteok.com/api"
        logger.info("RemoteOK fetch: query=%r", query or "(all)")

        jobs: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=DEFAULT_HEADERS)
                response.raise_for_status()
                data = response.json()

            for item in data[: limit * 3]:
                if not isinstance(item, dict) or "id" not in item:
                    continue
                title = item.get("position", "")
                company = item.get("company", "RemoteOK")
                desc = item.get("description", "") or ""
                tags = item.get("tags", []) or []
                apply_url = item.get("url", "")
                if not apply_url:
                    apply_url = f"https://remoteok.com/remote-jobs/{item.get('id', '')}"
                location_val = item.get("location", "Remote") or "Remote"
                haystack = f"{title} {company} {desc}"
                if query and not _matches_query(haystack, query):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=company,
                        location=location_val,
                        description=desc[:2000],
                        apply_url=apply_url,
                        skills=tags if isinstance(tags, list) else [],
                        source=self.source_name,
                        remote_type="remote",
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            logger.warning("RemoteOK fetch failed: %s", exc)

        logger.info("RemoteOK kept %d jobs", len(jobs))
        return jobs


class ArbeitnowScraper(BaseJobScraper):
    """Arbeitnow.com — free job board API (no auth required)."""

    source_name = "arbeitnow"

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        url = "https://www.arbeitnow.com/api/job-board-api"
        logger.info("Arbeitnow fetch: query=%r", query or "(all)")

        jobs: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=DEFAULT_HEADERS)
                response.raise_for_status()
                data = response.json()

            for item in data.get("data", [])[: limit * 3]:
                title = item.get("title", "")
                company = item.get("company_name", "Arbeitnow")
                desc = item.get("description", "") or ""
                tags = item.get("tags", []) or []
                remote = item.get("remote", False)
                apply_url = item.get("url", "")
                location_val = item.get("location", "") or ("Remote" if remote else "")
                haystack = f"{title} {company} {desc}"
                if query and not _matches_query(haystack, query):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=company,
                        location=location_val or "Remote",
                        description=desc[:2000],
                        apply_url=apply_url,
                        skills=tags if isinstance(tags, list) else [],
                        source=self.source_name,
                        remote_type="remote" if remote else "",
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            logger.warning("Arbeitnow fetch failed: %s", exc)

        logger.info("Arbeitnow kept %d jobs", len(jobs))
        return jobs


class FounditScraper(BaseJobScraper):
    """Foundit.in (ex-Monster India) — official middleware JSON API (no login).

    GET /middleware/jobsearch with Accept: application/json returns structured
    JSON including a site-reported total. Single gentle request per run
    (Foundit throttles aggressive datacenter IPs). Optional
    FOUNDIT_SESSION_COOKIE improves reliability.
    """

    source_name = "foundit"
    last_total_available: int | None = None

    async def fetch_jobs_with_count(
        self, query: str = "", location: str = "", limit: int = 50
    ) -> tuple[list[dict], int | None]:
        jobs = await self.fetch_jobs(query=query, location=location, limit=limit)
        return jobs, self.last_total_available

    @staticmethod
    def _exp_text(item: dict) -> str:
        def _yrs(v) -> str:
            if isinstance(v, dict):
                for k in ("years", "value", "min", "max"):
                    if v.get(k) is not None:
                        try:
                            return str(int(v[k]))
                        except (ValueError, TypeError):
                            continue
            try:
                return str(int(v))
            except (ValueError, TypeError):
                return ""

        lo, hi = _yrs(item.get("minimumExperience")), _yrs(item.get("maximumExperience"))
        if lo and hi and lo != hi:
            return f"{lo}-{hi} Yrs"
        if lo or hi:
            return f"{lo or hi} Yrs"
        return ""

    @staticmethod
    def _salary_text(item: dict) -> str:
        def _lpa(v) -> float | None:
            if not isinstance(v, dict):
                return None
            for k in ("absoluteValue", "value", "amount"):
                if v.get(k):
                    try:
                        return float(v[k]) / 100000.0
                    except (ValueError, TypeError):
                        continue
            return None

        lo, hi = _lpa(item.get("minimumSalary")), _lpa(item.get("maximumSalary"))
        if lo and hi and hi > 0:
            return f"₹{lo:g}-{hi:g} LPA"
        if hi and hi > 0:
            return f"₹{hi:g} LPA"
        return ""

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        q = (query or "software engineer").strip()
        logger.info("Foundit fetch: query=%r location=%r", q, location or "(default)")

        headers = dict(DEFAULT_HEADERS)
        headers["Accept"] = "application/json"
        headers["Referer"] = "https://www.foundit.in/"
        if settings.foundit_session_cookie:
            headers["Cookie"] = settings.foundit_session_cookie

        params: dict[str, str] = {"query": q}
        if location:
            params["location"] = location

        jobs: list[dict] = []
        self.last_total_available = None
        self.last_error = None
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    "https://www.foundit.in/middleware/jobsearch",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
            jsr = data.get("jobSearchResponse", {}) if isinstance(data, dict) else {}
            items = jsr.get("data", []) if isinstance(jsr, dict) else []
            total = _find_total_in_json(data)
            if total is not None:
                self.last_total_available = total
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "") or "").strip()
                if not title:
                    continue
                company = (
                    item.get("companyName")
                    or item.get("company")
                    or item.get("employerName")
                    or item.get("organization")
                    or item.get("hiringCompany")
                    or "Foundit Employer"
                )
                loc_val = item.get("locations") or item.get("location") or location or "India"
                if isinstance(loc_val, list):
                    loc_val = ", ".join(str(x) for x in loc_val[:2])
                skills = item.get("skills") or item.get("requirements") or item.get("tags") or []
                if isinstance(skills, str):
                    skills = [s.strip() for s in skills.split(",") if s.strip()]
                apply_url = (
                    item.get("redirectUrl")
                    or item.get("applyUrl")
                    or item.get("url")
                    or ""
                )
                if apply_url and apply_url.startswith("/"):
                    apply_url = f"https://www.foundit.in{apply_url}"
                desc = (
                    item.get("description")
                    or item.get("summary")
                    or item.get("jobDescription")
                    or ""
                )
                if isinstance(desc, str) and len(desc) > 2000:
                    desc = desc[:2000]
                haystack = f"{title} {company} {desc} {' '.join(skills) if isinstance(skills, list) else skills}"
                if q and not _matches_query(haystack, q):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=str(company),
                        location=str(loc_val) or "India",
                        description=str(desc),
                        apply_url=str(apply_url),
                        skills=list(skills)[:15] if isinstance(skills, list) else [],
                        source=self.source_name,
                        application_type="foundit",
                        experience_required=self._exp_text(item),
                        salary=self._salary_text(item),
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            self.last_error = (
                f"Foundit API failed ({exc}). Foundit throttles datacenter IPs — "
                "set FOUNDIT_SESSION_COOKIE or retry later."
            )
            logger.warning("Foundit middleware fetch failed: %s", exc)

        if jobs:
            if self.last_total_available is None:
                self.last_total_available = len(jobs)
            self.last_error = None
        elif not self.last_error:
            self.last_error = (
                "Foundit returned no jobs for this query. Retry later or set "
                "FOUNDIT_SESSION_COOKIE."
            )
        logger.info("Foundit kept %d jobs (site total=%s)", len(jobs), self.last_total_available)
        return jobs


class NaukriScraper(BaseJobScraper):
    """Naukri.com — authenticated jobapi v3 when keys exist, else public HTML best-effort.

    Naukri's /jobapi/v3/search needs nkparam + app headers (see NAUKRI_* env).
    Without them the site is JS-rendered and plain HTTP returns a shell page,
    so jobs may be empty — the error message tells the user exactly what to set.
    When the API responds, its `totaljobs`/`noOfJobs` field is the exact count.
    """

    source_name = "naukri"
    last_total_available: int | None = None

    async def fetch_jobs_with_count(
        self, query: str = "", location: str = "", limit: int = 50
    ) -> tuple[list[dict], int | None]:
        jobs = await self.fetch_jobs(query=query, location=location, limit=limit)
        return jobs, self.last_total_available

class NaukriScraper(BaseJobScraper):
    """Naukri.com — jobapi v3 search with generated request signature (no login).

    The search endpoint needs an `nkparam` header: RSA-encrypted
    `v0|<ms-timestamp>|121_srp` with Naukri's public key, base64-encoded.
    Generated locally per request — no credentials needed for search.
    Response `noOfJobs` is the exact available total for the query.
    Optional NAUKRI_NKPARAM override + NAUKRI_SESSION_COOKIE supported.
    """

    source_name = "naukri"
    last_total_available: int | None = None

    async def fetch_jobs_with_count(
        self, query: str = "", location: str = "", limit: int = 50
    ) -> tuple[list[dict], int | None]:
        jobs = await self.fetch_jobs(query=query, location=location, limit=limit)
        return jobs, self.last_total_available

    @staticmethod
    def _generate_nkparam() -> str | None:
        try:
            import base64 as _b64
            import time as _time

            from Crypto.Cipher import PKCS1_v1_5
            from Crypto.PublicKey import RSA

            key = RSA.import_key(_NAUKRI_PUBLIC_KEY)
            cipher = PKCS1_v1_5.new(key)
            plaintext = f"v0|{int(_time.time() * 1000)}|121_srp"
            return _b64.b64encode(cipher.encrypt(plaintext.encode())).decode()
        except Exception as exc:
            logger.warning("Naukri nkparam generation failed (install pycryptodome): %s", exc)
            return None

    @staticmethod
    def _seo_key(keyword: str, location: str, page: int = 1) -> str:
        import re as _re

        kw = _re.sub(r"[^a-z0-9]+", "-", keyword.strip().lower()).strip("-") or "jobs"
        if location.strip():
            loc = _re.sub(r"[^a-z0-9]+", "-", location.strip().lower()).strip("-")
            return f"{kw}-jobs-in-{loc}-{page}"
        return f"{kw}-jobs-{page}"

    @staticmethod
    def _placeholder(item: dict, ptype: str) -> str:
        for p in item.get("placeholders", []) or []:
            if isinstance(p, dict) and p.get("type") == ptype:
                return str(p.get("label", "") or "")
        return ""

    async def fetch_jobs(self, query: str = "", location: str = "", limit: int = 50) -> list[dict]:
        q = query or "software engineer"
        logger.info("Naukri fetch: query=%r location=%r", q, location or "(default)")
        jobs: list[dict] = []
        self.last_total_available = None
        self.last_error = None

        # 1) jobapi v3 search (exact total via noOfJobs; no login required).
        nkparam = settings.naukri_nkparam.strip() or (self._generate_nkparam() or "")
        if nkparam:
            try:
                api_params = {
                    "noOfResults": min(max(limit, 20), 50),
                    "urlType": "search_by_keyword",
                    "searchType": "adv",
                    "keyword": q,
                    "k": q,
                    "pageNo": 1,
                    "seoKey": self._seo_key(q, location or ""),
                    "src": "jobsearchDesk",
                    "jobAge": 30,
                }
                headers = dict(DEFAULT_HEADERS)
                headers["Accept"] = "application/json"
                headers["Referer"] = "https://www.naukri.com/"
                headers["Origin"] = "https://www.naukri.com"
                headers["appid"] = settings.naukri_app_id.strip() or "109"
                headers["clientid"] = "d3skt0p"
                headers["systemid"] = settings.naukri_system_id.strip() or "jobseeker"
                headers["gid"] = "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE"
                headers["nkparam"] = nkparam
                headers["x-requested-with"] = "XMLHttpRequest"
                if settings.naukri_session_cookie:
                    headers["Cookie"] = settings.naukri_session_cookie
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        "https://www.naukri.com/jobapi/v3/search",
                        headers=headers,
                        params=api_params,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                try:
                    total = int(str(data.get("noOfJobs", "")).replace(",", ""))
                    self.last_total_available = total
                except (ValueError, TypeError):
                    self.last_total_available = None
                for item in (data.get("jobDetails") or [])[: limit * 2]:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "") or "").strip()
                    if not title:
                        continue
                    company = str(item.get("companyName", "") or "Naukri Employer")
                    loc_text = self._placeholder(item, "location") or location or "India"
                    exp_text = self._placeholder(item, "experience")
                    sal_text = self._placeholder(item, "salary")
                    raw_desc = str(item.get("jobDescription", "") or "")
                    desc = (
                        BeautifulSoup(raw_desc, "lxml").get_text(" ", strip=True)[:2000]
                        if raw_desc
                        else ""
                    )
                    tags = str(item.get("tagsAndSkills", "") or "")
                    skills = [s.strip() for s in tags.split(",") if s.strip()][:15]
                    jd_url = str(item.get("jdURL", "") or "")
                    apply_url = (
                        jd_url
                        if jd_url.startswith("http")
                        else (f"https://www.naukri.com{jd_url}" if jd_url else "")
                    )
                    haystack = f"{title} {company} {desc} {' '.join(skills)}"
                    if q and not _matches_query(haystack, q):
                        continue
                    jobs.append(
                        _job(
                            title=title,
                            company=company,
                            location=loc_text,
                            description=desc,
                            apply_url=apply_url,
                            skills=skills,
                            source=self.source_name,
                            application_type="naukri",
                            experience_required=exp_text,
                            salary=sal_text,
                        )
                    )
                    if len(jobs) >= limit:
                        break
                if jobs or self.last_total_available is not None:
                    logger.info(
                        "Naukri API kept %d jobs (site total=%s)",
                        len(jobs),
                        self.last_total_available,
                    )
                    self.last_error = None
                    return jobs
            except Exception as exc:
                self.last_error = f"Naukri API failed ({exc})."
                logger.warning("Naukri API fetch failed: %s", exc)

        # 2) Public HTML best-effort (usually a JS shell — may be empty).
        try:
            search_url = f"https://www.naukri.com/{quote_plus(q)}-jobs"
            if location:
                search_url += f"-in-{quote_plus(location)}"
            headers = dict(DEFAULT_HEADERS)
            if settings.naukri_session_cookie:
                headers["Cookie"] = settings.naukri_session_cookie
            async with httpx.AsyncClient(timeout=30.0) as client:
                html = await _get_text(client, search_url, headers=headers)
            total = _parse_total_count(html)
            if total is not None:
                self.last_total_available = total
            soup = BeautifulSoup(html, "lxml")
            for card in soup.select("article.jobTuple, div.jobTuple, div.srpJobHldr")[: limit * 2]:
                title_el = card.select_one("a.title, a[title]")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                apply_url = href if href.startswith("http") else f"https://www.naukri.com{href}"
                company_el = card.select_one("a.subTitle, span.companyName")
                company = company_el.get_text(strip=True) if company_el else "Naukri Employer"
                loc_el = card.select_one("span.locWdth, li.location")
                loc_text = loc_el.get_text(strip=True) if loc_el else (location or "India")
                desc_el = card.select_one("span.job-desc, div.job-description")
                desc = desc_el.get_text(strip=True)[:2000] if desc_el else ""
                if q and not _matches_query(f"{title} {company} {desc}", q):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=company,
                        location=loc_text,
                        description=desc,
                        apply_url=apply_url,
                        source=self.source_name,
                        application_type="naukri",
                    )
                )
                if len(jobs) >= limit:
                    break
        except Exception as exc:
            if not self.last_error:
                self.last_error = f"Naukri page fetch failed ({exc})."
            logger.warning("Naukri HTML fetch failed: %s", exc)

        if jobs:
            self.last_error = None
        elif not self.last_error:
            self.last_error = (
                "Naukri returned no jobs for this query. The API signature may have "
                "rotated — retry later or set NAUKRI_NKPARAM override."
            )
        logger.info("Naukri kept %d jobs (site total=%s)", len(jobs), self.last_total_available)
        return jobs
