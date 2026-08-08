"""Cascade orchestration: fetch candidate pages, run strategies, merge."""

from __future__ import annotations

from leadgen.person.cascade import merge_hits, resolve_people
from leadgen.person.hit import PersonHit

IMPRESSUM = """
<html><body><p>Gesch&auml;ftsf&uuml;hrer: Anna Schmidt</p>
<p>E-Mail: anna.schmidt@acme.de</p></body></html>
"""
TEAM = """
<html><body><main>
<div><h3>Anna Schmidt</h3><p>Chief Technology Officer</p></div>
<div><h3>Peter Wolf</h3><p>Head of HR</p></div>
</main></body></html>
"""


class StubHttp:
    def __init__(self, pages):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for fragment, body in self.pages.items():
            if url.endswith(fragment):
                return url, body
        return None


def test_cascade_visits_localised_paths_and_collects_people():
    http = StubHttp({"/impressum": IMPRESSUM, "/team": TEAM})
    hits = resolve_people("acme.de", "DE", http)
    assert {h.name for h in hits} == {"Anna Schmidt", "Peter Wolf"}


def test_cascade_stops_at_max_pages():
    http = StubHttp({})
    resolve_people("acme.de", "DE", http, max_pages=3)
    assert len(http.calls) == 3


def test_cascade_needs_a_domain():
    assert resolve_people("", "DE", StubHttp({})) == []


def test_a_dead_site_yields_nothing_without_raising():
    assert resolve_people("dead.de", "DE", StubHttp({})) == []


def test_an_http_client_that_raises_does_not_abort_the_cascade():
    class Exploding:
        def get(self, url, **kwargs):
            raise RuntimeError("connection reset")

    assert resolve_people("acme.de", "DE", Exploding()) == []


def test_merge_prefers_the_richer_record_for_the_same_person():
    sparse = PersonHit(name="Anna Schmidt", role="CTO", strategy="team", source_url="u1")
    rich = PersonHit(
        name="Anna Schmidt",
        role="Geschäftsführer",
        email="a@acme.de",
        strategy="impressum",
        source_url="u2",
    )
    merged = merge_hits([sparse, rich])
    assert len(merged) == 1
    assert merged[0].email == "a@acme.de"
    # A target-role title beats a generic legal one — CTO is what the buyer wants.
    assert merged[0].role == "CTO"


def test_merge_fills_a_missing_role_from_any_source():
    merged = merge_hits(
        [PersonHit(name="Anna Schmidt"), PersonHit(name="Anna Schmidt", role="Geschäftsführer")]
    )
    assert merged[0].role == "Geschäftsführer"


def test_merge_keeps_distinct_people():
    assert len(merge_hits([PersonHit(name="Anna Schmidt"), PersonHit(name="Peter Wolf")])) == 2


def test_merge_is_case_and_whitespace_insensitive():
    assert len(merge_hits([PersonHit(name="Anna Schmidt"), PersonHit(name="anna  schmidt")])) == 1


def test_merge_drops_nameless_hits():
    assert merge_hits([PersonHit(name="", role="CTO")]) == []
