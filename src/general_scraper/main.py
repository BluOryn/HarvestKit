"""General scraper entrypoint — `python run.py --general`.

Workflow per target:
  1. Render listing page (HTTP or Playwright if `use_playwright: true`).
  2. Extract cards via config selectors / JSON-LD ItemList / heuristics.
  3. Paginate via config-supplied `selector`, a `param`+`step` rewriter, or
     `<link rel=next>`.
  4. Deep-scrape each detail URL → JSON-LD LocalBusiness extraction + heuristics.
  5. Dedupe + export to CSV.

See configs/general.example.yaml for a worked example.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from contextlib import suppress
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

# Reuse the job_scraper HTTP client + Playwright fetcher — same machinery.
from job_scraper.crawl import PlaywrightFetcher
from job_scraper.http import HttpClient

from .config import (
    GeneralAppConfig,
    GeneralTargetConfig,
    PaginationConfig,
    load_general_config,
    resolve_config_path,
)
from .extract import extract_listing_cards, extract_record_from_page
from .models import GENERAL_CSV_COLUMNS, GeneralRecord

DEFAULT_CONFIG = "general.example.yaml"

FetchResult = Optional[tuple[str, str]]


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s: %(message)s",
    )

    try:
        path = resolve_config_path(args.config)
        config = load_general_config(path)
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        sys.exit(2)
    except ValueError as exc:
        logging.error("Invalid config %s: %s", args.config, exc)
        sys.exit(2)

    if not config.run.confirm_permission and not args.confirm_permission:
        logging.error(
            "Confirm permission to scrape via run.confirm_permission: true or --confirm-permission."
        )
        sys.exit(2)

    if not config.targets:
        logging.error("No targets in %s.", path)
        sys.exit(2)

    http = HttpClient(
        user_agent=config.run.user_agent,
        delay_seconds=config.run.delay_seconds,
        obey_robots=config.run.obey_robots,
        cache_enabled=config.run.cache_enabled,
        cache_ttl_seconds=config.run.cache_ttl_seconds,
        cache_path=config.run.cache_path,
        rotate_user_agents=config.run.rotate_user_agents,
        use_impersonation=config.run.use_impersonation,
        use_browser=config.run.use_stealth_browser,
        browser_headless=config.run.stealth_browser_headless,
        browser_concurrency=config.run.stealth_browser_concurrency,
        escalate_on_block=config.run.escalate_on_block,
        transport_memory_path=config.run.transport_memory_path,
        robots_unreadable_is_allowed=config.run.robots_unreadable_is_allowed,
        require_proxy=config.run.require_proxy,
        index_cache_ttl_seconds=config.run.index_cache_ttl_seconds,
    )

    output_path = args.output or config.csv.path
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    all_records: list[GeneralRecord] = []
    seen_ids = set()

    pw: PlaywrightFetcher | None = None
    if config.run.use_playwright:
        try:
            pw = PlaywrightFetcher(http=http).__enter__()
        except Exception as exc:
            logging.warning("Playwright failed to start: %s — continuing without it.", exc)
            pw = None

    try:
        for target in config.targets:
            use_pw = config.run.use_playwright if target.use_playwright is None else target.use_playwright
            fetcher = pw if (use_pw and pw is not None) else None

            max_pages = target.pagination.max_pages or config.run.max_pages
            cards = _collect_cards(target, http, fetcher, max_pages)
            logging.info("%s: found %d cards", target.name, len(cards))

            if config.run.deep_scrape:
                _deep_scrape(cards, http, fetcher)

            stamp = datetime.now(timezone.utc).isoformat()
            for card in cards:
                card.scraped_at = card.scraped_at or stamp
                card.source = target.name
                fingerprint = card.fingerprint()
                if fingerprint in seen_ids:
                    continue
                seen_ids.add(fingerprint)
                all_records.append(card)
    finally:
        if pw is not None:
            with suppress(Exception):
                pw.__exit__(None, None, None)
        http.close()

    saved = datetime.now(timezone.utc).isoformat()
    for record in all_records:
        record.saved_at = record.saved_at or saved

    if config.csv.enabled:
        _write_csv(output_path, all_records)
        logging.info("Wrote %d records to %s", len(all_records), output_path)
    else:
        logging.info("Collected %d records (csv export disabled)", len(all_records))


def _deep_scrape(cards: list[GeneralRecord], http: HttpClient, fetcher: PlaywrightFetcher | None) -> None:
    for card in cards:
        if not card.source_url:
            continue
        fetched = _fetch(card.source_url, http, fetcher)
        if fetched is None:
            continue
        final_url, html = fetched
        detail = extract_record_from_page(html, final_url)
        if detail:
            card.merge(detail)


def _collect_cards(
    target: GeneralTargetConfig,
    http: HttpClient,
    fetcher: PlaywrightFetcher | None,
    max_pages: int,
) -> list[GeneralRecord]:
    cards: list[GeneralRecord] = []
    seen_urls = set()
    seen_pages = set()

    pages_done = 0
    current_url: str | None = target.url
    while current_url and pages_done < max(1, max_pages):
        # A pagination rule that returns a URL we already fetched (a "next" link
        # pointing at the current page) would otherwise loop until max_pages.
        if current_url in seen_pages:
            break
        seen_pages.add(current_url)

        fetched = _fetch(current_url, http, fetcher)
        if fetched is None:
            break
        final_url, html = fetched
        page_cards = extract_listing_cards(html, final_url, selectors=target.selectors or None)
        new = 0
        for card in page_cards:
            key = card.source_url or card.name
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            cards.append(card)
            new += 1
        pages_done += 1
        if new == 0:
            break
        current_url = _next_page_url(html, final_url, target.pagination)
    return cards


def _next_page_url(html: str, base_url: str, pagination: PaginationConfig) -> str | None:
    if pagination.param:
        parsed = urlparse(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        try:
            current = int(query.get(pagination.param, "0") or 0)
        except ValueError:
            current = 0
        query[pagination.param] = str(current + max(1, pagination.step))
        return parsed._replace(query=urlencode(query, doseq=True)).geturl()

    soup = BeautifulSoup(html, "lxml")

    if pagination.selector:
        anchor = soup.select_one(pagination.selector)
        href = anchor.get("href") if anchor else None
        return urljoin(base_url, href) if href else None

    link_next = soup.find("link", rel=lambda v: bool(v) and "next" in (v if isinstance(v, list) else [v]))
    if link_next and link_next.get("href"):
        return urljoin(base_url, link_next["href"])
    a_next = soup.select_one("a[rel='next'], a[aria-label*='next' i]")
    if a_next and a_next.get("href"):
        return urljoin(base_url, a_next["href"])
    return None


def _fetch(url: str, http: HttpClient, fetcher: PlaywrightFetcher | None) -> FetchResult:
    if fetcher is not None:
        result = fetcher.get(url)
        if result is not None:
            return result
    return http.get(url, allow_404=True)


def _write_csv(path: str, records: list[GeneralRecord]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GENERAL_CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_dict())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HarvestKit general-purpose scraper (businesses, listings)")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Config name or path; bare names are looked up under configs/",
    )
    parser.add_argument("-o", "--output", help="CSV output path (overrides exports.csv.path)")
    parser.add_argument("--confirm-permission", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Deprecated — general mode no longer requires `mode: general`",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


# Re-exported so `from general_scraper.main import GeneralAppConfig` keeps working.
__all__ = ["GeneralAppConfig", "main"]


if __name__ == "__main__":
    main()
