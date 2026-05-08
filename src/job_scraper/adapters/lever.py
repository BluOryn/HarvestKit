import logging
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class LeverAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        slug = self._extract_slug(target.url)
        if not slug:
            return []
        api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        payload = http.get_json(api_url)
        if payload is None or not isinstance(payload, list):
            logging.info("lever: no payload for %s", slug)
            return []
        listings: List[JobListing] = []
        for item in payload:
            description_html = item.get("description", "") or ""
            lists_html = ""
            for block in item.get("lists", []) or []:
                lists_html += f"<h3>{block.get('text','')}</h3>{block.get('content','')}"
            additional = item.get("additional", "") or ""
            full_html = description_html + lists_html + additional
            description = BeautifulSoup(full_html, "lxml").get_text(" ", strip=True) if full_html else ""
            posted_date = ""
            created_at = item.get("createdAt")
            if created_at:
                try:
                    posted_date = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc).isoformat()
                except Exception:
                    posted_date = ""
            categories = item.get("categories") or {}
            location = categories.get("location") or ""
            all_locations = item.get("allLocations") or []
            if all_locations:
                location = ", ".join([str(x) for x in all_locations if x])
            workplace = item.get("workplaceType") or ""
            listings.append(
                JobListing(
                    title=item.get("text") or "",
                    company=slug,
                    location=location,
                    remote="remote" if workplace.lower() == "remote" else "",
                    employment_type=categories.get("commitment") or "",
                    posted_date=posted_date,
                    description=description,
                    apply_url=canonicalize_url(item.get("applyUrl") or ""),
                    job_url=canonicalize_url(item.get("hostedUrl") or ""),
                )
            )
        logging.info("lever %s: %d jobs", slug, len(listings))
        return listings

    def _extract_slug(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            return ""
        return path.split("/")[0]
