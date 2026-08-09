"""Free-text location -> ISO country code."""

from __future__ import annotations

import pytest

from leadgen.geo import country_from_location, is_european


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Munich, Germany", "DE"),
        ("Berlin", "DE"),
        ("Paris, France", "FR"),
        ("Amsterdam", "NL"),
        ("Vienna, Austria", "AT"),
        ("Kraków, Poland", "PL"),
        ("Zürich", "CH"),
        ("London, United Kingdom", "GB"),
        ("Stockholm, Sweden", "SE"),
        ("Helsinki", "FI"),
        ("Cluj-Napoca", "RO"),
        ("Tallinn, Estonia", "EE"),
        ("New York, NY", "US"),
        ("Bengaluru, India", "IN"),
    ],
)
def test_locations_resolve(text, code):
    assert country_from_location(text) == code


def test_country_name_beats_a_city_elsewhere():
    """'Cambridge' exists on two continents; the stated country must win."""
    assert country_from_location("Cambridge, United Kingdom") == "GB"
    assert country_from_location("Cambridge, United States") == "US"


def test_remote_and_multi_location_strings():
    assert country_from_location("Remote - Germany") == "DE"
    assert country_from_location("Berlin / Munich") == "DE"
    assert country_from_location("Paris, France; London, UK") == "FR"


def test_unknown_and_empty_yield_nothing():
    assert country_from_location("") == ""
    assert country_from_location("Remote") == ""
    assert country_from_location("Anywhere on Earth") == ""


def test_ambiguous_city_names_are_deliberately_absent():
    """Better an empty country than a confidently wrong one."""
    assert country_from_location("Birmingham") == ""


def test_is_european_covers_eu_plus_efta_and_uk():
    assert is_european("DE") and is_european("GB") and is_european("CH") and is_european("NO")
    assert not is_european("US") and not is_european("IN") and not is_european("")
