"""Public ATS board APIs as a company seed.

Greenhouse and Lever both expose a documented, unauthenticated JSON endpoint per
board. Given a board slug they return that company's open roles, which makes
them a usable seed wherever a country-wide search API is not available.

The catch is that neither returns the employer's own website — only the ATS URL
— and the person cascade needs the company's real domain. `guess_domains`
proposes candidates from the slug and the resolver keeps the first that answers,
which is cheap and correct far more often than not for the kind of company that
runs a public board.
"""

from __future__ import annotations

import json
import logging
import re

from job_scraper.models import JobListing
from job_scraper.normalize import canonicalize_url

log = logging.getLogger(__name__)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

# Ordered by how often they turn out to be right for EU tech companies.
DOMAIN_TLDS = ("com", "de", "io", "co", "fr", "nl", "fi", "se", "eu", "ai", "es", "it", "pl")

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def guess_domains(slug: str, company: str = "") -> list[str]:
    """Candidate own-domains for a board slug, best guess first."""
    stems: list[str] = []
    for raw in (slug, company):
        cleaned = re.sub(r"[^a-z0-9\s-]", "", (raw or "").lower()).strip()
        if not cleaned:
            continue
        for variant in (cleaned.replace(" ", "").replace("-", ""), cleaned.replace(" ", "-")):
            if variant and variant not in stems:
                stems.append(variant)
    return [f"https://{stem}.{tld}/" for stem in stems for tld in DOMAIN_TLDS]


def _text(html: str) -> str:
    return _WS_RX.sub(" ", _TAG_RX.sub(" ", html or "")).strip()


def _greenhouse(slug: str, http) -> list[JobListing]:
    response = http.get(GREENHOUSE_URL.format(slug=slug))
    if not response:
        return []
    try:
        payload = json.loads(response[1])
    except (json.JSONDecodeError, TypeError):
        return []
    listings: list[JobListing] = []
    for item in payload.get("jobs", []) or []:
        if not isinstance(item, dict):
            continue
        location = (item.get("location") or {}).get("name", "")
        url = canonicalize_url(item.get("absolute_url") or "")
        listings.append(
            JobListing(
                title=item.get("title") or "",
                company=slug,
                location=location,
                description=_text(item.get("content") or "")[:20000],
                posted_date=item.get("updated_at") or "",
                job_url=url,
                apply_url=url,
                source_ats="greenhouse",
            )
        )
    return listings


def _lever(slug: str, http) -> list[JobListing]:
    response = http.get(LEVER_URL.format(slug=slug))
    if not response:
        return []
    try:
        payload = json.loads(response[1])
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, list):
        return []
    listings: list[JobListing] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        categories = item.get("categories") or {}
        url = canonicalize_url(item.get("hostedUrl") or "")
        listings.append(
            JobListing(
                title=item.get("text") or "",
                company=slug,
                location=categories.get("location") or "",
                department=categories.get("team") or "",
                description=_text(item.get("descriptionPlain") or item.get("description") or "")[:20000],
                job_url=url,
                apply_url=url,
                source_ats="lever",
            )
        )
    return listings


FETCHERS = {"greenhouse": _greenhouse, "lever": _lever}


def fetch_boards(boards: list[tuple[str, str]], http) -> list[JobListing]:
    """Pull every open role from the given (kind, slug) boards. Never raises."""
    listings: list[JobListing] = []
    for kind, slug in boards:
        fetcher = FETCHERS.get(kind)
        if fetcher is None:
            log.warning("atsboards: unknown board kind %r", kind)
            continue
        try:
            found = fetcher(slug, http)
        except Exception as exc:
            log.warning("atsboards: %s/%s failed: %s", kind, slug, exc)
            continue
        log.info("atsboards: %-11s %-22s %4d jobs", kind, slug, len(found))
        listings.extend(found)
    return listings
