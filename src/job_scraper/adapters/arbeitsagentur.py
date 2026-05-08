"""Bundesagentur für Arbeit jobsuche public API.

Public client_id `jobboerse-jobsuche` is used by the official frontend.
We respect a small page size and add a delay.
"""
import logging
from typing import List
from urllib.parse import urlencode, urlparse, parse_qs

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


CLIENT_ID = "jobboerse-jobsuche"
BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"


class ArbeitsagenturAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        params_from_url = self._params(target.url)
        was = params_from_url.get("was", ["Software Engineer"])[0]
        wo = params_from_url.get("wo", ["Deutschland"])[0]
        size = int(params_from_url.get("size", ["100"])[0])
        max_pages = int(params_from_url.get("max_pages", ["20"])[0])
        listings: List[JobListing] = []
        seen_ids = set()
        for page in range(1, max_pages + 1):
            qs = urlencode({"was": was, "wo": wo, "page": page, "size": size, "umkreis": 100})
            url = f"{BASE}?{qs}"
            payload = http.get_json(url, headers={"X-API-Key": CLIENT_ID, "Accept": "application/json"})
            if not payload:
                break
            angebote = payload.get("stellenangebote") or []
            if not angebote:
                break
            for item in angebote:
                hash_id = item.get("hashId") or item.get("refnr") or ""
                if hash_id in seen_ids:
                    continue
                seen_ids.add(hash_id)
                title = item.get("titel") or item.get("beruf") or ""
                company = item.get("arbeitgeber") or ""
                arbeitsort = item.get("arbeitsort") or {}
                location = ", ".join([p for p in [arbeitsort.get("ort"), arbeitsort.get("region"), arbeitsort.get("land")] if p])
                detail_url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{hash_id}" if hash_id else ""
                listings.append(
                    JobListing(
                        title=title,
                        company=company,
                        location=location,
                        employment_type=item.get("arbeitszeitmodelle") or "",
                        posted_date=item.get("aktuelleVeroeffentlichungsdatum") or "",
                        description=item.get("stellenbeschreibung") or "",
                        apply_url=canonicalize_url(detail_url),
                        job_url=canonicalize_url(detail_url),
                    )
                )
            total = payload.get("maxErgebnisse") or payload.get("anzahl") or 0
            if total and page * size >= total:
                break
        if listings:
            logging.info("arbeitsagentur %s/%s: %d jobs", was, wo, len(listings))
        return listings

    def _params(self, url: str) -> dict:
        parsed = urlparse(url)
        return parse_qs(parsed.query)
