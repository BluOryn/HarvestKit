"""SSRF guard: scraped URLs must not reach internal address space."""

from __future__ import annotations

import pytest

from leadgen import net_guard
from leadgen.net_guard import is_safe_url


@pytest.fixture(autouse=True)
def _clear_cache():
    net_guard._resolves_to_public.cache_clear()
    yield
    net_guard._resolves_to_public.cache_clear()


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://[fd00:ec2::254]/latest/meta-data/",  # AWS IMDSv6
        "http://127.0.0.1:8080/admin",
        "http://localhost/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
def test_internal_targets_are_refused(url):
    assert is_safe_url(url) is False


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "gopher://x/", "ftp://example.com/", "", "not-a-url", "http://"],
)
def test_non_http_and_malformed_urls_are_refused(url):
    assert is_safe_url(url) is False


@pytest.mark.parametrize("host", ["metadata.google.internal", "metadata.goog", "instance-data.ec2.internal"])
def test_named_metadata_hosts_are_refused(host):
    assert is_safe_url(f"http://{host}/computeMetadata/v1/") is False


def test_a_public_address_is_allowed(monkeypatch):
    monkeypatch.setattr(net_guard, "_resolves_to_public", lambda host: True)
    assert is_safe_url("https://acme.de/impressum") is True


def test_a_hostname_resolving_to_a_private_address_is_refused(monkeypatch):
    """DNS rebinding: the name looks fine, the address does not."""
    monkeypatch.setattr(net_guard, "_resolves_to_public", lambda host: False if host == "evil.test" else True)
    assert is_safe_url("https://evil.test/team") is False


def test_a_host_that_does_not_resolve_is_allowed(monkeypatch):
    """The fetch cannot reach anything, and refusing breaks offline fixtures."""
    monkeypatch.setattr(net_guard, "_resolves_to_public", lambda host: None)
    assert is_safe_url("https://acme-fixture.test/team") is True


def test_a_public_literal_ip_is_allowed():
    assert is_safe_url("http://8.8.8.8/") is True


def test_trailing_dot_hostname_is_normalised(monkeypatch):
    seen = []

    def fake(host):
        seen.append(host)
        return True

    monkeypatch.setattr(net_guard, "_resolves_to_public", fake)
    is_safe_url("https://acme.de./team")
    assert seen == ["acme.de"]


def test_cascade_refuses_an_internal_url():
    """End of the chain: the guard is actually wired into the fetch path."""
    from leadgen.person.cascade import resolve_people

    class Recorder:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return None

    http = Recorder()
    resolve_people("127.0.0.1", "DE", http, max_pages=2, use_sitemap=False)
    assert http.calls == [], "loopback domain must never be fetched"


def test_domain_resolution_refuses_an_internal_hint():
    from leadgen.company.domain import resolve_domain

    class Recorder:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return url, "<html></html>"

    http = Recorder()
    assert resolve_domain("Evil", ["http://169.254.169.254/"], http) == ""
    assert http.calls == []
