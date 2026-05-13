"""General scraper entrypoint — `python run.py --general`.

Workflow per target:
  1. Render listing page (HTTP or Playwright if `use_playwright: true`).
  2. Extract cards via config selectors / JSON-LD ItemList / heuristics.
  3. Paginate via config-supplied `next_page` selector or `?page=N` rewriter.
  4. Deep-scrape each detail URL → JSON-LD LocalBusiness extraction + heuristics.
  5. Dedupe + export to CSV.

Config example (YAML):
  mode: general
  run:
    confirm_permission: true
    use_playwright: true   # required for JS-heavy sites like Yelp
  targets:
    - name: yelp-clinics-chicago
      url: https://www.yelp.com/search?find_desc=Clinics&find_loc=Chicago%2C+IL
      pagination:
        param: start
        step: 10
        max_pages: 5
      selectors:
        card: "div[class*='businessName']"
        title: "a"
        url: "a"
        address: "span[class*='secondaryAttributes']"
        phone: "[class*='phone']"
        rating: "div[role='img'][aria-label*='star']"
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlencode, parse_qsl

from bs4 import BeautifulSoup

import yaml

from .extract import extract_listing_cards, extract_record_from_page
from .models import GENERAL_CSV_COLUMNS, GeneralRecord


# Reuse the job_scraper HTTP client + Playwright fetcher — same machinery.
from job_scraper.http import HttpClient
from job_scraper.crawl import PlaywrightFetcher
from job_scraper.deep_scrape import _HostThrottle  # internal import — same mechanism


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    raw = _load_yaml(args.config)
    if (raw.get("mode") or "").lower() != "general" and not args.force:
        logging.error("Config mode is not 'general'. Set mode: general or pass --force.")
        return

    run_cfg = raw.get("run", {}) or {}
    if not run_cfg.get("confirm_permission") and not args.confirm_permission:
        logging.error("Confirm permission to scrape via run.confirm_permission: true or --confirm-permission.")
        return

    http = HttpClient(
        user_agent=run_cfg.get("user_agent", "GeneralScraperBot/1.0 (+hriday.vig@bluoryn.com)"),
        delay_seconds=run_cfg.get("delay_seconds", 1.0),
        obey_robots=run_cfg.get("obey_robots", True),
        cache_enabled=run_cfg.get("cache_enabled", True),
        cache_ttl_seconds=run_cfg.get("cache_ttl_seconds", 86400),
        cache_path=run_cfg.get("cache_path", ".cache/general_http_cache.sqlite"),
        rotate_user_agents=run_cfg.get("rotate_user_agents", True),
    )
    use_playwright = run_cfg.get("use_playwright", False)

    targets = raw.get("targets", []) or []
    if not targets:
        logging.error("No targets in config.")
        return

    output_path = (raw.get("exports", {}).get("csv", {}) or {}).get("path", "output/general.csv")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    all_records: List[GeneralRecord] = []
    seen_ids = set()

    pw: Optional[PlaywrightFetcher] = None
    if use_playwright:
        try:
            pw = PlaywrightFetcher().__enter__()
        except Exception as exc:
            logging.warning("Playwright failed to start: %s — continuing without it.", exc)
            pw = None

    try:
        for target in targets:
            name = target.get("name") or urlparse(target.get("url", "")).netloc
            url = target.get("url", "")
            if not url:
                continue
            selectors = target.get("selectors") or {}
            pagination = target.get("pagination") or {}
            target_use_pw = target.get("use_playwright", use_playwright)

            cards = _collect_cards(
                start_url=url,
                http=http,
                playwright=pw if target_use_pw else None,
                selectors=selectors,
                pagination=pagination,
                max_pages=int(pagination.get("max_pages") or run_cfg.get("max_pages") or 5),
            )
            logging.info("%s: found %d cards", name, len(cards))

            # Deep-scrape each detail URL
            deep_cfg = run_cfg.get("deep_scrape", True)
            if deep_cfg:
                for c in cards:
                    if not c.source_url:
                        continue
                    fetched = _fetch(c.source_url, http, pw if target_use_pw else None)
                    if fetched is None:
                        continue
                    final_url, html = fetched
                    detail = extract_record_from_page(html, final_url)
                    if detail:
                        c.merge(detail)

            stamp = datetime.now(timezone.utc).isoformat()
            for c in cards:
                c.scraped_at = c.scraped_at or stamp
                c.source = name
                fp = c.fingerprint()
                if fp in seen_ids:
                    continue
                seen_ids.add(fp)
                all_records.append(c)
    finally:
        if pw is not None:
            try:
                pw.__exit__(None, None, None)
            except Exception:
                pass

    saved = datetime.now(timezone.utc).isoformat()
    for r in all_records:
        r.saved_at = r.saved_at or saved

    _write_csv(output_path, all_records)
    logging.info("Wrote %d records to %s", len(all_records), output_path)


def _collect_cards(
    start_url: str,
    http: HttpClient,
    playwright: Optional[PlaywrightFetcher],
    selectors: Dict[str, Any],
    pagination: Dict[str, Any],
    max_pages: int,
) -> List[GeneralRecord]:
    cards: List[GeneralRecord] = []
    seen_urls = set()

    pages_done = 0
    current_url = start_url
    while current_url and pages_done < max_pages:
        fetched = _fetch(current_url, http, playwright)
        if fetched is None:
            break
        final_url, html = fetched
        page_cards = extract_listing_cards(html, final_url, selectors=selectors if selectors.get("card") else None)
        new = 0
        for c in page_cards:
            key = c.source_url or c.name
            if key in seen_urls:
                continue
            seen_urls.add(key)
            cards.append(c)
            new += 1
        pages_done += 1
        if new == 0:
            break
        current_url = _next_page_url(html, final_url, pagination)
    return cards


def _next_page_url(html: str, base_url: str, pagination: Dict[str, Any]) -> Optional[str]:
    if not pagination:
        # Try <link rel="next">
        soup = BeautifulSoup(html, "lxml")
        link_next = soup.find("link", rel=lambda v: v and "next" in (v if isinstance(v, list) else [v]))
        if link_next and link_next.get("href"):
            from urllib.parse import urljoin
            return urljoin(base_url, link_next["href"])
        a_next = soup.select_one("a[rel='next'], a[aria-label*='next' i]")
        if a_next and a_next.get("href"):
            from urllib.parse import urljoin
            return urljoin(base_url, a_next["href"])
        return None

    # Config-driven param-based pagination
    if pagination.get("param"):
        param = pagination["param"]
        step = int(pagination.get("step") or 1)
        parsed = urlparse(base_url)
        qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
        try:
            cur = int(qs.get(param, "0") or 0)
        except ValueError:
            cur = 0
        qs[param] = str(cur + step)
        return parsed._replace(query=urlencode(qs, doseq=True)).geturl()

    if pagination.get("selector"):
        soup = BeautifulSoup(html, "lxml")
        a = soup.select_one(pagination["selector"])
        if a and a.get("href"):
            from urllib.parse import urljoin
            return urljoin(base_url, a["href"])
    return None


def _fetch(url: str, http: HttpClient, playwright: Optional[PlaywrightFetcher]):
    if playwright is not None:
        result = playwright.get(url)
        if result is not None:
            return result
    return http.get(url, allow_404=True)


def _write_csv(path: str, records: List[GeneralRecord]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GENERAL_CSV_COLUMNS)
        writer.writeheader()
        for r in records:
            writer.writerow(r.to_dict())


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as h:
        return yaml.safe_load(h) or {}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="General-purpose web scraper (businesses, listings)")
    p.add_argument("--config", default="config.general.yaml", help="Path to config file")
    p.add_argument("--confirm-permission", action="store_true")
    p.add_argument("--force", action="store_true", help="Run even if mode != general")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


if __name__ == "__main__":
    main()
