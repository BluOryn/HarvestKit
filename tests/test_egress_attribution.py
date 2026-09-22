"""Who gets blamed when a fetch fails, and what happens when the pool is empty.

Two defects here were quiet and expensive. A host refusing us was charged to the
proxy, so three walled pages on one stubborn site cooled down every proxy in the
pool and the whole run silently fell back to a direct connection — from the
machine whose address was the reason the pool existed. And the "per-proxy cookie
jar" isolated nothing, because a single shared `requests.Session` merges every
jar it is handed into its own and replays it through every exit.
"""

from __future__ import annotations

import time

from job_scraper.http import _bind_address, _looks_like_index, _ProxyPool, _SourceBoundAdapter


def pool(**kwargs):
    return _ProxyPool(
        ["http://a.example:8080", "http://b.example:8080", "http://c.example:8080"],
        max_failures=3,
        cooldown_seconds=60,
        **kwargs,
    )


# ------------------------------------------------------------- attribution


def test_one_stubborn_host_does_not_burn_the_pool():
    """The failure that made every run fall back to a direct connection."""
    proxies = pool()
    entry = proxies.acquire()
    for _ in range(10):
        proxies.report_block(entry, "hellofresh.de")
    assert entry["dead_until"] == 0.0, "one host refusing us says nothing about the proxy"


def test_several_unrelated_domains_refusing_the_same_proxy_does_cool_it_down():
    """That pattern is evidence about the exit IP, not about any one site."""
    proxies = pool()
    entry = proxies.acquire()
    for domain in ("hellofresh.de", "zalando.de", "sap.com"):
        proxies.report_block(entry, domain)
    assert entry["dead_until"] > time.time()


def test_a_transport_failure_is_charged_straight_to_the_proxy():
    proxies = pool()
    entry = proxies.acquire()
    for _ in range(3):
        proxies.report_failure(entry)
    assert entry["dead_until"] > time.time()


def test_a_success_clears_the_failure_count():
    proxies = pool()
    entry = proxies.acquire()
    proxies.report_failure(entry)
    proxies.report_failure(entry)
    proxies.report_success(entry)
    proxies.report_failure(entry)
    assert entry["dead_until"] == 0.0


# ----------------------------------------------------------- fail closed


def test_an_exhausted_pool_is_visible_as_exhausted():
    proxies = pool()
    for entry in proxies.entries:
        entry["dead_until"] = time.time() + 60
    assert proxies.exhausted is True


def test_no_proxies_configured_is_not_exhaustion():
    """Direct is the intended egress there, not a leak."""
    assert _ProxyPool([]).exhausted is False


def test_require_proxy_is_off_unless_asked_for():
    assert pool().require_proxy is False
    assert pool(require_proxy=True).require_proxy is True


# --------------------------------------------------------------- cookie jars


def test_each_proxy_gets_its_own_jar():
    proxies = pool()
    first, second = proxies.entries[0], proxies.entries[1]
    proxies.jar_for(first).set("session", "aaa", domain="example.com", path="/")
    other = proxies.jar_for(second)
    assert "session" not in other
    assert proxies.jar_for(first) is not other


# -------------------------------------------------------------- bind:// URLs


def test_bind_entries_name_a_local_address_not_a_proxy():
    assert _bind_address("bind://2a01:4f8:c17::a1") == "2a01:4f8:c17::a1"
    assert _bind_address("bind://[2a01:4f8:c17::a1]") == "2a01:4f8:c17::a1"
    assert _bind_address("socks5h://127.0.0.1:1080") == ""
    assert _bind_address("") == ""


def test_a_bound_adapter_carries_the_source_address_into_the_pool_manager():
    adapter = _SourceBoundAdapter("2a01:4f8:c17::a1")
    assert adapter._source == ("2a01:4f8:c17::a1", 0)
    # init_poolmanager already ran in __init__; the manager must have it.
    assert adapter.poolmanager.connection_pool_kw["source_address"] == ("2a01:4f8:c17::a1", 0)


# ------------------------------------------------------------- cache freshness


def test_index_urls_are_recognised_so_a_daily_run_does_not_reread_a_stale_page():
    for url in (
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "https://api.lever.co/v0/postings/acme?mode=json",
        "https://finn.no/job/fulltime/search.html?page=2",
        "https://acme.de/karriere",
        "https://acme.de/jobs/2",
    ):
        assert _looks_like_index(url) is True, url


def test_detail_pages_keep_the_long_cache_which_is_where_it_earns_its_keep():
    for url in (
        "https://acme.de/karriere/stelle/12345",
        "https://x.de/job/senior-developer-12345",
        "https://acme.com/impressum",
        "https://acme.com/",
    ):
        assert _looks_like_index(url) is False, url
