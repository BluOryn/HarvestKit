"""Single command: seed, harvest, score, cut, write."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from job_scraper.adapters import get_adapter
from job_scraper.config import load_config, resolve_config_path
from job_scraper.http import HttpClient

from .checkpoint import Checkpoint
from .export import write_csv
from .geo import EFTA_AND_UK, EU_COUNTRIES, search_names
from .pipeline import process_companies
from .score.quota import select
from .seed.atsboards import fetch_boards, guess_domains
from .seed.jobboard import companies_from_listings
from .seed.jobsearch import search_all

log = logging.getLogger("leadgen")


def _build_http(config) -> HttpClient:
    return HttpClient(
        user_agent=config.run.user_agent,
        delay_seconds=config.run.delay_seconds,
        obey_robots=config.run.obey_robots,
        # Company sites are slower and flakier than ATS APIs, and a lead lost to
        # an 8 s timeout is a lead lost for good, so this is deliberately patient.
        timeout_seconds=20.0,
        max_retries=1,
        cache_enabled=config.run.cache_enabled,
        cache_ttl_seconds=config.run.cache_ttl_seconds,
        cache_path=config.run.cache_path,
        rotate_user_agents=config.run.rotate_user_agents,
        per_host_concurrency=config.run.deep_per_host_concurrency,
        per_host_min_delay=config.run.deep_per_host_delay_seconds,
        proxies=config.run.proxies,
        proxy_rotation=config.run.proxy_rotation,
        proxy_max_failures=config.run.proxy_max_failures,
        proxy_cooldown_seconds=config.run.proxy_cooldown_seconds,
    )


def _read_lines(path: str) -> list[str]:
    """Non-empty, non-comment lines. Keywords may contain spaces, so no split."""
    lines: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _read_boards(path: str) -> list[tuple[str, str]]:
    """Parse `<kind> <slug>  # comment` lines into (kind, slug) pairs."""
    boards: list[tuple[str, str]] = []
    for line in _read_lines(path):
        parts = line.split()
        if len(parts) >= 2:
            boards.append((parts[0], parts[1]))
    return boards


def _collect_listings(config, http) -> list:
    listings: list = []
    for target in config.targets:
        try:
            found = get_adapter(target).fetch_jobs(target, config.run, http)
            log.info("seed: %-24s %5d listings", target.name, len(found))
            listings.extend(found)
        except Exception as exc:
            log.warning("seed: %s failed: %s", target.name, exc)
    return listings


def _country_set(spec: str) -> frozenset[str] | None:
    """'' -> everywhere, 'eu' -> the EU-27, 'europe' -> EU plus UK/CH/NO/IS."""
    wanted = (spec or "").strip().lower()
    if not wanted:
        return None
    if wanted == "eu":
        return EU_COUNTRIES
    if wanted == "europe":
        return EU_COUNTRIES | EFTA_AND_UK
    return frozenset(part.strip().upper() for part in wanted.split(",") if part.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="leadgen", description="Harvest a lead list.")
    parser.add_argument("--config", required=True, help="config name or path")
    parser.add_argument(
        "--boards",
        default="",
        help="file of '<kind> <slug>' public ATS boards to seed from, in addition to config targets",
    )
    parser.add_argument(
        "--search-keywords",
        default="",
        help="file of search terms for the cross-company job-search seed; paired with every "
        "country in --countries, which is what makes the seed geography-first",
    )
    parser.add_argument(
        "--search-keywords-multilingual",
        default="",
        help="file of native-language search terms for SmartRecruiters, which offers no "
        "geography parameter of its own",
    )
    parser.add_argument(
        "--search-max-pages",
        type=int,
        default=15,
        help="pages per (country, keyword) pairing before moving on",
    )
    parser.add_argument("--target", type=int, default=1000, help="exact number of rows wanted")
    parser.add_argument("--output", default="output/leads.csv")
    parser.add_argument("--checkpoint", default=".cache/leadgen.sqlite")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=8, help="guessed paths fetched per company")
    parser.add_argument(
        "--max-person-pages",
        type=int,
        default=10,
        help="per-person pages mined from each company's sitemap (0 disables)",
    )
    parser.add_argument("--country-ceiling", type=float, default=0.25)
    parser.add_argument(
        "--countries",
        default="",
        help="restrict to these ISO codes, comma separated; 'eu' means the EU-27, "
        "'europe' adds the UK, Switzerland, Norway and Iceland",
    )
    parser.add_argument(
        "--roles",
        default="hr,tech_leadership,executive",
        help="role families to keep, comma separated; 'any' disables the filter",
    )
    parser.add_argument(
        "--overfetch",
        type=float,
        default=3.0,
        help="stop harvesting once target*overfetch email-bearing leads are banked",
    )
    parser.add_argument("--no-smtp", action="store_true", help="skip catch-all probing")
    parser.add_argument(
        "--no-guess",
        action="store_true",
        help="never apply the modal first.last format to a domain with no published address",
    )
    parser.add_argument(
        "--select-only", action="store_true", help="skip harvesting, re-cut the existing checkpoint"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    config = load_config(resolve_config_path(args.config))
    checkpoint = Checkpoint(args.checkpoint)
    country_set = _country_set(args.countries)

    try:
        if not args.select_only:
            http = _build_http(config)
            try:
                listings = _collect_listings(config, http)
                if args.boards:
                    boards = _read_boards(args.boards)
                    log.info("seed: %d public ATS boards", len(boards))
                    listings.extend(fetch_boards(boards, http))
                if args.search_keywords or args.search_keywords_multilingual:
                    keywords = _read_lines(args.search_keywords) if args.search_keywords else []
                    multilingual = (
                        _read_lines(args.search_keywords_multilingual)
                        if args.search_keywords_multilingual
                        else []
                    )
                    # A cross-company search needs somewhere to search. Without
                    # --countries there is no geography to pair the keywords
                    # with, and the seed would silently return nothing.
                    names = search_names(country_set) if country_set else []
                    if keywords and not names:
                        log.warning("seed: --search-keywords needs --countries; skipping search seed")
                    log.info(
                        "seed: job search over %d countries x %d keywords, %d multilingual",
                        len(names),
                        len(keywords),
                        len(multilingual),
                    )
                    listings.extend(
                        search_all(
                            http,
                            countries=names,
                            keywords=keywords,
                            smartrecruiters_keywords=multilingual,
                            max_pages=args.search_max_pages,
                            concurrency=args.concurrency,
                        )
                    )
                log.info("seed: %d listings total", len(listings))
                companies = companies_from_listings(
                    listings,
                    http,
                    guess_domains=guess_domains,
                    concurrency=args.concurrency * 2,
                    countries=country_set,
                )
                log.info("seed: %d unique companies with a resolved own-domain", len(companies))
                funnel = process_companies(
                    companies,
                    http,
                    checkpoint,
                    concurrency=args.concurrency,
                    smtp=not args.no_smtp,
                    max_pages=args.max_pages,
                    max_person_pages=args.max_person_pages,
                    guess_without_anchor=not args.no_guess,
                    stop_after=int(args.target * args.overfetch) if args.overfetch else None,
                )
                log.info("funnel: %s", dict(funnel))
            finally:
                http.close()

        families = (
            None
            if args.roles.strip().lower() == "any"
            else frozenset(part.strip() for part in args.roles.split(",") if part.strip())
        )
        leads, report = select(
            checkpoint.all_leads(),
            target=args.target,
            country_ceiling=args.country_ceiling,
            role_families=families,
            countries=country_set,
        )
        write_csv(leads, args.output)
    finally:
        checkpoint.close()

    print()
    print(report.summary())
    print(f"\nwrote {len(leads)} rows to {Path(args.output).resolve()}")
    if report.shortfall:
        print(f"\nSHORTFALL: {report.shortfall} rows short of {args.target}.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
