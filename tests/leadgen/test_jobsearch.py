"""Cross-company job-search seeds: Workable and SmartRecruiters."""

from __future__ import annotations

import json

from leadgen.geo import search_name, search_names
from leadgen.seed.jobsearch import (
    _domain,
    search_smartrecruiters,
    search_workable,
    website_hints,
)


class FakeHttp:
    """Serves canned JSON by URL substring and records what was asked for."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        for fragment, payload in self.routes.items():
            if fragment in url:
                return (200, json.dumps(payload))
        return None


def _workable_job(company="Acme GmbH", website="https://acme.de", country="Germany", city="Berlin"):
    return {
        "title": "Backend Engineer",
        "company": {"title": company, "website": website},
        "location": {"city": city, "subregion": "Berlin", "countryName": country},
        "department": "Engineering",
        "description": "<p>Wir suchen</p>",
        "url": "https://jobs.workable.com/view/abc/backend",
    }


def test_workable_reads_the_country_from_the_api_rather_than_guessing():
    http = FakeHttp({"jobs.workable.com": {"jobs": [_workable_job()], "nextPageToken": None}})
    listings = search_workable(http, countries=["Germany"], keywords=["developer"])
    assert len(listings) == 1
    assert listings[0].country == "DE"
    assert listings[0].city == "Berlin"
    assert listings[0].company_website == "https://acme.de"


def test_workable_follows_the_page_token_until_it_runs_out():
    pages = [
        {"jobs": [_workable_job(company="One")], "nextPageToken": "t1"},
        {"jobs": [_workable_job(company="Two")], "nextPageToken": "t2"},
        {"jobs": [_workable_job(company="Three")], "nextPageToken": None},
    ]

    class Paging(FakeHttp):
        def get(self, url):
            self.calls.append(url)
            return (200, json.dumps(pages[min(len(self.calls) - 1, len(pages) - 1)]))

    http = Paging({})
    listings = search_workable(http, countries=["Germany"], keywords=["dev"])
    assert [listing.company for listing in listings] == ["One", "Two", "Three"]
    # The token from each page must be the one carried into the next request.
    assert "pageToken=t1" in http.calls[1] and "pageToken=t2" in http.calls[2]


def test_workable_stops_at_max_pages_so_one_pairing_cannot_monopolise_the_run():
    endless = {"jobs": [_workable_job()], "nextPageToken": "always-more"}
    http = FakeHttp({"jobs.workable.com": endless})
    listings = search_workable(http, countries=["Germany"], keywords=["dev"], max_pages=3)
    assert len(listings) == 3
    assert len(http.calls) == 3


def test_workable_pairs_every_country_with_every_keyword():
    http = FakeHttp({"jobs.workable.com": {"jobs": [_workable_job()], "nextPageToken": None}})
    search_workable(http, countries=["Germany", "France"], keywords=["dev", "data"])
    assert len(http.calls) == 4
    assert any("location=France" in call and "query=data" in call for call in http.calls)


def test_workable_skips_a_job_with_no_company_name():
    job = _workable_job()
    job["company"] = {"title": "", "website": "https://x.de"}
    http = FakeHttp({"jobs.workable.com": {"jobs": [job], "nextPageToken": None}})
    assert search_workable(http, countries=["Germany"], keywords=["dev"]) == []


def test_requests_can_be_paced_between_pages_but_not_before_the_first():
    """This host blocked the run after ~1500 requests. A seed that gets itself
    blocked is worth less than a slower one that does not."""
    pages = [
        {"jobs": [_workable_job()], "nextPageToken": "t1"},
        {"jobs": [_workable_job()], "nextPageToken": None},
    ]

    class Paging(FakeHttp):
        def get(self, url):
            self.calls.append(url)
            return (200, json.dumps(pages[min(len(self.calls) - 1, len(pages) - 1)]))

    waits: list[float] = []
    search_workable(
        Paging({}), countries=["Germany"], keywords=["dev"], delay_seconds=1.5, sleep=waits.append
    )
    assert waits == [1.5]


def test_a_city_query_labels_the_country_from_the_result_not_the_query():
    """Searching "Berlin" must not assume Germany — the API states the country,
    and a city listed under a neighbour has to come out right."""
    http = FakeHttp(
        {"jobs.workable.com": {"jobs": [_workable_job(country="Austria")], "nextPageToken": None}}
    )
    listings = search_workable(http, countries=["Berlin"], keywords=["dev"])
    assert listings[0].country == "AT"


def test_no_pacing_by_default():
    waits: list[float] = []
    http = FakeHttp({"jobs.workable.com": {"jobs": [_workable_job()], "nextPageToken": None}})
    search_workable(http, countries=["Germany"], keywords=["dev"], sleep=waits.append)
    assert waits == []


def test_a_rate_limited_host_is_reported_not_mistaken_for_an_empty_result(caplog):
    """Every query returning nothing reads exactly like a vocabulary that
    matches nothing. The run must say which it is."""

    class Blocked:
        def get(self, url):
            return None

    with caplog.at_level("WARNING"):
        search_workable(Blocked(), countries=["Germany", "France"], keywords=["dev", "data"])
    assert "rate-limiting" in caplog.text


def test_a_normal_run_does_not_cry_rate_limit(caplog):
    http = FakeHttp({"jobs.workable.com": {"jobs": [_workable_job()], "nextPageToken": None}})
    with caplog.at_level("WARNING"):
        search_workable(http, countries=["Germany"], keywords=["dev"])
    assert "rate-limiting" not in caplog.text


def test_workable_survives_a_non_json_response():
    class Broken:
        def get(self, url):
            return (200, "<html>rate limited</html>")

    assert search_workable(Broken(), countries=["Germany"], keywords=["dev"]) == []


def test_an_unknown_country_name_leaves_the_code_empty_rather_than_wrong():
    """A country the geo table cannot name must not be silently mislabelled."""
    http = FakeHttp(
        {"jobs.workable.com": {"jobs": [_workable_job(country="Ruritania")], "nextPageToken": None}}
    )
    assert search_workable(http, countries=["Ruritania"], keywords=["dev"])[0].country == ""


def test_smartrecruiters_takes_the_country_code_from_the_response():
    payload = {
        "content": [
            {
                "name": "Softwareentwickler (m/w/d)",
                "company": {"identifier": "Bosch1", "name": "Bosch"},
                "location": {"city": "Stuttgart", "region": "BW", "country": "de"},
                "shortLocation": "Stuttgart, Germany",
                "applyUrl": "https://jobs.smartrecruiters.com/Bosch1/123",
            }
        ]
    }
    http = FakeHttp({"sr-jobs/search": payload})
    listings = search_smartrecruiters(http, keywords=["Softwareentwickler"])
    assert listings[0].country == "DE"
    assert listings[0].company == "Bosch"
    assert listings[0].city == "Stuttgart"


def test_smartrecruiters_falls_back_to_the_location_text_when_the_code_is_missing():
    payload = {
        "content": [
            {
                "name": "Dev",
                "company": {"identifier": "x", "name": "X"},
                "location": {"city": "Vienna"},
                "shortLocation": "Vienna, Austria",
                "applyUrl": "https://jobs.smartrecruiters.com/x/1",
            }
        ]
    }
    assert search_smartrecruiters(FakeHttp({"sr-jobs": payload}), keywords=["dev"])[0].country == "AT"


def test_smartrecruiters_sends_one_query_per_keyword():
    http = FakeHttp({"sr-jobs": {"content": []}})
    search_smartrecruiters(http, keywords=["Entwickler", "développeur"])
    assert len(http.calls) == 2
    assert any("keyword=Entwickler" in call for call in http.calls)


def test_website_hints_are_keyed_the_way_companies_are_grouped():
    """The hint lookup and the company grouping must agree on what is one company."""
    listings = search_workable(
        FakeHttp(
            {"jobs.workable.com": {"jobs": [_workable_job(company="Acme GmbH")], "nextPageToken": None}}
        ),
        countries=["Germany"],
        keywords=["dev"],
    )
    assert website_hints(listings) == {"acme gmbh": ["acme.de"]}


def test_domain_strips_scheme_path_and_www():
    assert _domain("https://www.Acme.de/careers?x=1") == "acme.de"
    assert _domain("") == ""


def test_search_name_inverts_to_the_english_spelling():
    assert search_name("DE") == "Germany"
    assert search_name("FR") == "France"
    assert search_name("zz") == ""


def test_search_names_skips_codes_it_cannot_name():
    assert search_names(frozenset({"DE", "XX"})) == ["Germany"]
