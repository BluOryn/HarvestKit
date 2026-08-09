"""Role-family classification across EU languages."""

from __future__ import annotations

import pytest

from leadgen.person.roles import classify_role, is_target_role, split_name


@pytest.mark.parametrize(
    "title",
    [
        "Head of HR",
        "HR Manager",
        "People Operations Lead",
        "Talent Acquisition Partner",
        "Recruiter",
        "Personalleiterin",
        "Leiter Personalwesen",
        "Responsable Ressources Humaines",
        "Responsabile Risorse Umane",
        "Director de Recursos Humanos",
        "HR-sjef",
    ],
)
def test_hr_titles_classify_as_hr(title):
    assert classify_role(title) == "hr"


@pytest.mark.parametrize(
    "title",
    [
        "CTO",
        "Chief Technology Officer",
        "VP of Engineering",
        "Head of Engineering",
        "Engineering Manager",
        "Technischer Leiter",
        "IT-Leiter",
        "Directeur Technique",
        "Direttore Tecnico",
    ],
)
def test_tech_leadership_titles_classify(title):
    assert classify_role(title) == "tech_leadership"


@pytest.mark.parametrize("title", ["Software Engineer", "Sales Director", "Barista", ""])
def test_non_target_titles_are_other(title):
    assert classify_role(title) == "other"


def test_hr_wins_over_a_generic_leader_token():
    # "Leiter Personalwesen" contains a leadership word but is unambiguously HR.
    assert classify_role("Leiter Personalwesen") == "hr"


def test_substring_false_positives_are_rejected():
    # "cto" must not fire inside "Director"; "hr" must not fire inside "Thrive".
    assert classify_role("Director of Sales") == "other"
    assert classify_role("Thrive Coach") == "other"


def test_is_target_role():
    assert is_target_role("CTO") is True
    assert is_target_role("HR Manager") is True
    assert is_target_role("Software Engineer") is False


def test_split_name_handles_particles_and_titles():
    assert split_name("Jane Doe") == ("Jane", "Doe")
    assert split_name("Dr. Anna Schmidt") == ("Anna", "Schmidt")
    assert split_name("Jan van der Berg") == ("Jan", "van der Berg")
    assert split_name("Müller") == ("", "Müller")
    assert split_name("") == ("", "")


@pytest.mark.parametrize(
    "title",
    [
        "CEO",
        "Chief Executive Officer",
        "Co-Founder & Chief Executive Officer",
        "Founder",
        "Geschäftsführer",
        "Geschäftsführerin",
        "Managing Director",
        "President & Chief Operating Officer",
        "Chief Financial Officer",
        "Board Director",
        "Inhaber",
        "Gérant",
    ],
)
def test_executive_titles_classify(title):
    assert classify_role(title) == "executive"


@pytest.mark.parametrize(
    "title", ["VP, Engineering", "VP of Engineering", "SVP Eng", "Vice President, Technology"]
)
def test_vp_engineering_with_a_comma_is_tech_leadership(title):
    """'VP, Engineering' is as common as 'VP of Engineering' on real team pages."""
    assert classify_role(title) == "tech_leadership"


def test_the_function_outranks_the_office():
    """A founder who is also the CTO should be reachable as the CTO."""
    from leadgen.person.roles import role_rank

    assert role_rank("Chief Technology Officer") > role_rank("Geschäftsführer")
    assert role_rank("Head of HR") > role_rank("Managing Director")
    assert role_rank("Geschäftsführer") > role_rank("Software Engineer")


def test_a_combined_title_files_under_the_function_asked_for():
    assert classify_role("Co-Founder & CTO") == "tech_leadership"
