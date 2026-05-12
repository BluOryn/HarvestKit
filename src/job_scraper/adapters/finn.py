"""finn.no adapter — Norway's largest job marketplace.

finn.no SSRs the search results as HTML, and each detail page contains a
JobPosting JSON-LD blob wrapped in a `script:ld+json` key. The base extractor
already unwraps that wrapper (see extract.py:_flatten_json_ld).

Listing URL: https://www.finn.no/job/fulltime/search.html?q=<query>&published=<days>
Detail URL: https://www.finn.no/job/ad/{adId}

This adapter:
  1. Walks search HTML pages, harvesting /job/ad/{id} anchors.
  2. Returns minimal JobListing stubs (job_url + identifier).
  3. main.py's deep_scrape pass visits each detail page and merges JSON-LD +
     section parser + HR miner output.
"""
from __future__ import annotations

import logging
import re
from typing import List, Set
from urllib.parse import urlencode, urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


SEARCH_BASE = "https://www.finn.no/job/fulltime/search.html"
AD_PATH_RX = re.compile(r"/job/ad/(\d+)")


class FinnNoAdapter(BaseAdapter):
    """Listing harvester for finn.no. Detail extraction handled by deep_scrape."""

    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        # Pull every query parameter from the user-supplied URL (q, published,
        # location, industry, etc.) — pass them through to the SSR search.
        parsed = urlparse(target.url)
        base_params = parse_qs(parsed.query, keep_blank_values=True)
        # Flatten parse_qs's {k: [v1, v2]} into a list of tuples to preserve repeats
        flat_params: List[tuple] = []
        for k, vs in base_params.items():
            for v in vs:
                flat_params.append((k, v))

        listings: List[JobListing] = []
        seen_ids: Set[str] = set()
        max_pages = max(1, run_config.max_pages or 50)
        for page in range(1, max_pages + 1):
            page_params = list(flat_params)
            if page > 1:
                page_params.append(("page", str(page)))
            url = f"{SEARCH_BASE}?{urlencode(page_params, doseq=True)}"
            result = http.get(url)
            if result is None:
                logging.info("finn.no: empty response at page %d (%s)", page, url)
                break
            _, html = result
            page_ids = self._extract_ad_ids(html)
            new = 0
            for ad_id in page_ids:
                if ad_id in seen_ids:
                    continue
                seen_ids.add(ad_id)
                listing = JobListing(
                    job_url=canonicalize_url(f"https://www.finn.no/job/ad/{ad_id}"),
                    apply_url=canonicalize_url(f"https://www.finn.no/job/ad/{ad_id}"),
                    external_id=ad_id,
                    source_ats="finn.no",
                    source_domain="www.finn.no",
                )
                listing.set_extra("finnkode", ad_id)
                listings.append(listing)
                new += 1
            if new == 0:
                break
        logging.info("finn.no: %d listings", len(listings))
        return listings

    @staticmethod
    def _extract_ad_ids(html: str) -> List[str]:
        # Match every /job/ad/<digits> regardless of absolute vs relative URL.
        ids = AD_PATH_RX.findall(html)
        seen = set()
        out: List[str] = []
        for i in ids:
            if i in seen:
                continue
            seen.add(i)
            out.append(i)
        return out
