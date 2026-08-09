"""Job boards as a company seed, with the recruiter address as an anchor."""

from __future__ import annotations

from job_scraper.models import JobListing
from leadgen.seed.jobboard import companies_from_listings, looks_like_it_role


class StubHttp:
    def __init__(self, reachable=("acme.de",)):
        self.reachable = reachable

    def get(self, url, **kwargs):
        return (url, "<html></html>") if any(host in url for host in self.reachable) else None


def test_it_role_detection():
    for title in [
        "Backend Engineer",
        "DevOps Engineer",
        "Softwareentwickler",
        "Data Engineer",
        "IT Administrator",
        "Développeur Full Stack",
        "QA Engineer",
        "Cloud Architect",
    ]:
        assert looks_like_it_role(title) is True, title
    for title in ["Barista", "Warehouse Picker", "Sales Representative", "Krankenpfleger", ""]:
        assert looks_like_it_role(title) is False, title


def test_listings_collapse_to_one_company():
    listings = [
        JobListing(title="Backend Engineer", company="Acme GmbH", job_url="https://acme.de/jobs/1"),
        JobListing(title="Frontend Engineer", company="Acme GmbH", job_url="https://acme.de/jobs/2"),
    ]
    companies = companies_from_listings(listings, StubHttp())
    assert len(companies) == 1
    assert companies[0].name == "Acme GmbH"


def test_non_it_listings_are_skipped():
    listings = [JobListing(title="Barista", company="Cafe", job_url="https://cafe.de/jobs/1")]
    assert companies_from_listings(listings, StubHttp(reachable=("cafe.de",))) == []


def test_listings_without_a_company_are_skipped():
    listings = [JobListing(title="Backend Engineer", company="", job_url="https://acme.de/jobs/1")]
    assert companies_from_listings(listings, StubHttp()) == []


def test_recruiter_address_becomes_a_pattern_anchor():
    listing = JobListing(
        title="Backend Engineer",
        company="Acme GmbH",
        job_url="https://acme.de/jobs/1",
        recruiter_name="Anna Schmidt",
        recruiter_email="anna.schmidt@acme.de",
    )
    company = companies_from_listings([listing], StubHttp())[0]
    assert ("Anna Schmidt", "anna.schmidt@acme.de") in company.extra_anchors


def test_a_role_account_recruiter_address_is_not_an_anchor():
    listing = JobListing(
        title="Backend Engineer",
        company="Acme GmbH",
        job_url="https://acme.de/jobs/1",
        recruiter_name="Recruiting Team",
        recruiter_email="jobs@acme.de",
    )
    assert companies_from_listings([listing], StubHttp())[0].extra_anchors == []


def test_company_country_and_city_carry_through():
    listing = JobListing(
        title="DevOps Engineer",
        company="Acme GmbH",
        job_url="https://acme.de/jobs/1",
        country="DE",
        city="Munich",
    )
    company = companies_from_listings([listing], StubHttp())[0]
    assert company.country == "DE"
    assert company.city == "Munich"


def test_a_company_whose_domain_cannot_be_resolved_is_dropped():
    listing = JobListing(title="Backend Engineer", company="Ghost", job_url="https://ghost.xyz/jobs/1")
    assert companies_from_listings([listing], StubHttp(reachable=())) == []


def test_an_ats_only_company_is_dropped_rather_than_crawled():
    """No own-site URL anywhere means we would only find the ATS's Impressum."""
    listing = JobListing(
        title="Backend Engineer",
        company="Acme",
        job_url="https://boards.greenhouse.io/acme/jobs/1",
        apply_url="https://boards.greenhouse.io/acme/jobs/1/apply",
    )
    assert companies_from_listings([listing], StubHttp()) == []


def test_tech_stack_is_unioned_across_a_companys_listings():
    listings = [
        JobListing(
            title="Backend Engineer", company="Acme", job_url="https://acme.de/1", tech_stack="python"
        ),
        JobListing(title="SRE", company="Acme", job_url="https://acme.de/2", tech_stack="kubernetes"),
    ]
    company = companies_from_listings(listings, StubHttp())[0]
    assert "python" in company.tech_stack and "kubernetes" in company.tech_stack


def test_guess_domains_filters_by_dns_before_returning(monkeypatch):
    """A guess for a domain that does not exist must never reach the HTTP
    resolver: each one costs a full connect timeout there and milliseconds here."""
    from leadgen.seed import atsboards

    monkeypatch.setattr(atsboards, "_resolves_to_public", lambda host: host == "acme.de")
    assert atsboards.guess_domains("acme") == ["https://acme.de/"]


def test_guess_domains_is_capped(monkeypatch):
    from leadgen.seed import atsboards

    monkeypatch.setattr(atsboards, "_resolves_to_public", lambda host: True)
    assert len(atsboards.guess_domains("acme")) == atsboards.MAX_GUESSES


def test_guess_domains_tries_slug_and_company_name_variants(monkeypatch):
    from leadgen.seed import atsboards

    seen = []

    def fake(host):
        seen.append(host)
        return False

    monkeypatch.setattr(atsboards, "_resolves_to_public", fake)
    atsboards.guess_domains("trade-republic", "Trade Republic")
    assert "traderepublic.com" in seen
    assert "trade-republic.com" in seen
