"""Sitemap mining — free person URLs, no guessing.

Many sites expose one page per person: /team/jane-doe, /ueber-uns/team/j-schmidt,
/people/anna-schmidt. Those URLs are listed in sitemap.xml, so instead of
guessing paths we can read the site's own index and fetch exactly the pages that
name somebody.

This module only *selects* URLs. Fetching and parsing stays in the cascade, so
the same three extractors run on whatever this finds.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from job_scraper import safe_xml

from ...net_guard import guard, is_safe_url

log = logging.getLogger(__name__)

SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml")

# Path segments that precede a person slug.
_PERSON_PATH_RX = re.compile(
    r"/(?:team|teams|people|persons?|mitarbeiter|kollegen|unser-team|das-team|ueber-uns/team"
    r"|about/team|leadership|management|vorstand|geschaeftsfuehrung|equipe|notre-equipe"
    r"|il-team|equipo|zespol|medarbejdere|medarbetare|ansatte|tiimi|autor|author)/"
    r"(?P<slug>[a-z0-9][a-z0-9-]{2,60})/?$",
    re.I,
)
# Slugs that are a section index rather than a person.
_NOT_A_PERSON = {
    "index",
    "all",
    "overview",
    "uebersicht",
    "list",
    "page",
    "team",
    "management",
    "leadership",
    "board",
    "vorstand",
    "more",
    "mehr",
    "alle",
    "search",
    "kontakt",
    "contact",
    "join",
    "jobs",
    "careers",
    "karriere",
}
MAX_SITEMAP_DOCS = 5
MAX_PERSON_URLS = 25


def _looks_like_a_person_url(url: str) -> bool:
    match = _PERSON_PATH_RX.search(urlparse(url).path)
    if not match:
        return False
    slug = match.group("slug").lower()
    if slug in _NOT_A_PERSON:
        return False
    # "jane-doe" / "j-schmidt" — a person slug almost always has two parts.
    return "-" in slug or len(slug) > 6


def _urls_in(document: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, nested_sitemap_urls) from one sitemap document."""
    root = safe_xml.fromstring(document)
    if root is None:
        return [], []
    pages: list[str] = []
    nested: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag != "loc" or not (element.text or "").strip():
            continue
        location = element.text.strip()
        parent_tag = ""
        # A <loc> inside <sitemap> points at another index, not a page.
        for candidate in root.iter():
            if element in list(candidate):
                parent_tag = candidate.tag.rsplit("}", 1)[-1]
                break
        (nested if parent_tag == "sitemap" else pages).append(location)
    return pages, nested


def person_urls(domain: str, http, *, limit: int = MAX_PERSON_URLS) -> list[str]:
    """Find per-person pages via the site's own sitemap. Never raises."""
    if not domain:
        return []
    queue = [f"https://{domain}{path}" for path in SITEMAP_PATHS]
    seen_docs: set[str] = set()
    found: list[str] = []

    while queue and len(seen_docs) < MAX_SITEMAP_DOCS and len(found) < limit:
        url = queue.pop(0)
        if url in seen_docs:
            continue
        seen_docs.add(url)
        # Nested <loc> entries are remote input: a sitemap can point its index
        # at anything, including an internal address.
        if not guard(url):
            continue
        try:
            response = http.get(url)
        except Exception as exc:
            log.debug("sitemap: %s failed: %s", url, exc)
            continue
        if not response:
            continue
        _, body = response
        if not body:
            continue
        pages, nested = _urls_in(body)
        queue.extend(nested)
        for page in pages:
            if _looks_like_a_person_url(page) and page not in found and is_safe_url(page):
                found.append(page)
                if len(found) >= limit:
                    break
    return found
