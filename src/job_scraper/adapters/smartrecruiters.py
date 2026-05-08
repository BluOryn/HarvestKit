import logging
from typing import List, Optional, Set
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class SmartRecruitersAdapter(BaseAdapter):
    PAGE_LIMIT = 100

    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        slug = self._resolve_slug(target.url, http)
        if not slug:
            return []
        listings: List[JobListing] = []
        offset = 0
        while True:
            api_url = (
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                f"?limit={self.PAGE_LIMIT}&offset={offset}"
            )
            payload = http.get_json(api_url)
            if payload is None:
                break
            postings = payload.get("content", []) or []
            if not postings:
                break
            for item in postings:
                listings.append(self._to_listing(item, slug))
            total = payload.get("totalFound", 0)
            offset += self.PAGE_LIMIT
            if offset >= total or offset > 5000:
                break
        if listings:
            logging.info("smartrecruiters %s: %d jobs", slug, len(listings))
        return listings

    def _resolve_slug(self, url: str, http: HttpClient) -> Optional[str]:
        slug = self._extract_slug(url)
        if not slug:
            return None
        if self._has_postings(slug, http):
            return slug
        # Try variants
        bases = {slug, slug.lower(), slug.upper(), slug.capitalize()}
        suffixes = ["", "Group", "SE", "AG", "GmbH", "Inc", "Holding", "International"]
        seen: Set[str] = set()
        for base in bases:
            for suf in suffixes:
                cand = f"{base}{suf}"
                if cand in seen or cand == slug:
                    continue
                seen.add(cand)
                if self._has_postings(cand, http):
                    logging.info("smartrecruiters resolved slug %s -> %s", slug, cand)
                    return cand
        return None

    def _has_postings(self, slug: str, http: HttpClient) -> bool:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
        payload = http.get_json(url)
        if not payload:
            return False
        return (payload.get("totalFound") or 0) > 0

    def _to_listing(self, item: dict, slug: str) -> JobListing:
        location = item.get("location") or {}
        loc_parts = [location.get("city"), location.get("region"), location.get("country")]
        loc_str = ", ".join([p for p in loc_parts if p])
        posting_id = item.get("id")
        company_name = (item.get("company") or {}).get("name", "") or slug
        job_ad = item.get("jobAd") or {}
        sections = (job_ad.get("sections") or {})
        description = ""
        for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
            block = sections.get(key) or {}
            html = block.get("text") or ""
            if html:
                description += " " + BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        ref = item.get("ref") or ""
        if posting_id and not ref:
            ref = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
        public_url = f"https://jobs.smartrecruiters.com/{slug}/{posting_id}" if posting_id else ""
        return JobListing(
            title=item.get("name") or "",
            company=company_name,
            location=loc_str,
            employment_type=(item.get("typeOfEmployment") or {}).get("label", ""),
            posted_date=item.get("releasedDate") or item.get("createdOn") or "",
            description=description.strip(),
            apply_url=canonicalize_url(public_url or ref),
            job_url=canonicalize_url(public_url or ref),
        )

    def _extract_slug(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            host = parsed.netloc.lower()
            if host.startswith("jobs.smartrecruiters.com"):
                return ""
            return host.split(".")[0]
        parts = path.split("/")
        if "companies" in parts:
            idx = parts.index("companies")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return parts[-1] if parts else ""
