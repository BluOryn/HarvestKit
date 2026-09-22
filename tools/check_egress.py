"""Can this machine actually read European company sites?

Run this before a long lead run. It answers the question that matters — "does a
page come back" — rather than the one a `curl` status loop answers, which is
"did something respond". A 403 responds. A 403 yields no leads.

It fetches through the engine's own `HttpClient`, so it exercises the real
robots gate, the real proxy pool and the real transport ladder. A result here is
therefore a genuine prediction of what the run will do, not an approximation.

    python tools/check_egress.py
    python tools/check_egress.py --config configs/leads/eu-it.yaml
    python tools/check_egress.py --hosts acme.de,example.ch
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from job_scraper.config import RunConfig, load_config, resolve_config_path  # noqa: E402
from job_scraper.http import HttpClient  # noqa: E402
from job_scraper.transport import Outcome  # noqa: E402

#: A deliberately mixed sample: two that have never blocked us, several behind
#: Cloudflare or Akamai, and one (sap.com) that publishes `Disallow: /` so the
#: robots gate shows up in the output when it is switched on.
DEFAULT_HOSTS = (
    "www.siemens.com",
    "www.sap.com",
    "www.zalando.de",
    "www.hellofresh.de",
    "www.getyourguide.com",
    "www.personio.de",
    "www.celonis.com",
    "www.trivago.com",
    "www.flixbus.de",
    "www.klarna.com",
    "www.adyen.com",
    "www.bolt.eu",
)

_VERDICT = {
    Outcome.OK: "readable",
    Outcome.BLOCKED: "BLOCKED",
    Outcome.RATE_LIMITED: "rate-limited",
    Outcome.NOT_FOUND: "not found",
    Outcome.SERVER_ERROR: "server error",
    Outcome.ERROR: "unreachable",
    Outcome.ROBOTS_DENIED: "robots says no",
}


def _client(config_name: str) -> HttpClient:
    run = RunConfig()
    if config_name:
        run = load_config(resolve_config_path(config_name)).run
    return HttpClient(
        user_agent=run.user_agent,
        delay_seconds=run.delay_seconds,
        obey_robots=run.obey_robots,
        timeout_seconds=20.0,
        cache_enabled=False,  # a cached answer would tell us nothing about now
        rotate_user_agents=run.rotate_user_agents,
        per_host_concurrency=2,
        proxies=run.proxies,
        proxy_rotation=run.proxy_rotation,
        use_impersonation=run.use_impersonation,
        use_browser=run.use_stealth_browser,
        escalate_on_block=run.escalate_on_block,
        robots_unreadable_is_allowed=run.robots_unreadable_is_allowed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default="", help="use this config's transport and proxy settings")
    parser.add_argument("--hosts", default="", help="comma-separated hosts to check instead of the sample")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")

    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()] or list(DEFAULT_HOSTS)
    http = _client(args.config)
    try:
        try:
            import curl_cffi  # noqa: F401

            print("impersonation rung: available (curl_cffi)")
        except Exception:
            print(
                "impersonation rung: MISSING — `pip install curl_cffi`. Without it every "
                "TLS-fingerprinting site stays blocked."
            )
        print(f"proxies configured : {len(http.proxy_stats())}")
        print(f"robots.txt         : {'obeyed' if http.obey_robots else 'not consulted'}")
        print()
        print(f"{'host':26} {'verdict':16} {'via':12} {'status':>6} {'bytes':>9}")
        print("-" * 76)

        def check(host: str):
            return host, http.fetch(f"https://{host}/", use_cache=False)

        results = []
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            for host, result in pool.map(check, hosts):
                results.append((host, result))

        results.sort(key=lambda pair: hosts.index(pair[0]))
        for host, result in results:
            print(
                f"{host:26} {_VERDICT.get(result.outcome, '?'):16} "
                f"{result.transport or '-':12} {result.status or 0:>6} {len(result.text):>9}"
            )

        readable = sum(1 for _, r in results if r.ok)
        blocked = sum(1 for _, r in results if r.outcome is Outcome.BLOCKED)
        robots = sum(1 for _, r in results if r.outcome is Outcome.ROBOTS_DENIED)
        broken = sum(1 for _, r in results if r.outcome in (Outcome.ERROR, Outcome.SERVER_ERROR))
        print("-" * 76)
        print(
            f"{readable}/{len(results)} readable | {blocked} blocked | "
            f"{robots} refused by robots | {broken} unreachable"
        )

        if blocked:
            print(
                "\nBlocked hosts yield no people, and the run counts them under "
                "`blocked_no_pages_seen`. To improve this:\n"
                "  1. keep run.use_impersonation: true (and install curl_cffi)\n"
                "  2. configure run.proxies — python tools/proxy_sources.py --print-setup\n"
                "  3. for the stubborn ones, run.use_stealth_browser: true"
            )
        if broken >= len(results) // 2:
            print(
                "\nMost hosts were unreachable rather than blocked. That is a network or DNS "
                "problem on this machine, not a scraping one — fix it before running."
            )
        sys.exit(0 if readable else 1)
    finally:
        http.close()


if __name__ == "__main__":
    main()
