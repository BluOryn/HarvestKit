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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urlparse

from .extract import extract_job_from_page
from .http import HostThrottle, HttpClient
from .models import JobListing
from .universal import mine_contacts, universal_extract

# Playwright's sync API is pinned to the greenlet that created it, and a lock
# does not fix that: a second thread calling in raises `greenlet.error` whether
# or not it holds one. Every call from this pool used to raise, and the raise
# was caught at DEBUG, so `use_playwright: true` looked enabled while fetching
# nothing. PlaywrightFetcher now runs the driver on its own owner thread
# (job_scraper.browser.BrowserWorker), so calls from here are genuinely safe.
# The semaphore that remains is a *cost* control, not a correctness one: two
# browser contexts at a time, not one per worker.
_PLAYWRIGHT_SLOTS = threading.Semaphore(2)


def _playwright_get(fetcher: object, url: str) -> tuple[str, str] | None:
    """Fetch through the browser, bounded to a couple of contexts at a time."""
    if fetcher is None:
        return None
    with _PLAYWRIGHT_SLOTS:
        return fetcher.get(url)  # type: ignore[attr-defined]


def _country_hint(url: str) -> str | None:
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


# Hosts whose critical fields (contact block, description) only exist after
# client-side JS runs. When Playwright is available we re-render these even if
# the plain HTTP fetch succeeded. Override via DeepScrapeConfig.playwright_hosts.
DEFAULT_PLAYWRIGHT_HOSTS: tuple[str, ...] = (
    "karrierestart.no",
    "candidate.webcruiter.com",
    "jobbnorge.no",
)


@dataclass
class DeepScrapeConfig:
    concurrency: int = 6
    per_host_concurrency: int = 1
    per_host_delay_seconds: float = 1.5
    max_retries: int = 2
    use_playwright_fallback: bool = False
    playwright_after_failures: int = 1  # retry with playwright after N normal failures
    playwright_hosts: tuple[str, ...] = DEFAULT_PLAYWRIGHT_HOSTS
    progress_every: int = 25
    # LLM-fallback (Anthropic Haiku) — auto-learn selectors for unknown hosts
    llm_fallback_enabled: bool = False
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_min_fields: int = 5
    llm_max_html_chars: int = 60000
    llm_cache_path: str = ".cache/llm_selectors.sqlite"
    llm_monthly_budget_usd: float = 5.0


def deep_scrape_jobs(
    listings: list[JobListing],
    http: HttpClient,
    config: DeepScrapeConfig | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
    playwright_fetcher: object | None = None,
) -> list[JobListing]:
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

    throttle = HostThrottle(cfg.per_host_concurrency, cfg.per_host_delay_seconds)
    done = ok = failed = 0
    progress_lock = threading.Lock()
    total = len(listings)

    def _scrape_one(listing: JobListing) -> tuple[bool, str]:
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
                    hint = _country_hint(final_url)
                    got_any = False

                    # Pass 0: For sites that hide critical fields behind client-side
                    # JS (e.g. karrierestart contact block), re-fetch with Playwright
                    # and use its HTML for the rest of the pipeline.
                    final_host = urlparse(final_url).netloc.lower()
                    needs_js = (
                        cfg.use_playwright_fallback
                        and playwright_fetcher is not None
                        and any(h in final_host for h in cfg.playwright_hosts)
                    )
                    if needs_js:
                        try:
                            pw_result = _playwright_get(playwright_fetcher, final_url)
                            if pw_result is not None:
                                _, pw_html = pw_result
                                if pw_html and len(pw_html) > len(html) * 0.7:
                                    html = pw_html
                        except Exception as exc:
                            logging.debug("Playwright re-fetch failed for %s: %s", final_url, exc)

                    # Pass 1: JSON-LD / microdata extractor
                    extracted = extract_job_from_page(html, final_url)
                    if extracted and (extracted.title or extracted.description):
                        listing.merge(extracted)
                        got_any = True

                    # Pass 2: universal smart-DOM extractor — always run, even when
                    # JSON-LD found a title. JSON-LD may give title but skip
                    # company/location/employment_type that live in DOM
                    # (e.g. karrierestart .fact-card pattern).
                    uni = universal_extract(html, final_url, country_hint=hint)
                    if uni and (uni.title or uni.description):
                        listing.merge(uni)
                        got_any = True

                    # Pass 3: contact miner (recruiter name/phone/email)
                    contacts = mine_contacts(html, country_hint=hint)
                    for k, v in contacts.items():
                        if k == "contact_section_text":
                            listing.set_extra("contact_section", v)
                            continue
                        if v and not getattr(listing, k, ""):
                            setattr(listing, k, v)

                    if got_any:
                        # Pass 4: LLM-fallback for fields heuristics missed
                        if cfg.llm_fallback_enabled:
                            try:
                                from .llm_adapter import llm_enrich

                                llm_enrich(
                                    html,
                                    final_url,
                                    listing,
                                    api_key=cfg.llm_api_key,
                                    model=cfg.llm_model,
                                    min_fields=cfg.llm_min_fields,
                                    max_html_chars=cfg.llm_max_html_chars,
                                    cache_path=cfg.llm_cache_path,
                                    monthly_budget_usd=cfg.llm_monthly_budget_usd,
                                )
                            except Exception as exc:
                                logging.debug("llm_enrich failed for %s: %s", final_url, exc)
                        return True, "ok-universal"
                    last_err = "no-content"
                else:
                    last_err = "empty-body"
            else:
                last_err = "http-fail"
            # Exponential backoff with full jitter (AWS guidance):
            # waits 1-2s, 2-4s, 4-8s, capped at 20s. Skipped after the final
            # attempt — sleeping there delays the Playwright escalation and the
            # worker slot for up to 16s per listing while changing nothing.
            if attempt < cfg.max_retries:
                base = min(20.0, 2.0 * (2**attempt))
                time.sleep(random.uniform(base / 2, base))

        # Optional: escalate to Playwright for known-JS sites
        if cfg.use_playwright_fallback and playwright_fetcher is not None:
            try:
                pw_result = _playwright_get(playwright_fetcher, url)
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
            try:
                ok_flag, reason = fut.result()
            except Exception as exc:
                # One listing raising is one listing lost, not the run. This
                # used to propagate out of `as_completed`, abandoning every
                # job already scraped in the batch and aborting the whole run
                # on a single malformed page.
                ok_flag, reason = False, f"exception:{type(exc).__name__}: {exc}"
                logging.warning(
                    "deep-scrape: %s raised %s: %s",
                    (futures[fut].job_url or futures[fut].apply_url or "?"),
                    type(exc).__name__,
                    exc,
                )
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
