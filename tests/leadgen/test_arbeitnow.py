"""The Arbeitnow German job feed as a seed."""

from __future__ import annotations

import json

from leadgen.seed.arbeitnow import fetch


def _job(company="Blue Incite GmbH", location="Munich", title="Softwareentwickler"):
    return {
        "company_name": company,
        "title": title,
        "location": location,
        "description": "<p>Ihre Ansprechpartnerin: Anna Müller</p>",
        "tags": ["python", "django"],
        "job_types": ["full-time"],
        "url": "https://www.arbeitnow.com/jobs/companies/x/abc",
    }


class FakeHttp:
    """Serves a fixed number of pages, then reports no next link."""

    def __init__(self, pages: int, rows_per_page: int = 2):
        self.pages = pages
        self.rows_per_page = rows_per_page
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        index = len(self.calls)
        body = {
            "data": [_job(company=f"Firma {index}-{n} GmbH") for n in range(self.rows_per_page)],
            "links": (
                {"next": f"https://www.arbeitnow.com/api/job-board-api?page={index + 1}"}
                if index < self.pages
                else {}
            ),
        }
        return (200, json.dumps(body))


def test_listings_carry_the_german_ad_text_and_a_country():
    http = FakeHttp(pages=1, rows_per_page=1)
    listings = fetch(http, max_pages=5, sleep=lambda _: None)
    assert len(listings) == 1
    assert listings[0].company == "Firma 1-0 GmbH"
    assert listings[0].country == "DE"
    assert "Ansprechpartnerin" in listings[0].description
    assert listings[0].source_ats == "arbeitnow"


def test_it_follows_the_next_link_until_it_runs_out():
    http = FakeHttp(pages=4, rows_per_page=2)
    listings = fetch(http, max_pages=10, sleep=lambda _: None)
    assert len(listings) == 8
    assert len(http.calls) == 4


def test_max_pages_bounds_the_walk():
    http = FakeHttp(pages=50, rows_per_page=1)
    fetch(http, max_pages=3, sleep=lambda _: None)
    assert len(http.calls) == 3


def test_pages_are_paced_but_the_first_request_is_not_delayed():
    """The API refuses two requests a second. A single-page caller should still
    not pay for a wait it never needed."""
    waits: list[float] = []
    http = FakeHttp(pages=3, rows_per_page=1)
    fetch(http, max_pages=3, delay_seconds=5.0, sleep=waits.append)
    assert waits == [5.0, 5.0]


def test_a_rate_limited_page_ends_the_walk_rather_than_looping():
    class Blocked:
        def __init__(self):
            self.calls = 0

        def get(self, url):
            self.calls += 1
            return None

    http = Blocked()
    assert fetch(http, max_pages=10, sleep=lambda _: None) == []
    assert http.calls == 1


def test_a_non_json_page_ends_the_walk():
    class Garbage:
        def get(self, url):
            return (200, "<html>rate limited</html>")

    assert fetch(Garbage(), max_pages=5, sleep=lambda _: None) == []


def test_a_row_without_a_company_is_skipped():
    class OneBad:
        def get(self, url):
            return (200, json.dumps({"data": [{"title": "Dev", "company_name": ""}], "links": {}}))

    assert fetch(OneBad(), max_pages=1, sleep=lambda _: None) == []
