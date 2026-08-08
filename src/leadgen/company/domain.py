"""Find the company's *own* website.

A job ad's URL is usually on an ATS or an aggregator, and crawling those for an
Impressum finds the ATS vendor's legal notice rather than the employer's.
Everything in ATS_HOSTS is therefore rejected before any fetch.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ATS_HOSTS: frozenset[str] = frozenset(
    {
        # Applicant tracking systems — the vendor's site, not the employer's.
        "greenhouse.io",
        "lever.co",
        "ashbyhq.com",
        "myworkdayjobs.com",
        "workday.com",
        "personio.de",
        "personio.com",
        "recruitee.com",
        "workable.com",
        "smartrecruiters.com",
        "successfactors.com",
        "successfactors.eu",
        "taleo.net",
        "icims.com",
        "bamboohr.com",
        "teamtailor.com",
        "jobvite.com",
        "breezy.hr",
        "join.com",
        "softgarden.io",
        "softgarden.de",
        "recruitis.io",
        "jobs.cz",
        "concludis.de",
        "d-vinci.de",
        "rexx-systems.com",
        "haufe.com",
        "prescreen.io",
        # Aggregators and boards.
        "linkedin.com",
        "indeed.com",
        "glassdoor.com",
        "xing.com",
        "stepstone.de",
        "monster.com",
        "monster.de",
        "jobs.ch",
        "finn.no",
        "arbeitsagentur.de",
        "welcometothejungle.com",
        "jobbsafari.no",
        "karrierestart.no",
        "nav.no",
        "totaljobs.com",
        "reed.co.uk",
        "infojobs.net",
        "pracuj.pl",
        "jobrapido.com",
        "talent.com",
        "adzuna.com",
        "jooble.org",
        "neuvoo.com",
        "eures.europa.eu",
        # Platforms that host pages but are never the company itself.
        "facebook.com",
        "twitter.com",
        "x.com",
        "instagram.com",
        "youtube.com",
        "google.com",
        "github.com",
        "medium.com",
        "notion.site",
        "wixsite.com",
        "squarespace.com",
        "wordpress.com",
        "blogspot.com",
        "bit.ly",
        "crunchbase.com",
    }
)


def _registrable(host: str) -> str:
    return (host or "").lower().strip().removeprefix("www.")


def is_company_site(url: str) -> bool:
    """False for ATS vendors, job aggregators and social platforms."""
    host = _registrable(urlparse(url or "").netloc)
    if not host or "." not in host:
        return False
    return not any(host == bad or host.endswith("." + bad) for bad in ATS_HOSTS)


def resolve_domain(company_name: str, hints: list[str], http) -> str:
    """Return the registrable domain of the company's own site, or "".

    `hints` are URLs seen alongside the company — the ad's apply link, a website
    field, a logo link. They are tried in order; the first that is not an ATS
    host and actually responds wins. No search-engine querying: it is
    rate-limited, unreliable, and against most engines' terms.
    """
    tried: set[str] = set()
    for hint in hints:
        if not hint or not is_company_site(hint):
            continue
        host = _registrable(urlparse(hint).netloc)
        if not host or host in tried:
            continue
        tried.add(host)
        for scheme in ("https", "http"):
            try:
                if http.get(f"{scheme}://{host}/") is not None:
                    return host
            except Exception as exc:
                log.debug("domain: %s://%s failed: %s", scheme, host, exc)
    return ""
