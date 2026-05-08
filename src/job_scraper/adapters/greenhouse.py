import logging
from typing import List
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class GreenhouseAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        slug = self._extract_slug(target.url)
        if not slug:
            return []
        slug = self._resolve(slug, http) or slug
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        payload = http.get_json(url)
        if payload is None:
            logging.info("greenhouse: no payload for %s", slug)
            return []
        items = payload.get("jobs", []) or []
        listings: List[JobListing] = []
        for item in items:
            description_html = item.get("content") or ""
            description = BeautifulSoup(description_html, "lxml").get_text(" ", strip=True) if description_html else ""
            location = (item.get("location") or {}).get("name", "")
            offices = item.get("offices") or []
            office_names = ", ".join(o.get("name", "") for o in offices if o.get("name"))
            full_loc = location or office_names
            job_url = item.get("absolute_url") or ""
            listings.append(
                JobListing(
                    title=item.get("title") or "",
                    company=slug,
                    location=full_loc,
                    employment_type="",
                    posted_date=item.get("updated_at") or "",
                    description=description,
                    apply_url=canonicalize_url(job_url),
                    job_url=canonicalize_url(job_url),
                )
            )
        logging.info("greenhouse %s: %d jobs", slug, len(listings))
        return listings

    def _extract_slug(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            return ""
        return path.split("/")[0]

    def _resolve(self, slug: str, http: HttpClient) -> str:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        if http.get_json(url):
            return slug
        for variant in (slug.replace("-", ""), slug + "inc", slug + "ag", slug + "se", slug + "gmbh"):
            if variant == slug:
                continue
            url = f"https://boards-api.greenhouse.io/v1/boards/{variant}/jobs"
            if http.get_json(url):
                logging.info("greenhouse resolved %s -> %s", slug, variant)
                return variant
        return slug
