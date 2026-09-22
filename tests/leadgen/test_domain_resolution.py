"""Resolving a company to its own site, and to the domain its mail lives on.

Four separate failures used to live in this one function, and every one of them
deleted companies from the run rather than degrading them:

* a bot wall on the homepage read as "this company has no website";
* the hint host returned verbatim, so `careers.sap.com` became the mail domain
  and every address for that employer failed the MX gate;
* no check at all that the accepted site belongs to the company, so the largest
  Austrian job board passed as an employer's own site;
* a stated `company_website` discarded because its homepage answered 403.
"""

from __future__ import annotations

from leadgen.company.domain import (
    SiteResolution,
    body_mentions_company,
    host_matches_name,
    is_company_site,
    mail_domain_for,
    resolve_domain,
    resolve_site,
)


class Fetching:
    """A stub `HttpClient` whose `fetch` reports status, like the real one."""

    def __init__(self, responses: dict[str, tuple[int, str]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    def fetch(self, url, *args, **kwargs):
        self.calls.append(url)
        for fragment, (status, body) in self.responses.items():
            if fragment in url:
                return _Response(status, body)
        return _Response(0, "", error="getaddrinfo failed")


class _Response:
    def __init__(self, status: int, text: str, error: str = "") -> None:
        self.status = status
        self.text = text
        self.error = error
        self.outcome = _Outcome("ok" if 200 <= status < 300 else "blocked" if status else "error")


class _Outcome:
    def __init__(self, value: str) -> None:
        self.value = value


# --------------------------------------------------------------- mail domain


def test_a_career_subdomain_resolves_to_the_domain_that_has_mx():
    """karriere.sap.com has no MX; sap.com does. Every address depended on this."""
    assert mail_domain_for("karriere.sap.com") == "sap.com"
    assert mail_domain_for("jobs.zalando.de") == "zalando.de"
    assert mail_domain_for("careers.hellofresh.de") == "hellofresh.de"
    assert mail_domain_for("www.acme.co.uk") == "acme.co.uk"


def test_the_site_to_crawl_and_the_domain_to_mail_are_kept_apart():
    http = Fetching({"karriere.sap.com": (200, "<title>SAP Karriere</title>")})
    site = resolve_site("SAP", ["https://karriere.sap.com/jobs/1"], http)
    assert site.host == "karriere.sap.com", "crawl the host the ad pointed at"
    assert site.domain == "sap.com", "but mail the registrable domain"
    assert site.website == "https://karriere.sap.com/"


# ------------------------------------------------------------------- blocked


def test_a_bot_wall_keeps_the_company_instead_of_deleting_it():
    """403 means the host exists and refused us — not that it is not the company."""
    http = Fetching({"hellofresh.de": (403, "<html>Access denied</html>")})
    site = resolve_site(
        "HelloFresh SE",
        ["https://www.hellofresh.de/careers/1"],
        http,
        stated=["https://www.hellofresh.de/careers/1"],
    )
    assert site.domain == "hellofresh.de"
    assert site.blocked is True


def test_a_rate_limit_is_also_not_a_missing_company():
    http = Fetching({"personio.example": (429, "slow down")})
    assert resolve_domain("Personio Example", ["https://personio.example/x"], http) == "personio.example"


def test_a_genuine_404_on_the_apex_is_still_a_rejection():
    http = Fetching({"nothing.example": (404, "not found")})
    assert resolve_domain("Nothing Ltd", ["https://nothing.example/x"], http) == ""


def test_a_host_that_does_not_resolve_is_rejected():
    assert resolve_domain("Ghost", ["https://ghost.invalid/x"], Fetching()) == ""


def test_a_stated_website_survives_a_homepage_that_never_answers():
    """The seed API asserted this is the employer's site; DNS failing our probe
    is not evidence against that, and dropping it loses the company."""
    http = Fetching()
    site = resolve_site(
        "Eurodyn",
        ["https://eurodyn.com"],
        http,
        stated=["https://eurodyn.com"],
    )
    assert site.domain == "eurodyn.com"
    assert site.stated is True


# ------------------------------------------------------------- corroboration


def test_the_biggest_austrian_job_board_is_not_a_companys_own_site():
    """karriere.at was not on the old denylist, so it passed as the employer."""
    assert is_company_site("https://www.karriere.at/jobs/123") is False
    for url in (
        "https://www.indeed.de/cmp/Acme",
        "https://www.stepstone.nl/vacature/1",
        "https://www.jobup.ch/de/jobs/1",
        "https://glassdoor.de/x",
        "https://acme.myworkdaysite.com/x",
        "https://www.hellowork.com/x",
    ):
        assert is_company_site(url) is False, url


def test_the_domain_stem_spelling_the_name_is_enough_corroboration():
    assert host_matches_name("acme.de", "Acme GmbH") is True
    assert host_matches_name("acme-software.de", "Acme Software GmbH & Co. KG") is True
    assert host_matches_name("mueller.de", "Müller AG") is True
    assert host_matches_name("bmw.de", "Bayerische Motoren Werke") is True
    assert host_matches_name("completely-different.com", "Acme GmbH") is False


def test_a_page_that_names_the_company_corroborates_it():
    page = "<html><head><title>Beispiel Technik GmbH — Startseite</title></head></html>"
    assert body_mentions_company(page, "Beispiel Technik GmbH") is True
    assert body_mentions_company(page, "Andere Firma AG") is False


def test_an_uncorroborated_host_loses_to_a_corroborated_one():
    http = Fetching(
        {
            "somewhere-else.com": (200, "<title>Somewhere Else</title>"),
            "acme.de": (200, "<title>Acme GmbH</title>"),
        }
    )
    site = resolve_site(
        "Acme GmbH",
        ["https://somewhere-else.com/x", "https://acme.de/jobs"],
        http,
    )
    assert site.host == "acme.de"
    assert site.corroborated is True


def test_a_parked_page_is_never_a_company_site():
    http = Fetching({"acme.de": (200, "<html><body>This domain is for sale</body></html>")})
    assert resolve_domain("Acme GmbH", ["https://acme.de/"], http) == ""


# ------------------------------------------------------------------ hygiene


def test_a_host_is_probed_once_however_many_hints_name_it():
    http = Fetching({"acme.de": (200, "<title>Acme</title>")})
    resolve_site("Acme", ["https://acme.de/a", "https://acme.de/b", "https://www.acme.de/c"], http)
    assert len(http.calls) == 1


def test_an_empty_resolution_is_falsey():
    assert not SiteResolution()
    assert SiteResolution(domain="acme.de")
