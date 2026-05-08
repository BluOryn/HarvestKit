import logging
from typing import List, Optional
from urllib.parse import urlparse

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class WorkableAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        slug = self._extract_slug(target.url)
        if not slug:
            return []
        # Try widget first
        listings = self._widget(slug, http)
        if listings:
            return listings
        # Fallback to v3
        listings = self._v3(slug, http)
        return listings

    def _widget(self, slug: str, http: HttpClient) -> List[JobListing]:
        api = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        payload = http.get_json(api)
        if not payload:
            return []
        jobs = payload.get("jobs", []) or []
        out: List[JobListing] = []
        for item in jobs:
            shortcode = item.get("shortcode") or ""
            location_obj = item.get("location") or {}
            loc_parts = [location_obj.get("city"), location_obj.get("region"), location_obj.get("country")]
            location = ", ".join([p for p in loc_parts if p])
            remote = "remote" if item.get("remote") or location_obj.get("workplace") == "remote" else ""
            apply_url = item.get("url") or f"https://apply.workable.com/{slug}/j/{shortcode}/"
            out.append(
                JobListing(
                    title=item.get("title") or "",
                    company=slug,
                    location=location,
                    remote=remote,
                    employment_type=item.get("type") or "",
                    posted_date=item.get("published") or item.get("created_at") or "",
                    description="",
                    apply_url=canonicalize_url(apply_url),
                    job_url=canonicalize_url(apply_url),
                )
            )
        if out:
            logging.info("workable widget %s: %d jobs", slug, len(out))
        return out

    def _v3(self, slug: str, http: HttpClient) -> List[JobListing]:
        url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
        body = {"query": "", "department": [], "location": [], "workplace": [], "remote": []}
        headers = {
            "User-Agent": http.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://apply.workable.com",
            "Referer": f"https://apply.workable.com/{slug}/",
        }
        all_results: List[dict] = []
        token: Optional[str] = None
        for _ in range(50):
            payload = dict(body)
            if token:
                payload["token"] = token
            data = http.post_json(url, payload, headers=headers)
            if not data:
                break
            chunk = data.get("results", []) or []
            all_results.extend(chunk)
            token = data.get("nextPage")
            if not token or not chunk:
                break
        out: List[JobListing] = []
        for item in all_results:
            shortcode = item.get("shortcode") or ""
            loc = item.get("location") or {}
            loc_parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            location = ", ".join([p for p in loc_parts if p])
            apply_url = f"https://apply.workable.com/{slug}/j/{shortcode}/"
            out.append(
                JobListing(
                    title=item.get("title") or "",
                    company=slug,
                    location=location,
                    remote="remote" if item.get("remote") else "",
                    employment_type=item.get("employment_type") or "",
                    posted_date=item.get("published_on") or "",
                    description="",
                    apply_url=canonicalize_url(apply_url),
                    job_url=canonicalize_url(apply_url),
                )
            )
        if out:
            logging.info("workable v3 %s: %d jobs", slug, len(out))
        return out

    def _extract_slug(self, url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.endswith(".workable.com") and host != "apply.workable.com":
            return host.split(".")[0]
        path = parsed.path.strip("/")
        if path:
            return path.split("/")[0]
        return ""
