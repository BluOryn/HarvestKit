"""Score a lead on how useful it will be to whoever works the list.

Email quality dominates because a lead you cannot contact is not a lead;
role-family match comes next because the brief asked for HR and CTOs
specifically; everything else is incremental context.
"""

from __future__ import annotations

from ..models import Lead

EMAIL_STATUS_WEIGHTS: dict[str, float] = {
    "published": 40.0,
    "verified": 36.0,
    "inferred_high": 26.0,
    "inferred_medium": 16.0,
    # No anchor on the domain — the modal format applied blind. Ranks below
    # every evidenced status so these fill the tail of a quota, never the head.
    "inferred_low": 8.0,
    "unknown": 10.0,
    "catch_all": 4.0,
}

ROLE_FAMILY_WEIGHTS: dict[str, float] = {
    "hr": 20.0,
    "tech_leadership": 20.0,
    "other": 2.0,
}

# Everything else contributes a flat amount when populated.
FIELD_WEIGHTS: dict[str, float] = {
    "person_name": 10.0,
    "person_role": 6.0,
    "company_name": 5.0,
    "company_domain": 4.0,
    "company_country": 4.0,
    "person_linkedin": 3.0,
    "person_phone": 3.0,
    "company_city": 2.0,
    "company_region": 1.0,
    "tech_stack": 2.0,
    "seniority": 1.0,
    "company_size_hint": 1.0,
    "company_industry": 1.0,
    "source_person_url": 1.0,
}


def score_lead(lead: Lead) -> float:
    total = EMAIL_STATUS_WEIGHTS.get(lead.email_status, 0.0)
    total += ROLE_FAMILY_WEIGHTS.get(lead.person_role_family, 0.0)
    for name, weight in FIELD_WEIGHTS.items():
        if str(getattr(lead, name, "") or "").strip():
            total += weight
    # Evidence is cheap to carry and makes a row defensible; reward it lightly so
    # a traceable lead beats an identical untraceable one.
    total += min(len(lead.evidence), 5) * 0.5
    return total
