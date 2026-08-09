"""Job boards as the company seed.

A company on an IT job board employs IT staff by construction, so the targeting
criterion needs no classifier. Better, the ad often names a recruiter and gives
their direct address, which becomes the anchor that email-pattern inference
needs.
"""

from __future__ import annotations

import logging
import re

from ..assemble import CompanyContext
from ..company.domain import resolve_domain
from ..email.validate import is_role_account

log = logging.getLogger(__name__)

# Deliberately broad: the brief is "any sector which employs IT", so a logistics
# firm hiring one sysadmin qualifies.
IT_TITLE_PATTERNS: list[str] = [
    r"software",
    r"developer",
    r"entwickler(?:in)?",
    r"d[ée]veloppeur(?:se)?",
    r"sviluppatore",
    r"desarrollador(?:a)?",
    r"programm(?:er|ier\w*|atore)",
    r"engineer(?:ing)?",
    r"ingenieur|ingénieur|ingegnere",
    r"devops|sre|site reliability",
    r"data (?:engineer|scientist|analyst|architect)",
    r"machine learning|ml engineer|\bai\b engineer",
    r"cloud|kubernetes|\baws\b|\bazure\b|terraform",
    r"backend|back-end|frontend|front-end|fullstack|full-stack|full stack",
    r"\bit\b|informatik\w*|informatique|informatica|informatyk\w*",
    r"sysadmin|system(?:s)? admin\w*|systemadministrator(?:in)?",
    r"database|datenbank|\bdba\b",
    r"security engineer|cyber ?security|it[- ]security|informationssicherheit",
    r"qa engineer|test engineer|quality assurance|testautomat\w*",
    r"architect(?:ure)?|architekt(?:in)?",
    r"technical lead|tech lead|teamlead|team lead",
    r"scrum master|product owner",
    r"\bsap\b|salesforce|servicenow",
    r"mobile developer|ios developer|android developer",
    r"platform engineer|integration engineer|network engineer|netzwerk\w*",
]
IT_TITLE_RX = re.compile(r"(?:" + "|".join(IT_TITLE_PATTERNS) + r")", re.I)


def looks_like_it_role(title: str) -> bool:
    return bool(title) and bool(IT_TITLE_RX.search(title))


def _first(values) -> str:
    return next((value for value in values if value), "")


def _company_key(listing) -> str:
    return (getattr(listing, "company", "") or "").strip().lower()


def companies_from_listings(listings: list, http, *, guess_domains=None) -> list[CompanyContext]:
    """Collapse listings to unique companies with resolved domains.

    One domain probe per company, not per listing — a company with forty open
    roles must not cost forty probes.

    `guess_domains(company) -> list[str]` supplies fallback candidates for seeds
    whose listings only ever carry an ATS URL. Greenhouse and Lever both do:
    they return the board's jobs but never the employer's own site, so without a
    guesser every company from those boards would be dropped for having no
    resolvable domain.
    """
    grouped: dict[str, list] = {}
    for listing in listings:
        if not looks_like_it_role(getattr(listing, "title", "")) or not _company_key(listing):
            continue
        grouped.setdefault(_company_key(listing), []).append(listing)

    companies: list[CompanyContext] = []
    for group in grouped.values():
        first = group[0]
        hints: list[str] = []
        for listing in group:
            for attribute in ("company_website", "apply_url", "job_url"):
                url = getattr(listing, attribute, "") or ""
                if url:
                    hints.append(url)

        if guess_domains is not None:
            hints.extend(guess_domains(first.company))

        domain = resolve_domain(first.company, hints, http)
        if not domain:
            log.debug("seed: no own-domain for %r, dropping", first.company)
            continue

        anchors: list[tuple[str, str]] = []
        tech: set[str] = set()
        for listing in group:
            for name_attr, email_attr in (
                ("recruiter_name", "recruiter_email"),
                ("hiring_manager", "hiring_manager_email"),
            ):
                name = getattr(listing, name_attr, "") or ""
                email = getattr(listing, email_attr, "") or ""
                if name and email and not is_role_account(email):
                    anchors.append((name, email))
            for token in (getattr(listing, "tech_stack", "") or "").split(","):
                if token.strip():
                    tech.add(token.strip().lower())

        companies.append(
            CompanyContext(
                name=first.company,
                domain=domain,
                website=f"https://{domain}",
                country=_first(getattr(item, "country", "") for item in group),
                region=_first(getattr(item, "region", "") for item in group),
                city=_first(getattr(item, "city", "") for item in group),
                size_hint=_first(getattr(item, "company_size", "") for item in group),
                industry=_first(getattr(item, "company_industry", "") for item in group),
                tech_stack=", ".join(sorted(tech)),
                seed_url=first.job_url or getattr(first, "apply_url", ""),
                extra_anchors=anchors,
            )
        )
    return companies
