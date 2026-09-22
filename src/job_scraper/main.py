import argparse
import logging
import sys
from contextlib import suppress
from datetime import datetime, timezone
from urllib.parse import urlparse

from .adapters import get_adapter
from .config import (
    AppConfig,
    KeywordConfig,
    LocationConfig,
    TargetConfig,
    load_config,
    resolve_config_path,
)
from .crawl import PlaywrightFetcher
from .dedupe import dedupe_jobs
from .deep_scrape import DeepScrapeConfig, deep_scrape_jobs
from .export import run_exports
from .http import HttpClient
from .models import JobListing
from .normalize import match_keywords, match_location

DEFAULT_CONFIG = "example.yaml"


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s: %(message)s",
    )

    try:
        config = load_config(resolve_config_path(args.config))
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        sys.exit(2)
    except ValueError as exc:
        logging.error("Invalid config %s: %s", args.config, exc)
        sys.exit(2)
    config = _apply_overrides(config, args)

    if not config.run.confirm_permission:
        logging.error(
            "Confirm permission to scrape by setting run.confirm_permission: true "
            "or passing --confirm-permission."
        )
        sys.exit(2)

    if not config.targets:
        logging.error("No targets provided. Add targets to your config file or use --urls.")
        sys.exit(2)

    http = HttpClient(
        user_agent=config.run.user_agent,
        delay_seconds=config.run.delay_seconds,
        obey_robots=config.run.obey_robots,
        cache_enabled=config.run.cache_enabled,
        cache_ttl_seconds=config.run.cache_ttl_seconds,
        cache_path=config.run.cache_path,
        rotate_user_agents=config.run.rotate_user_agents,
        proxies=config.run.proxies,
        proxy_rotation=config.run.proxy_rotation,
        proxy_max_failures=config.run.proxy_max_failures,
        proxy_cooldown_seconds=config.run.proxy_cooldown_seconds,
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
    if http.has_proxies:
        # With proxies, we can scale per-host concurrency without hitting rate limits.
        # Each proxy gets its own (host, proxy) bucket via the throttle key.
        config.run.deep_per_host_concurrency = max(
            config.run.deep_per_host_concurrency,
            min(8, len(config.run.proxies)),
        )
        config.run.deep_concurrency = max(
            config.run.deep_concurrency,
            min(16, len(config.run.proxies) * 2),
        )

    deep_cfg = DeepScrapeConfig(
        concurrency=config.run.deep_concurrency,
        per_host_concurrency=config.run.deep_per_host_concurrency,
        per_host_delay_seconds=config.run.deep_per_host_delay_seconds,
        max_retries=config.run.deep_max_retries,
        llm_fallback_enabled=config.run.llm_fallback_enabled,
        llm_api_key=config.run.llm_api_key,
        llm_model=config.run.llm_model,
        llm_min_fields=config.run.llm_min_fields,
        llm_max_html_chars=config.run.llm_max_html_chars,
        llm_cache_path=config.run.llm_cache_path,
        llm_monthly_budget_usd=config.run.llm_monthly_budget_usd,
    )

    # Lazy-open Playwright only if any target needs it
    pw_fetcher = None
    if config.run.use_playwright:
        deep_cfg.use_playwright_fallback = True
        try:
            pw_fetcher = PlaywrightFetcher(http=http).__enter__()
        except Exception as exc:
            logging.warning("Playwright init failed: %s — falling back to HTTP-only", exc)
            pw_fetcher = None

    try:
        all_jobs: list[JobListing] = []
        for target in config.targets:
            adapter = get_adapter(target)
            logging.info("Scraping %s (%s)", target.name, target.url)
            # If the target opts into Playwright, pre-render the search page so the
            # adapter's plain HTTP fetch picks up the (now cached) JS-rendered HTML.
            if target.use_playwright and pw_fetcher is not None:
                try:
                    pw_result = pw_fetcher.get(target.url)
                    if pw_result is not None:
                        pw_final, pw_html = pw_result
                        cache = getattr(http, "_cache", None)
                        if cache is not None and pw_html:
                            try:
                                cache.put(target.url, pw_final, pw_html)
                            except Exception as cexc:
                                logging.debug("cache put failed: %s", cexc)
                except Exception as exc:
                    logging.warning("Playwright pre-fetch failed for %s: %s", target.name, exc)
            try:
                jobs = adapter.fetch_jobs(target, config.run, http)
            except Exception as exc:
                logging.warning("Adapter failed for %s: %s", target.name, exc)
                continue
            for job in jobs:
                job.source = target.name
                job.scraped_at = datetime.now(timezone.utc).isoformat()
            if config.run.deep_scrape and jobs:
                needs_deep = [j for j in jobs if (j.job_url or j.apply_url)]
                if needs_deep:
                    logging.info("%s: deep-scraping %d/%d postings…", target.name, len(needs_deep), len(jobs))
                    deep_scrape_jobs(
                        needs_deep,
                        http,
                        deep_cfg,
                        on_progress=_progress_logger(target.name),
                        playwright_fetcher=pw_fetcher,
                    )
            all_jobs.extend(jobs)
    finally:
        if pw_fetcher is not None:
            with suppress(Exception):
                pw_fetcher.__exit__(None, None, None)
        http.close()

    after_keywords = _filter_by_keywords(all_jobs, config.keywords)
    after_location = _filter_by_location(after_keywords, config.locations)
    unique = dedupe_jobs(after_location)

    # Stamp saved_at on every row so the CSV is parity with extension.
    saved_stamp = datetime.now(timezone.utc).isoformat()
    for j in unique:
        j.saved_at = j.saved_at or saved_stamp

    run_exports(unique, config.exports)
    logging.info(
        "Done. Total: %d | KW: %d | Location: %d | Dedup: %d",
        len(all_jobs),
        len(after_keywords),
        len(after_location),
        len(unique),
    )


def _progress_logger(target_name: str):
    """Build a progress callback bound to `target_name`.

    Defining the closure inline inside the target loop captured the loop
    variable by reference, so every callback reported whichever target the loop
    had reached by the time it fired.
    """

    def _on_progress(done: int, ok: int, failed: int) -> None:
        logging.info("  %s: %d done · %d ok · %d failed", target_name, done, ok, failed)

    return _on_progress


def _filter_by_location(jobs: list[JobListing], locations: LocationConfig) -> list[JobListing]:
    if not locations.include and not locations.exclude:
        return jobs
    return [
        job
        for job in jobs
        if match_location(job, locations.include, locations.exclude, locations.allow_remote)
    ]


def _filter_by_keywords(jobs: list[JobListing], keywords: KeywordConfig) -> list[JobListing]:
    include = list(dict.fromkeys((keywords.include or []) + (keywords.sectors or [])))
    exclude = keywords.exclude or []

    if not include and not exclude:
        return jobs

    filtered = []
    for job in jobs:
        matched = match_keywords(job, include)
        if include and not matched:
            continue
        if exclude and match_keywords(job, exclude):
            continue
        job.keywords_matched = matched
        filtered.append(job)
    return filtered


def _apply_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    if args.max_pages is not None:
        config.run.max_pages = args.max_pages
    if args.max_depth is not None:
        config.run.max_depth = args.max_depth
    if args.use_playwright:
        config.run.use_playwright = True
    if args.confirm_permission:
        config.run.confirm_permission = True
    if args.obey_robots:
        config.run.obey_robots = True
    if args.no_deep_scrape:
        config.run.deep_scrape = False
    if args.no_cache:
        config.run.cache_enabled = False
    if args.output:
        config.exports.csv.enabled = True
        config.exports.csv.path = args.output
    if args.keywords:
        config.keywords.include = _split_list(args.keywords)
    if args.sectors:
        config.keywords.sectors = _split_list(args.sectors)
    if args.exclude:
        config.keywords.exclude = _split_list(args.exclude)
    if args.locations:
        config.locations.include = _split_list(args.locations)
    if args.urls:
        config.targets = _targets_from_urls(args.urls)
    return config


def _targets_from_urls(urls: list[str]) -> list[TargetConfig]:
    targets: list[TargetConfig] = []
    for url in urls:
        parsed = urlparse(url)
        name = parsed.netloc or url
        targets.append(TargetConfig(name=name, url=url, adapter="auto"))
    return targets


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HarvestKit job scraper (deep-scrape capable)")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=(
            "Config file. Accepts a bare name (norway-big), a filename "
            "(norway-big.yaml) or a path; bare names are looked up under configs/."
        ),
    )
    parser.add_argument("--urls", nargs="*", help="One or more target URLs")
    parser.add_argument("--keywords", help="Comma-separated keywords")
    parser.add_argument("--sectors", help="Comma-separated sector keywords")
    parser.add_argument("--exclude", help="Comma-separated exclude keywords")
    parser.add_argument("--locations", help="Comma-separated location filter")
    parser.add_argument("-o", "--output", help="CSV output path (overrides exports.csv.path)")
    parser.add_argument("--max-pages", type=int, help="Max pages per target")
    parser.add_argument("--max-depth", type=int, help="Max crawl depth")
    parser.add_argument(
        "--use-playwright", action="store_true", help="Enable Playwright fallback for JS-heavy sites"
    )
    parser.add_argument(
        "--confirm-permission", action="store_true", help="Confirm you have permission to scrape"
    )
    parser.add_argument(
        "--obey-robots", action="store_true", help="Force robots.txt enforcement on for this run"
    )
    parser.add_argument(
        "--no-deep-scrape",
        action="store_true",
        help="Skip per-posting deep scrape (faster but fewer fields)",
    )
    parser.add_argument("--no-cache", action="store_true", help="Disable HTTP cache")
    parser.add_argument("--log-level", default="INFO", help="Log verbosity (DEBUG/INFO/WARNING/ERROR)")
    return parser.parse_args()


if __name__ == "__main__":
    main()
