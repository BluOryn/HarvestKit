"""A bot wall must not be mistaken for a crawling policy.

The failure this pins down cost roughly a quarter of the European company
domains in a lead run, silently. robots.txt was fetched with bare `urllib` —
no proxy, no browser headers — which is the most blockable request the engine
could possibly send. WAFs answered it with 403, RFC 9309 says an unavailable
robots.txt means the whole host is off-limits, and so the host was dropped
before a single page request. Measured live: hellofresh.de, getyourguide.com
and zalando.de were all being written off this way, and none of them publishes
a robots.txt that disallows anything.

The distinction that has to hold: a site that *says* no is obeyed; a site we
were *prevented from asking* is not treated as having said anything.
"""

from __future__ import annotations

from job_scraper.robots import RobotsCache, Verdict

UA = "HarvestKitBot/1.0"

# What a Cloudflare / Akamai edge actually returns for /robots.txt when it does
# not like the client: HTTP 403 whose body is a web page, not a robots file.
CHALLENGE_BODY = (
    "<!DOCTYPE html><html><head><title>Access denied</title></head>"
    "<body><h1>Error 1020</h1><p>Ray ID: 8f2c</p></body></html>"
)

REAL_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
REAL_PERMISSIVE = "User-agent: *\nDisallow: /admin/\nAllow: /\n"


def _cache(response, **kwargs) -> RobotsCache:
    """A RobotsCache whose transport returns exactly `response`."""
    return RobotsCache(fetcher=lambda url: response, **kwargs)


def test_a_waf_403_does_not_disallow_the_whole_host():
    """The regression. A 403 carrying an HTML block page is the WAF talking,
    not the site, so no restriction may be inferred from it — and RFC 9309
    §2.3.1.3 says a 4xx permits crawling regardless of what the body holds."""
    cache = _cache((403, CHALLENGE_BODY))
    verdict, _ = cache.verdict("https://hellofresh.de/impressum", UA)
    assert verdict is Verdict.ALLOW
    assert cache.is_allowed("https://hellofresh.de/impressum", UA) is True


def test_a_403_permits_crawling_because_that_is_what_the_rfc_says():
    """RFC 9309 §2.3.1.3: the whole 400-499 range means the file is
    "Unavailable", and "the crawler MAY access any resources on the server".

    The old code had this exactly backwards — it cited the RFC while doing the
    opposite of what the RFC says — and that inversion is what dropped the
    walled hosts.
    """
    cache = _cache((403, "Forbidden"))
    verdict, _ = cache.verdict("https://api.example.com/v1", UA)
    assert verdict is Verdict.ALLOW
    assert cache.is_allowed("https://api.example.com/v1", UA) is True


def test_a_plain_text_403_is_not_rescued_by_html_sniffing_alone():
    """The case that survived the first fix attempt.

    `rest.arbeitsagentur.de/robots.txt` answers 403 with `text/plain` and a
    body of a single space. An HTML-sniffing heuristic says "not a challenge
    page" and would still disallow the host. The status alone has to settle
    it, which is also what the RFC requires.
    """
    cache = _cache((403, " "))
    assert cache.is_allowed("https://rest.arbeitsagentur.de/jobboerse/x", UA) is True


def test_a_5xx_is_unreachable_and_expresses_no_policy():
    """RFC 9309 §2.3.1.4 mandates complete disallow here. We report UNKNOWN and
    let the operator's `unreadable_is_allowed` decide, because a transient 502
    should not blackhole a company for the life of the process."""
    cache = _cache((503, "<html>Service Unavailable</html>"))
    verdict, _ = cache.verdict("https://example.com/", UA)
    assert verdict is Verdict.UNKNOWN
    assert cache.is_allowed("https://example.com/", UA) is True


def test_a_5xx_can_be_made_to_disallow_for_literal_rfc_behaviour():
    cache = _cache((500, ""), unreadable_is_allowed=False)
    assert cache.is_allowed("https://example.com/", UA) is False


def test_a_site_that_really_says_disallow_all_is_obeyed():
    """sap.com and n26.com both publish exactly this. Ignoring it would be
    ignoring an answer we actually received."""
    cache = _cache((200, REAL_DISALLOW_ALL))
    assert cache.is_allowed("https://www.sap.com/about", UA) is False


def test_a_permissive_robots_file_is_parsed_normally():
    cache = _cache((200, REAL_PERMISSIVE))
    assert cache.is_allowed("https://example.com/team", UA) is True
    assert cache.is_allowed("https://example.com/admin/secrets", UA) is False


def test_a_200_that_is_secretly_an_html_page_grants_nothing():
    """The mirror of the 403 case, and the more dangerous direction: an HTML
    body parses into zero rules, which `RobotFileParser` reports as blanket
    permission. Inferring consent from a block page would be worse than
    inferring refusal."""
    cache = _cache((200, CHALLENGE_BODY))
    verdict, _ = cache.verdict("https://example.com/", UA)
    assert verdict is Verdict.UNKNOWN


def test_no_robots_file_at_all_is_permission():
    cache = _cache((404, "Not Found"))
    verdict, _ = cache.verdict("https://example.com/", UA)
    assert verdict is Verdict.ALLOW
    assert cache.is_allowed("https://example.com/anything", UA) is True


def test_an_unparseable_robots_can_be_made_strict_for_operators_who_want_that():
    """The permissive reading of an *unparseable* body is a default, not a law.

    Note this applies to the UNKNOWN cases only — a 4xx is ALLOW outright,
    because that is what the RFC says and there is no discretion in it.
    """
    cache = _cache((200, CHALLENGE_BODY), unreadable_is_allowed=False)
    assert cache.is_allowed("https://hellofresh.de/impressum", UA) is False


def test_robots_is_fetched_through_the_injected_transport_not_urllib():
    """The IP-leak regression.

    `urllib.request.urlopen` ignores the proxy pool, so every host in the run
    saw the operator's real address on the first request no matter how the run
    was proxied. The engine now hands its own fetcher in, and this asserts the
    class actually uses it.
    """
    seen: list[str] = []

    def fetcher(url: str):
        seen.append(url)
        return 200, REAL_PERMISSIVE

    cache = RobotsCache(fetcher=fetcher)
    cache.is_allowed("https://example.com/team", UA)
    assert seen == ["https://example.com/robots.txt"]


def test_a_transport_failure_expresses_no_policy():
    cache = RobotsCache(fetcher=lambda url: None)
    verdict, _ = cache.verdict("https://example.com/", UA)
    assert verdict is Verdict.UNKNOWN


def test_a_fetcher_that_raises_does_not_take_down_the_run():
    def boom(url: str):
        raise RuntimeError("connection reset")

    cache = RobotsCache(fetcher=boom)
    assert cache.is_allowed("https://example.com/", UA) is True


def test_the_verdict_is_cached_per_origin():
    calls: list[str] = []

    def fetcher(url: str):
        calls.append(url)
        return 200, REAL_PERMISSIVE

    cache = RobotsCache(fetcher=fetcher)
    for path in ("/a", "/b", "/c"):
        cache.is_allowed(f"https://example.com{path}", UA)
    assert len(calls) == 1, "robots.txt must be fetched once per origin, not once per URL"


def test_different_schemes_and_ports_are_different_origins():
    calls: list[str] = []

    def fetcher(url: str):
        calls.append(url)
        return 200, REAL_PERMISSIVE

    cache = RobotsCache(fetcher=fetcher)
    cache.is_allowed("https://example.com/a", UA)
    cache.is_allowed("https://example.com:8443/a", UA)
    assert len(calls) == 2
