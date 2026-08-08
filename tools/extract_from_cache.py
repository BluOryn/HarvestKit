"""Salvage a blocked run: re-extract everything already in the HTTP cache.

When a live run gets partially WAF-blocked you still have every page it *did*
fetch sitting in `.cache/http_cache.sqlite`. This re-runs the full extractor
pipeline over those cached pages and writes a canonical CSV — no new requests.

Usage:
  python tools/extract_from_cache.py                          # all cached job pages
  python tools/extract_from_cache.py --url-like '%/detail/%'  # only jobs.ch details
  python tools/extract_from_cache.py --config sites/jobsch --merge-listings
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sqlite3
import sys
import zlib
from datetime import datetime, timezone
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from job_scraper.adapters import get_adapter
from job_scraper.config import load_config, resolve_config_path
from job_scraper.extract import extract_job_from_page
from job_scraper.http import HttpClient
from job_scraper.models import CSV_COLUMNS, JobListing
from job_scraper.universal import universal_extract

# Fields worth reporting fill-rate on after a salvage run.
REPORT_FIELDS = [
    "title",
    "company",
    "company_logo",
    "company_website",
    "company_industry",
    "department",
    "description",
    "responsibilities",
    "requirements",
    "qualifications",
    "benefits",
    "skills",
    "recruiter_name",
    "recruiter_title",
    "recruiter_phone",
    "recruiter_email",
    "hiring_manager",
    "education_required",
    "experience_years",
    "salary_min",
    "salary_max",
    "salary_currency",
    "tech_stack",
    "remote_type",
    "seniority",
    "employment_type",
    "posted_date",
    "language",
    "apply_url",
    "raw_jsonld",
]

MIN_USEFUL_HTML = 5000


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    cache_path = args.cache or os.path.join(ROOT, ".cache", "http_cache.sqlite")
    if not os.path.isfile(cache_path):
        logging.error("No cache at %s — nothing to salvage.", cache_path)
        sys.exit(2)

    by_url: dict[str, JobListing] = {}

    # Optionally re-run the listing adapters (served from cache) so the salvaged
    # detail pages merge into real stubs rather than standing alone.
    if args.merge_listings:
        by_url.update(_listing_stubs(args.config, cache_path))
        logging.info("listing stubs: %d", len(by_url))

    enriched = fresh = skipped = 0
    conn = sqlite3.connect(cache_path)
    try:
        rows = conn.execute("SELECT url, final_url, body FROM http_cache WHERE url LIKE ?", (args.url_like,))
        for url, final_url, body in rows:
            try:
                html = zlib.decompress(body).decode("utf-8", errors="replace")
            except (zlib.error, TypeError):
                skipped += 1
                continue
            if len(html) < MIN_USEFUL_HTML:
                skipped += 1  # truncated / WAF-poisoned entry
                continue

            page_url = final_url or url
            extracted = extract_job_from_page(html, page_url)
            if extracted is None or not (extracted.title or extracted.description):
                # No JSON-LD — fall back to the universal smart-DOM extractor,
                # which the original tool never tried.
                extracted = universal_extract(html, page_url)
            if extracted is None or not (extracted.title or extracted.description):
                skipped += 1
                continue

            match = _match_stub(by_url, url, final_url)
            if match is not None:
                match.merge(extracted)
                enriched += 1
            else:
                host = urlparse(page_url).netloc
                extracted.source = extracted.source or host
                extracted.source_domain = extracted.source_domain or host
                by_url[url] = extracted
                fresh += 1
    finally:
        conn.close()

    logging.info("enriched %d listings, recovered %d fresh, skipped %d unusable", enriched, fresh, skipped)

    stamp = datetime.now(timezone.utc).isoformat()
    unique: list[JobListing] = []
    seen: set[str] = set()
    for listing in by_url.values():
        if not listing.title:
            continue
        listing.scraped_at = listing.scraped_at or stamp
        listing.saved_at = listing.saved_at or stamp
        fingerprint = listing.fingerprint()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(listing)

    out_path = args.output or os.path.join(ROOT, "output", "salvaged.csv")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for listing in unique:
            writer.writerow(listing.to_dict())
    logging.info("wrote %d rows → %s", len(unique), out_path)

    if unique:
        print("\nField-fill on final CSV:")
        for field in REPORT_FIELDS:
            filled = sum(1 for r in unique if getattr(r, field, ""))
            print(f"  {field:22s} {filled:4d}/{len(unique)} ({100 * filled / len(unique):5.1f}%)")


def _listing_stubs(config_name: str, cache_path: str) -> dict[str, JobListing]:
    """Replay a config's listing pages from cache to rebuild the stub set."""
    try:
        config = load_config(resolve_config_path(config_name))
    except (FileNotFoundError, ValueError) as exc:
        logging.warning("could not load %s (%s) — continuing without listing stubs", config_name, exc)
        return {}

    stubs: dict[str, JobListing] = {}
    http = HttpClient(
        user_agent=config.run.user_agent,
        delay_seconds=0.0,
        obey_robots=False,
        cache_enabled=True,
        cache_ttl_seconds=config.run.cache_ttl_seconds,
        cache_path=cache_path,
        rotate_user_agents=False,
    )
    try:
        for target in config.targets:
            try:
                for listing in get_adapter(target).fetch_jobs(target, config.run, http):
                    if listing.job_url:
                        listing.source = listing.source or target.name
                        stubs[listing.job_url] = listing
            except Exception as exc:
                logging.warning("listing replay failed for %s: %s", target.name, exc)
    finally:
        http.close()
    return stubs


def _match_stub(by_url: dict[str, JobListing], url: str, final_url: str | None) -> JobListing | None:
    """Find the stub for a cached page, tolerating trailing-slash differences."""
    for candidate in (url, final_url):
        if candidate and candidate in by_url:
            return by_url[candidate]
    keys = {url.rstrip("/"), (final_url or "").rstrip("/")} - {""}
    for stub_url, stub in by_url.items():
        if stub_url.rstrip("/") in keys:
            return stub
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cache", help="Path to http_cache.sqlite (default .cache/http_cache.sqlite)")
    parser.add_argument("-o", "--output", help="CSV output path (default output/salvaged.csv)")
    parser.add_argument(
        "--url-like",
        default="%",
        help="SQL LIKE filter on the cached URL, e.g. '%%/detail/%%' (default: everything)",
    )
    parser.add_argument(
        "--config",
        default="example",
        help="Config whose listing pages to replay from cache (with --merge-listings)",
    )
    parser.add_argument(
        "--merge-listings",
        action="store_true",
        help="Replay the config's listing adapters from cache and merge details into those stubs",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
