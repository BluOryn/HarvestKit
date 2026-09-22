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
from .person import register
from .pipeline import process_companies
from .score.quota import select
from .seed.arbeitnow import fetch as fetch_arbeitnow
from .seed.atsboards import fetch_boards, guess_domains
from .seed.jobboard import companies_from_listings
from .seed.jobsch import DEFAULT_DAYS as JOBSCH_DEFAULT_DAYS
from .seed.jobsch import IT_CATEGORIES as JOBSCH_IT_CATEGORIES
from .seed.jobsch import build_filter_url as build_jobsch_url
from .seed.jobsch import fetch as fetch_jobsch
from .seed.jobsearch import search_all

log = logging.getLogger("leadgen")


def _build_http(config, *, robots_exempt_hosts: tuple[str, ...] = ()) -> HttpClient:
    return HttpClient(
        robots_exempt_hosts=robots_exempt_hosts,
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


def _harvest_failure(listings, companies, funnel) -> str:
    """Why this run harvested nothing, or "" when it harvested something.

    The harvest and the export used to be completely decoupled: `all_leads()`
    reads every lead ever banked, so a run in which every seed API answered 403
    and every company domain served a bot wall still wrote a full CSV and
    returned 0. On a fresh checkpoint the same total failure wrote a
    header-only file and returned 2 — which `daily.sh` reports as "short of
    target", i.e. a thin market rather than a dead network.
    """
    if not listings:
        return "no seed listing was harvested at all — every seed source failed or was blocked"
    if not companies:
        return f"{len(listings)} listings seeded but not one company resolved to its own domain"
    if not funnel.get("companies_with_people"):
        blocked = funnel.get("blocked_no_pages_seen", 0)
        unreachable = funnel.get("unreachable", 0)
        detail = (
            f" ({blocked} were blocked before any page was read, {unreachable} were unreachable)"
            if blocked or unreachable
            else ""
        )
        return f"{len(companies)} companies crawled and none named a single person{detail}"
    return ""


def _report_blocking(funnel, http) -> None:
    """Say plainly how much of the run was refused rather than empty.

    The funnel Counter is accurate but easy to skim past. A run whose companies
    were mostly walled looks, in the delivered CSV, exactly like a run in a
    thin market — so the difference has to be stated, not left to be inferred.
    """
    walled = funnel.get("blocked_no_pages_seen", 0)
    unreachable = funnel.get("unreachable", 0)
    empty = funnel.get("no_person_found", 0)
    with_people = funnel.get("companies_with_people", 0)
    attempted = walled + unreachable + empty + with_people
    if not attempted:
        return

    share = 100.0 * walled / attempted
    log.info(
        "reachability: %d/%d companies (%.0f%%) were blocked before any page was read; "
        "%d were unreachable; %d answered but named nobody; %d yielded people",
        walled,
        attempted,
        share,
        unreachable,
        empty,
        with_people,
    )
    if share >= 20.0:
        log.warning(
            "reachability: %.0f%% of companies were blocked. That is an egress problem, not a "
            "parser problem — the team-page parser never saw those sites. Configure "
            "run.proxies (see tools/proxy_sources.py) and keep run.use_impersonation on.",
            share,
        )
        if not getattr(http, "has_proxies", False):
            log.warning(
                "reachability: this run used no proxies at all, so every request came from one "
                "address. A single address is what a WAF rate-limits first."
            )
    stats = getattr(http, "transport_stats", None)
    if callable(stats):
        escalated = [row for row in stats() if row.get("rung", 0) > 0]
        if escalated:
            log.info(
                "transport: %d domains needed a stronger client than plain requests "
                "(remembered for next run)",
                len(escalated),
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


def _int_list(spec: str, fallback: tuple[int, ...]) -> tuple[int, ...]:
    """Parse "106,146" into ids, keeping the shipped set when nothing parses."""
    found = tuple(int(part) for part in (spec or "").split(",") if part.strip().isdigit())
    return found or fallback


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
    parser.add_argument(
        "--search-locations",
        default="",
        help="file of place names to search, one per line, used verbatim. Defaults to the "
        "country names implied by --countries. Cities matter because each query is "
        "capped, so 'Berlin' reaches employers a 'Germany' query never returns",
    )
    parser.add_argument(
        "--search-delay",
        type=float,
        default=0.0,
        help="extra seconds between search requests. This host blocked a whole run "
        "after ~1500 requests, and a seed that gets itself blocked is worth less "
        "than a slower one that does not",
    )
    parser.add_argument(
        "--arbeitnow-pages",
        type=int,
        default=0,
        help="pages to walk of the Arbeitnow German job feed (0 disables). Paced at "
        "5s a page because the API refuses anything faster",
    )
    parser.add_argument(
        "--jobsch-pages",
        type=int,
        default=0,
        help="pages of the jobs.ch filtered search to walk (0 disables). The Swiss "
        "seed: 83%% of its employers publish their own website in the posting, "
        "which skips domain resolution entirely",
    )
    parser.add_argument(
        "--jobsch-days",
        type=int,
        default=JOBSCH_DEFAULT_DAYS,
        help="how recently a posting must have appeared, in days. Measured live against "
        "the shipped sector filter: 1 day returns 25 postings, 3 returns 185, 7 returns "
        "478, 30 returns 1396. After the first sweep a daily run only needs the last few "
        "days; asking for 30 every morning re-fetches postings whose employers are "
        "already crawled",
    )
    parser.add_argument(
        "--jobsch-term",
        default="",
        help="free-text keyword added to the jobs.ch filter (empty = the whole sector)",
    )
    parser.add_argument(
        "--jobsch-categories",
        default=",".join(str(c) for c in JOBSCH_IT_CATEGORIES),
        help="jobs.ch category ids, comma separated. Shipped: 106 IT/Telecom, "
        "146 Engineering/Technical, 156 Management/Consulting, 167 Electronics",
    )
    parser.add_argument(
        "--jobsch-url",
        default="",
        help="a complete jobs.ch search URL, which overrides --jobsch-days/-term/"
        "-categories entirely. Build the search you want in a browser and paste the "
        "address bar",
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
    parser.add_argument(
        "--max-nav-pages",
        type=int,
        default=6,
        help="people-pages followed from each company's own navigation (0 disables). "
        "A guessed path only finds a layout somebody anticipated; this finds whatever "
        "the site actually calls its team page",
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
    parser.add_argument(
        "--only-new",
        action="store_true",
        help="export only leads never delivered from this checkpoint before, and record the "
        "ones written. This is what makes a daily run deliver fresh people instead of "
        "re-sending yesterday's file with today's date on it",
    )
    parser.add_argument(
        "--recrawl-after",
        type=float,
        default=0.0,
        help="days after which an already-crawled company is crawled again (0 = never). "
        "Staff change: an employer crawled in January may name three new directors by "
        "June, and 'crawled once' meaning 'crawled forever' never finds them",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="for companies whose own site names nobody, read the board and officers out of "
        f"the Swiss commercial register (needs {register.USER_ENV}/{register.PASSWORD_ENV})",
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

    if args.register and not register.is_configured():
        # Silently doing nothing would look like "the register had no data on
        # these companies", which is a very different conclusion.
        log.warning(
            "--register was asked for but %s/%s are not set, so the commercial register "
            "will not be consulted; register free at %s",
            register.USER_ENV,
            register.PASSWORD_ENV,
            "https://www.zefix.admin.ch/en/search/entity/welcome",
        )

    harvest_failure = ""
    try:
        if not args.select_only:
            # The register API disallows anonymous crawlers in robots.txt and
            # issues credentials instead; holding those credentials is the
            # authorisation, so that one host -- and only it -- is exempted.
            exempt = (register.API_HOST,) if (args.register and register.is_configured()) else ()
            http = _build_http(config, robots_exempt_hosts=exempt)
            try:
                listings = _collect_listings(config, http)
                if args.boards:
                    boards = _read_boards(args.boards)
                    log.info("seed: %d public ATS boards", len(boards))
                    listings.extend(fetch_boards(boards, http))
                jobsch_listings: list = []
                if args.jobsch_pages:
                    # An explicit URL is taken whole: somebody who pasted a search
                    # out of their browser means that search, not that search with
                    # our defaults grafted back on.
                    filter_url = args.jobsch_url or build_jobsch_url(
                        days=args.jobsch_days,
                        categories=_int_list(args.jobsch_categories, JOBSCH_IT_CATEGORIES),
                        term=args.jobsch_term,
                    )
                    log.info("seed: jobs.ch filter %s", filter_url)
                    jobsch_listings = fetch_jobsch(
                        http,
                        filter_url=filter_url,
                        max_pages=args.jobsch_pages,
                        concurrency=args.concurrency,
                    )
                    listings.extend(jobsch_listings)
                if args.arbeitnow_pages:
                    listings.extend(fetch_arbeitnow(http, max_pages=args.arbeitnow_pages))
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
                    names = (
                        _read_lines(args.search_locations)
                        if args.search_locations
                        else (search_names(country_set) if country_set else [])
                    )
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
                            delay_seconds=args.search_delay,
                        )
                    )
                log.info("seed: %d listings total", len(listings))
                jobsch_urls = {id(item) for item in jobsch_listings}
                other = [item for item in listings if id(item) not in jobsch_urls]
                companies = companies_from_listings(
                    other,
                    http,
                    guess_domains=guess_domains,
                    concurrency=args.concurrency * 2,
                    countries=country_set,
                )
                if jobsch_listings:
                    # The jobs.ch filter already chose the sector, so its postings
                    # skip the title gate rather than being re-judged by it.
                    seen_domains = {company.domain for company in companies}
                    for company in companies_from_listings(
                        jobsch_listings,
                        http,
                        guess_domains=guess_domains,
                        concurrency=args.concurrency * 2,
                        countries=country_set,
                        require_it_role=False,
                    ):
                        if company.domain not in seen_domains:
                            seen_domains.add(company.domain)
                            companies.append(company)
                log.info("seed: %d unique companies with a resolved own-domain", len(companies))
                funnel = process_companies(
                    companies,
                    http,
                    checkpoint,
                    concurrency=args.concurrency,
                    smtp=not args.no_smtp,
                    max_pages=args.max_pages,
                    max_person_pages=args.max_person_pages,
                    max_nav_pages=args.max_nav_pages,
                    guess_without_anchor=not args.no_guess,
                    stop_after=int(args.target * args.overfetch) if args.overfetch else None,
                    register=args.register,
                    recrawl_after_days=args.recrawl_after or None,
                )
                log.info("funnel: %s", dict(funnel))
                _report_blocking(funnel, http)
                harvest_failure = _harvest_failure(listings, companies, funnel)
            finally:
                http.close()

        if harvest_failure:
            # Exporting here would write a full, successful-looking CSV of
            # leads banked on an earlier day and exit 0. That is the single
            # worst failure this tool can have: the operator ships yesterday's
            # file believing it is today's, and nothing anywhere says otherwise.
            # A blocked network is precisely when this happens, and precisely
            # when the checkpoint is fullest.
            print()
            print(f"HARVEST FAILED: {harvest_failure}")
            print(
                "Refusing to export. The checkpoint still holds earlier leads, and writing "
                "them now would look like a successful run."
            )
            print(
                "Run `python tools/check_egress.py` to see whether this machine can read "
                "European sites at all, then `--select-only` if you really do want to "
                "re-cut the existing checkpoint."
            )
            checkpoint.close()
            return 4

        families = (
            None
            if args.roles.strip().lower() == "any"
            else frozenset(part.strip() for part in args.roles.split(",") if part.strip())
        )
        candidates = checkpoint.all_leads()
        if args.only_new:
            already = checkpoint.delivered_ids()
            before = len(candidates)
            candidates = [lead for lead in candidates if lead.fingerprint() not in already]
            log.info(
                "delivered ledger: %d of %d leads already went out, %d left to choose from",
                before - len(candidates),
                before,
                len(candidates),
            )
        leads, report = select(
            candidates,
            target=args.target,
            country_ceiling=args.country_ceiling,
            role_families=families,
            countries=country_set,
        )
        write_csv(leads, args.output)
        # Only after the file exists. A lead marked delivered but never written
        # is one the buyer never receives at all, and no later run will retry it.
        if args.only_new:
            checkpoint.mark_delivered(leads, batch=Path(args.output).name)
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
