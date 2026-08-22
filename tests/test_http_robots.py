"""Robots enforcement, and the one narrow way out of it.

Some APIs disallow anonymous crawlers in robots.txt and hand out credentials
instead. Holding those credentials is the authorisation, but that must not
become a reason to stop honouring robots anywhere else, so the exemption is a
per-host list rather than a switch.
"""

from __future__ import annotations

from job_scraper.http import HttpClient


class _DenyAll:
    def is_allowed(self, url: str, user_agent: str) -> bool:
        return False


def _client(**kwargs) -> HttpClient:
    client = HttpClient(
        user_agent="test",
        delay_seconds=0.0,
        obey_robots=True,
        cache_enabled=False,
        **kwargs,
    )
    client._robots = _DenyAll()
    return client


def test_a_disallowing_host_is_not_fetched():
    client = _client()
    try:
        assert client._robots_allow("https://example.com/anything") is False
    finally:
        client.close()


def test_an_exempted_host_is_fetched_even_though_robots_says_no():
    client = _client(robots_exempt_hosts=("api.example.com",))
    try:
        assert client._robots_allow("https://api.example.com/v1/thing") is True
    finally:
        client.close()


def test_the_exemption_does_not_leak_to_any_other_host():
    """The failure this guards against is a run that quietly stops honouring
    robots everywhere because one API needed an exception."""
    client = _client(robots_exempt_hosts=("api.example.com",))
    try:
        assert client._robots_allow("https://example.com/") is False
        assert client._robots_allow("https://other.example.com/") is False
        # A host that merely ends with the exempted name is a different host.
        assert client._robots_allow("https://evil-api.example.com/") is False
    finally:
        client.close()


def test_no_exemptions_are_configured_by_default():
    client = HttpClient(user_agent="test", delay_seconds=0.0, obey_robots=True, cache_enabled=False)
    try:
        assert client.robots_exempt_hosts == frozenset()
    finally:
        client.close()
