"""Standalone tool: extract everything from the HTTP cache + write a final CSV.

Use when a live run got partially blocked and you want to salvage the cached pages.
Reads the sqlite cache, runs the full extractor pipeline on every cached detail page,
merges with the API stub from a fresh listing fetch, and writes the canonical CSV.
"""
from __future__ import annotations

import csv
import logging
import os
import sqlite3
import sys
import zlib
from datetime import datetime, timezone
from typing import Dict
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from job_scraper.adapters.jobsch import JobsChAdapter
from job_scraper.config import RunConfig, TargetConfig
from job_scraper.extract import extract_job_from_page
from job_scraper.http import HttpClient
from job_scraper.models import CSV_COLUMNS, JobListing


JOBSCH_URL = (
    "https://www.jobs.ch/en/vacancies/?category=106&category=146&category=156&category=167"
    "&employment-type=1&employment-type=2&employment-type=4&employment-type=5"
    "&publication-date=30&term="
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    out_path = os.path.join(ROOT, "output", "jobsch_full.csv")
    cache_path = os.path.join(ROOT, ".cache", "http_cache.sqlite")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # 1. Pull all listings from API (uses cache — no extra hits if listing pages cached).
    http = HttpClient(
        user_agent="HarvestKitBot/1.0 (+hriday.vig@bluoryn.com)",
        delay_seconds=0.5,
        obey_robots=False,
        cache_enabled=True,
        cache_ttl_seconds=86400,
        cache_path=cache_path,
        rotate_user_agents=True,
    )
    target = TargetConfig(name="jobs.ch", url=JOBSCH_URL, adapter="jobs.ch")
    run_cfg = RunConfig(
        user_agent=http.user_agent,
        delay_seconds=0.5,
        obey_robots=False,
        confirm_permission=True,
        max_pages=100,
    )
    adapter = JobsChAdapter()
    try:
        listings = adapter.fetch_jobs(target, run_cfg, http)
    except Exception as exc:
        logging.warning("API fetch failed (%s) — proceeding without listings", exc)
        listings = []
    logging.info("API listings: %d", len(listings))
    by_url: Dict[str, JobListing] = {l.job_url: l for l in listings if l.job_url}

    # 2. For every cached detail page, extract + merge into stub (or create fresh).
    conn = sqlite3.connect(cache_path)
    enriched_count = 0
    fresh_count = 0
    cur = conn.execute(
        "SELECT url, final_url, body FROM http_cache WHERE url LIKE '%/detail/%'"
    )
    for url, final_url, body in cur:
        try:
            html = zlib.decompress(body).decode("utf-8", errors="replace")
        except Exception:
            continue
        if len(html) < 5000:
            continue  # poisoned
        extracted = extract_job_from_page(html, final_url or url)
        if not extracted:
            continue
        # Match by URL — strip trailing slash for robustness
        key = url.rstrip("/")
        match = None
        for k, l in by_url.items():
            if k.rstrip("/") == key:
                match = l
                break
        if match is not None:
            match.merge(extracted)
            enriched_count += 1
        else:
            extracted.source = "jobs.ch"
            extracted.source_ats = "jobs.ch"
            extracted.source_domain = urlparse(final_url or url).netloc
            by_url[url] = extracted
            fresh_count += 1
    conn.close()

    logging.info("Enriched %d listings, added %d fresh from cache", enriched_count, fresh_count)

    stamp = datetime.now(timezone.utc).isoformat()
    rows = []
    for l in by_url.values():
        if not l.title:
            continue
        l.scraped_at = l.scraped_at or stamp
        l.saved_at = l.saved_at or stamp
        if not l.source:
            l.source = "jobs.ch"
        rows.append(l)

    # Dedupe by fingerprint
    seen = set()
    unique = []
    for r in rows:
        fp = r.fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        unique.append(r)

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in unique:
            writer.writerow(r.to_dict())

    logging.info("wrote %d rows → %s", len(unique), out_path)

    # Field-fill report
    if unique:
        report_fields = [
            "title", "company", "company_logo", "company_website", "company_industry",
            "department", "description",
            "responsibilities", "requirements", "qualifications", "benefits", "skills",
            "recruiter_name", "recruiter_title", "recruiter_phone", "recruiter_email",
            "hiring_manager", "education_required", "experience_years",
            "salary_min", "salary_max", "salary_currency", "tech_stack",
            "remote_type", "seniority", "employment_type", "posted_date",
            "language", "apply_url", "raw_jsonld",
        ]
        print("\nField-fill on final CSV:")
        for f in report_fields:
            n = sum(1 for r in unique if getattr(r, f, ""))
            pct = 100 * n / len(unique)
            print(f"  {f:22s} {n:4d}/{len(unique)} ({pct:5.1f}%)")


if __name__ == "__main__":
    main()
