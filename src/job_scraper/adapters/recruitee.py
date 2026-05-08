import logging
from typing import List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class RecruiteeAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        slug = self._extract_slug(target.url)
        if not slug:
            return []
        offers = self._fetch_offers(slug, http)
        if not offers:
            return []
        listings: List[JobListing] = []
        for item in offers:
            description_html = item.get("description") or item.get("requirements") or ""
            description = BeautifulSoup(description_html, "lxml").get_text(" ", strip=True) if description_html else ""
            locs = []
            for loc in item.get("locations", []) or []:
                pieces = [loc.get(k) for k in ("city", "state", "country") if loc.get(k)]
                if pieces:
                    locs.append(", ".join(pieces))
            if not locs:
                fallback = item.get("location") or item.get("city") or ""
                if fallback:
                    locs = [fallback]
            location = "; ".join(locs)
            remote_flag = "remote" if item.get("remote") else ""
            url = item.get("careers_url") or item.get("careers_apply_url") or ""
            apply_url = item.get("careers_apply_url") or url
            listings.append(
                JobListing(
                    title=item.get("title") or "",
                    company=slug,
                    location=location,
                    remote=remote_flag,
                    employment_type=item.get("employment_type_code") or item.get("employment_type") or "",
                    posted_date=item.get("created_at") or "",
                    description=description,
                    apply_url=canonicalize_url(apply_url),
                    job_url=canonicalize_url(url),
                )
            )
        if listings:
            logging.info("recruitee %s: %d jobs", slug, len(listings))
        return listings

    def _fetch_offers(self, slug: str, http: HttpClient) -> List[dict]:
        candidates = [slug, slug.replace("-", ""), slug.replace("_", "-")]
        endpoints = ("/api/offers/", "/api/offers")
        for c in candidates:
            for path in endpoints:
                url = f"https://{c}.recruitee.com{path}"
                payload = http.get_json(url)
                if payload and isinstance(payload, dict):
                    offers = payload.get("offers")
                    if isinstance(offers, list):
                        return offers
            # widget endpoint as fallback
            url = f"https://careers.{c}.com/api/offers"
            payload = http.get_json(url)
            if payload and isinstance(payload, dict):
                offers = payload.get("offers")
                if isinstance(offers, list):
                    return offers
        return []

    def _extract_slug(self, url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc
        if host.endswith(".recruitee.com"):
            return host.split(".")[0]
        # careers.{slug}.com
        if host.startswith("careers."):
            parts = host.split(".")
            if len(parts) >= 3:
                return parts[1]
        path = parsed.path.strip("/")
        if path:
            return path.split("/")[0]
        return host or ""
