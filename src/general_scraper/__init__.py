"""General-purpose web scraper — businesses, listings, products, anything that's
not a job posting.

Mirrors the architecture of `job_scraper`:
  - HttpClient (shared)
  - Playwright fallback
  - Per-host throttle + cache + retries
  - Pagination + listing extraction
  - Detail-page deep-scrape
  - Pluggable site adapters (yelp, etc.)
  - Schema.org-aware extraction (LocalBusiness / Restaurant / Product / Place)

Driven by the same config.yaml, with an additional `mode: general` and `selectors:`
block per target so users can scrape any structured listing page declaratively.
"""
from .models import GENERAL_CSV_COLUMNS, GENERAL_FIELDS, GeneralRecord
from .extract import extract_record_from_page

__all__ = ["GENERAL_CSV_COLUMNS", "GENERAL_FIELDS", "GeneralRecord", "extract_record_from_page"]
