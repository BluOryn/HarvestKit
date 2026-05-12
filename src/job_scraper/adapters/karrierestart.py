"""karrierestart.no adapter — Norwegian job aggregator.

Listing URL: https://karrierestart.no/jobb/?page=<n> (~20 ads/page, 600+ pages)
Detail URL:  https://karrierestart.no/ledig-stilling/{id}

Pulls each /ledig-stilling/{id} link from search HTML. Detail extraction goes
through the universal smart-DOM path (no JSON-LD JobPosting reliably).
"""
from __future__ import annotations

import logging
import re
from typing import List, Set
from urllib.parse import urlencode, urlparse, parse_qs

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


SEARCH_BASE = "https://karrierestart.no/jobb/"
AD_PATH_RX = re.compile(r"/ledig-stilling/(\d+)")


class KarrierestartAdapter(BaseAdapter):
    def fetch_jobs(self, target: TargetConfig, run_config: RunConfig, http: HttpClient) -> List[JobListing]:
        parsed = urlparse(target.url)
        base_params = parse_qs(parsed.query, keep_blank_values=True)
        flat = [(k, v) for k, vs in base_params.items() for v in vs]

        listings: List[JobListing] = []
        seen: Set[str] = set()
        max_pages = max(1, run_config.max_pages or 50)
        for page in range(1, max_pages + 1):
            page_params = list(flat)
            if page > 1:
                page_params.append(("page", str(page)))
            url = f"{SEARCH_BASE}?{urlencode(page_params, doseq=True)}"
            result = http.get(url)
            if result is None:
                break
            _, html = result
            ids = AD_PATH_RX.findall(html)
            new = 0
            for aid in ids:
                if aid in seen:
                    continue
                seen.add(aid)
                detail = f"https://karrierestart.no/ledig-stilling/{aid}"
                listing = JobListing(
                    job_url=canonicalize_url(detail),
                    apply_url=canonicalize_url(detail),
                    external_id=aid,
                    source_ats="karrierestart.no",
                    source_domain="karrierestart.no",
                    country="Norway",
                )
                listing.set_extra("karrierestart_id", aid)
                listings.append(listing)
                new += 1
            if new == 0:
                break
        logging.info("karrierestart.no: %d listings", len(listings))
        return listings
