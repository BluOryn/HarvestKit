"""The scraper is aimed at Europe, and these are the things it could not read.

Each case here failed before. Not degraded — failed outright, and silently, so
the column came back empty and looked like a site that simply did not publish
the value.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from job_scraper.extract import (
    _apply_k,
    _currency_code,
    _first_salary_match,
    _normalize_amount,
    _period_to_unit,
)
from job_scraper.universal import CONTACT_KEYWORDS, PHONE_RX, _looks_like_job_page


def _salary(text: str):
    """(min, max, currency, period) as the extractor would record them."""
    match = _first_salary_match(text)
    if match is None:
        return None
    groups = match.groupdict()
    return (
        _apply_k(_normalize_amount(groups.get("lo")), groups.get("lok")),
        _apply_k(_normalize_amount(groups.get("hi")), groups.get("hik")) if groups.get("hi") else "",
        _currency_code(match.group(0)),
        _period_to_unit(groups.get("period") or ""),
    )


# --------------------------------------------------------------------------
# Salary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        # The dominant European form: the currency comes *after* the amount.
        # The old pattern made the leading currency mandatory, so none of these
        # parsed at all.
        ("Gehalt: 45.000 EUR bis 60.000 EUR pro Jahr", ("45000", "60000", "EUR", "year")),
        ("Wir bieten 60.000 – 75.000 EUR pro Jahr", ("60000", "75000", "EUR", "year")),
        ("Salaire: 45 000 € à 55 000 € par an", ("45000", "55000", "EUR", "year")),
        ("RAL 35.000 - 45.000 EUR annuo", ("35000", "45000", "EUR", "year")),
        # Swiss apostrophe thousands separator.
        ("CHF 80'000 - 110'000 / Jahr", ("80000", "110000", "CHF", "year")),
        # Unseparated figures. `_NUM` required a separator after 2-3 digits, so
        # "60000" was truncated to "600".
        ("EUR 60000 - 80000 per year", ("60000", "80000", "EUR", "year")),
        # The k suffix was matched and then thrown away, exporting a salary of 45.
        ("EUR 45k - 60k per year", ("45000", "60000", "EUR", "year")),
        # Four-digit monthly rates, which German and Swiss ads quote constantly.
        ("4.500 EUR monatlich", ("4500", "", "EUR", "month")),
        ("Ab 65.000 EUR jährlich", ("65000", "", "EUR", "year")),
        ("£55,000 to £70,000 per annum", ("55000", "70000", "GBP", "year")),
        ("$120,000 - $150,000 per year", ("120000", "150000", "USD", "year")),
    ],
)
def test_european_salary_formats_parse(text, expected):
    assert _salary(text) == expected


def test_a_german_annual_salary_is_not_called_hourly():
    """The chain read `elif "hr" in p`, and "jahr" contains "hr". Every German
    annual figure was exported as an hourly rate — a number off by a factor of
    about 1,800."""
    assert _period_to_unit("Jahr") == "year"
    assert _period_to_unit("jährlich") == "year"


def test_german_monthly_pay_is_not_called_annual():
    """ "mo" sat before "monat" in the alternation, so "monat" captured as "mo",
    matched none of the month tests, and fell through to the year default."""
    assert _period_to_unit("Monat") == "month"
    assert _period_to_unit("monatlich") == "month"


@pytest.mark.parametrize(
    "period,unit",
    [
        ("mois", "month"),
        ("mensile", "month"),
        ("maand", "month"),
        ("Stunde", "hour"),
        ("heure", "hour"),
        ("ora", "hour"),
        ("uur", "hour"),
        ("année", "year"),
        ("annuale", "year"),
        ("p.a.", "year"),
    ],
)
def test_period_words_across_europe(period, unit):
    assert _period_to_unit(period) == unit


@pytest.mark.parametrize(
    "text",
    [
        "Founded in 1998",
        "Over 200 employees",
        "Team of 50 people",
        "We have 1200 customers worldwide",
        "Postfach 80331 München",
        "Reference 2024 000123",
    ],
)
def test_a_bare_number_is_not_a_salary(text):
    """Allowing the currency to be optional is what lets "45.000 EUR" parse,
    and it is also what would turn "Founded in 1998" into a salary of 1998. A
    match must carry a currency or a period word."""
    assert _first_salary_match(text) is None


def test_an_ambiguous_kr_does_not_guess_a_currency():
    """`kr` is SEK, NOK and DKK. Picking one would put a wrong currency on the
    row, which is worse than leaving the column empty."""
    result = _salary("kr 650 000 - 750 000 per år")
    assert result is not None
    assert result[0] == "650000"
    assert result[2] == "", "no currency is honest; a guessed one is not"


# --------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "+41 44 123 45 67",
        "+49 30 12345678",
        "+47 22 12 34 56",
        "+33 1 42 68 53 00",
        "+39 02 1234 5678",
        "+31 20 123 4567",
    ],
)
def test_international_numbers_still_match(number):
    match = PHONE_RX.search(number)
    assert match and match.group(0).strip() == number


@pytest.mark.parametrize(
    "number",
    [
        "030 12345678",  # Berlin
        "089-1234567",  # Munich
        "0221 / 1234567",  # Cologne
        "044 123 45 67",  # Zurich
        "01 42 68 53 00",  # Paris
        "02 1234 5678",  # Milan
        "020 123 4567",  # Amsterdam
    ],
)
def test_national_format_numbers_match(number):
    """The national branch read `(?:0\\d{1,3}…){2,5}`, which requires *every*
    repeated group to start with a zero. Only the trunk prefix does, so the
    expression matched no national European number at all — while looking as
    though it supported them. That is the format an Impressum prints."""
    match = PHONE_RX.search(number)
    assert match, f"{number} must be recognised as a phone number"


@pytest.mark.parametrize(
    "text",
    ["12.03.2024", "Ref 2024 000123", "ISBN 978 3 16 148410", "2019 2020 2021 2022"],
)
def test_dates_and_reference_numbers_are_not_phone_numbers(text):
    assert PHONE_RX.search(text) is None


# --------------------------------------------------------------------------
# Contact vocabulary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    [
        "Ansprechpartner",
        "Ansprechpartnerin",
        "Ihr Ansprechpartner",
        "Bei Fragen",
        "Interlocuteur",
        "Votre contact",
        "Referente",
        "Contactpersoon",
        "Persona de contacto",
        "Kontaktperson",
    ],
)
def test_the_word_that_introduces_a_contact_is_recognised(label):
    """ "Ansprechpartner" is simply the German for this, and it is the label
    above the recruiter's name on a large share of DACH ads. It matched
    nothing."""
    assert CONTACT_KEYWORDS.search(label)


# --------------------------------------------------------------------------
# Job-page detection
# --------------------------------------------------------------------------


def _page(body: str) -> BeautifulSoup:
    return BeautifulSoup(f"<html><body>{body}</body></html>", "html.parser")


@pytest.mark.parametrize(
    "body",
    [
        "<h1>Développeur</h1><p>Vos missions</p><p>Votre profil</p><p>Postuler</p>",
        "<h1>Sviluppatore</h1><p>Mansioni</p><p>Requisiti</p><p>Candidatura</p>",
        "<h1>Desarrollador</h1><p>Funciones</p><p>Requisitos</p>",
        "<h1>Ontwikkelaar</h1><p>Taken</p><p>Vereisten</p><p>Solliciteren</p>",
        "<h1>Entwickler</h1><p>Ihr Profil</p><p>Wir bieten</p><p>Jetzt bewerben</p>",
        "<h1>Utvecklare</h1><p>Arbetsuppgifter</p><p>Kvalifikationer</p>",
    ],
)
def test_non_english_postings_are_recognised_as_job_pages(body):
    """The signal list was English plus a little German and Norwegian, and
    needed two hits. A French, Italian, Spanish or Dutch posting scored zero,
    so `universal_extract` returned None and every field it would have filled
    was lost — across most of the continent."""
    assert _looks_like_job_page(_page(body), "https://firma.example/detail/1") is True


def test_an_ordinary_marketing_page_is_still_rejected():
    body = "<h1>About us</h1><p>We are a company that makes software.</p>"
    assert _looks_like_job_page(_page(body), "https://firma.example/about") is False
