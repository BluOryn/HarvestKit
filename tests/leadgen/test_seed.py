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


def test_the_stated_country_is_the_modal_one_not_the_first_seen():
    """A cross-company search sweeps countries in turn, so the same employer
    arrives once per country it advertises in. Search order must not decide."""
    listings = [
        JobListing(title="Backend Engineer", company="Acme GmbH", job_url="https://acme.de/1", country="IE"),
        JobListing(title="Data Engineer", company="Acme GmbH", job_url="https://acme.de/2", country="DE"),
        JobListing(title="Cloud Architect", company="Acme GmbH", job_url="https://acme.de/3", country="DE"),
    ]
    assert companies_from_listings(listings, StubHttp())[0].country == "DE"


def test_a_stated_country_outranks_one_inferred_from_location_text():
    listings = [
        JobListing(
            title="Backend Engineer",
            company="Acme GmbH",
            job_url="https://acme.de/1",
            location="Remote - Paris, France",
            country="DE",
        ),
    ]
    assert companies_from_listings(listings, StubHttp())[0].country == "DE"


def test_the_country_falls_back_to_location_text_when_none_is_stated():
    listings = [
        JobListing(
            title="Backend Engineer", company="Acme GmbH", job_url="https://acme.de/1", location="Munich"
        ),
        JobListing(
            title="Data Engineer", company="Acme GmbH", job_url="https://acme.de/2", location="Munich"
        ),
        JobListing(
            title="DevOps Engineer", company="Acme GmbH", job_url="https://acme.de/3", location="Austin"
        ),
    ]
    assert companies_from_listings(listings, StubHttp())[0].country == "DE"


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


def test_a_legal_name_is_reduced_to_the_trading_name():
    """A German board reports the entity, not the brand. Guessing from it
    verbatim yields blueincitegmbhacompanyofallianz.de, which is nobody."""
    from leadgen.seed.atsboards import _trading_name

    assert _trading_name("Blue Incite GmbH (A company of Allianz)") == "blue incite"
    assert _trading_name("ottonova Holding AG") == "ottonova"
    assert _trading_name("Muster Software GmbH & Co. KG") == "muster software"
    assert _trading_name("Acme B.V.") == "acme"
    assert _trading_name("Sp. z o.o. Przyklad") == "przyklad"


def test_the_trading_name_is_tried_before_the_raw_legal_name(monkeypatch):
    from leadgen.seed import atsboards

    seen = []

    def fake(host):
        seen.append(host)
        return False

    monkeypatch.setattr(atsboards, "_resolves_to_public", fake)
    atsboards.guess_domains("", "Blue Incite GmbH (A company of Allianz)")
    assert "blueincite.com" in seen
    assert seen.index("blueincite.com") < seen.index("blueincitegmbhacompanyofallianz.com")


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


def test_country_is_derived_from_the_listing_location():
    listings = [
        JobListing(
            title="Backend Engineer", company="Acme", job_url="https://acme.de/1", location="Munich, Germany"
        ),
    ]
    assert companies_from_listings(listings, StubHttp())[0].country == "DE"


def test_the_modal_country_wins_over_the_first_one():
    """One remote US role must not relabel a Berlin company."""
    listings = [
        JobListing(
            title="SRE", company="Acme", job_url="https://acme.de/1", location="Remote - United States"
        ),
        JobListing(title="Backend Engineer", company="Acme", job_url="https://acme.de/2", location="Berlin"),
        JobListing(title="Data Engineer", company="Acme", job_url="https://acme.de/3", location="Munich"),
    ]
    assert companies_from_listings(listings, StubHttp())[0].country == "DE"


def test_companies_outside_the_requested_geography_are_never_crawled():
    """Geography must filter before domain resolution, not after: resolution and
    the person cascade are the expensive steps and there is no point spending
    them on a company the cut will discard."""
    probed = []

    class Recorder:
        def get(self, url, **kwargs):
            probed.append(url)
            return url, "<html></html>"

    listings = [
        JobListing(
            title="Backend Engineer",
            company="Berlin Co",
            job_url="https://berlinco.de/1",
            location="Berlin, Germany",
        ),
        JobListing(
            title="Backend Engineer",
            company="Texas Co",
            job_url="https://texasco.com/1",
            location="Austin, United States",
        ),
    ]
    companies = companies_from_listings(listings, Recorder(), countries=frozenset({"DE"}))
    assert [c.name for c in companies] == ["Berlin Co"]
    assert not any("texasco" in url for url in probed), "US company must not be probed at all"


PERSONIO_JSON = """[
 {"id":2547139,"name":"Senior Software Engineer (m/w/d)","employment_type":"Festanstellung",
  "seniority":"Berufserfahren","keywords":"Python,Kubernetes","office":"München",
  "offices":["München"],"department":"Engineering","subcompany":"ottonova Holding AG - 9680"},
 {"id":2626047,"name":"Werkstudent Rechtsabteilung","employment_type":"Praktikum",
  "seniority":"Studierende","keywords":"","office":"München","offices":["München"],
  "department":"Legal","subcompany":"ottonova Holding AG - 9680"}
]"""


class PersonioHttp:
    def get(self, url, **kwargs):
        return (url, PERSONIO_JSON) if "personio.de" in url else None


def test_personio_board_parses_into_listings():
    from leadgen.seed.atsboards import fetch_boards

    listings = fetch_boards([("personio", "ottonova")], PersonioHttp())
    assert len(listings) == 2
    engineer = listings[0]
    assert engineer.title.startswith("Senior Software Engineer")
    assert engineer.city == "München"
    assert engineer.department == "Engineering"


def test_personio_prefers_the_legal_entity_over_the_slug():
    """subcompany carries the real company name; the trailing Personio account
    number is not part of it."""
    from leadgen.seed.atsboards import fetch_boards

    assert fetch_boards([("personio", "ottonova")], PersonioHttp())[0].company == "ottonova Holding AG"


def test_personio_listings_resolve_to_a_german_country_code():
    from leadgen.seed.atsboards import fetch_boards

    listings = fetch_boards([("personio", "ottonova")], PersonioHttp())
    companies = companies_from_listings(
        listings,
        StubHttp(reachable=("ottonova.de",)),
        guess_domains=lambda slug, name="", country="": [f"https://{slug}.de/"],
    )
    assert companies and companies[0].country == "DE"


def test_the_board_slug_beats_the_legal_name_when_guessing_a_domain():
    """Personio reports 'ottonova Holding AG', whose site is ottonova.de — not
    ottonovaholdingag.de. The slug in the ATS URL is the better basis."""
    from leadgen.seed.atsboards import fetch_boards

    seen = []

    def guesser(slug, name="", country=""):
        seen.append(slug)
        return [f"https://{slug}.de/"]

    companies_from_listings(
        fetch_boards([("personio", "ottonova")], PersonioHttp()),
        StubHttp(reachable=("ottonova.de",)),
        guess_domains=guesser,
    )
    assert seen == ["ottonova"], f"guessed from {seen}, not the slug"


def test_an_unknown_board_kind_is_reported_not_crashed():
    from leadgen.seed.atsboards import fetch_boards

    assert fetch_boards([("nosuchats", "acme")], PersonioHttp()) == []
