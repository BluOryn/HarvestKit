"""jobbsafari.no adapter — Norwegian job aggregator.

Listing URL: /ledige-stillinger or /ledige-stillinger/yrkeskategori/<slug>
Detail URL : /jobb/<slug>-<id>

Detail pages embed full structured data in <script id="__NEXT_DATA__">
under `props.pageProps.jobEntry`. We grab listing IDs from the SSR HTML
then let deep_scrape walk each detail page.

The universal extractor handles the detail-page parse via our new
__NEXT_DATA__ hook in universal.py.
"""
from __future__ import annotations

import logging
import re
from typing import List, Set
from urllib.parse import urlparse

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


# Detail URL contains slug ending in a numeric id (last 6+ digits)
JOB_PATH_RX = re.compile(r"/jobb/([\w\-]+-(\d{5,}))")


class JobbsafariAdapter(BaseAdapter):
    def fetch_jobs(self, target: TargetConfig, run_config: RunConfig, http: HttpClient) -> List[JobListing]:
        max_pages = max(1, run_config.max_pages or 30)
        base = target.url
        seen: Set[str] = set()
        listings: List[JobListing] = []

        for page in range(1, max_pages + 1):
            sep = "&" if "?" in base else "?"
            url = base if page == 1 else f"{base}{sep}side={page}"
            result = http.get(url)
            if result is None:
                break
            _, html = result
            matches = JOB_PATH_RX.findall(html)
            new = 0
            for slug, jid in matches:
                if jid in seen:
                    continue
                seen.add(jid)
                detail = f"https://jobbsafari.no/jobb/{slug}"
                listing = JobListing(
                    job_url=canonicalize_url(detail),
                    apply_url=canonicalize_url(detail),
                    external_id=jid,
                    source_ats="jobbsafari.no",
                    source_domain="jobbsafari.no",
                    country="Norway",
                )
                listing.set_extra("jobbsafari_id", jid)
                listings.append(listing)
                new += 1
            if new == 0:
                break
        logging.info("jobbsafari.no: %d listings", len(listings))
        return listings
