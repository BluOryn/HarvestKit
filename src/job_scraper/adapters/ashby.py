import logging
from typing import List
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class AshbyAdapter(BaseAdapter):
    """Ashby HQ public job board API.
    URL form: https://jobs.ashbyhq.com/{slug}
    """

    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        slug = self._extract_slug(target.url)
        if not slug:
            return []
        api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
        payload = http.get_json(api)
        if payload is None:
            logging.info("ashby: no payload for %s", slug)
            return []
        jobs = payload.get("jobs", []) or []
        listings: List[JobListing] = []
        for item in jobs:
            description = ""
            html = item.get("descriptionHtml") or ""
            if html:
                description = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
            elif item.get("descriptionPlain"):
                description = item.get("descriptionPlain")
            location = item.get("location") or ""
            secondary = item.get("secondaryLocations") or []
            sec_locs = ", ".join(s.get("location", "") for s in secondary if s.get("location"))
            if sec_locs:
                location = (location + ", " + sec_locs).strip(", ")
            workplace = item.get("workplaceType") or ""
            job_url = item.get("jobUrl") or item.get("applyUrl") or ""
            apply_url = item.get("applyUrl") or job_url
            listings.append(
                JobListing(
                    title=item.get("title") or "",
                    company=slug,
                    location=location,
                    remote="remote" if workplace.lower() == "remote" else "",
                    employment_type=item.get("employmentType") or "",
                    posted_date=item.get("publishedAt") or item.get("updatedAt") or "",
                    description=description,
                    apply_url=canonicalize_url(apply_url),
                    job_url=canonicalize_url(job_url),
                )
            )
        logging.info("ashby %s: %d jobs", slug, len(listings))
        return listings

    def _extract_slug(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            return ""
        return path.split("/")[0]
