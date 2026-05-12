import argparse
import logging
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlparse

from .adapters import get_adapter
from .config import AppConfig, KeywordConfig, LocationConfig, RunConfig, TargetConfig, load_config
from .dedupe import dedupe_jobs
from .deep_scrape import DeepScrapeConfig, deep_scrape_jobs
from .export import run_exports
from .http import HttpClient
from .normalize import match_keywords, match_location


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    config = load_config(args.config)
    config = _apply_overrides(config, args)

    if not config.run.confirm_permission:
        logging.error("Confirm permission to scrape by setting run.confirm_permission: true or passing --confirm-permission.")
        return

    if not config.targets:
        logging.error("No targets provided. Add targets to config.yaml or use --urls.")
        return

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

    all_jobs = []
    for target in config.targets:
        adapter = get_adapter(target)
        logging.info("Scraping %s (%s)", target.name, target.url)
        try:
            jobs = adapter.fetch_jobs(target, config.run, http)
        except Exception as exc:
            logging.warning("Adapter failed for %s: %s", target.name, exc)
            continue
        for job in jobs:
            job.source = target.name
            job.scraped_at = datetime.now(timezone.utc).isoformat()
        # Deep-scrape every posting that has a URL — always, no skipping.
        # The merge in JobListing.merge() only overwrites when the new value is longer,
        # so re-running on already-rich rows is idempotent (and benefits from cache).
        if config.run.deep_scrape and jobs:
            needs_deep = [j for j in jobs if (j.job_url or j.apply_url)]
            if needs_deep:
                logging.info("%s: deep-scraping %d/%d postings…", target.name, len(needs_deep), len(jobs))
                def _on_progress(done: int, ok: int, failed: int) -> None:
                    logging.info("  %s: %d done · %d ok · %d failed", target.name, done, ok, failed)
                deep_scrape_jobs(needs_deep, http, deep_cfg, on_progress=_on_progress)
        all_jobs.extend(jobs)

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
        len(all_jobs), len(after_keywords), len(after_location), len(unique),
    )


def _filter_by_location(jobs: List, locations: LocationConfig) -> List:
    if not locations.include and not locations.exclude:
        return jobs
    out = []
    for job in jobs:
        if match_location(job, locations.include, locations.exclude, locations.allow_remote):
            out.append(job)
    return out


def _filter_by_keywords(jobs: List, keywords: KeywordConfig) -> List:
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
    if args.no_deep_scrape:
        config.run.deep_scrape = False
    if args.no_cache:
        config.run.cache_enabled = False
    if args.keywords:
        config.keywords.include = _split_list(args.keywords)
    if args.sectors:
        config.keywords.sectors = _split_list(args.sectors)
    if args.exclude:
        config.keywords.exclude = _split_list(args.exclude)
    if args.urls:
        config.targets = _targets_from_urls(args.urls)
    return config


def _targets_from_urls(urls: List[str]) -> List[TargetConfig]:
    targets: List[TargetConfig] = []
    for url in urls:
        parsed = urlparse(url)
        name = parsed.netloc or url
        targets.append(TargetConfig(name=name, url=url, adapter="auto"))
    return targets


def _split_list(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Job listing scraper (deep-scrape capable)")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--urls", nargs="*", help="One or more target URLs")
    parser.add_argument("--keywords", help="Comma-separated keywords")
    parser.add_argument("--sectors", help="Comma-separated sector keywords")
    parser.add_argument("--exclude", help="Comma-separated exclude keywords")
    parser.add_argument("--max-pages", type=int, help="Max pages per target")
    parser.add_argument("--max-depth", type=int, help="Max crawl depth")
    parser.add_argument("--use-playwright", action="store_true", help="Enable Playwright fallback for JS-heavy sites")
    parser.add_argument("--confirm-permission", action="store_true", help="Confirm you have permission to scrape")
    parser.add_argument("--no-deep-scrape", action="store_true", help="Skip per-posting deep scrape (faster but fewer fields)")
    parser.add_argument("--no-cache", action="store_true", help="Disable HTTP cache")
    parser.add_argument("--log-level", default="INFO", help="Log verbosity (DEBUG/INFO/WARNING/ERROR)")
    return parser.parse_args()


if __name__ == "__main__":
    main()
