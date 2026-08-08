"""Email-format inference from known (name, email) pairs on a domain."""

from __future__ import annotations

from leadgen.email.pattern import apply_pattern, infer_pattern


def test_apply_each_pattern():
    assert apply_pattern("first.last", "Anna", "Schmidt", "acme.de") == "anna.schmidt@acme.de"
    assert apply_pattern("f.last", "Anna", "Schmidt", "acme.de") == "a.schmidt@acme.de"
    assert apply_pattern("first", "Anna", "Schmidt", "acme.de") == "anna@acme.de"
    assert apply_pattern("firstlast", "Anna", "Schmidt", "acme.de") == "annaschmidt@acme.de"
    assert apply_pattern("last.first", "Anna", "Schmidt", "acme.de") == "schmidt.anna@acme.de"
    assert apply_pattern("flast", "Anna", "Schmidt", "acme.de") == "aschmidt@acme.de"
    assert apply_pattern("first_last", "Anna", "Schmidt", "acme.de") == "anna_schmidt@acme.de"


def test_apply_pattern_folds_accents_and_strips_particle_spaces():
    assert apply_pattern("first.last", "Jörg", "Müller", "acme.de") == "joerg.mueller@acme.de"
    assert apply_pattern("first.last", "Jan", "van der Berg", "acme.nl") == "jan.vanderberg@acme.nl"


def test_apply_pattern_needs_both_parts_except_single_token_formats():
    assert apply_pattern("first.last", "", "Schmidt", "acme.de") == ""
    assert apply_pattern("first", "Anna", "", "acme.de") == "anna@acme.de"
    assert apply_pattern("first.last", "Anna", "Schmidt", "") == ""


def test_two_consistent_examples_give_high_confidence():
    pattern, confidence = infer_pattern(
        [("Anna Schmidt", "anna.schmidt@acme.de"), ("Peter Wolf", "peter.wolf@acme.de")]
    )
    assert pattern == "first.last"
    assert confidence == "high"


def test_one_example_gives_medium_confidence():
    pattern, confidence = infer_pattern([("Anna Schmidt", "a.schmidt@acme.de")])
    assert pattern == "f.last"
    assert confidence == "medium"


def test_conflicting_examples_yield_no_pattern():
    """Two formats on one domain means we cannot safely extrapolate."""
    pattern, confidence = infer_pattern(
        [("Anna Schmidt", "anna.schmidt@acme.de"), ("Peter Wolf", "p.wolf@acme.de")]
    )
    assert pattern == ""
    assert confidence == ""


def test_role_accounts_are_not_treated_as_examples():
    pattern, _ = infer_pattern([("Anna Schmidt", "info@acme.de")])
    assert pattern == ""


def test_the_same_address_twice_is_still_one_example():
    _, confidence = infer_pattern(
        [("Anna Schmidt", "anna.schmidt@acme.de"), ("Anna Schmidt", "anna.schmidt@acme.de")]
    )
    assert confidence == "medium", "duplicates must not inflate confidence"


def test_no_examples_yields_nothing():
    assert infer_pattern([]) == ("", "")
