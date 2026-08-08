"""Resolving a company to its own website, not its ATS or an aggregator."""

from __future__ import annotations

from leadgen.company.domain import is_company_site, resolve_domain


class StubHttp:
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for fragment, body in self.pages.items():
            if fragment in url:
                return url, body
        return None


def test_ats_and_aggregator_hosts_are_not_company_sites():
    for url in [
        "https://boards.greenhouse.io/acme",
        "https://jobs.lever.co/acme",
        "https://acme.jobs.personio.de/",
        "https://www.linkedin.com/company/acme",
        "https://www.indeed.com/cmp/Acme",
        "https://acme.recruitee.com/",
        "https://apply.workable.com/acme/",
        "https://acme.teamtailor.com/",
    ]:
        assert is_company_site(url) is False, url


def test_a_real_company_site_is_accepted():
    assert is_company_site("https://www.acme.de/karriere") is True


def test_a_bare_or_broken_url_is_not_a_company_site():
    assert is_company_site("") is False
    assert is_company_site("not-a-url") is False


def test_hints_are_preferred_over_guessing():
    http = StubHttp(pages={"acme.de": "<html><title>Acme</title></html>"})
    domain = resolve_domain("Acme GmbH", ["https://boards.greenhouse.io/acme", "https://acme.de/jobs"], http)
    assert domain == "acme.de"


def test_ats_hints_are_skipped():
    http = StubHttp(pages={"acme.de": "<html></html>"})
    resolve_domain("Acme GmbH", ["https://boards.greenhouse.io/acme"], http)
    assert not any("greenhouse" in call for call in http.calls)


def test_unreachable_candidates_yield_empty_string():
    assert resolve_domain("Nowhere Ltd", [], StubHttp()) == ""


def test_www_prefix_is_normalised_away():
    http = StubHttp(pages={"acme.de": "<html></html>"})
    assert resolve_domain("Acme", ["https://www.acme.de/about"], http) == "acme.de"


def test_a_host_is_probed_only_once_across_many_hints():
    http = StubHttp(pages={})
    resolve_domain("Acme", ["https://acme.de/a", "https://acme.de/b", "https://www.acme.de/c"], http)
    # https then http for one host — not six calls.
    assert len(http.calls) == 2
