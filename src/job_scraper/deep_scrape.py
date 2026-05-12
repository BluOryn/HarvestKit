"""Deep-scrape orchestrator — fetches each job-detail URL, runs the extractor,
merges into the partial JobListing produced by an adapter.

Used by every adapter that yields stub listings from a list/feed (Arbeitsagentur,
Workable, jobs.ch, etc.). Adapters call `deep_scrape_jobs()` after producing
their initial list; this module visits each URL, runs `extract_job_from_page`,
and merges richer fields (description, salary, recruiter, etc.) into the stub.

Design:
  - Per-host token bucket. Default: 1 in-flight request per host, 1.5 s spacing.
  - Bounded global parallelism (ThreadPoolExecutor).
  - Three retry attempts with exponential backoff.
  - Optional Playwright fallback for JS-heavy sites (only for sites the HttpClient
    can't render). The HttpClient itself doesn't render JS — sites that require it
    must set `target.use_playwright: true` or be invoked through PlaywrightFetcher.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .extract import extract_job_from_page
from .http import HttpClient
from .models import JobListing
from .universal import universal_extract, mine_contacts


def _country_hint(url: str) -> Optional[str]:
    """Best-effort country guess from URL/TLD. Drives phone-regex selection."""
    host = urlparse(url).netloc.lower()
    if host.endswith(".no") or "nav.no" in host:
        return "NO"
    if host.endswith(".de") or "arbeitsagentur" in host:
        return "DE"
    if host.endswith(".ch") or "jobs.ch" in host:
        return "CH"
    if host.endswith(".se"):
        return "SE"
    if host.endswith(".dk"):
        return "DK"
    if host.endswith(".fi"):
        return "FI"
    return None


@dataclass
class DeepScrapeConfig:
    concurrency: int = 6
    per_host_concurrency: int = 1
    per_host_delay_seconds: float = 1.5
    max_retries: int = 2
    use_playwright_fallback: bool = False
    playwright_after_failures: int = 1  # retry with playwright after N normal failures
    progress_every: int = 25


class _HostThrottle:
    """Token-bucket-ish throttle: per-host inflight cap + min delay between launches."""

    def __init__(self, max_inflight: int, min_delay: float) -> None:
        self.max_inflight = max(1, max_inflight)
        self.min_delay = max(0.0, min_delay)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._inflight: Dict[str, int] = {}
        self._last: Dict[str, float] = {}

    def acquire(self, host: str) -> None:
        with self._cv:
            while True:
                inflight = self._inflight.get(host, 0)
                last = self._last.get(host, 0.0)
                wait = self.min_delay - (time.time() - last)
                if inflight < self.max_inflight and wait <= 0:
                    self._inflight[host] = inflight + 1
                    self._last[host] = time.time()
                    return
                self._cv.wait(timeout=max(0.05, wait))

    def release(self, host: str) -> None:
        with self._cv:
            self._inflight[host] = max(0, self._inflight.get(host, 0) - 1)
            self._cv.notify_all()


def deep_scrape_jobs(
    listings: List[JobListing],
    http: HttpClient,
    config: Optional[DeepScrapeConfig] = None,
    on_progress: Optional[Callable[[int, int, int], None]] = None,
    playwright_fetcher: Optional[object] = None,
) -> List[JobListing]:
    """Visit each listing's detail URL and merge in richer fields.

    Args:
      listings: stub JobListings produced by an adapter (typically from a feed).
      http: the shared HttpClient (already configured with delay/UA/robots).
      config: throttling + retry knobs.
      on_progress: optional callback (done, ok, failed).
      playwright_fetcher: optional context-managed PlaywrightFetcher for JS-heavy sites.

    Returns the same list, with each JobListing potentially enriched in-place. The list
    is returned so callers can chain.
    """
    cfg = config or DeepScrapeConfig()
    if not listings:
        return listings

    throttle = _HostThrottle(cfg.per_host_concurrency, cfg.per_host_delay_seconds)
    done = ok = failed = 0
    progress_lock = threading.Lock()
    total = len(listings)

    def _scrape_one(listing: JobListing) -> Tuple[bool, str]:
        url = listing.job_url or listing.apply_url
        if not url:
            return False, "no-url"
        host = urlparse(url).netloc or "_"
        last_err = ""
        # Per-host throttle is *also* enforced inside http.get via its own
        # (host,proxy) bucket; this outer one ensures a sane minimum even when
        # proxies are absent.
        for attempt in range(cfg.max_retries + 1):
            throttle.acquire(host)
            try:
                fetched = http.get(url, allow_404=True)
            finally:
                throttle.release(host)
            if fetched is not None:
                final_url, html = fetched
                if html and len(html) > 200:
                    # First pass: JSON-LD / microdata extractor
                    extracted = extract_job_from_page(html, final_url)
                    if extracted and (extracted.title or extracted.description):
                        listing.merge(extracted)
                        # Even with JSON-LD, run the contact miner on the raw
                        # HTML to fill recruiter_email/phone fields that may
                        # only exist outside the JSON-LD blob.
                        hint = _country_hint(final_url)
                        contacts = mine_contacts(html, country_hint=hint)
                        for k, v in contacts.items():
                            if k == "contact_section_text":
                                listing.set_extra("contact_section", v)
                                continue
                            # mine_contacts is more accurate than the JSON-LD path
                            # for recruiter_name (uses Norwegian "Stillingstittel"
                            # separator) — always prefer its value when non-empty.
                            if v:
                                setattr(listing, k, v)
                        return True, "ok"
                    # Second pass: universal smart-DOM extractor (no Schema.org needed)
                    hint = _country_hint(final_url)
                    uni = universal_extract(html, final_url, country_hint=hint)
                    if uni and (uni.title or uni.description):
                        listing.merge(uni)
                        return True, "ok-universal"
                    last_err = "no-content"
                else:
                    last_err = "empty-body"
            else:
                last_err = "http-fail"
            # Exponential backoff with full jitter (AWS guidance):
            # waits 1-2s, 2-4s, 4-8s, 8-16s, capped at 20s.
            base = min(20.0, 2.0 * (2 ** attempt))
            time.sleep(random.uniform(base / 2, base))

        # Optional: escalate to Playwright for known-JS sites
        if cfg.use_playwright_fallback and playwright_fetcher is not None:
            try:
                pw_result = playwright_fetcher.get(url)  # type: ignore[attr-defined]
            except Exception as exc:
                last_err = f"playwright-error:{exc}"
                pw_result = None
            if pw_result is not None:
                final_url, html = pw_result
                extracted = extract_job_from_page(html, final_url)
                if extracted and (extracted.title or extracted.description):
                    listing.merge(extracted)
                    return True, "ok-playwright"
        return False, last_err or "unknown"

    with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
        futures = {pool.submit(_scrape_one, j): j for j in listings}
        for fut in as_completed(futures):
            ok_flag, reason = fut.result()
            with progress_lock:
                done += 1
                if ok_flag:
                    ok += 1
                else:
                    failed += 1
                    listing = futures[fut]
                    logging.debug(
                        "deep-scrape miss: %s — %s",
                        listing.job_url or listing.apply_url,
                        reason,
                    )
                if on_progress is not None and (done % cfg.progress_every == 0 or done == total):
                    on_progress(done, ok, failed)

    logging.info("deep-scrape: %d/%d OK · %d failed", ok, total, failed)
    return listings
