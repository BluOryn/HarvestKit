"""A request must describe one browser, consistently.

The header builder this replaced drew a User-Agent at random on every request
and then attached `Sec-CH-UA: "Chromium";v="120"` regardless of what it had
drawn. One host would see a Safari UA announce itself as Chromium 120, then a
Firefox UA do the same, all over a single connection. Every one of those is a
stronger bot signal than sending no client hints at all, because no real
browser can produce them.
"""

from __future__ import annotations

from job_scraper.identity import (
    IDENTITY_POOL,
    accept_language_for,
    country_for_host,
    headers_for,
    identity_for,
)


def test_a_host_always_sees_the_same_browser():
    """A browser does not change what it is between two page loads."""
    first = identity_for("example.de")
    for _ in range(50):
        assert identity_for("example.de") == first


def test_different_hosts_get_different_browsers_across_the_pool():
    seen = {identity_for(f"host{n}.com").impersonate for n in range(200)}
    assert len(seen) > 1, "every host landing on one identity defeats the point"


def test_a_new_proxy_means_a_new_browser():
    """A fresh egress IP presenting the previous IP's browser hands the site a
    correlation between the two addresses."""
    direct = identity_for("example.de")
    viaproxy = identity_for("example.de", salt="http://proxy-b:8080")
    # Not a guarantee for every host, but it must be possible and stable.
    assert identity_for("example.de", salt="http://proxy-b:8080") == viaproxy
    assert any(
        identity_for(h, salt="http://proxy-b:8080") != identity_for(h) for h in ("a.de", "b.de", "c.de")
    ), "the proxy must be able to change the identity"
    assert direct == identity_for("example.de")


def test_the_client_hints_match_the_user_agent():
    """The exact contradiction the old code shipped."""
    for identity in IDENTITY_POOL:
        headers = headers_for("https://example.com/", identity)
        if not identity.is_chromium:
            assert "Sec-CH-UA" not in headers, f"{identity.impersonate} is not Chromium"
            continue
        version = identity.user_agent.split("Chrome/")[1].split(".")[0]
        assert f'v="{version}"' in headers["Sec-CH-UA"], (
            f"{identity.impersonate} announces Chrome {version} in its UA but "
            f"{headers['Sec-CH-UA']} in its client hints"
        )


def test_the_platform_hint_matches_the_user_agent():
    for identity in IDENTITY_POOL:
        headers = headers_for("https://example.com/", identity)
        if "Sec-CH-UA-Platform" not in headers:
            continue
        platform = headers["Sec-CH-UA-Platform"].strip('"')
        if platform == "Windows":
            assert "Windows NT" in identity.user_agent
        elif platform == "macOS":
            assert "Macintosh" in identity.user_agent


def test_first_contact_with_a_host_claims_no_referring_site():
    """A browser navigating somewhere it was not linked to sends
    `Sec-Fetch-Site: none`. The old code always sent `same-origin`, which with
    no Referer is a combination a real browser cannot produce."""
    headers = headers_for("https://example.com/", identity_for("example.com"))
    assert headers["Sec-Fetch-Site"] == "none"
    assert "Referer" not in headers


def test_a_referred_request_is_consistent_about_it():
    headers = headers_for(
        "https://example.com/jobs/1",
        identity_for("example.com"),
        referer="https://example.com/jobs",
    )
    assert headers["Sec-Fetch-Site"] == "same-origin"
    assert headers["Referer"] == "https://example.com/jobs"


def test_the_impersonating_transport_is_not_handed_a_conflicting_identity():
    """curl_cffi emits a UA and hints matched to the TLS fingerprint it
    presents. Overriding them recreates the original bug one layer down."""
    headers = headers_for("https://example.com/", identity_for("example.com"), for_impersonation=True)
    for name in ("User-Agent", "Sec-CH-UA", "Sec-CH-UA-Platform", "Accept-Encoding"):
        assert name not in headers


def test_the_language_asked_for_suits_the_destination():
    assert accept_language_for("www.siemens.de").startswith("de-DE")
    assert accept_language_for("www.finn.no").startswith("nb-NO")
    assert accept_language_for("www.jobs.ch").startswith("de-CH")
    assert accept_language_for("www.example.fr").startswith("fr-FR")


def test_an_explicit_country_beats_the_domain():
    """A .com belonging to a German company should still be asked in German."""
    assert accept_language_for("www.example.com", "DE").startswith("de-DE")


def test_a_gtld_with_no_known_country_falls_back_rather_than_guessing():
    assert country_for_host("www.example.com") == ""
    assert accept_language_for("www.example.com") == "en-US,en;q=0.9"
    assert country_for_host("www.example.eu") == "", ".eu implies no single country"


def test_every_language_string_offers_an_english_fallback():
    """A header naming exactly one language and nothing else is unusual, and
    unusual is what a fingerprinter scores."""
    for code in ("DE", "NO", "FR", "IT", "PL", "CH"):
        assert "en" in accept_language_for("x.example", code)


def test_no_identity_in_the_pool_is_an_outdated_browser():
    """The pool this replaced was pinned to Chrome 120, released in December
    2023. A stale version is itself a signal."""
    for identity in IDENTITY_POOL:
        if "Chrome/" in identity.user_agent and "Safari" in identity.user_agent:
            major = int(identity.user_agent.split("Chrome/")[1].split(".")[0])
            assert major >= 140, f"{identity.impersonate} is too old to be unremarkable"


def test_headers_never_include_a_do_not_track_header():
    """DNT was removed from Chrome in 2024. Sending it now narrows the
    fingerprint instead of widening it."""
    headers = headers_for("https://example.com/", identity_for("example.com"))
    assert "DNT" not in headers
