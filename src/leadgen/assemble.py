"""Turn one company and its extracted people into Lead rows.

This is where the pattern anchor pays off: any person whose address was
published on the site anchors the domain's format, and every other named person
on that domain inherits a derived address with an honest confidence label
rather than being dropped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .email.pattern import apply_pattern, infer_pattern
from .email.validate import is_role_account, validate
from .models import Lead
from .person.hit import PersonHit
from .person.roles import classify_role, split_name

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
    published = bool(hit.email) and not is_role_account(hit.email)
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
    if verdict.status == "ok":
        # RCPT confirmed the mailbox exists, which is stronger evidence than
        # the pattern that produced it.
        return candidate, "verified", evidence_url
    if verdict.status == "catch_all":
        return candidate, "catch_all", evidence_url
    if guessed:
        return candidate, "inferred_low", evidence_url
    status = "inferred_high" if pattern_confidence == "high" else "inferred_medium"
    return candidate, status, evidence_url


def build_leads(
    company: CompanyContext,
    hits: list[PersonHit],
    *,
    smtp: bool = True,
    guess_without_anchor: bool = True,
) -> list[Lead]:
    anchors: list[tuple[str, str]] = list(company.extra_anchors)
    for hit in hits:
        if hit.name and hit.email and not is_role_account(hit.email):
            anchors.append((hit.name, hit.email))

    pattern, pattern_confidence = infer_pattern(anchors)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    leads: list[Lead] = []
    for hit in hits:
        if not hit.name:
            continue
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
