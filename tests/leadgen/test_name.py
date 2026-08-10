"""Telling a person's name from something a parser mistook for one."""

from __future__ import annotations

from leadgen.assemble import CompanyContext, build_leads
from leadgen.person.hit import PersonHit
from leadgen.person.name import looks_like_person_name, split_people, strip_leading_title
from leadgen.score.quota import select

from .test_score import _lead


def test_real_names_are_accepted():
    for name in (
        "Anna Dahlfors",
        "Martin Brunthaler",
        "Jan de Vries",
        "Peter van der Berg",
        "Dr. Stefan Strobl",
        "Marina ter Wiel",
        "Jean-Luc Picard",
        "Rosa Falk Badensø",
    ):
        assert looks_like_person_name(name) is True, name


def test_initials_only_are_rejected_because_no_address_can_be_built():
    """ "J. K. Rowling" is a real name, but first="J." yields j..rowling@ —
    a guaranteed bounce. A contact we cannot address is not a lead."""
    assert looks_like_person_name("J. K. Rowling") is False


def test_page_furniture_and_job_titles_are_rejected():
    """All of these were extracted from live company pages as people."""
    for name in (
        "Account Manager",
        "Search Engine Marketing Agencies",
        "Getting Started",
        "Our Guiding Principles",
        "Follow Us",
        "Register Now",
        "Read More",
        "Cash Management",
        "Digital Banking",
        "Social Media",
        "Head of Product",
        "Global Advisory Council",
        "The Beginning",
        "Fin Launches",
    ):
        assert looks_like_person_name(name) is False, name


def test_a_surname_that_is_also_a_common_word_still_passes():
    """Page, Baker, Fox, Mason, Hunter and Cook are real surnames — the
    vocabulary check must not swallow them."""
    for name in ("Dan Page", "Sarah Baker", "Megan Fox", "Perry Mason", "Cathy Hunter", "Tim Cook"):
        assert looks_like_person_name(name) is True, name


def test_a_single_token_or_a_paragraph_is_not_a_name():
    assert looks_like_person_name("Anna") is False
    assert looks_like_person_name("") is False
    assert looks_like_person_name("Series A raised. San Francisco office opens.") is False


def test_a_title_glued_to_a_real_name_is_recovered():
    assert strip_leading_title("Product Owner Line Benzin") == "Line Benzin"
    assert strip_leading_title("Solution Architect Henrik Oxlund") == "Henrik Oxlund"


def test_stripping_a_pure_title_leaves_nothing():
    assert strip_leading_title("Account Manager") == ""


def test_an_impressum_naming_two_directors_yields_two_people():
    """§5 TMG names every managing director and gives no reason to use separate
    lines. Read as one person that becomes a fabricated address."""
    assert split_people("Dana Aleff, Erik Müller") == ["Dana Aleff", "Erik Müller"]
    assert split_people("Florian Biller und Sebastian Schlecht") == ["Florian Biller", "Sebastian Schlecht"]
    assert split_people("Dr. Stefan Strobl, Christopher Koker") == ["Dr. Stefan Strobl", "Christopher Koker"]


def test_a_surname_first_name_is_not_torn_in_half():
    """ "Smith, John" is one person; its parts are one token each."""
    assert split_people("Smith, John") == ["Smith, John"]


def test_a_name_with_a_trailing_title_is_not_split_into_a_fake_person():
    assert split_people("Anna Müller, CEO") == ["Anna Müller, CEO"]


def test_build_leads_splits_and_cleans_in_one_place():
    company = CompanyContext(name="Acme", domain="acme.de", country="DE")
    hits = [
        PersonHit(name="Dana Aleff, Erik Müller", role="Geschäftsführer"),
        PersonHit(name="Account Manager", role="Recruiting Contact"),
        PersonHit(name="Product Owner Line Benzin", role="Recruiting Contact"),
    ]
    leads = build_leads(company, hits, smtp=False)
    names = {lead.person_name for lead in leads}
    assert names == {"Dana Aleff", "Erik Müller", "Line Benzin"}
    # And the address follows the corrected name, not the raw string.
    assert all("accountmanager" not in (lead.person_email or "") for lead in leads)


def test_selection_drops_a_fabricated_contact_already_in_the_checkpoint():
    """A checkpoint outlives the code that filled it."""
    leads = [
        _lead(person_name="Anna Dahlfors", person_email="a@a.de", company_domain="a.de"),
        _lead(person_name="Account Manager", person_email="b@b.de", company_domain="b.de"),
    ]
    selected, report = select(leads, target=10)
    assert [lead.person_name for lead in selected] == ["Anna Dahlfors"]
    assert report.dropped["not_a_person"] == 1
