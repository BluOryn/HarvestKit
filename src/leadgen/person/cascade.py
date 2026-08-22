"""Fetch the pages that name people, run every strategy, merge the results.

Bounded on purpose: eight pages per company across thousands of companies is
the difference between a run that finishes tonight and one that does not.
Localised paths come first because they are the higher-yield ones.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..net_guard import guard
from .hit import PersonHit
from .paths import candidate_paths
from .roles import role_rank
from .strategies import impressum, jsonld_person, press, sitemap, team

log = logging.getLogger(__name__)

_WS_RX = re.compile(r"\s+")

# Every strategy runs on every fetched page; order only breaks merge ties.
STRATEGIES = (jsonld_person, impressum, team, press)


def _key(name: str) -> str:
    return _WS_RX.sub(" ", (name or "").strip().lower())


def merge_hits(hits: list[PersonHit]) -> list[PersonHit]:
    """One record per person, taking the best value for each field."""
    merged: dict[str, PersonHit] = {}
    for hit in hits:
        key = _key(hit.name)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = PersonHit(**vars(hit))
            continue
        for attribute in ("email", "phone", "linkedin", "source_url"):
            if not getattr(existing, attribute) and getattr(hit, attribute):
                setattr(existing, attribute, getattr(hit, attribute))
        # A title the buyer asked for beats one they did not, and among titles
        # they did ask for, the function beats the office: "CTO" is more useful
        # on the row than "Geschäftsführer" even though both are true of the
        # same person. A binary target/not-target test cannot express that.
        if hit.role and (not existing.role or role_rank(hit.role) > role_rank(existing.role)):
            existing.role = hit.role
    return list(merged.values())


def _harvest(url: str, http, hits: list[PersonHit]) -> None:
    """Fetch one page and run every strategy over it. Never raises."""
    if not guard(url):
        return
    try:
        response = http.get(url)
    except Exception as exc:
        log.debug("cascade: %s failed: %s", url, exc)
        return
    if not response:
        return
    final_url, html = response
    if not html:
        return
    for strategy in STRATEGIES:
        try:
            hits.extend(strategy.extract(html, final_url))
        except Exception as exc:
            log.debug("cascade: %s raised on %s: %s", strategy.__name__, final_url, exc)


#: Link vocabulary for pages that name people, most specific first. A guessed
#: path only finds a page the guesser already thought of; a site's own
#: navigation names whatever it actually calls that page — deloitte.ch links
#: "Our People" and "Governance", and no fixed path list reaches either.
_PEOPLE_LINK_TIERS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (
        3,
        re.compile(
            r"gesch[äa]ftsleitung|verwaltungsrat|management|leadership|vorstand|direktion|"
            r"executive|governance|board|leitung|direction",
            re.I,
        ),
    ),
    (
        2,
        re.compile(
            r"\bteam\b|our-people|our_people|/people|mitarbeiter|kollegen|equipe|chi-siamo|"
            r"unser-team|das-team|who-we-are",
            re.I,
        ),
    ),
    (
        1,
        re.compile(
            r"impressum|about|ueber-uns|über-uns|unternehmen|company|kontakt|contact|legal-notice", re.I
        ),
    ),
)

_SKIP_LINK_RX = re.compile(r"\.(pdf|jpe?g|png|svg|zip|docx?|xlsx?)($|\?)|^(mailto|tel|javascript):", re.I)


def discover_people_pages(domain: str, http, *, limit: int = 6) -> list[str]:
    """Pages on the company's own site that its navigation says name people.

    One homepage fetch, then the site tells us where its team lives instead of
    us guessing. Never raises; an unreachable homepage simply yields nothing.
    """
    if not domain or limit <= 0:
        return []
    home = f"https://{domain}/"
    if not guard(home):
        return []
    try:
        response = http.get(home)
    except Exception as exc:
        log.debug("cascade: homepage %s failed: %s", home, exc)
        return []
    if not response:
        return []
    final_url, html = response
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    scored: dict[str, int] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or _SKIP_LINK_RX.search(href):
            continue
        text = anchor.get_text(" ", strip=True)[:60]
        haystack = f"{href} {text}"
        rank = next((weight for weight, rx in _PEOPLE_LINK_TIERS if rx.search(haystack)), 0)
        if not rank:
            continue
        absolute = urljoin(final_url, href).split("#")[0]
        parsed = urlparse(absolute)
        # Same site only: a link to the parent group's site is a different
        # company, and filing its executives here would be wrong.
        if parsed.scheme not in ("http", "https") or domain not in parsed.netloc:
            continue
        scored[absolute] = max(scored.get(absolute, 0), rank)

    ordered = sorted(scored.items(), key=lambda pair: -pair[1])
    return [url for url, _ in ordered[:limit]]


def resolve_people(
    domain: str,
    country: str,
    http,
    *,
    max_pages: int = 8,
    use_sitemap: bool = True,
    max_person_pages: int = 10,
    max_nav_pages: int = 6,
) -> list[PersonHit]:
    """Crawl a company's own site for named people. Never raises."""
    if not domain:
        return []
    hits: list[PersonHit] = []
    for path in candidate_paths(country)[:max_pages]:
        _harvest(f"https://{domain}{path}", http, hits)

    # Whatever the site itself links to. Guessed paths only cover the layouts
    # someone thought of in advance; this covers the rest.
    seen_urls = {f"https://{domain}{path}" for path in candidate_paths(country)[:max_pages]}
    try:
        for url in discover_people_pages(domain, http, limit=max_nav_pages):
            if url not in seen_urls:
                seen_urls.add(url)
                _harvest(url, http, hits)
    except Exception as exc:
        log.debug("cascade: nav discovery failed for %s: %s", domain, exc)

    # Per-person pages the site lists in its own sitemap. Cheaper and far more
    # precise than guessing more paths, because each URL is known to exist.
    if use_sitemap and max_person_pages > 0:
        try:
            for url in sitemap.person_urls(domain, http, limit=max_person_pages):
                _harvest(url, http, hits)
        except Exception as exc:
            log.debug("cascade: sitemap mining failed for %s: %s", domain, exc)

    return merge_hits(hits)
