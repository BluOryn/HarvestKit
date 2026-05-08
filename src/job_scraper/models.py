"""Job listing schema — kept in lock-step with the browser extension.

The 60-field schema was originally only in the extension (extension/app/src/lib/schema.ts).
This module is the local-Python mirror so CSV output from `python run.py` is column-for-column
compatible with the extension's CSV export.

Adding/removing fields: keep CSV_COLUMNS, JobListing dataclass attrs, and the extension's
JOB_FIELDS array in sync. The fingerprint algorithm must match exactly.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List


# Mirror of extension/app/src/lib/schema.ts JOB_FIELDS — order matters (CSV columns).
JOB_FIELDS: List[str] = [
    "title", "company", "company_logo", "company_size", "company_industry", "company_website",
    "department", "team", "location", "city", "region", "country", "postal_code",
    "remote_type", "employment_type", "seniority",
    "salary_min", "salary_max", "salary_currency", "salary_period", "equity",
    "posted_date", "valid_through", "start_date", "language",
    "description", "responsibilities", "requirements", "qualifications", "benefits",
    "tech_stack", "skills", "education_required", "experience_years",
    "work_authorization", "visa_sponsorship", "relocation", "travel_required",
    "recruiter_name", "recruiter_title", "recruiter_email", "recruiter_phone", "recruiter_linkedin",
    "hiring_manager", "hiring_manager_email", "application_email", "application_phone",
    "apply_url", "job_url", "external_id", "requisition_id",
    "source_ats", "source_domain", "raw_jsonld",
    "confidence", "scraped_at",
]

# Local-only book-keeping fields appended after the schema fields (parity with extension).
EXTRA_FIELDS: List[str] = ["id", "source", "keywords_matched", "saved_at"]

CSV_COLUMNS: List[str] = ["id"] + JOB_FIELDS + ["source", "keywords_matched", "saved_at"]


@dataclass
class JobListing:
    title: str = ""
    company: str = ""
    company_logo: str = ""
    company_size: str = ""
    company_industry: str = ""
    company_website: str = ""
    department: str = ""
    team: str = ""
    location: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    postal_code: str = ""
    remote_type: str = ""
    employment_type: str = ""
    seniority: str = ""
    salary_min: str = ""
    salary_max: str = ""
    salary_currency: str = ""
    salary_period: str = ""
    equity: str = ""
    posted_date: str = ""
    valid_through: str = ""
    start_date: str = ""
    language: str = ""
    description: str = ""
    responsibilities: str = ""
    requirements: str = ""
    qualifications: str = ""
    benefits: str = ""
    tech_stack: str = ""
    skills: str = ""
    education_required: str = ""
    experience_years: str = ""
    work_authorization: str = ""
    visa_sponsorship: str = ""
    relocation: str = ""
    travel_required: str = ""
    recruiter_name: str = ""
    recruiter_title: str = ""
    recruiter_email: str = ""
    recruiter_phone: str = ""
    recruiter_linkedin: str = ""
    hiring_manager: str = ""
    hiring_manager_email: str = ""
    application_email: str = ""
    application_phone: str = ""
    apply_url: str = ""
    job_url: str = ""
    external_id: str = ""
    requisition_id: str = ""
    source_ats: str = ""
    source_domain: str = ""
    raw_jsonld: str = ""
    confidence: str = ""
    scraped_at: str = ""

    # ---- legacy / extra ----
    source: str = ""                 # config target.name (CLI only)
    keywords_matched: List[str] = field(default_factory=list)
    saved_at: str = ""               # local persistence stamp

    @property
    def remote(self) -> str:
        """Backwards-compat alias for old code that read .remote."""
        return self.remote_type

    @remote.setter
    def remote(self, value: str) -> None:
        if value:
            self.remote_type = value

    @property
    def salary(self) -> str:
        if self.salary_min and self.salary_max:
            return f"{self.salary_min}-{self.salary_max} {self.salary_currency} / {self.salary_period}".strip()
        if self.salary_min:
            return f"{self.salary_min} {self.salary_currency} / {self.salary_period}".strip()
        return ""

    def fingerprint(self) -> str:
        # Match extension/app/src/lib/schema.ts fingerprint() exactly.
        parts = [
            (self.apply_url or self.job_url or "").lower(),
            (self.title or "").lower(),
            (self.company or "").lower(),
            (self.location or "").lower(),
        ]
        joined = " | ".join(parts)
        normalized = " ".join(joined.split()).strip()
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, str]:
        out: Dict[str, str] = {"id": self.fingerprint()}
        for f in JOB_FIELDS:
            v = getattr(self, f, "")
            out[f] = v if isinstance(v, str) else str(v)
        out["source"] = self.source
        out["keywords_matched"] = ", ".join(self.keywords_matched) if isinstance(self.keywords_matched, list) else str(self.keywords_matched)
        out["saved_at"] = self.saved_at
        return out

    def to_row(self) -> List[str]:
        d = self.to_dict()
        return [d.get(c, "") for c in CSV_COLUMNS]

    def merge(self, other: "JobListing") -> None:
        """Field-wise merge: prefer the longer non-empty string. Used to combine listing-page
        cards with deep-scrape results.
        """
        for f in JOB_FIELDS:
            cur = getattr(self, f, "") or ""
            new = getattr(other, f, "") or ""
            if not new:
                continue
            if not cur or len(new) > len(cur):
                setattr(self, f, new)
        if other.source and not self.source:
            self.source = other.source
        if other.keywords_matched and not self.keywords_matched:
            self.keywords_matched = list(other.keywords_matched)
