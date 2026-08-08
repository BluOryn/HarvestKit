"""Job listing schema — kept in lock-step with the browser extension.

The 56-field schema was originally only in the extension (extension/app/src/lib/schema.ts).
This module is the local-Python mirror so CSV output from `python run.py` is column-for-column
compatible with the extension's CSV export.

Adding/removing fields: keep CSV_COLUMNS, JobListing dataclass attrs, and the extension's
JOB_FIELDS array in sync. The fingerprint algorithm must match exactly — see
`fingerprint()` below and `fingerprint()` in extension/app/src/lib/schema.ts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Query params that identify a marketing campaign, not a resource. Stripped before
# fingerprinting so the same posting reached from a newsletter and from search
# collapses to one row. Shared with normalize.canonicalize_url.
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "gbraid",
        "wbraid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref_src",
    }
)


def canonicalize_url(url: str) -> str:
    """Strip fragment + tracking params and normalise the trailing slash.

    Used for both dedupe keys and the `id` column, so the CSV `id` and the
    in-memory dedupe key are always derived from the same string.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()
    query = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS
    ]
    normalized = parsed._replace(fragment="", query=urlencode(query, doseq=True))
    return urlunparse(normalized).rstrip("/")


# Mirror of extension/app/src/lib/schema.ts JOB_FIELDS — order matters (CSV columns).
JOB_FIELDS: list[str] = [
    "title",
    "company",
    "company_logo",
    "company_size",
    "company_industry",
    "company_website",
    "department",
    "team",
    "location",
    "city",
    "region",
    "country",
    "postal_code",
    "remote_type",
    "employment_type",
    "seniority",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "equity",
    "posted_date",
    "valid_through",
    "start_date",
    "language",
    "description",
    "responsibilities",
    "requirements",
    "qualifications",
    "benefits",
    "tech_stack",
    "skills",
    "education_required",
    "experience_years",
    "work_authorization",
    "visa_sponsorship",
    "relocation",
    "travel_required",
    "recruiter_name",
    "recruiter_title",
    "recruiter_email",
    "recruiter_phone",
    "recruiter_linkedin",
    "hiring_manager",
    "hiring_manager_email",
    "application_email",
    "application_phone",
    "apply_url",
    "job_url",
    "external_id",
    "requisition_id",
    "source_ats",
    "source_domain",
    "raw_jsonld",
    "confidence",
    "scraped_at",
]

# Local-only book-keeping columns appended after the schema fields. The extension
# writes the same trailing block (see extension/app/src/lib/export.ts) so a CLI CSV
# and an extension CSV can be concatenated without realigning columns.
EXTRA_COLUMNS: list[str] = ["source", "keywords_matched", "saved_at", "extras_json"]

CSV_COLUMNS: list[str] = ["id"] + JOB_FIELDS + EXTRA_COLUMNS

# Fields where the *first* non-empty value wins on merge. Identifiers, dates,
# URLs and enums are not "more complete" just because they are longer — picking
# the longer string there turns "2026-05-01" into a stray sentence fragment.
_FIRST_WINS_FIELDS: frozenset = frozenset(
    {
        "posted_date",
        "valid_through",
        "start_date",
        "scraped_at",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "external_id",
        "requisition_id",
        "source_ats",
        "source_domain",
        "confidence",
        "apply_url",
        "job_url",
        "company_website",
        "company_logo",
        "recruiter_email",
        "recruiter_phone",
        "recruiter_linkedin",
        "application_email",
        "application_phone",
        "hiring_manager_email",
    }
)


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
    source: str = ""  # config target.name (CLI only)
    keywords_matched: list[str] = field(default_factory=list)
    saved_at: str = ""  # local persistence stamp
    # Dynamic catch-all for site-specific fields not in the core schema.
    # Examples: finnkode, nav_uuid, jobbnorge_position_id, deadline_text,
    # work_languages, contact_persons (list), nav_categories, etc.
    # Serialized as JSON in CSV via the `extras_json` virtual column.
    extras: dict[str, object] = field(default_factory=dict)

    @property
    def remote(self) -> str:
        """Backwards-compat alias for old code that read .remote.

        NOTE: this is a property, not a dataclass field — `JobListing(remote=...)`
        raises TypeError. Adapters must pass `remote_type=` to the constructor.
        """
        return self.remote_type

    @remote.setter
    def remote(self, value: str) -> None:
        if value:
            self.remote_type = value

    @property
    def salary(self) -> str:
        amount = ""
        if self.salary_min and self.salary_max:
            amount = f"{self.salary_min}-{self.salary_max}"
        elif self.salary_min or self.salary_max:
            amount = self.salary_min or self.salary_max
        if not amount:
            return ""
        parts = [amount]
        if self.salary_currency:
            parts.append(self.salary_currency)
        if self.salary_period:
            parts.append(f"/ {self.salary_period}")
        return " ".join(parts)

    def fingerprint(self) -> str:
        """Stable dedupe key. Must stay byte-identical to the extension's
        `fingerprint()` in extension/app/src/lib/schema.ts — the two halves of
        HarvestKit write into the same `id` column.

        Contract: canonicalised apply/job URL + lowercased title/company/location,
        joined with " | ", whitespace-collapsed, SHA-1 hex.
        """
        parts = [
            canonicalize_url(self.apply_url or self.job_url or "").lower(),
            (self.title or "").lower(),
            (self.company or "").lower(),
            (self.location or "").lower(),
        ]
        joined = " | ".join(parts)
        normalized = re.sub(r"\s+", " ", joined).strip()
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        out: dict[str, str] = {"id": self.fingerprint()}
        for f in JOB_FIELDS:
            v = getattr(self, f, "")
            out[f] = v if isinstance(v, str) else _stringify(v)
        out["source"] = self.source
        out["keywords_matched"] = (
            ", ".join(self.keywords_matched)
            if isinstance(self.keywords_matched, list)
            else _stringify(self.keywords_matched)
        )
        out["saved_at"] = self.saved_at
        # Serialize extras as compact JSON (preserves nested structures + future-proofs CSV).
        try:
            out["extras_json"] = (
                json.dumps(self.extras, ensure_ascii=False, default=str) if self.extras else ""
            )
        except (TypeError, ValueError):
            out["extras_json"] = ""
        return out

    def set_extra(self, key: str, value: object) -> None:
        """Stash a site-specific value that doesn't fit the core schema."""
        if value is None or value == "":
            return
        self.extras[key] = value

    def to_row(self) -> list[str]:
        d = self.to_dict()
        return [d.get(c, "") for c in CSV_COLUMNS]

    def merge(self, other: JobListing) -> None:
        """Field-wise merge used to fold a deep-scrape result into a listing stub.

        Free-text fields take the longer value (a full description beats a card
        snippet). Identifier / date / URL / enum fields in `_FIRST_WINS_FIELDS`
        keep whatever was already set, because "longer" is meaningless there and
        actively harmful — it lets a prose sentence overwrite an ISO date.
        """
        for f in JOB_FIELDS:
            cur = _stringify(getattr(self, f, ""))
            new = _stringify(getattr(other, f, ""))
            if not new:
                continue
            if not cur or f not in _FIRST_WINS_FIELDS and len(new) > len(cur):
                setattr(self, f, new)
        if other.source and not self.source:
            self.source = other.source
        if other.keywords_matched and not self.keywords_matched:
            self.keywords_matched = list(other.keywords_matched)
        # Merge extras — new value wins for same key
        if other.extras:
            self.extras.update({k: v for k, v in other.extras.items() if v not in (None, "")})


def _stringify(value: object) -> str:
    """Coerce whatever an adapter handed us into a CSV-safe scalar string.

    Upstream JSON APIs are inconsistent about scalar-vs-list (Arbeitsagentur's
    `arbeitszeitmodelle`, Ashby's `secondaryLocations`), so a field typed `str`
    can legitimately arrive as a list. Joining beats `str(['Vollzeit'])`.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "yes" if value else ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_stringify(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value).strip()
