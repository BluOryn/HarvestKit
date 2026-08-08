"""Fetch the pages that name people, run every strategy, merge the results.

Bounded on purpose: eight pages per company across thousands of companies is
the difference between a run that finishes tonight and one that does not.
Localised paths come first because they are the higher-yield ones.
"""

from __future__ import annotations

import logging
import re

from .hit import PersonHit
from .paths import candidate_paths
from .roles import classify_role
from .strategies import impressum, jsonld_person, team

log = logging.getLogger(__name__)

_WS_RX = re.compile(r"\s+")

# Every strategy runs on every fetched page; order only breaks merge ties.
STRATEGIES = (jsonld_person, impressum, team)


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
        # A title the buyer asked for beats one they did not. "CTO" is more
        # useful on the row than "Geschäftsführer" even though both are true.
        incoming_is_target = classify_role(hit.role) != "other"
        existing_is_target = classify_role(existing.role) != "other"
        if hit.role and (not existing.role or (incoming_is_target and not existing_is_target)):
            existing.role = hit.role
    return list(merged.values())


def resolve_people(domain: str, country: str, http, *, max_pages: int = 8) -> list[PersonHit]:
    """Crawl a company's own site for named people. Never raises."""
    if not domain:
        return []
    hits: list[PersonHit] = []
    for path in candidate_paths(country)[:max_pages]:
        url = f"https://{domain}{path}"
        try:
            response = http.get(url)
        except Exception as exc:
            log.debug("cascade: %s failed: %s", url, exc)
            continue
        if not response:
            continue
        final_url, html = response
        if not html:
            continue
        for strategy in STRATEGIES:
            try:
                hits.extend(strategy.extract(html, final_url))
            except Exception as exc:
                log.debug("cascade: %s raised on %s: %s", strategy.__name__, final_url, exc)
    return merge_hits(hits)
