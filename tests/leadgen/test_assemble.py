"""Company + person hits -> Leads, with email resolution and provenance."""

from __future__ import annotations

import pytest

from leadgen.assemble import CompanyContext, build_leads
from leadgen.email import validate as email_validate
from leadgen.person.hit import PersonHit


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(email_validate, "has_mx", lambda domain: True)
    monkeypatch.setattr(email_validate, "is_catch_all", lambda domain: False)


COMPANY = CompanyContext(
    name="Acme GmbH",
    domain="acme.de",
    website="https://acme.de",
    country="DE",
    city="Munich",
    seed_url="https://boards.greenhouse.io/acme/jobs/1",
)


def test_published_email_is_marked_published():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="anna.schmidt@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.email_status == "published"
    assert lead.person_email == "anna.schmidt@acme.de"


def test_pattern_is_inferred_from_an_anchor_and_applied_to_the_others():
    hits = [
        PersonHit(name="Anna Schmidt", role="CTO", email="anna.schmidt@acme.de", source_url="u1"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u2"),
    ]
    leads = {lead.person_name: lead for lead in build_leads(COMPANY, hits)}
    assert leads["Peter Wolf"].person_email == "peter.wolf@acme.de"
    # The mailbox probe confirmed it, which beats the single-example pattern.
    assert leads["Peter Wolf"].email_status == "verified"


def test_probe_refusal_falls_back_to_the_pattern_confidence(monkeypatch):
    monkeypatch.setattr(email_validate, "is_catch_all", lambda domain: None)
    hits = [
        PersonHit(name="Anna Schmidt", email="anna.schmidt@acme.de", role="CTO", source_url="u1"),
        PersonHit(name="Klaus Berg", email="klaus.berg@acme.de", role="CEO", source_url="u2"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u3"),
    ]
    leads = {lead.person_name: lead for lead in build_leads(COMPANY, hits)}
    assert leads["Peter Wolf"].email_status == "inferred_high"
    assert leads["Peter Wolf"].email_confidence == "high"


def test_one_anchor_only_gives_inferred_medium_when_unprobed(monkeypatch):
    monkeypatch.setattr(email_validate, "is_catch_all", lambda domain: None)
    hits = [
        PersonHit(name="Anna Schmidt", email="anna.schmidt@acme.de", role="CTO", source_url="u1"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u2"),
    ]
    leads = {lead.person_name: lead for lead in build_leads(COMPANY, hits)}
    assert leads["Peter Wolf"].email_status == "inferred_medium"


def test_catch_all_domain_is_flagged_not_trusted(monkeypatch):
    monkeypatch.setattr(email_validate, "is_catch_all", lambda domain: True)
    hits = [
        PersonHit(name="Anna Schmidt", email="anna.schmidt@acme.de", role="CTO", source_url="u1"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u2"),
    ]
    leads = {lead.person_name: lead for lead in build_leads(COMPANY, hits)}
    assert leads["Peter Wolf"].email_status == "catch_all"


def test_a_role_account_is_never_attached_to_a_person():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="info@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.person_email != "info@acme.de", "info@ must not become Anna's address"
    assert not lead.person_email.startswith("info@")


def test_a_role_account_leaves_no_email_when_guessing_is_off():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="info@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits, guess_without_anchor=False)[0]
    assert lead.person_email == ""


def test_an_anchorless_domain_falls_back_to_the_modal_format(monkeypatch):
    """No published address anywhere: the guess is emitted, labelled as a guess."""
    monkeypatch.setattr(email_validate, "is_catch_all", lambda domain: None)
    hits = [PersonHit(name="Peter Wolf", role="CTO", source_url="https://acme.de/team")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.person_email == "peter.wolf@acme.de"
    assert lead.email_status == "inferred_low"
    assert lead.email_confidence == "low"
    assert lead.evidence["person_email"].startswith("guessed:")


def test_guessing_can_be_disabled():
    hits = [PersonHit(name="Peter Wolf", role="CTO", source_url="u")]
    lead = build_leads(COMPANY, hits, guess_without_anchor=False)[0]
    assert lead.person_email == ""


def test_a_real_anchor_still_beats_the_blind_guess(monkeypatch):
    """An observed f.last domain must not be overridden by the modal format."""
    monkeypatch.setattr(email_validate, "is_catch_all", lambda domain: None)
    hits = [
        PersonHit(name="Anna Schmidt", email="a.schmidt@acme.de", role="CTO", source_url="u1"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u2"),
    ]
    leads = {lead.person_name: lead for lead in build_leads(COMPANY, hits)}
    assert leads["Peter Wolf"].person_email == "p.wolf@acme.de"
    assert leads["Peter Wolf"].email_status == "inferred_medium"


def test_a_recruiter_anchor_from_the_seed_unlocks_the_domain():
    company = CompanyContext(
        name="Acme",
        domain="acme.de",
        country="DE",
        extra_anchors=[("Anna Schmidt", "anna.schmidt@acme.de")],
    )
    hits = [PersonHit(name="Peter Wolf", role="CTO", source_url="https://acme.de/team")]
    lead = build_leads(company, hits)[0]
    assert lead.person_email == "peter.wolf@acme.de"


def test_role_family_is_classified():
    hits = [
        PersonHit(name="Anna Schmidt", role="CTO", email="a.s@acme.de", source_url="u"),
        PersonHit(name="Peter Wolf", role="Head of HR", email="p.w@acme.de", source_url="u"),
    ]
    families = {lead.person_name: lead.person_role_family for lead in build_leads(COMPANY, hits)}
    assert families["Anna Schmidt"] == "tech_leadership"
    assert families["Peter Wolf"] == "hr"


def test_company_fields_are_copied_onto_every_lead():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="a@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.company_name == "Acme GmbH"
    assert lead.company_country == "DE"
    assert lead.company_city == "Munich"
    assert lead.source_seed_url == COMPANY.seed_url


def test_evidence_records_where_each_fact_came_from():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="a@acme.de", source_url="https://acme.de/team")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.evidence["person_name"] == "https://acme.de/team"
    assert lead.evidence["person_email"] == "https://acme.de/team"


def test_inferred_email_evidence_points_at_the_pattern_not_a_page():
    hits = [
        PersonHit(name="Anna Schmidt", email="anna.schmidt@acme.de", role="CTO", source_url="u1"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u2"),
    ]
    wolf = next(lead for lead in build_leads(COMPANY, hits) if lead.person_name == "Peter Wolf")
    assert wolf.evidence["person_email"].startswith("inferred:")


def test_a_dead_domain_drops_the_email_but_keeps_the_person(monkeypatch):
    monkeypatch.setattr(email_validate, "has_mx", lambda domain: False)
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="a@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.person_name == "Anna Schmidt"
    assert lead.person_email == ""
    assert lead.email_status == ""


def test_names_are_split_for_downstream_use():
    hits = [PersonHit(name="Dr. Anna Schmidt", role="CTO", email="a@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.person_first_name == "Anna"
    assert lead.person_last_name == "Schmidt"


def test_nameless_hits_are_skipped():
    assert build_leads(COMPANY, [PersonHit(name="", role="CTO")]) == []


def test_a_shared_mailbox_on_the_page_is_not_the_persons_address():
    """A site strategy attributes whatever single address it finds on the page.
    "informatik.admin@" is a shared inbox, not the HR manager, and shipping it
    as her personal address is the most visible quality failure there is."""
    hits = [
        PersonHit(
            name="Romana Plaz",
            role="HR Managerin",
            email="informatik.admin@acme.de",
            source_url="https://acme.de/team",
        )
    ]
    lead = build_leads(COMPANY, hits, smtp=False)[0]
    assert lead.person_email != "informatik.admin@acme.de"
    assert lead.email_status != "published"


def test_an_initials_mailbox_is_still_treated_as_theirs():
    """ "rp@" at a small firm is Romana Plaz, not a shared inbox."""
    hits = [PersonHit(name="Romana Plaz", role="HR", email="rp@acme.de", source_url="https://acme.de/team")]
    lead = build_leads(COMPANY, hits, smtp=False)[0]
    assert lead.person_email == "rp@acme.de"
    assert lead.email_status == "published"
