"""arbeidsplassen.nav.no adapter — Norwegian gov "PAM" job board.

The site SSRs results as HTML; detail pages are full-text (no JSON-LD) but
contain explicit contact emails + phones in the body, which is what we want.

Listing: https://arbeidsplassen.nav.no/stillinger?q=<query>&page=<n>
Detail:  https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}

Listing page hands out 25 detail UUIDs each; deep_scrape visits each and the
universal smart-DOM extractor (no JSON-LD needed) pulls the rest.
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


SEARCH_BASE = "https://arbeidsplassen.nav.no/stillinger"
DETAIL_URL_RX = re.compile(r"/stillinger/stilling/([a-f0-9\-]{20,})", re.I)


class NavNoAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        parsed = urlparse(target.url)
        base_params = parse_qs(parsed.query, keep_blank_values=True)
        flat: List[tuple] = []
        for k, vs in base_params.items():
            for v in vs:
                flat.append((k, v))

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
            uuids = self._extract_uuids(html)
            new = 0
            for uuid in uuids:
                if uuid in seen:
                    continue
                seen.add(uuid)
                detail = f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
                listing = JobListing(
                    job_url=canonicalize_url(detail),
                    apply_url=canonicalize_url(detail),
                    external_id=uuid,
                    source_ats="nav.no",
                    source_domain="arbeidsplassen.nav.no",
                    country="Norway",
                )
                listing.set_extra("nav_uuid", uuid)
                listings.append(listing)
                new += 1
            if new == 0:
                break
        logging.info("nav.no: %d listings", len(listings))
        return listings

    @staticmethod
    def _extract_uuids(html: str) -> List[str]:
        seen = set()
        out: List[str] = []
        for u in DETAIL_URL_RX.findall(html):
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out
