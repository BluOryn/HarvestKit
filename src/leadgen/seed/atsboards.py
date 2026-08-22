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

from ..net_guard import _resolves_to_public

log = logging.getLogger(__name__)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
# Personio's customer base is overwhelmingly DACH SMEs, which makes it the
# highest-value seed for a European list — and DACH is also where §5 TMG
# guarantees the Impressum names a real person.
PERSONIO_URL = "https://{slug}.jobs.personio.de/search.json"

# Ordered by how often they turn out to be right for EU tech companies.
DOMAIN_TLDS = (
    "com",
    "de",
    "ch",
    "io",
    "co",
    "fr",
    "nl",
    "at",
    "fi",
    "se",
    "eu",
    "ai",
    "es",
    "it",
    "pl",
)

#: The national TLD to try first when the company's country is known. A Swiss
#: company is on .ch, and guessing .com first found someone else's site —
#: "Empa" became empa.com, "Suva" became suva.de. A wrong domain is worse than
#: no domain: it crawls a stranger and files the result under this company.
COUNTRY_TLD: dict[str, str] = {
    "CH": "ch",
    "DE": "de",
    "AT": "at",
    "LI": "li",
    "FR": "fr",
    "IT": "it",
    "NL": "nl",
    "BE": "be",
    "ES": "es",
    "PT": "pt",
    "PL": "pl",
    "CZ": "cz",
    "SE": "se",
    "NO": "no",
    "DK": "dk",
    "FI": "fi",
    "IE": "ie",
    "GB": "co.uk",
    "LU": "lu",
}

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


MAX_GUESSES = 4

# A German job board reports the legal entity, not the brand: "Blue Incite GmbH
# (A company of Allianz)". Guessing from that verbatim yields
# blueincitegmbhacompanyofallianz.de, which resolves for nobody, so the trading
# name has to be recovered first. Ordered longest-first so "GmbH & Co. KG" is
# removed whole rather than leaving "& Co." behind.
_LEGAL_SUFFIXES = (
    "gmbh & co. kg",
    "gmbh & co kg",
    "ag & co. kg",
    "sp. z o.o.",
    "s.p.a.",
    "s.r.l.",
    "gmbh",
    "mbh",
    "ug",
    "ag",
    "kg",
    "ohg",
    "gbr",
    "se",
    "e.v.",
    "ev",
    "e.k.",
    "ltd",
    "limited",
    "plc",
    "inc",
    "llc",
    "corp",
    "b.v.",
    "bv",
    "n.v.",
    "nv",
    "sarl",
    "sas",
    "sa",
    "spa",
    "srl",
    "oy",
    "ab",
    "as",
    "aps",
    "a/s",
    "kft",
    "zrt",
    "holding",
    "group",
    "gruppe",
)
_PARENTHETICAL_RX = re.compile(r"\([^)]*\)")


def _trading_name(company: str) -> str:
    """Strip the parts of a legal name that never appear in a domain."""
    name = _PARENTHETICAL_RX.sub(" ", (company or "").lower())
    name = re.sub(r"[^a-z0-9\s&.-]", " ", name)
    for _ in range(3):  # "Beispiel Holding GmbH" needs two passes
        stripped = name
        for suffix in _LEGAL_SUFFIXES:
            stripped = re.sub(rf"(?:^|\s){re.escape(suffix)}(?=\s|$)", " ", stripped)
        stripped = re.sub(r"\s+", " ", stripped).strip(" .,&-")
        if stripped == name.strip(" .,&-"):
            break
        name = stripped
    return re.sub(r"\s+", " ", name).strip(" .,&-")


def guess_domains(slug: str, company: str = "", country: str = "") -> list[str]:
    """Candidate own-domains for a board slug, best guess first.

    Candidates are filtered by DNS before being returned. Most guesses are for
    domains that simply do not exist, and letting those reach the HTTP resolver
    costs a full connect timeout each — with a dozen TLDs per company that is
    minutes of dead waiting per board. A failed getaddrinfo costs milliseconds,
    and net_guard already caches the result.

    `country` puts that market's TLD first. Without it a Swiss run resolved
    "Empa" to empa.com and "Suva" to suva.de — real sites, wrong companies, and
    the crawler then filed a stranger's staff under this employer.
    """
    stems: list[str] = []
    for raw in (slug, _trading_name(company), company):
        cleaned = re.sub(r"[^a-z0-9\s-]", "", (raw or "").lower()).strip()
        if not cleaned:
            continue
        for variant in (cleaned.replace(" ", "").replace("-", ""), cleaned.replace(" ", "-")):
            if variant and variant not in stems:
                stems.append(variant)

    national = COUNTRY_TLD.get((country or "").strip().upper(), "")
    tlds = (national, *DOMAIN_TLDS) if national else DOMAIN_TLDS

    live: list[str] = []
    for stem in stems:
        for tld in dict.fromkeys(tlds):
            host = f"{stem}.{tld}"
            if _resolves_to_public(host):
                live.append(f"https://{host}/")
                if len(live) >= MAX_GUESSES:
                    return live
    return live


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


def _personio(slug: str, http) -> list[JobListing]:
    response = http.get(PERSONIO_URL.format(slug=slug))
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
        offices = item.get("offices") or []
        location = item.get("office") or (offices[0] if offices else "")
        # `subcompany` carries the legal entity ("ottonova Holding AG - 9680").
        # It is a far better company name than the board slug, once the trailing
        # Personio account number is stripped.
        subcompany = re.sub(r"\s*-\s*\d+\s*$", "", str(item.get("subcompany") or "")).strip()
        listings.append(
            JobListing(
                title=item.get("name") or "",
                company=subcompany or slug,
                location=location,
                city=location,
                department=item.get("department") or "",
                seniority=item.get("seniority") or "",
                employment_type=item.get("employment_type") or "",
                description=_text(item.get("description") or "")[:20000],
                skills=item.get("keywords") or "",
                job_url=f"https://{slug}.jobs.personio.de/job/{item.get('id')}",
                apply_url=f"https://{slug}.jobs.personio.de/job/{item.get('id')}",
                source_ats="personio",
            )
        )
    return listings


FETCHERS = {"greenhouse": _greenhouse, "lever": _lever, "personio": _personio}


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
