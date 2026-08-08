"""General-record schema — businesses, places, listings.

Aligned with the Schema.org LocalBusiness / Restaurant / Place vocabulary so JSON-LD
extraction maps cleanly. Same dataclass shape on local + extension.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from job_scraper.models import _stringify, canonicalize_url

GENERAL_FIELDS: list[str] = [
    "name",
    "category",
    "subcategories",
    "description",
    "address",
    "street_address",
    "city",
    "region",
    "country",
    "postal_code",
    "latitude",
    "longitude",
    "phone",
    "email",
    "website",
    "social_links",  # comma-joined list
    "rating",
    "review_count",
    "price_range",
    "hours",
    "image",
    "tags",
    "amenities",
    "menu_url",
    "reservation_url",
    "is_claimed",
    "external_id",
    "source_url",  # detail page URL
    "source_listing_url",  # listing page where we found it
    "source_domain",
    "raw_jsonld",
    "scraped_at",
]

GENERAL_CSV_COLUMNS: list[str] = ["id"] + GENERAL_FIELDS + ["source", "saved_at"]

# See JobListing._FIRST_WINS_FIELDS — same reasoning: "longer" is meaningless
# for numbers, coordinates, identifiers and URLs.
_FIRST_WINS_FIELDS: frozenset = frozenset(
    {
        "latitude",
        "longitude",
        "rating",
        "review_count",
        "postal_code",
        "external_id",
        "source_url",
        "source_listing_url",
        "source_domain",
        "scraped_at",
        "phone",
        "email",
        "website",
        "menu_url",
        "reservation_url",
    }
)


@dataclass
class GeneralRecord:
    name: str = ""
    category: str = ""
    subcategories: str = ""
    description: str = ""
    address: str = ""
    street_address: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    postal_code: str = ""
    latitude: str = ""
    longitude: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    social_links: str = ""
    rating: str = ""
    review_count: str = ""
    price_range: str = ""
    hours: str = ""
    image: str = ""
    tags: str = ""
    amenities: str = ""
    menu_url: str = ""
    reservation_url: str = ""
    is_claimed: str = ""
    external_id: str = ""
    source_url: str = ""
    source_listing_url: str = ""
    source_domain: str = ""
    raw_jsonld: str = ""
    scraped_at: str = ""

    source: str = ""
    saved_at: str = ""

    def fingerprint(self) -> str:
        """Stable dedupe key — must match `fingerprintRecord()` in
        extension/app/src/lib/generalSchema.ts.
        """
        parts = [
            canonicalize_url(self.source_url).lower(),
            (self.name or "").lower(),
            (self.address or "").lower(),
            (self.phone or "").lower(),
        ]
        joined = " | ".join(parts)
        normalized = re.sub(r"\s+", " ", joined).strip()
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        out: dict[str, str] = {"id": self.fingerprint()}
        for f in GENERAL_FIELDS:
            v = getattr(self, f, "")
            out[f] = v if isinstance(v, str) else _stringify(v)
        out["source"] = self.source
        out["saved_at"] = self.saved_at
        return out

    def to_row(self) -> list[str]:
        d = self.to_dict()
        return [d.get(c, "") for c in GENERAL_CSV_COLUMNS]

    def merge(self, other: GeneralRecord) -> None:
        """Fold a detail-page record into a listing card.

        Free-text fields take the longer value; identifiers, coordinates, ratings
        and URLs keep the first non-empty one — "4.5" is not worse than "4.5000".
        """
        for f in GENERAL_FIELDS:
            cur = _stringify(getattr(self, f, ""))
            new = _stringify(getattr(other, f, ""))
            if not new:
                continue
            if not cur or f not in _FIRST_WINS_FIELDS and len(new) > len(cur):
                setattr(self, f, new)
