"""A company we were blocked from reading is not a company that names nobody.

This is the reporting half of the same defect. `resolve_people` returned an
empty list whether the site had no people on it or a WAF had refused every
request, and `process_companies` filed both under `no_person_found`. A run
could therefore report thousands of companies "not naming anyone" while the
real answer was that it never saw their pages — and the runbook's advice for a
high `no_person_found` count was to go and look at the team-page parser.
"""

from __future__ import annotations

from job_scraper.transport import FetchResponse, Outcome
from leadgen.person.cascade import Reachability, resolve_people_detailed


class _Http:
    """A client whose every fetch returns one prepared outcome."""

    def __init__(self, outcome: Outcome, text: str = "") -> None:
        self.outcome = outcome
        self.text = text
        self.calls = 0

    def fetch(self, url, *args, **kwargs) -> FetchResponse:
        self.calls += 1
        return FetchResponse(
            url=url,
            final_url=url,
            status=403 if self.outcome is Outcome.BLOCKED else 200,
            text=self.text,
            outcome=self.outcome,
            transport="test",
        )

    def get(self, url, *args, **kwargs):  # pragma: no cover - fetch is preferred
        result = self.fetch(url)
        return (result.final_url, result.text) if result.ok else None


EMPTY_BUT_REAL = (
    "<!DOCTYPE html><html><head><title>Über uns</title></head>"
    "<body><main><p>Wir sind ein Unternehmen.</p></main></body></html>"
)


def test_a_walled_company_is_recorded_as_blocked_not_as_empty():
    http = _Http(Outcome.BLOCKED)
    people, reach = resolve_people_detailed("walled.de", "DE", http, max_pages=3, max_person_pages=0)
    assert people == []
    assert reach.blocked > 0
    assert reach.saw_content is False
    assert reach.walled is True


def test_a_company_whose_site_simply_names_nobody_is_not_reported_as_blocked():
    http = _Http(Outcome.OK, EMPTY_BUT_REAL)
    people, reach = resolve_people_detailed("open.de", "DE", http, max_pages=3, max_person_pages=0)
    assert people == []
    assert reach.saw_content is True
    assert reach.walled is False, "this one really did answer us"


def test_a_dead_domain_is_neither_blocked_nor_empty():
    http = _Http(Outcome.ERROR)
    _, reach = resolve_people_detailed("dead.de", "DE", http, max_pages=2, max_person_pages=0)
    assert reach.walled is False
    assert reach.saw_content is False
    assert reach.errors > 0


def test_a_robots_refusal_counts_as_walled():
    """We were told no before asking, so we learned nothing about the site."""
    http = _Http(Outcome.ROBOTS_DENIED)
    _, reach = resolve_people_detailed("strict.de", "DE", http, max_pages=2, max_person_pages=0)
    assert reach.robots_denied > 0
    assert reach.walled is True


def test_one_page_getting_through_is_enough_to_stop_calling_it_walled():
    reach = Reachability(fetched=1, blocked=7)
    assert reach.walled is False, "we saw the site; our conclusions about it are our own"


def test_the_pipeline_counts_a_walled_company_separately(tmp_path, monkeypatch):
    from leadgen import pipeline
    from leadgen.assemble import CompanyContext
    from leadgen.checkpoint import Checkpoint

    monkeypatch.setattr(
        pipeline,
        "resolve_people_detailed",
        lambda *a, **k: ([], Reachability(blocked=4)),
    )
    checkpoint = Checkpoint(str(tmp_path / "cp.sqlite"))
    try:
        funnel = pipeline.process_companies(
            [CompanyContext(domain="walled.de", name="Walled", country="DE")],
            http=None,
            checkpoint=checkpoint,
            concurrency=1,
            smtp=False,
        )
    finally:
        checkpoint.close()
    assert funnel["blocked_no_pages_seen"] == 1
    assert funnel["no_person_found"] == 0, (
        "counting a bot wall as 'no person found' is the claim that sends the "
        "next person to debug an innocent parser"
    )


def test_the_pipeline_still_counts_a_genuinely_empty_site_as_empty(tmp_path, monkeypatch):
    from leadgen import pipeline
    from leadgen.assemble import CompanyContext
    from leadgen.checkpoint import Checkpoint

    monkeypatch.setattr(
        pipeline,
        "resolve_people_detailed",
        lambda *a, **k: ([], Reachability(fetched=5)),
    )
    checkpoint = Checkpoint(str(tmp_path / "cp.sqlite"))
    try:
        funnel = pipeline.process_companies(
            [CompanyContext(domain="open.de", name="Open", country="DE")],
            http=None,
            checkpoint=checkpoint,
            concurrency=1,
            smtp=False,
        )
    finally:
        checkpoint.close()
    assert funnel["no_person_found"] == 1
    assert funnel["blocked_no_pages_seen"] == 0
