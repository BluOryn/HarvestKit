"""Completeness scoring and exact-N selection."""

from __future__ import annotations

from leadgen.models import Lead
from leadgen.score.completeness import score_lead
from leadgen.score.quota import select


def _lead(**kwargs) -> Lead:
    base = {
        "person_name": "Jane Doe",
        "person_role": "CTO",
        "person_role_family": "tech_leadership",
        "person_email": "j.doe@acme.de",
        "email_status": "verified",
        "company_name": "Acme",
        "company_domain": "acme.de",
        "company_country": "DE",
    }
    base.update(kwargs)
    return Lead(**base)


def _many(count: int, country: str = "DE", prefix: str = "x") -> list[Lead]:
    return [
        _lead(
            person_name=f"P{prefix}{i}",
            person_email=f"p{prefix}{i}@{prefix}{i}.de",
            company_domain=f"{prefix}{i}.de",
            company_country=country,
        )
        for i in range(count)
    ]


def test_published_email_outscores_inferred():
    assert score_lead(_lead(email_status="published")) > score_lead(_lead(email_status="inferred_medium"))


def test_catch_all_scores_lowest_of_the_statuses():
    assert score_lead(_lead(email_status="catch_all")) < score_lead(_lead(email_status="unknown"))


def test_target_role_outscores_other():
    assert score_lead(_lead(person_role_family="hr")) > score_lead(_lead(person_role_family="other"))


def test_more_populated_fields_score_higher():
    rich = _lead(person_phone="+49 89 1234", person_linkedin="https://x/in/j", company_city="Munich")
    assert score_lead(rich) > score_lead(_lead())


def test_select_returns_exactly_the_target():
    selected, report = select(_many(50), target=10)
    assert len(selected) == 10
    assert report.selected == 10
    assert report.shortfall == 0


def test_select_reports_shortfall_rather_than_padding():
    selected, report = select(_many(4), target=10)
    assert len(selected) == 4
    assert report.shortfall == 6


def test_select_drops_leads_without_an_email():
    selected, report = select([_lead(person_email="", email_status=""), _lead()], target=10)
    assert len(selected) == 1
    assert report.dropped["no_email"] == 1


def test_select_drops_leads_without_a_name():
    selected, report = select([_lead(person_name=""), _lead()], target=10)
    assert len(selected) == 1
    assert report.dropped["no_name"] == 1


def test_select_deduplicates_the_same_person_found_twice():
    a = _lead(source_person_url="https://acme.de/team")
    b = _lead(source_person_url="https://acme.de/impressum")
    selected, report = select([a, b], target=10)
    assert len(selected) == 1
    assert report.dropped["duplicate"] == 1


def test_ceiling_caps_a_dominant_country_when_alternatives_exist():
    """DE has ten times the supply, but the list must not become a German list."""
    leads = _many(200, "DE", "de") + _many(20, "FR", "fr") + _many(20, "IT", "it") + _many(20, "ES", "es")
    _, report = select(leads, target=40, country_ceiling=0.25)
    assert report.by_country["DE"] <= 10, "25% of 40 is 10"
    assert report.selected == 40


def test_spread_is_even_when_supply_allows():
    leads = _many(50, "DE", "de") + _many(50, "FR", "fr")
    _, report = select(leads, target=20, country_ceiling=0.25)
    # Only two countries have supply, so 25% each is unreachable. The best
    # achievable spread is an even split, and that is what must happen — not a
    # cap-then-backfill that hands the remainder to whichever country sorted first.
    assert abs(report.by_country["DE"] - report.by_country["FR"]) <= 1
    assert report.selected == 20


def test_ceiling_relaxes_rather_than_under_delivering():
    """One country only — the ceiling must not block delivery."""
    selected, report = select(_many(20, "DE", "de"), target=10, country_ceiling=0.25)
    assert len(selected) == 10
    assert report.shortfall == 0


def test_the_best_lead_in_each_country_is_taken_first():
    weak = _lead(person_name="Weak", person_email="w@w.de", company_domain="w.de", email_status="catch_all")
    strong = _lead(
        person_name="Strong", person_email="s@s.de", company_domain="s.de", email_status="published"
    )
    selected, _ = select([weak, strong], target=1)
    assert selected[0].person_name == "Strong"


def test_report_breaks_down_status_and_role():
    leads = [
        _lead(person_email="a@a.de", company_domain="a.de", email_status="published"),
        _lead(
            person_email="b@b.de",
            company_domain="b.de",
            email_status="inferred_high",
            person_role_family="hr",
        ),
    ]
    _, report = select(leads, target=10)
    assert report.by_status["published"] == 1
    assert report.by_role_family["hr"] == 1


def test_role_filter_keeps_only_the_families_the_brief_asked_for():
    leads = [
        _lead(person_name="A", person_email="a@a.de", company_domain="a.de", person_role_family="hr"),
        _lead(person_name="B", person_email="b@b.de", company_domain="b.de", person_role_family="other"),
        _lead(
            person_name="C",
            person_email="c@c.de",
            company_domain="c.de",
            person_role_family="tech_leadership",
        ),
    ]
    selected, report = select(leads, target=10, role_families=frozenset({"hr", "tech_leadership"}))
    assert {lead.person_name for lead in selected} == {"A", "C"}
    assert report.dropped["wrong_role"] == 1


def test_no_role_filter_keeps_everything():
    leads = [_lead(person_role_family="other", person_email="b@b.de", company_domain="b.de")]
    selected, _ = select(leads, target=10, role_families=None)
    assert len(selected) == 1
