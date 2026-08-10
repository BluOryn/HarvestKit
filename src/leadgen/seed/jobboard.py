"""Job boards as the company seed.

A company on an IT job board employs IT staff by construction, so the targeting
criterion needs no classifier. Better, the ad often names a recruiter and gives
their direct address, which becomes the anchor that email-pattern inference
needs.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from ..assemble import CompanyContext
from ..company.domain import resolve_domain
from ..email.validate import is_role_account
from ..geo import country_from_location

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


# The board slug is usually a better basis for guessing a company's own domain
# than its legal name: Personio reports "ottonova Holding AG", whose domain is
# ottonova.de, not ottonovaholdingag.de.
_SLUG_FROM_URL = (
    re.compile(r"https?://([a-z0-9][a-z0-9-]{1,40})\.jobs\.personio\.de", re.I),
    re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/([a-z0-9][a-z0-9_-]{1,40})", re.I),
    re.compile(r"https?://jobs\.lever\.co/([a-z0-9][a-z0-9-]{1,40})", re.I),
    re.compile(r"https?://jobs\.ashbyhq\.com/([a-z0-9][a-z0-9.-]{1,40})", re.I),
)


def _ats_slug(listing) -> str:
    for attribute in ("job_url", "apply_url"):
        url = getattr(listing, attribute, "") or ""
        for pattern in _SLUG_FROM_URL:
            match = pattern.match(url)
            if match:
                return match.group(1)
    return ""


def companies_from_listings(
    listings: list,
    http,
    *,
    guess_domains=None,
    concurrency: int = 12,
    countries: frozenset[str] | None = None,
) -> list[CompanyContext]:
    """Collapse listings to unique companies with resolved domains.

    One domain probe per company, not per listing — a company with forty open
    roles must not cost forty probes.

    `guess_domains(company) -> list[str]` supplies fallback candidates for seeds
    whose listings only ever carry an ATS URL. Greenhouse and Lever both do:
    they return the board's jobs but never the employer's own site, so without a
    guesser every company from those boards would be dropped for having no
    resolvable domain.
    """

    def country_of(group: list) -> str:
        """Modal country across a company's ads — one remote US role should not
        relabel a Berlin company."""
        explicit = _first(getattr(item, "country", "") for item in group)
        if explicit:
            return explicit
        codes = Counter(
            code for item in group if (code := country_from_location(getattr(item, "location", "") or ""))
        )
        return codes.most_common(1)[0][0] if codes else ""

    grouped: dict[str, list] = {}
    for listing in listings:
        if not looks_like_it_role(getattr(listing, "title", "")) or not _company_key(listing):
            continue
        grouped.setdefault(_company_key(listing), []).append(listing)

    # Filtering geography here rather than at the cut is the difference between
    # crawling 900 companies to keep 300 and crawling 300. Domain resolution and
    # the person cascade are the expensive steps, and there is no point spending
    # them on a company the geography filter will discard afterwards.
    if countries is not None:
        before = len(grouped)
        grouped = {key: group for key, group in grouped.items() if country_of(group) in countries}
        log.info("seed: %d/%d companies are in the requested geography", len(grouped), before)

    def build(group: list) -> CompanyContext | None:
        first = group[0]
        hints: list[str] = []
        for listing in group:
            for attribute in ("company_website", "apply_url", "job_url"):
                url = getattr(listing, attribute, "") or ""
                if url:
                    hints.append(url)

        if guess_domains is not None:
            hints.extend(guess_domains(_ats_slug(first) or first.company, first.company))

        domain = resolve_domain(first.company, hints, http)
        if not domain:
            log.debug("seed: no own-domain for %r, dropping", first.company)
            return None

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

        return CompanyContext(
            name=first.company,
            domain=domain,
            website=f"https://{domain}",
            country=country_of(group),
            region=_first(getattr(item, "region", "") for item in group),
            city=_first(getattr(item, "city", "") for item in group),
            size_hint=_first(getattr(item, "company_size", "") for item in group),
            industry=_first(getattr(item, "company_industry", "") for item in group),
            tech_stack=", ".join(sorted(tech)),
            seed_url=first.job_url or getattr(first, "apply_url", ""),
            extra_anchors=anchors,
        )

    # Domain resolution is the slowest step in the whole run: several candidate
    # hosts per company, each costing a connect timeout when the guess is wrong.
    # Serially that is hours for a few hundred companies, and it is pure I/O
    # wait, so it parallelises cleanly. Per-host throttling still applies.
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        resolved = list(pool.map(build, grouped.values()))
    return [company for company in resolved if company is not None]
