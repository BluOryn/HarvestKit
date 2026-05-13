"""jobs.ch adapter.

Two listing paths:

1. **HTML search page** (`/en/vacancies/?category=...&employment-type=...&publication-date=30`)
   — honors all filter querystring params. UUIDs render in the SSR HTML
   (22/page). Use this when the target URL is the public search page.
2. **JSON API** (`/api/v1/public/search`) — IGNORES filter querystring
   (`category`, `employment-type`, etc.) and returns all jobs. Useful only
   as a fallback when the HTML path can't be parsed.

Either way, we return JobListing stubs with the canonical detail URL,
and main.py's deep_scrape visits each detail page.
"""
from __future__ import annotations

import logging
import re
from typing import List, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


API_BASE = "https://www.jobs.ch/api/v1/public/search"
PAGE_SIZE = 20  # API default

# Detail UUID pattern from the SSR HTML listing.
DETAIL_RX = re.compile(
    r"/(?:en|de|fr|it)/(?:vacancies|stellenangebote|offres-emplois|offerte-lavoro)/detail/([\w\-]{36})/?"
)


class JobsChAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        # If user passed an HTML search URL, walk it page-by-page — server honors
        # the filter querystring. Otherwise fall back to the API.
        parsed = urlparse(target.url)
        if "/vacancies/" in parsed.path or "/stellenangebote/" in parsed.path or "/offres-emplois/" in parsed.path:
            return self._fetch_via_html(target, run_config, http)
        return self._fetch_via_api(target, run_config, http)

    def _fetch_via_html(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        max_pages = max(1, run_config.max_pages or 100)
        seen: set[str] = set()
        listings: List[JobListing] = []
        base = target.url
        sep = "&" if "?" in base else "?"
        for page in range(1, max_pages + 1):
            url = base if page == 1 else f"{base}{sep}page={page}"
            result = http.get(url)
            if result is None:
                logging.info("jobs.ch HTML: empty response at page %d", page)
                break
            _, html = result
            uuids = DETAIL_RX.findall(html)
            new = 0
            for uid in uuids:
                if uid in seen:
                    continue
                seen.add(uid)
                detail = f"https://www.jobs.ch/en/vacancies/detail/{uid}/"
                listing = JobListing(
                    job_url=canonicalize_url(detail),
                    apply_url=canonicalize_url(detail),
                    external_id=uid,
                    source_ats="jobs.ch",
                    source_domain="www.jobs.ch",
                    country="Switzerland",
                )
                listings.append(listing)
                new += 1
            if new == 0:
                break
        logging.info("jobs.ch HTML: %d listings (filter honored)", len(listings))
        return listings

    def _fetch_via_api(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        params = self._extract_query_params(target.url)
        listings: List[JobListing] = []
        seen_ids = set()
        max_pages = max(1, run_config.max_pages or 200)
        for page in range(1, max_pages + 1):
            page_params: List[Tuple[str, str]] = list(params)
            page_params.append(("page", str(page)))
            page_params.append(("rows", str(PAGE_SIZE)))
            url = f"{API_BASE}?{urlencode(page_params, doseq=True)}"
            payload = http.get_json(url, headers={"Accept": "application/json"})
            if not payload:
                logging.warning("jobs.ch API: empty payload at page %d", page)
                break
            docs = payload.get("documents") or []
            if not docs:
                break
            for doc in docs:
                job_id = doc.get("job_id") or doc.get("datapool_id") or ""
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                listing = self._build_listing(doc)
                if listing.job_url:
                    listings.append(listing)
            num_pages = payload.get("num_pages") or 0
            if num_pages and page >= num_pages:
                break
        logging.info("jobs.ch API: %d listings (filter IGNORED, all-jobs corpus)", len(listings))
        return listings

    def _extract_query_params(self, url: str) -> List[Tuple[str, str]]:
        parsed = urlparse(url)
        return parse_qsl(parsed.query, keep_blank_values=True)

    def _build_listing(self, doc: dict) -> JobListing:
        # Pick the English detail URL; fall back to whatever's available.
        links = doc.get("_links") or {}
        detail = ""
        for key in ("detail_en", "detail_de", "detail_fr"):
            entry = links.get(key)
            if isinstance(entry, dict) and entry.get("href"):
                detail = entry["href"]
                if key == "detail_en":
                    break
        title = doc.get("title") or ""
        company = doc.get("company_name") or ""
        place = doc.get("place") or ""
        # Country is implicit (Switzerland) for jobs.ch
        listing = JobListing(
            title=title,
            company=company,
            location=place,
            city=place,
            country="Switzerland",
            posted_date=doc.get("publication_date") or doc.get("initial_publication_date") or "",
            description=doc.get("preview") or "",
            apply_url=canonicalize_url(detail),
            job_url=canonicalize_url(detail),
            external_id=doc.get("job_id") or "",
            source_ats="jobs.ch",
            source_domain="www.jobs.ch",
            company_logo=doc.get("company_logo_file") or "",
        )
        # Employment grade → percentage range, e.g. "60–80%"
        grades = doc.get("employment_grades") or []
        if grades:
            lo, hi = min(grades), max(grades)
            listing.employment_type = f"{lo}–{hi}%" if lo != hi else f"{lo}%"
        # Tags: extract employment_grade min/max if not already set
        for tag in doc.get("tags") or []:
            if isinstance(tag, dict) and tag.get("type") == "employment_grade" and not grades:
                vmin, vmax = tag.get("value_min"), tag.get("value_max")
                if vmin and vmax:
                    listing.employment_type = f"{vmin}–{vmax}%"
        # Language skills (ids only — but flag presence)
        langs = doc.get("language_skills") or []
        if langs:
            listing.language = ", ".join(str(l) for l in langs if l)
        # Work experience hints
        we = doc.get("work_experience") or []
        if we:
            listing.experience_years = ", ".join(str(x) for x in we if x)
        return listing
