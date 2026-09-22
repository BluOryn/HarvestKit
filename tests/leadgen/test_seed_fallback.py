"""One dead seed must not be the whole run.

Workable rate-limits an address off entirely after a few thousand requests —
measured live, an hour of solid 429s. A run whose only seed is Workable then
harvests nothing, and "nothing" has looked exactly like a thin market for far
too long. SmartRecruiters is a completely independent provider that does not
share that reputation.
"""

from __future__ import annotations

import json

from leadgen.seed import jobsearch


class Rejecting:
    """Everything 429s, which `http.get` surfaces as None."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url, *args, **kwargs):
        self.calls.append(url)
        return None


class OnlySmartRecruiters:
    """Workable refuses; SmartRecruiters answers."""

    SEARCH = {
        "totalFound": 2,
        "content": [
            {
                "name": "Softwareentwickler (m/w/d)",
                "company": {"identifier": "Beispiel", "name": "Beispiel GmbH"},
                "location": {"city": "Berlin", "country": "de"},
                "applyUrl": "https://jobs.smartrecruiters.com/Beispiel/1",
            }
        ],
    }
    POSTINGS = {"totalFound": 1, "content": []}

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url, *args, **kwargs):
        self.calls.append(url)
        if "workable" in url:
            return None
        if "sr-jobs/search" in url:
            return url, json.dumps(self.SEARCH)
        if "/postings" in url:
            return url, json.dumps(self.POSTINGS)
        return None


def test_a_rate_limited_workable_falls_back_rather_than_returning_nothing():
    http = OnlySmartRecruiters()
    listings = jobsearch.search_all(
        http,
        countries=["Germany"],
        keywords=["Softwareentwickler"],
        max_pages=1,
        concurrency=1,
    )
    assert listings, "the run must still get a seed when one provider is refusing us"
    assert listings[0].company == "Beispiel GmbH"
    assert any("sr-jobs/search" in call for call in http.calls)


def test_the_fallback_does_not_fire_when_workable_is_working(monkeypatch):
    """It is a fallback, not a second pass: doubling the request count against
    a provider that is already answering is how the *other* one gets blocked."""
    seen: list[list[str]] = []

    def fake_smartrecruiters(http, *, keywords, **kwargs):
        seen.append(list(keywords))
        return []

    def fake_workable(http, *, countries, keywords, **kwargs):
        from job_scraper.models import JobListing

        return [JobListing(title="Dev", company="Acme", job_url="https://acme.de/1")]

    monkeypatch.setattr(jobsearch, "search_smartrecruiters", fake_smartrecruiters)
    monkeypatch.setattr(jobsearch, "search_workable", fake_workable)

    listings = jobsearch.search_all(
        http=Rejecting(), countries=["Germany"], keywords=["developer"], concurrency=1
    )
    assert len(listings) == 1
    assert seen == [], "SmartRecruiters must not be queried when Workable answered"


def test_explicit_multilingual_keywords_still_win(monkeypatch):
    seen: list[list[str]] = []

    def fake_smartrecruiters(http, *, keywords, **kwargs):
        seen.append(list(keywords))
        return []

    monkeypatch.setattr(jobsearch, "search_smartrecruiters", fake_smartrecruiters)
    jobsearch.search_all(
        http=Rejecting(),
        countries=["Germany"],
        keywords=["developer"],
        smartrecruiters_keywords=["Softwareentwickler"],
        concurrency=1,
    )
    assert seen == [["Softwareentwickler"]]
