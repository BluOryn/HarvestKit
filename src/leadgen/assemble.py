"""Turn one company and its extracted people into Lead rows.

This is where the pattern anchor pays off: any person whose address was
published on the site anchors the domain's format, and every other named person
on that domain inherits a derived address with an honest confidence label
rather than being dropped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace

from .email.pattern import _slug, apply_pattern, infer_pattern
from .email.validate import is_role_account, validate
from .models import Lead
from .person.hit import PersonHit
from .person.jobad import _email_belongs_to
from .person.name import looks_like_person_name, split_people, strip_leading_title
from .person.roles import classify_role, split_name

log = logging.getLogger(__name__)

# When a domain publishes no address at all there is nothing to infer from, but
# "first.last@" is the dominant corporate format by a wide margin. Applying it
# blind is a guess, not evidence, so it is emitted as inferred_low and scored
# below every evidenced status — it fills the tail of a quota, never the head.
FALLBACK_PATTERN = "first.last"


@dataclass
class CompanyContext:
    name: str = ""
    domain: str = ""
    website: str = ""
    country: str = ""
    region: str = ""
    city: str = ""
    size_hint: str = ""
    industry: str = ""
    tech_stack: str = ""
    seed_url: str = ""
    # (name, email) pairs seen outside the site crawl — typically the recruiter
    # named in a job ad. These are the highest-value anchors we get.
    extra_anchors: list[tuple[str, str]] = field(default_factory=list)
    # People named in the company's own job ads. Kept apart from the site crawl
    # because they are found before it and survive it finding nothing.
    ad_contacts: list[PersonHit] = field(default_factory=list)


def _resolve_email(
    hit: PersonHit,
    company: CompanyContext,
    first: str,
    last: str,
    pattern: str,
    pattern_confidence: str,
    smtp: bool,
    guess_without_anchor: bool,
) -> tuple[str, str, str]:
    """Return (email, status, evidence_url). Empty email means none was usable."""
    # An address found on the same page as a name is only *theirs* if it reads
    # like it. The site strategies attribute a page-level mailbox to whoever
    # they found there, which turned "informatik.admin@" into the HR manager's
    # personal address — a shared inbox is not a person, and `is_role_account`
    # only knows the common stems. When it does not echo the name, the address
    # is dropped and the domain's own format is applied instead.
    published = bool(hit.email) and not is_role_account(hit.email) and _address_echoes(hit.email, hit.name)
    guessed = False
    if published:
        candidate, evidence_url = hit.email.strip().lower(), hit.source_url
    elif pattern and company.domain:
        candidate = apply_pattern(pattern, first, last, company.domain)
        evidence_url = f"inferred:{pattern}"
    elif guess_without_anchor and company.domain:
        guessed = True
        candidate = apply_pattern(FALLBACK_PATTERN, first, last, company.domain)
        evidence_url = f"guessed:{FALLBACK_PATTERN}"
    else:
        return "", "", ""

    if not candidate:
        return "", "", ""

    verdict = validate(candidate, smtp=smtp)
    if verdict.status == "rejected":
        return "", "", ""
    if published:
        # An address printed on the company's own page is the strongest
        # provenance available; a probe cannot improve on it and a refused
        # probe must not demote it.
        return candidate, "published", evidence_url
    if verdict.status == "ok" and not guessed:
        # NOTE: "ok" means the domain rejected a random probe address, i.e. it
        # does per-mailbox validation — NOT that this specific mailbox was
        # confirmed. The address is still derived; what is verified is that the
        # domain would bounce a wrong guess rather than silently accept it.
        return candidate, "verified", evidence_url
    if verdict.status == "catch_all":
        return candidate, "catch_all", evidence_url
    if guessed:
        # No anchor at all: the local part is the modal `first.last` applied
        # blind. The SMTP probe said something about the *domain*, never about
        # this address, so it cannot lift a blind guess above the floor. The
        # old ordering tested the probe first and stamped these "verified",
        # which is the single most misleading value the column can carry —
        # a buyer filtering to `verified` would have got pure guesses.
        return candidate, "inferred_low", evidence_url
    status = "inferred_high" if pattern_confidence == "high" else "inferred_medium"
    return candidate, status, evidence_url


def _address_echoes(email: str, name: str) -> bool:
    """Does the local part carry any of this person's name?

    Initial-only forms count: "rp@firma.ch" and "a@firma.ch" are ordinary
    personal addresses at small Swiss firms. A shared mailbox is a *word* —
    info@, kontakt@, informatik.admin@ — so it never looks like this.
    """
    if _email_belongs_to(email, name):
        return True
    local = _slug(email.split("@", 1)[0])
    if not local:
        return False
    parts = [_slug(token) for token in name.split() if len(token.strip(".")) > 1]
    initials = [part[:1] for part in parts if part]
    if not initials:
        return False
    return local in {"".join(initials), "".join(reversed(initials)), initials[0], initials[-1]}


def _clean_hits(hits: list[PersonHit]) -> list[PersonHit]:
    """Drop what is not a person, and split lines that name several.

    Every strategy reads human-authored HTML, where a name and a menu item are
    both short runs of Title Case words. Left alone, "Account Manager" becomes
    account.manager@company.com: a fabricated address that bounces and costs
    sender reputation. Doing this once here means no strategy can forget it.
    """
    cleaned: list[PersonHit] = []
    for hit in hits:
        for part in split_people(hit.name):
            # A title glued to the front of a real name ("Product Owner Line
            # Benzin") is recoverable; a bare title is not.
            candidate = part if looks_like_person_name(part) else strip_leading_title(part)
            if not looks_like_person_name(candidate):
                log.debug("assemble: %r is not a person name, dropping", part)
                continue
            cleaned.append(replace(hit, name=candidate))
    return cleaned


def build_leads(
    company: CompanyContext,
    hits: list[PersonHit],
    *,
    smtp: bool = True,
    guess_without_anchor: bool = True,
) -> list[Lead]:
    # Cleaning happens once, here, and everything downstream uses the result.
    # Anchors used to be drawn from the *raw* hits, which meant an Impressum
    # line naming two people ("Dana Aleff, Erik Mueller") voted on the domain's
    # address format as a single mangled name, and a page-level mailbox voted
    # as though it belonged to whoever happened to be on the page.
    clean = _clean_hits(hits)

    anchors: list[tuple[str, str]] = list(company.extra_anchors)
    for hit in clean:
        # The same three conditions `_resolve_email` uses to call an address
        # *published*. `infer_pattern` requires a format consistent with every
        # example and returns nothing on any disagreement, so one unattributed
        # address destroys inference for the whole domain — and every real
        # person there drops from inferred_high to a fallback guess.
        if not (hit.name and hit.email):
            continue
        if is_role_account(hit.email) or not _address_echoes(hit.email, hit.name):
            log.debug(
                "assemble: %r does not attribute to %r — not voting on the domain pattern",
                hit.email,
                hit.name,
            )
            continue
        anchors.append((hit.name, hit.email))

    pattern, pattern_confidence = infer_pattern(anchors)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    leads: list[Lead] = []
    for hit in clean:
        first, last = split_name(hit.name)
        lead = Lead(
            person_name=hit.name,
            person_first_name=first,
            person_last_name=last,
            person_role=hit.role,
            person_role_family=classify_role(hit.role),
            person_linkedin=hit.linkedin,
            person_phone=hit.phone,
            company_name=company.name,
            company_domain=company.domain,
            company_website=company.website,
            company_country=company.country,
            company_region=company.region,
            company_city=company.city,
            company_size_hint=company.size_hint,
            company_industry=company.industry,
            tech_stack=company.tech_stack,
            source_seed_url=company.seed_url,
            source_person_url=hit.source_url,
            scraped_at=now,
        )
        lead.set_evidence("person_name", hit.source_url)
        if hit.role:
            lead.set_evidence("person_role", hit.source_url)

        email, status, evidence_url = _resolve_email(
            hit, company, first, last, pattern, pattern_confidence, smtp, guess_without_anchor
        )
        lead.person_email = email
        lead.email_status = status
        if status == "inferred_low":
            lead.email_confidence = "low"
        elif status.startswith("inferred"):
            lead.email_confidence = pattern_confidence
        if email:
            lead.source_email_url = evidence_url
            lead.set_evidence("person_email", evidence_url)
        leads.append(lead)

    return leads
