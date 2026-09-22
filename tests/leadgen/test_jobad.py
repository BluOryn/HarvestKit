"""Named contacts mined from job-ad text — the only reliable HR source."""

from __future__ import annotations

from job_scraper.models import JobListing
from leadgen.person.jobad import contacts_from_ad
from leadgen.person.roles import classify_role
from leadgen.seed.jobboard import MAX_AD_CONTACTS, companies_from_listings


def _seen_but_empty():
    """Reachability for a site that answered us and simply named nobody.

    The distinction matters to `process_companies`: a company we could not
    reach is counted separately from one that had no people on it, so a stub
    that claimed to be blocked would exercise a different branch than these
    tests intend.
    """
    from leadgen.person.cascade import Reachability

    return Reachability(fetched=1)


class StubHttp:
    def get(self, url, **kwargs):
        return (url, "<html></html>") if "acme.de" in url else None


def test_a_german_ad_contact_is_found_with_their_address():
    text = "Ihre Ansprechpartnerin: Frau Anna Müller, a.mueller@acme.de, Tel. +49 89 123456"
    hit = contacts_from_ad(text, "acme.de")[0]
    assert hit.name == "Anna Müller"
    assert hit.email == "a.mueller@acme.de"
    assert hit.strategy == "job_ad"


def test_the_ad_contact_classifies_as_hr_which_is_what_the_brief_asked_for():
    hit = contacts_from_ad("Ansprechpartner: Max Schmidt", "acme.de")[0]
    assert classify_role(hit.role) == "hr"


def test_an_explicit_title_beside_the_name_wins_over_the_default():
    text = "Ihr Kontakt: Anna Müller, Leiterin Personalwesen, a.mueller@acme.de"
    hit = contacts_from_ad(text, "acme.de")[0]
    assert "Personalwesen" in hit.role
    assert classify_role(hit.role) == "hr"


def test_a_shared_mailbox_is_not_treated_as_the_persons_address():
    """bewerbung@ is the company's inbox, not Anna's. Leaving it off lets
    pattern inference produce the individual address the brief requires."""
    hit = contacts_from_ad("Ansprechpartnerin: Anna Müller, bewerbung@acme.de", "acme.de")[0]
    assert hit.email == ""


def test_an_address_that_does_not_echo_the_name_is_not_attributed_to_them():
    """Ads print several addresses; proximity alone is not evidence."""
    text = "Ansprechpartner: Max Schmidt. Weitere Infos bei peter.wolf@acme.de"
    assert contacts_from_ad(text, "acme.de")[0].email == ""


def test_an_address_at_another_domain_is_ignored():
    text = "Ansprechpartnerin: Anna Müller, anna.mueller@some-agency.com"
    assert contacts_from_ad(text, "acme.de")[0].email == ""


def test_transliterated_local_parts_still_match_the_name():
    text = "Ansprechpartner: Jürgen Schröder, juergen.schroeder@acme.de"
    assert contacts_from_ad(text, "acme.de")[0].email == "juergen.schroeder@acme.de"


def test_a_function_name_is_not_mistaken_for_a_person():
    for text in (
        "Ansprechpartner: Human Resources",
        "Ihr Kontakt: Recruiting Team",
        "Contact person: Talent Acquisition",
    ):
        assert contacts_from_ad(text, "acme.de") == [], text


def test_english_and_french_cues_are_recognised():
    assert contacts_from_ad("Your contact: Sarah Connor", "acme.de")[0].name == "Sarah Connor"
    assert contacts_from_ad("Personne de contact : Marie Dupont", "acme.de")[0].name == "Marie Dupont"
    assert contacts_from_ad("Contactpersoon: Jan de Vries", "acme.de")[0].name == "Jan de Vries"


def test_a_nobiliary_particle_stays_part_of_the_surname():
    assert contacts_from_ad("Ansprechpartner: Peter van der Berg", "acme.de")[0].name == "Peter van der Berg"


def test_the_same_person_named_twice_in_one_ad_yields_one_hit():
    text = "Ansprechpartnerin: Anna Müller ... Bei Fragen wenden Sie sich an Anna Müller"
    assert len(contacts_from_ad(text, "acme.de")) == 1


def test_a_phone_number_is_captured_only_when_it_is_really_one():
    with_phone = contacts_from_ad("Kontakt: Anna Müller, Tel. +49 89 123 4567", "acme.de")[0]
    assert with_phone.phone
    short = contacts_from_ad("Kontakt: Anna Müller, Ref. 12", "acme.de")[0]
    assert short.phone == ""


def test_an_ad_naming_nobody_returns_nothing():
    assert contacts_from_ad("We are hiring a Backend Engineer. Apply online.", "acme.de") == []
    assert contacts_from_ad("", "acme.de") == []
    assert contacts_from_ad("Ansprechpartner: Anna Müller", "") == []


def test_ad_contacts_reach_the_company_context():
    listing = JobListing(
        title="Backend Engineer",
        company="Acme GmbH",
        job_url="https://acme.de/jobs/1",
        description="Ihre Ansprechpartnerin: Anna Müller, a.mueller@acme.de",
    )
    company = companies_from_listings([listing], StubHttp())[0]
    assert [hit.name for hit in company.ad_contacts] == ["Anna Müller"]
    # The address beside the name states the company's format outright.
    assert ("Anna Müller", "a.mueller@acme.de") in company.extra_anchors


def test_ad_contacts_are_capped_so_one_prolific_advertiser_cannot_dominate():
    listings = [
        JobListing(
            title="Backend Engineer",
            company="Acme GmbH",
            job_url=f"https://acme.de/jobs/{index}",
            description=f"Ansprechpartner: Person Number{index}",
        )
        for index in range(20)
    ]
    company = companies_from_listings(listings, StubHttp())[0]
    assert len(company.ad_contacts) <= MAX_AD_CONTACTS


def test_an_ad_contact_rescues_a_company_whose_site_names_nobody(tmp_path, monkeypatch):
    """The largest single funnel loss: 42 of 91 companies had no person on
    their website. A company whose ad names one is still a lead."""
    from leadgen import pipeline
    from leadgen.assemble import CompanyContext
    from leadgen.checkpoint import Checkpoint
    from leadgen.person.hit import PersonHit

    monkeypatch.setattr(pipeline, "resolve_people_detailed", lambda *a, **k: ([], _seen_but_empty()))
    checkpoint = Checkpoint(str(tmp_path / "cp.sqlite"))
    try:
        company = CompanyContext(
            name="Acme GmbH",
            domain="acme.de",
            country="DE",
            ad_contacts=[PersonHit(name="Anna Müller", role="Recruiting Contact", strategy="job_ad")],
        )
        funnel = pipeline.process_companies([company], StubHttp(), checkpoint, smtp=False)
        assert funnel["no_person_found"] == 0
        assert funnel["companies_with_people"] == 1
        assert [lead.person_name for lead in checkpoint.all_leads()] == ["Anna Müller"]
    finally:
        checkpoint.close()


def test_the_same_recruiter_across_many_ads_is_one_contact():
    listings = [
        JobListing(
            title="Backend Engineer",
            company="Acme GmbH",
            job_url=f"https://acme.de/jobs/{index}",
            description="Ihre Ansprechpartnerin: Anna Müller",
        )
        for index in range(8)
    ]
    company = companies_from_listings(listings, StubHttp())[0]
    assert len(company.ad_contacts) == 1
