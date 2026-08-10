"""Cross-company job search APIs as a geography-first seed.

A per-board ATS API answers "what is this company hiring for?", which makes
geography a filter applied after the fact — and on a board list sourced from
public datasets that means crawling nine companies to keep one. These two
endpoints search across every customer of the platform at once, so the question
becomes "who is hiring in Germany?" and the geography is decided before any
crawling is paid for.

Workable is the stronger of the two: it filters by location server-side,
paginates properly, and returns each employer's own website. That last field
matters more than it looks — domain resolution is the slowest and most lossy
step in the pipeline, and a website supplied by the API skips it entirely.

SmartRecruiters ignores every geography parameter it was offered, so its
geography comes from the query language instead: "Softwareentwickler" returns
German and Austrian employers because that is who advertises in German. The
country is then read from the response rather than inferred, so the trick costs
nothing in precision.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlencode

from job_scraper.models import JobListing
from job_scraper.normalize import canonicalize_url

from ..geo import COUNTRY_NAMES, country_from_location

log = logging.getLogger(__name__)

WORKABLE_URL = "https://jobs.workable.com/api/v1/jobs"
SMARTRECRUITERS_URL = "https://jobs.smartrecruiters.com/sr-jobs/search"

# Workable hands back 20 rows a page; this bounds a single (country, keyword)
# sweep so one dense pairing cannot monopolise the run.
MAX_PAGES = 25
SMARTRECRUITERS_LIMIT = 100

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def _text(html: str) -> str:
    return _WS_RX.sub(" ", _TAG_RX.sub(" ", html or "")).strip()


def _json(http, url):
    response = http.get(url)
    if not response:
        return None
    try:
        return json.loads(response[1])
    except (json.JSONDecodeError, TypeError, IndexError):
        return None


def _domain(website: str) -> str:
    """Bare host from the employer website the API supplies."""
    host = re.sub(r"^https?://", "", (website or "").strip(), flags=re.I)
    host = host.split("/")[0].split("?")[0].strip().lower()
    return host[4:] if host.startswith("www.") else host


def _workable_listing(job: dict) -> JobListing | None:
    company = job.get("company") or {}
    name = (company.get("title") or "").strip()
    if not name:
        return None
    location = job.get("location") or {}
    country_name = location.get("countryName") or ""
    url = canonicalize_url(job.get("url") or "")
    return JobListing(
        title=job.get("title") or "",
        company=name,
        company_website=company.get("website") or "",
        location=", ".join(part for part in (location.get("city"), country_name) if part),
        city=location.get("city") or "",
        region=location.get("subregion") or "",
        # The API states the country, so this is read rather than guessed.
        country=COUNTRY_NAMES.get(country_name.strip().lower(), ""),
        department=job.get("department") or "",
        employment_type=job.get("employmentType") or "",
        description=_text(job.get("description") or "")[:20000],
        job_url=url,
        apply_url=url,
        source_ats="workable",
    )


def search_workable(http, *, countries: list[str], keywords: list[str], max_pages: int = MAX_PAGES) -> list:
    """Every (country, keyword) pairing, paginated to exhaustion or `max_pages`."""
    listings: list[JobListing] = []
    pairs = empty = 0
    for country in countries:
        for keyword in keywords:
            pairs += 1
            token, pages = "", 0
            while pages < max_pages:
                params = {"query": keyword, "location": country}
                if token:
                    params["pageToken"] = token
                payload = _json(http, f"{WORKABLE_URL}?{urlencode(params)}")
                if not isinstance(payload, dict):
                    break
                jobs = payload.get("jobs") or []
                if not jobs:
                    break
                listings.extend(filter(None, (_workable_listing(job) for job in jobs)))
                pages += 1
                token = payload.get("nextPageToken") or ""
                if not token:
                    break
            empty += pages == 0
            log.info("jobsearch: workable %-14s %-18s %2d pages", country, keyword, pages)

    # A rate-limited host returns nothing for every query, which reads exactly
    # like a vocabulary that matches nothing. Saying so turns a silent no-op
    # into something actionable.
    if pairs and empty > pairs // 2:
        log.warning(
            "jobsearch: workable returned nothing for %d of %d queries — "
            "the host is probably rate-limiting this IP, not out of results",
            empty,
            pairs,
        )
    return listings


def _smartrecruiters_listing(job: dict) -> JobListing | None:
    company = job.get("company") or {}
    slug = (company.get("identifier") or "").strip()
    name = (company.get("name") or slug).strip()
    if not name:
        return None
    location = job.get("location") or {}
    city = location.get("city") or ""
    code = (location.get("country") or "").strip().upper()
    url = canonicalize_url(job.get("applyUrl") or "")
    return JobListing(
        title=job.get("name") or "",
        company=name,
        location=job.get("shortLocation") or city,
        city=city,
        region=location.get("region") or "",
        country=code or country_from_location(job.get("shortLocation") or ""),
        job_url=url,
        apply_url=url,
        source_ats="smartrecruiters",
    )


def search_smartrecruiters(http, *, keywords: list[str], limit: int = SMARTRECRUITERS_LIMIT) -> list:
    """One query per keyword. Geography rides on the language of the keyword."""
    listings: list[JobListing] = []
    for keyword in keywords:
        payload = _json(http, f"{SMARTRECRUITERS_URL}?limit={limit}&keyword={quote(keyword)}")
        rows = payload.get("content") or [] if isinstance(payload, dict) else []
        listings.extend(filter(None, (_smartrecruiters_listing(job) for job in rows)))
        log.info("jobsearch: smartrecruiters %-24s %4d jobs", keyword, len(rows))
    return listings


def website_hints(listings: list) -> dict[str, list[str]]:
    """Employer websites keyed by company, so the seed can skip domain guessing.

    Keyed the same way `companies_from_listings` groups, so the two agree on
    what counts as one company.
    """
    hints: dict[str, list[str]] = {}
    for listing in listings:
        key = (getattr(listing, "company", "") or "").strip().lower()
        domain = _domain(getattr(listing, "company_website", ""))
        if key and domain:
            hints.setdefault(key, [])
            if domain not in hints[key]:
                hints[key].append(domain)
    return hints


def search_all(
    http,
    *,
    countries: list[str],
    keywords: list[str],
    smartrecruiters_keywords: list[str] | None = None,
    max_pages: int = MAX_PAGES,
    concurrency: int = 8,
) -> list:
    """Run both providers. A provider that fails must not take the run with it."""

    def workable_slice(country: str) -> list:
        try:
            return search_workable(http, countries=[country], keywords=keywords, max_pages=max_pages)
        except Exception as exc:
            log.warning("jobsearch: workable %s failed: %s", country, exc)
            return []

    listings: list[JobListing] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for found in pool.map(workable_slice, countries):
            listings.extend(found)

    if smartrecruiters_keywords:
        try:
            listings.extend(search_smartrecruiters(http, keywords=smartrecruiters_keywords))
        except Exception as exc:
            log.warning("jobsearch: smartrecruiters failed: %s", exc)
    return listings
