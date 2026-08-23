"""jobs.ch as a Swiss company seed.

Every other seed reaches Swiss employers by accident. Greenhouse and Lever are
US products, Workable's search is a global index filtered down, and Arbeitnow is
German-market. jobs.ch is the Swiss market, and its search URL carries the
sector and the recency window as query parameters, so the targeting happens at
the source instead of being filtered back out afterwards.

Two things make it the highest-yield seed in this pipeline:

  1. `hiringOrganization.sameAs` in the posting's JSON-LD is the employer's own
     website. Measured over 52 companies, 83% arrive with a usable own-domain,
     which skips domain resolution entirely — the slowest and most lossy step in
     the whole run.

  2. Swiss ads print a named contact with a direct phone far more often than an
     ATS feed does, and `person.jobad` reads it straight out of the description.

The public JSON API at /api/v1/public/search is deliberately not used: it
ignores the filter querystring outright and returns the whole corpus. The SSR
HTML honours it, which is the entire point of seeding from a filter.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from job_scraper.models import JobListing
from job_scraper.normalize import canonicalize_url

log = logging.getLogger(__name__)

#: The shipped filter: four IT categories, four employment types, last 30 days.
#: Categories are jobs.ch's own taxonomy ids —
#:   106 Information technology / Telecom.   146 Engineering / Technical
#:   156 Management / Consulting             167 Electronics / Electrotechnics
#: Employment types 1/2/4/5 are the permanent and fixed-term staff contracts;
#: apprenticeships and internships are deliberately absent.
DEFAULT_FILTER_URL = (
    "https://www.jobs.ch/en/vacancies/"
    "?category=106&category=146&category=156&category=167"
    "&employment-type=1&employment-type=2&employment-type=4&employment-type=5"
    "&publication-date=30&term="
)

#: jobs.ch's own taxonomy ids for the sectors the shipped filter selects.
IT_CATEGORIES = (106, 146, 156, 167)

#: Permanent and fixed-term staff contracts. Apprenticeships (3) and internships
#: (6) are deliberately absent: they have no budget and no hiring authority.
STAFF_EMPLOYMENT_TYPES = (1, 2, 4, 5)

#: Default recency window, in days.
DEFAULT_DAYS = 30


def build_filter_url(
    *,
    days: int = DEFAULT_DAYS,
    categories: tuple[int, ...] = IT_CATEGORIES,
    employment_types: tuple[int, ...] = STAFF_EMPLOYMENT_TYPES,
    term: str = "",
) -> str:
    """A jobs.ch search URL. The filter is the targeting, so this is the knob.

    `days` is the one that matters day to day. Measured against the live board
    with the shipped sector filter: 1 day returns 25 postings, 3 returns 185,
    7 returns 478, 30 returns 1,396. A daily run that keeps asking for 30 days
    re-fetches thirteen hundred postings to discover employers it already
    crawled; asking for 3 covers everything posted since yesterday at an eighth
    of the requests.
    """
    parts = [f"category={cat}" for cat in categories]
    parts += [f"employment-type={kind}" for kind in employment_types]
    parts.append(f"publication-date={max(1, int(days))}")
    parts.append(f"term={quote_plus(term.strip())}")
    return "https://www.jobs.ch/en/vacancies/?" + "&".join(parts)


DETAIL_RX = re.compile(r"/(?:en|de|fr|it)/(?:vacancies|stellenangebote|offres-emplois)/detail/([\w\-]{36})")
_RESULT_COUNT_RX = re.compile(r"([\d'’,\.]+)\s*(?:jobs|Jobs|Stellen|emplois)")

#: jobs.ch renders 22 cards per SSR page.
PAGE_SIZE = 22


def _detail_url(uuid: str) -> str:
    return f"https://www.jobs.ch/en/vacancies/detail/{uuid}/"


def _job_posting(html: str) -> dict | None:
    for script in BeautifulSoup(html, "lxml").find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)).strip()


def _org_website(org: dict) -> str:
    """The employer's own site, when `sameAs` actually points at one.

    jobs.ch puts a LinkedIn page or its own company profile there for employers
    that gave it no homepage, and neither is the company's site.
    """
    from ..company.domain import is_company_site

    same = str(org.get("sameAs") or "").strip()
    return same if same and is_company_site(same) else ""


def _listing_from_detail(html: str, url: str) -> JobListing | None:
    posting = _job_posting(html)
    if not posting:
        return None
    org = posting.get("hiringOrganization")
    org = org if isinstance(org, dict) else {}
    company = str(org.get("name") or "").strip()
    if not company:
        return None

    address = {}
    location = posting.get("jobLocation")
    location = location[0] if isinstance(location, list) and location else location
    if isinstance(location, dict) and isinstance(location.get("address"), dict):
        address = location["address"]
    # jobs.ch puts the town in `addressRegion` and leaves `addressLocality`
    # empty, so reading city off the standard field alone yields nothing.
    city = str(address.get("addressLocality") or address.get("addressRegion") or "").strip()

    listing = JobListing(
        title=str(posting.get("title") or "").strip(),
        company=company,
        company_website=_org_website(org),
        company_industry=str(posting.get("industry") or "").strip(),
        location=", ".join(part for part in (city, str(address.get("postalCode") or "").strip()) if part),
        city=city,
        postal_code=str(address.get("postalCode") or "").strip(),
        country="CH",
        employment_type=str(posting.get("employmentType") or "").strip(),
        posted_date=str(posting.get("datePosted") or "").strip(),
        description=_text(str(posting.get("description") or ""))[:20000],
        job_url=canonicalize_url(url),
        apply_url=canonicalize_url(url),
        source_ats="jobs.ch",
        source_domain="www.jobs.ch",
    )
    # The employer overview is the company's own boilerplate, which is where the
    # size and sector wording lives when the profile page is not fetched.
    overview = _text(str(posting.get("employerOverview") or ""))
    if overview:
        listing.set_extra("employer_overview", overview[:2000])
    profile = str(posting.get("sameAs") or "")
    if "jobs.ch/" in profile:
        listing.set_extra("jobsch_company_profile", profile)
    return listing


def _uuids(http, filter_url: str, max_pages: int) -> list[str]:
    separator = "&" if "?" in filter_url else "?"
    found: list[str] = []
    seen: set[str] = set()
    for page in range(1, max(1, max_pages) + 1):
        url = filter_url if page == 1 else f"{filter_url}{separator}page={page}"
        response = http.get(url)
        if not response:
            log.warning("jobs.ch: listing page %d returned nothing — stopping", page)
            break
        if page == 1:
            total = _RESULT_COUNT_RX.search(response[1])
            if total:
                log.info("jobs.ch: filter reports %s matching jobs", total.group(1))
        new = [u for u in DETAIL_RX.findall(response[1]) if u not in seen]
        if not new:
            break
        seen.update(new)
        found.extend(new)
    return found


def _organization(html: str) -> dict:
    for script in BeautifulSoup(html, "lxml").find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") in ("Organization", "Corporation"):
                return item
    return {}


def _enrich_from_profiles(http, listings: list[JobListing], concurrency: int) -> int:
    """Fill the employer's own website (and size) from its jobs.ch profile page.

    When an employer gives jobs.ch no homepage, the posting's
    `hiringOrganization.sameAs` points at the jobs.ch profile instead. Falling
    back to guessing the domain from the company name is how "Empa" became
    empa.com and "Suva" became suva.de — real sites belonging to other people.
    The profile page carries an `Organization` block whose `url` is the real
    website, so one fetch per company recovers it outright.

    It also carries `numberOfEmployees`, which nothing else in this pipeline
    provides. One request per company, not per posting.
    """
    wanted: dict[str, list[JobListing]] = {}
    for listing in listings:
        profile = str(listing.extras.get("jobsch_company_profile") or "")
        if profile and not listing.company_website:
            wanted.setdefault(profile, []).append(listing)
    if not wanted:
        return 0

    def one(profile: str) -> tuple[str, dict]:
        try:
            response = http.get(profile)
        except Exception:
            return profile, {}
        return profile, _organization(response[1]) if response else {}

    recovered = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for profile, org in pool.map(one, list(wanted)):
            if not org:
                continue
            website = str(org.get("url") or "").strip()
            employees = org.get("numberOfEmployees")
            size = ""
            if isinstance(employees, dict):
                size = str(employees.get("minValue") or employees.get("value") or "").strip()
            elif employees:
                size = str(employees).strip()
            catalog = org.get("hasOfferCatalog")
            sector = str(catalog.get("name") or "").strip() if isinstance(catalog, dict) else ""
            address = org.get("address") if isinstance(org.get("address"), dict) else {}
            locality = str(address.get("addressLocality") or "").strip()

            for listing in wanted[profile]:
                from ..company.domain import is_company_site

                if website and is_company_site(website):
                    listing.company_website = website
                    recovered += 1
                if size:
                    listing.company_size = f"{size}+ employees"
                if sector and not listing.company_industry:
                    listing.company_industry = sector
                if locality and not listing.city:
                    listing.city = locality
    return recovered


def fetch(
    http,
    *,
    filter_url: str = DEFAULT_FILTER_URL,
    max_pages: int = 40,
    concurrency: int = 8,
) -> list[JobListing]:
    """Walk the filtered listing and return one JobListing per posting.

    Never raises: a dead page ends the walk and a dead posting is skipped, so a
    partial harvest still seeds the run.
    """
    uuids = _uuids(http, filter_url, max_pages)
    log.info("jobs.ch: %d postings matched the filter", len(uuids))
    if not uuids:
        return []

    def one(uuid: str) -> JobListing | None:
        url = _detail_url(uuid)
        try:
            response = http.get(url)
        except Exception as exc:
            log.debug("jobs.ch: %s failed: %s", url, exc)
            return None
        if not response:
            return None
        try:
            return _listing_from_detail(response[1], response[0] or url)
        except Exception as exc:
            log.debug("jobs.ch: could not parse %s: %s", url, exc)
            return None

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        listings = [item for item in pool.map(one, uuids) if item is not None]

    direct = sum(1 for item in listings if item.company_website)
    recovered = _enrich_from_profiles(http, listings, concurrency)
    log.info(
        "jobs.ch: %d postings parsed · %d carried the employer's website · %d more recovered "
        "from the company profile",
        len(listings),
        direct,
        recovered,
    )
    return listings
