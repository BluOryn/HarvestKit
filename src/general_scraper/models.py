"""General-record schema — businesses, places, listings.

Aligned with the Schema.org LocalBusiness / Restaurant / Place vocabulary so JSON-LD
extraction maps cleanly. Same dataclass shape on local + extension.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List


GENERAL_FIELDS: List[str] = [
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
    "social_links",      # comma-joined list
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
    "source_url",        # detail page URL
    "source_listing_url",  # listing page where we found it
    "source_domain",
    "raw_jsonld",
    "scraped_at",
]

EXTRA_FIELDS: List[str] = ["id", "source", "saved_at"]

GENERAL_CSV_COLUMNS: List[str] = ["id"] + GENERAL_FIELDS + ["source", "saved_at"]


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
        parts = [
            (self.source_url or "").lower(),
            (self.name or "").lower(),
            (self.address or "").lower(),
            (self.phone or "").lower(),
        ]
        joined = " | ".join(parts)
        normalized = " ".join(joined.split()).strip()
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, str]:
        out: Dict[str, str] = {"id": self.fingerprint()}
        for f in GENERAL_FIELDS:
            v = getattr(self, f, "")
            out[f] = v if isinstance(v, str) else str(v)
        out["source"] = self.source
        out["saved_at"] = self.saved_at
        return out

    def to_row(self) -> List[str]:
        d = self.to_dict()
        return [d.get(c, "") for c in GENERAL_CSV_COLUMNS]

    def merge(self, other: "GeneralRecord") -> None:
        for f in GENERAL_FIELDS:
            cur = getattr(self, f, "") or ""
            new = getattr(other, f, "") or ""
            if not new:
                continue
            if not cur or len(new) > len(cur):
                setattr(self, f, new)
