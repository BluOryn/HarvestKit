"""Lead schema — one row is one person, not one company.

A company that yields both an HR contact and a CTO produces two Leads that
share the company fields. Identity therefore keys on the person, and the email
dominates when we have one because it is the only globally unique handle a
person has in this dataset.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields

from job_scraper.models import _stringify

# Best first. The quota scorer and the CSV consumer both rely on this order.
EMAIL_STATUS_ORDER: list[str] = [
    "published",
    "verified",
    "inferred_high",
    "inferred_medium",
    "unknown",
    "catch_all",
]

LEAD_FIELDS: list[str] = [
    "person_name",
    "person_first_name",
    "person_last_name",
    "person_role",
    "person_role_family",
    "person_email",
    "email_status",
    "email_confidence",
    "person_linkedin",
    "person_phone",
    "company_name",
    "company_domain",
    "company_website",
    "company_country",
    "company_region",
    "company_city",
    "company_size_hint",
    "company_industry",
    "tech_stack",
    "seniority",
    "source_seed_url",
    "source_person_url",
    "source_email_url",
    "scraped_at",
]

LEAD_CSV_COLUMNS: list[str] = ["id"] + LEAD_FIELDS + ["evidence_json"]

_WS_RX = re.compile(r"\s+")


@dataclass
class Lead:
    person_name: str = ""
    person_first_name: str = ""
    person_last_name: str = ""
    person_role: str = ""
    person_role_family: str = ""
    person_email: str = ""
    email_status: str = ""
    email_confidence: str = ""
    person_linkedin: str = ""
    person_phone: str = ""
    company_name: str = ""
    company_domain: str = ""
    company_website: str = ""
    company_country: str = ""
    company_region: str = ""
    company_city: str = ""
    company_size_hint: str = ""
    company_industry: str = ""
    tech_stack: str = ""
    seniority: str = ""
    source_seed_url: str = ""
    source_person_url: str = ""
    source_email_url: str = ""
    scraped_at: str = ""
    evidence: dict[str, str] = field(default_factory=dict)

    def set_evidence(self, field_name: str, url: str) -> None:
        """Record where a field came from. Silently ignores empty URLs."""
        if url:
            self.evidence[field_name] = url

    def fingerprint(self) -> str:
        email = (self.person_email or "").strip().lower()
        if email:
            basis = email
        else:
            name = _WS_RX.sub(" ", (self.person_name or "").strip().lower())
            basis = f"{name}@@{(self.company_domain or '').strip().lower()}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        row = {name: _stringify(getattr(self, name)) for name in LEAD_FIELDS}
        row["id"] = self.fingerprint()
        row["evidence_json"] = json.dumps(self.evidence, ensure_ascii=False) if self.evidence else ""
        return row

    def to_row(self) -> list[str]:
        row = self.to_dict()
        return [row[column] for column in LEAD_CSV_COLUMNS]


# Guard against a field being added to the dataclass but not to LEAD_FIELDS.
_DATACLASS_FIELDS = {f.name for f in fields(Lead)} - {"evidence"}
_DECLARED_FIELDS = set(LEAD_FIELDS)
assert (
    _DATACLASS_FIELDS == _DECLARED_FIELDS
), f"LEAD_FIELDS out of sync with the dataclass: {_DATACLASS_FIELDS ^ _DECLARED_FIELDS}"
