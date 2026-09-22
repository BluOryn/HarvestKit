"""Find the company's *own* website, and the domain its mail lives on.

A job ad's URL is usually on an ATS or an aggregator, and crawling those for an
Impressum finds the ATS vendor's legal notice rather than the employer's.
Everything in ATS_HOSTS is therefore rejected before any fetch.

Three distinctions here are worth more than the rest of the module:

**"Not the company's site" is not the same as "the site refused us."** The old
probe accepted a host only when `http.get()` returned a body, and `get()`
returns `None` for 403. Four of six European employer domains measured in the
2026-09 audit answer 403 to a plain request, so a bot wall deleted the company
from the run before person discovery began — with the correct domain sitting
right there in the hint. A host that answers *anything*, wall included, exists.

**The host we crawl is not the domain that receives mail.** `karriere.sap.com`
is where the ad points; `sap.com` is where the mailbox is. Career subdomains
almost never carry MX, so returning one meant every generated address for that
employer failed the MX gate. Both are kept, separately.

**A host nobody put on a denylist is not thereby the employer's.** The old
check was a fixed list of ~70 aggregators, so karriere.at, jobup.ch, indeed.de
and stepstone.nl all passed as "the company's own site" and the cascade mined
the *board operator's* Impressum. Acceptance now needs positive corroboration
that the site belongs to this company.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from ..net_guard import guard

log = logging.getLogger(__name__)

ATS_HOSTS: frozenset[str] = frozenset(
    {
        # Applicant tracking systems — the vendor's site, not the employer's.
        "greenhouse.io",
        "lever.co",
        "ashbyhq.com",
        "myworkdayjobs.com",
        "myworkdaysite.com",
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
        "jobteaser.com",
        "eploy.co.uk",
        "hrs4r.eu",
        "onlyfy.com",
        "dvinci-hr.com",
        # Aggregators and boards. The long tail matters as much as the famous
        # names: every one of these was accepted as "the company's own site"
        # before, because only its .com sibling had been listed.
        "linkedin.com",
        "indeed.com",
        "indeed.de",
        "indeed.fr",
        "indeed.nl",
        "indeed.it",
        "indeed.es",
        "indeed.at",
        "indeed.ch",
        "indeed.be",
        "indeed.pl",
        "indeed.se",
        "indeed.co.uk",
        "glassdoor.com",
        "glassdoor.de",
        "glassdoor.fr",
        "glassdoor.nl",
        "glassdoor.co.uk",
        "xing.com",
        "kununu.com",
        "stepstone.de",
        "stepstone.at",
        "stepstone.be",
        "stepstone.nl",
        "stepstone.fr",
        "stepstone.co.uk",
        "monster.com",
        "monster.de",
        "monster.fr",
        "monster.it",
        "monster.ch",
        "monster.at",
        "monster.co.uk",
        "jobs.ch",
        "jobup.ch",
        "jobscout24.ch",
        "ostjob.ch",
        "jobagent.ch",
        "jobwinner.ch",
        "karriere.at",
        "willhaben.at",
        "derstandard.at",
        "finn.no",
        "arbeitsagentur.de",
        "welcometothejungle.com",
        "jobbsafari.no",
        "jobbsafari.se",
        "karrierestart.no",
        "nav.no",
        "arbetsformedlingen.se",
        "jobnet.dk",
        "jobindex.dk",
        "totaljobs.com",
        "reed.co.uk",
        "cv-library.co.uk",
        "infojobs.net",
        "infojobs.it",
        "pracuj.pl",
        "nofluffjobs.com",
        "justjoin.it",
        "jobrapido.com",
        "talent.com",
        "adzuna.com",
        "adzuna.de",
        "adzuna.co.uk",
        "jooble.org",
        "neuvoo.com",
        "eures.europa.eu",
        "hellowork.com",
        "apec.fr",
        "cadremploi.fr",
        "nationalevacaturebank.nl",
        "werkzoeken.nl",
        "intermediair.nl",
        "vacaturebank.nl",
        "tecnoempleo.com",
        "infoempleo.com",
        "subito.it",
        "trovolavoro.it",
        "stellenanzeigen.de",
        "jobware.de",
        "meinestadt.de",
        "absolventa.de",
        "get-in-it.de",
        "yourfirm.de",
        "empregos.com.br",
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
        "northdata.de",
        "northdata.com",
        "firmenwissen.de",
        "wikipedia.org",
        "youtu.be",
        "t.co",
        "tiktok.com",
    }
)

#: Legal-form and filler tokens that carry no identity. Stripped before a
#: company name is compared with a domain stem, so "Acme Software GmbH & Co.
#: KG" is matched by acme.de.
_LEGAL_FORMS: frozenset[str] = frozenset(
    {
        "gmbh",
        "mbh",
        "ug",
        "ag",
        "kg",
        "kgaa",
        "ohg",
        "gbr",
        "ev",
        "eg",
        "se",
        "co",
        "cie",
        "inc",
        "llc",
        "ltd",
        "limited",
        "plc",
        "corp",
        "corporation",
        "company",
        "holding",
        "holdings",
        "group",
        "gruppe",
        "groupe",
        "gruppo",
        "sa",
        "sas",
        "sarl",
        "sasu",
        "eurl",
        "sprl",
        "bv",
        "nv",
        "vof",
        "cv",
        "aps",
        "as",
        "asa",
        "ab",
        "oy",
        "oyj",
        "srl",
        "spa",
        "snc",
        "sl",
        "slu",
        "sau",
        "sp",
        "zoo",
        "sro",
        "kft",
        "doo",
        "the",
        "and",
        "und",
        "et",
        "di",
        "de",
        "da",
        "von",
        "van",
        "der",
        "den",
        "deutschland",
        "schweiz",
        "suisse",
        "austria",
        "france",
        "italia",
        "espana",
        "nederland",
        "norge",
        "sverige",
        "international",
        "europe",
        "global",
        "solutions",
        "services",
        "systems",
        "technologies",
        "technology",
        "consulting",
        "digital",
    }
)

#: Subdomain labels that mean "this is a section of the site", never a separate
#: organisation. Their presence is the signal that the mail domain is a label up.
_SITE_SECTION_LABELS: frozenset[str] = frozenset(
    {
        "www",
        "careers",
        "career",
        "karriere",
        "jobs",
        "job",
        "emploi",
        "carriere",
        "carrieres",
        "lavora",
        "empleo",
        "werkenbij",
        "werken",
        "jobb",
        "recruiting",
        "recruitment",
        "hr",
        "apply",
        "bewerbung",
        "stellen",
        "talent",
        "people",
        "join",
        "m",
        "mobile",
        "en",
        "de",
        "fr",
        "it",
        "es",
        "nl",
        "no",
        "se",
        "dk",
        "fi",
        "pl",
        "cz",
        "at",
        "ch",
        "uk",
        "eu",
        "corporate",
        "group",
        "about",
        "info",
        "home",
        "web",
        "portal",
    }
)

#: A parked or placeholder page. Checked only when the body is small, so a real
#: site that happens to use one of these words in a sentence is not thrown away.
_PARKED_MARKERS: tuple[str, ...] = (
    "domain is for sale",
    "domain for sale",
    "this domain is parked",
    "buy this domain",
    "diese domain steht zum verkauf",
    "ce nom de domaine est à vendre",
    "sedoparking",
    "parkingcrew",
    "afternic",
    "hugedomains",
    "domain name registration",
    "website coming soon",
    "under construction",
    "default web page",
    "apache2 ubuntu default page",
    "welcome to nginx",
    "it works!",
)

_PARKED_BODY_LIMIT = 3000

_TITLE_RX = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_OG_SITE_RX = re.compile(
    r"""<meta[^>]+property=["']og:site_name["'][^>]+content=["']([^"']{1,200})["']""", re.I
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    """Lowercase, expand the German/Nordic digraphs, strip accents.

    Domain registrars cannot carry an umlaut, so "Müller" registers muellerNN or
    mullerNN. Both have to compare equal to the name or the corroboration check
    rejects the employer's real site.
    """
    lowered = (text or "").lower()
    for source, target in (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("ß", "ss"),
        ("å", "aa"),
        ("æ", "ae"),
        ("ø", "oe"),
    ):
        lowered = lowered.replace(source, target)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _name_tokens(company_name: str) -> list[str]:
    """Identity-bearing tokens of a company name, legal forms removed."""
    folded = _fold(company_name)
    tokens = [token for token in _NON_ALNUM.split(folded) if token]
    meaningful = [token for token in tokens if token not in _LEGAL_FORMS and len(token) > 1]
    # An all-filler name ("Holding GmbH") must not corroborate everything.
    return meaningful or [token for token in tokens if len(token) > 1]


def _name_slug(company_name: str) -> str:
    return "".join(_name_tokens(company_name))


def registrable(host_or_url: str) -> str:
    """eTLD+1 — the domain that receives mail.

    Uses the Public Suffix List via tldextract so `acme.co.uk` is not truncated
    to `co.uk`, and falls back to the last two labels when the list is
    unavailable (a fresh machine with no cache and no egress).
    """
    host = _host(host_or_url)
    if not host:
        return ""
    try:
        import tldextract

        extracted = tldextract.extract(host)
        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}"
    except Exception:  # tldextract missing, or its suffix cache unwritable
        pass
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _host(host_or_url: str) -> str:
    """The bare hostname of a URL or host string, www. stripped."""
    raw = (host_or_url or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        try:
            raw = urlparse(raw).netloc or ""
        except ValueError:
            return ""
    raw = raw.split("@")[-1].split("/")[0].split("?")[0]
    # Strip a port, but leave a bracketed IPv6 literal intact enough to fail
    # the "." test below rather than being mangled into something plausible.
    if raw.count(":") == 1:
        raw = raw.split(":")[0]
    return raw.lower().removeprefix("www.")


# Kept under its historical name: callers outside this module import it.
_registrable = _host


def is_company_site(url: str) -> bool:
    """False for ATS vendors, job aggregators and social platforms."""
    host = _host(url or "")
    if not host or "." not in host:
        return False
    return not any(host == bad or host.endswith("." + bad) for bad in ATS_HOSTS)


def mail_domain_for(host: str) -> str:
    """The domain a company's addresses live on, given a host we can crawl.

    `careers.acme.com` -> `acme.com`. A subdomain that is *not* a site section
    (`shop.example.com`, a true subsidiary like `de.example.com` is ambiguous)
    still collapses to the registrable domain, because that is where MX is
    published in every case measured.
    """
    return registrable(host)


def _looks_parked(body: str) -> bool:
    if not body:
        return False
    if len(body) > _PARKED_BODY_LIMIT:
        return False
    lowered = body.lower()
    return any(marker in lowered for marker in _PARKED_MARKERS)


def _page_identity(body: str) -> str:
    """Title plus og:site_name — the two places a site states who it is."""
    pieces: list[str] = []
    title = _TITLE_RX.search(body or "")
    if title:
        pieces.append(re.sub(r"\s+", " ", title.group(1)))
    site_name = _OG_SITE_RX.search(body or "")
    if site_name:
        pieces.append(site_name.group(1))
    return " ".join(pieces)


def host_matches_name(host: str, company_name: str) -> bool:
    """True when the domain stem plausibly spells the company name.

    Compared on the *registrable* stem only: `acme.de` and `careers.acme.de`
    are the same company, and a subdomain label must never be what makes a
    match, or `acme.jobboard.com` would corroborate as Acme's own site.
    """
    stem = registrable(host).split(".")[0]
    if not stem:
        return False
    stem = _fold(stem).replace("-", "")
    tokens = _name_tokens(company_name)
    if not tokens:
        return False
    slug = "".join(tokens)
    if not slug:
        return False
    if stem == slug or stem.startswith(slug) or slug.startswith(stem):
        return True
    # "Acme Software" vs acme.de: the first identity-bearing token carries it,
    # but only when it is long enough not to collide by accident.
    first = tokens[0]
    if len(first) >= 4 and (stem == first or stem.startswith(first)):
        return True
    # An acronym site (bmw.de for "Bayerische Motoren Werke") is common enough
    # in DACH to be worth one cheap check.
    initials = "".join(token[0] for token in tokens)
    return len(initials) >= 3 and stem == initials


def body_mentions_company(body: str, company_name: str) -> bool:
    """True when the page itself claims to be this company.

    Checks the title and og:site_name first — those are assertions of identity.
    A mention anywhere in the body is accepted too, because plenty of employer
    sites put the legal name only in the footer, but it must be the *joined*
    name, not one common token.
    """
    tokens = _name_tokens(company_name)
    if not tokens:
        return False
    slug = "".join(tokens)
    if len(slug) < 3:
        return False
    identity = _NON_ALNUM.sub("", _fold(_page_identity(body)))
    if slug in identity:
        return True
    if len(tokens) > 1 and len(tokens[0]) >= 4 and tokens[0] in identity:
        return True
    whole = _NON_ALNUM.sub("", _fold(body or ""))
    return slug in whole


@dataclass(frozen=True)
class SiteResolution:
    """What we learned about a company's own site."""

    #: The host that answered, e.g. `careers.acme.com`. Crawl this.
    host: str = ""
    #: The registrable domain, e.g. `acme.com`. Mail lives here.
    domain: str = ""
    #: `https://<host>/`.
    website: str = ""
    #: The site asserted this company's identity, or the domain stem spells it.
    corroborated: bool = False
    #: The host is real but refused us. The company stays in the funnel; the
    #: transport ladder is the thing that has to get through, not the seed.
    blocked: bool = False
    #: Where the host came from — a stated `company_website` field carries more
    #: authority than a guessed domain and is accepted on weaker evidence.
    stated: bool = False

    def __bool__(self) -> bool:
        return bool(self.domain)


def _probe(http, url: str) -> tuple[int, str] | None:
    """(status, body) for a URL, or None if nothing answered at all.

    Prefers `http.fetch()`, which reports *why* a fetch failed. Falls back to
    `http.get()` for the stub clients in the test-suite and for any caller
    passing something simpler than HttpClient.
    """
    fetch = getattr(http, "fetch", None)
    if callable(fetch):
        try:
            result = fetch(url)
        except Exception as exc:
            log.debug("domain: fetch %s failed: %s", url, exc)
            return None
        status = getattr(result, "status", 0) or 0
        outcome = getattr(getattr(result, "outcome", None), "value", "")
        if not status and outcome in ("error", ""):
            return None
        return status, getattr(result, "text", "") or ""
    try:
        got = http.get(url)
    except Exception as exc:
        log.debug("domain: get %s failed: %s", url, exc)
        return None
    if got is None:
        return None
    return 200, got[1] if isinstance(got, tuple) and len(got) > 1 else ""


def resolve_site(
    company_name: str,
    hints: Sequence[str],
    http,
    *,
    stated: Sequence[str] = (),
) -> SiteResolution:
    """Resolve a company to its own site, or an empty SiteResolution.

    `hints` are URLs seen alongside the company — the ad's apply link, a website
    field, a logo link — tried in order. `stated` names the subset the source
    *asserted* is the employer's website (a seed API's `company.website`); those
    are accepted on a name match alone, because the source already did the
    attribution work and re-deriving it from a bot-walled homepage loses
    companies for no gain.

    No search-engine querying: it is rate-limited, unreliable, and against most
    engines' terms.
    """
    stated_hosts = {_host(url) for url in stated if url}
    stated_hosts.discard("")
    tried: set[str] = set()
    # A host that answered but could not be corroborated is not thrown away
    # outright — it is the fallback if nothing better turns up, since a wrong
    # domain still beats dropping the company when the name simply is not on
    # the page (a holding company trading under a brand, say).
    fallback: SiteResolution | None = None

    for hint in hints:
        if not hint or not is_company_site(hint):
            continue
        host = _host(hint)
        if not host or "." not in host or host in tried:
            continue
        tried.add(host)
        is_stated = host in stated_hosts
        name_match = host_matches_name(host, company_name)

        answered: tuple[int, str] | None = None
        for scheme in ("https", "http"):
            probe = f"{scheme}://{host}/"
            # A listing's apply_url is attacker-influenced; it must not be able
            # to point the crawler at the metadata service or an internal host.
            if not guard(probe):
                break
            answered = _probe(http, probe)
            if answered is not None:
                break

        if answered is None:
            # Nothing answered on either scheme. If the source stated this is
            # the employer's site and the name matches, keep it anyway — DNS
            # exists, the homepage may simply be behind something our probe
            # cannot reach, and the cascade gets another go at it.
            if is_stated and name_match:
                log.debug("domain: %s did not answer but was stated and matches the name", host)
                return _resolution(host, corroborated=True, blocked=True, stated=True)
            continue

        status, body = answered
        blocked = status in (401, 403, 405, 406, 429) or status >= 500
        if status in (404, 410) and not is_stated:
            # The apex genuinely has no page. That is a real signal the host is
            # not a live company site, unlike a wall.
            log.debug("domain: %s answered %d on the apex", host, status)
            continue
        if _looks_parked(body):
            log.debug("domain: %s looks parked, rejecting", host)
            continue

        corroborated = name_match or is_stated or body_mentions_company(body, company_name)
        resolution = _resolution(host, corroborated=corroborated, blocked=blocked, stated=is_stated)
        if corroborated:
            return resolution
        if fallback is None:
            fallback = resolution
        log.debug(
            "domain: %s answered %d but does not corroborate %r — holding as fallback",
            host,
            status,
            company_name,
        )

    if fallback is not None:
        return fallback
    return SiteResolution()


def _resolution(host: str, *, corroborated: bool, blocked: bool, stated: bool) -> SiteResolution:
    return SiteResolution(
        host=host,
        domain=mail_domain_for(host),
        website=f"https://{host}/",
        corroborated=corroborated,
        blocked=blocked,
        stated=stated,
    )


def resolve_domain(
    company_name: str,
    hints: Sequence[str],
    http,
    *,
    stated: Sequence[str] = (),
) -> str:
    """The registrable domain of the company's own site, or "".

    This is the *mail* domain. Callers that need the host to crawl should use
    `resolve_site` and read `.host`: returning `careers.sap.com` here made every
    address for that employer fail the MX gate.
    """
    return resolve_site(company_name, hints, http, stated=stated).domain
