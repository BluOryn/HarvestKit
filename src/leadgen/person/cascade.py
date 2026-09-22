"""Fetch the pages that name people, run every strategy, merge the results.

Bounded on purpose: eight pages per company across thousands of companies is
the difference between a run that finishes tonight and one that does not.
Localised paths come first because they are the higher-yield ones.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..email.validate import is_role_account
from ..net_guard import guard
from .hit import PersonHit
from .jobad import _email_belongs_to
from .paths import candidate_paths
from .roles import role_rank
from .strategies import impressum, jsonld_person, press, sitemap, team

log = logging.getLogger(__name__)

_WS_RX = re.compile(r"\s+")

# Every strategy runs on every fetched page; order only breaks merge ties.
STRATEGIES = (jsonld_person, impressum, team, press)


def _key(name: str) -> str:
    return _WS_RX.sub(" ", (name or "").strip().lower())


def _email_rank(email: str, name: str) -> int:
    """How good an address is *for this person*. Higher wins.

    2 — a personal address that echoes the name: unambiguous.
    1 — a personal-looking address that does not: better than a shared inbox.
    0 — a role account, or nothing at all.
    """
    if not email:
        return 0
    if is_role_account(email):
        return 0
    return 2 if _email_belongs_to(email, name) else 1


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
        for attribute in ("phone", "linkedin", "source_url"):
            if not getattr(existing, attribute) and getattr(hit, attribute):
                setattr(existing, attribute, getattr(hit, attribute))
        # Email is not first-wins. The crawl order puts /impressum before
        # /team in DACH, so a shared `info@` scraped from the legal notice used
        # to beat the person's real address found a page later — and then the
        # domain had no personal address left for pattern inference. Rank the
        # candidates instead of trusting arrival order.
        if hit.email and _email_rank(hit.email, existing.name) > _email_rank(existing.email, existing.name):
            existing.email = hit.email
        # A title the buyer asked for beats one they did not, and among titles
        # they did ask for, the function beats the office: "CTO" is more useful
        # on the row than "Geschäftsführer" even though both are true of the
        # same person. A binary target/not-target test cannot express that.
        if hit.role and (not existing.role or role_rank(hit.role) > role_rank(existing.role)):
            existing.role = hit.role
    return list(merged.values())


@dataclass
class Reachability:
    """What the network said while crawling one company.

    Without this, a company behind a bot wall and a company whose site genuinely
    names nobody are the same event: `resolve_people` returns an empty list for
    both, and the funnel files both under `no_person_found`. That conflation is
    why a run could report thousands of companies "not naming anyone" while the
    real answer was that we never saw their pages — and why the runbook's advice
    for that symptom pointed at the team-page parser, which was innocent.
    """

    fetched: int = 0
    blocked: int = 0
    errors: int = 0
    not_found: int = 0
    robots_denied: int = 0

    @property
    def saw_content(self) -> bool:
        return self.fetched > 0

    @property
    def walled(self) -> bool:
        """Every attempt was refused, and at least one refusal was a refusal of
        *us* rather than a missing page."""
        return not self.saw_content and (self.blocked > 0 or self.robots_denied > 0)

    def note(self, outcome_value: str) -> None:
        if outcome_value == "ok":
            self.fetched += 1
        elif outcome_value == "blocked":
            self.blocked += 1
        elif outcome_value == "robots_denied":
            self.robots_denied += 1
        elif outcome_value == "not_found":
            self.not_found += 1
        else:
            self.errors += 1


def _harvest(url: str, http, hits: list[PersonHit], reach: Optional[Reachability] = None) -> None:
    """Fetch one page and run every strategy over it. Never raises."""
    if not guard(url):
        return
    # `fetch` carries the reason a page did not arrive; `get` flattens every
    # reason to None. Prefer the former, but keep working against the simpler
    # client that tests and tools supply.
    fetch = getattr(http, "fetch", None)
    if callable(fetch):
        try:
            result = fetch(url)
        except Exception as exc:
            log.debug("cascade: %s failed: %s", url, exc)
            if reach is not None:
                reach.errors += 1
            return
        outcome = getattr(result, "outcome", None)
        if reach is not None:
            reach.note(getattr(outcome, "value", str(outcome or "error")))
        if not getattr(result, "ok", False) or not result.text:
            return
        final_url, html = result.final_url or url, result.text
    else:
        try:
            response = http.get(url)
        except Exception as exc:
            log.debug("cascade: %s failed: %s", url, exc)
            if reach is not None:
                reach.errors += 1
            return
        if not response:
            if reach is not None:
                reach.errors += 1
            return
        final_url, html = response
        if not html:
            if reach is not None:
                reach.errors += 1
            return
        if reach is not None:
            reach.fetched += 1

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
    people, _ = resolve_people_detailed(
        domain,
        country,
        http,
        max_pages=max_pages,
        use_sitemap=use_sitemap,
        max_person_pages=max_person_pages,
        max_nav_pages=max_nav_pages,
    )
    return people


def resolve_people_detailed(
    domain: str,
    country: str,
    http,
    *,
    max_pages: int = 8,
    use_sitemap: bool = True,
    max_person_pages: int = 10,
    max_nav_pages: int = 6,
) -> tuple[list[PersonHit], Reachability]:
    """`resolve_people`, plus what the network said while doing it.

    The caller needs both: an empty result means something different depending
    on whether the site answered.
    """
    reach = Reachability()
    if not domain:
        return [], reach
    hits: list[PersonHit] = []
    for path in candidate_paths(country)[:max_pages]:
        _harvest(f"https://{domain}{path}", http, hits, reach)

    # Whatever the site itself links to. Guessed paths only cover the layouts
    # someone thought of in advance; this covers the rest.
    seen_urls = {f"https://{domain}{path}" for path in candidate_paths(country)[:max_pages]}
    try:
        for url in discover_people_pages(domain, http, limit=max_nav_pages):
            if url not in seen_urls:
                seen_urls.add(url)
                _harvest(url, http, hits, reach)
    except Exception as exc:
        log.debug("cascade: nav discovery failed for %s: %s", domain, exc)

    # Per-person pages the site lists in its own sitemap. Cheaper and far more
    # precise than guessing more paths, because each URL is known to exist.
    if use_sitemap and max_person_pages > 0:
        try:
            for url in sitemap.person_urls(domain, http, limit=max_person_pages):
                _harvest(url, http, hits, reach)
        except Exception as exc:
            log.debug("cascade: sitemap mining failed for %s: %s", domain, exc)

    return merge_hits(hits), reach
