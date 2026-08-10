"""Arbeitnow: a German-market job board with a free public API.

The other seeds reach European companies obliquely — Greenhouse and Lever are
US products whose EU customers are a minority, and Workable's search is a
global index filtered down. This one is German-market by construction, so
almost every row is in scope before any filtering happens.

It matters for a second reason. Its ads carry the employer's own text rather
than an ATS's normalised summary, which is where the "Ihre Ansprechpartnerin:
Anna Müller" block lives — the one convention that yields HR contacts with
published addresses. Workable strips that; measured across 146 of its ads, not
one carried an address at the employer's domain.

The API asks not to be abused and enforces it: two requests a second earns an
immediate 429. Pagination is therefore deliberately paced and sequential, which
is slow and correct — a source that blocks us is worth less than a source we
read politely.
"""

from __future__ import annotations

import json
import logging
import re
import time

from job_scraper.models import JobListing
from job_scraper.normalize import canonicalize_url

from ..geo import country_from_location

log = logging.getLogger(__name__)

FEED_URL = "https://www.arbeitnow.com/api/job-board-api"

# Measured: 5s between pages runs indefinitely, 1s is refused at once.
PAGE_DELAY_SECONDS = 5.0

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def _text(html: str) -> str:
    return _WS_RX.sub(" ", _TAG_RX.sub(" ", html or "")).strip()


def _listing(job: dict) -> JobListing | None:
    company = (job.get("company_name") or "").strip()
    if not company:
        return None
    location = (job.get("location") or "").strip()
    url = canonicalize_url(job.get("url") or "")
    tags = job.get("tags") or []
    return JobListing(
        title=job.get("title") or "",
        company=company,
        location=location,
        city=location,
        country=country_from_location(location),
        # The full employer-authored ad, which is the point of this source.
        description=_text(job.get("description") or "")[:20000],
        skills=", ".join(str(tag) for tag in tags if tag)[:500],
        employment_type=", ".join(str(t) for t in (job.get("job_types") or []) if t)[:120],
        job_url=url,
        apply_url=url,
        source_ats="arbeitnow",
    )


def fetch(
    http,
    *,
    max_pages: int = 60,
    delay_seconds: float = PAGE_DELAY_SECONDS,
    sleep=time.sleep,
) -> list:
    """Walk the feed politely. Never raises; a dead page ends the walk."""
    listings: list[JobListing] = []
    url = FEED_URL
    for page in range(1, max_pages + 1):
        response = http.get(url)
        if not response:
            log.warning("arbeitnow: page %d returned nothing — stopping (rate limit?)", page)
            break
        try:
            payload = json.loads(response[1])
        except (json.JSONDecodeError, TypeError, IndexError):
            log.warning("arbeitnow: page %d was not JSON — stopping", page)
            break
        rows = payload.get("data") or []
        if not rows:
            break
        listings.extend(filter(None, (_listing(job) for job in rows)))
        url = (payload.get("links") or {}).get("next") or ""
        if not url:
            break
        # Paced between pages, not before the first: a single-page caller
        # should not pay for a wait it never needed.
        sleep(delay_seconds)
    log.info("arbeitnow: %d listings from %d pages", len(listings), page)
    return listings
