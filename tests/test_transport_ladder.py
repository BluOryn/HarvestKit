"""The escalation ladder, and the classification it depends on.

Before this existed, `HttpClient.get()` returned `None` for a 403 and `None`
for a page with no content, so nothing downstream could tell a bot wall from an
empty site. A run therefore reported thousands of companies as "naming nobody"
when the truth was that we never saw their pages — and the runbook's advice for
that symptom sent the reader to the team-page parser, which was innocent.
"""

from __future__ import annotations

from job_scraper.transport import (
    FetchResponse,
    Outcome,
    Transport,
    TransportLadder,
    TransportMemory,
    classify,
    registrable_domain,
)

REAL_PAGE = (
    "<!DOCTYPE html><html><head><title>Unser Team</title>"
    '<meta property="og:title" content="Team"></head>'
    "<body><main><h1>Team</h1><p>Dr. Anna Meier, Geschäftsführerin</p></main>"
    "<footer>Impressum</footer></body></html>"
)

CLOUDFLARE_CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    '<body><div id="cf-content">Enable JavaScript and cookies to continue</div>'
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1"></script>'
    "</body></html>"
)

DATADOME_CHALLENGE = (
    "<html><head><title>Blocked</title></head><body>"
    '<script src="https://geo.captcha-delivery.com/captcha/"></script></body></html>'
)


class _Scripted(Transport):
    """A transport that returns a prepared answer and counts its calls."""

    def __init__(self, name: str, rung: int, answer, available: bool = True) -> None:
        self.name = name
        self.rung = rung
        self._answer = answer
        self._available = available
        self.calls = 0

    @property
    def available(self) -> bool:
        return self._available

    def fetch(self, url, **kwargs) -> FetchResponse:
        self.calls += 1
        status, text, error = self._answer
        return FetchResponse(
            url=url,
            final_url=url,
            status=status,
            text=text,
            outcome=classify(status, text) if not error else Outcome.ERROR,
            transport=self.name,
            error=error,
        )


def _ok(text=REAL_PAGE):
    return (200, text, "")


def _blocked(status=403, text=""):
    return (status, text, "")


def _missing():
    return (404, "<html><body>Not found</body></html>", "")


def _dns_error():
    return (0, "", "NameResolutionError: Failed to resolve 'nope.invalid'")


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------


def test_a_403_is_a_block():
    assert classify(403, "") is Outcome.BLOCKED


def test_a_404_is_not_a_block():
    """Escalating a genuinely absent page to a browser is pure waste."""
    assert classify(404, "<html>gone</html>") is Outcome.NOT_FOUND


def test_a_429_is_rate_limiting_not_blocking():
    assert classify(429, "") is Outcome.RATE_LIMITED


def test_a_real_page_is_ok():
    assert classify(200, REAL_PAGE) is Outcome.OK


def test_a_cloudflare_challenge_served_with_http_200_is_a_block():
    """The one that mattered most. A 200 whose body is an interstitial used to
    sail through as content, and the extractor then mined a challenge page for
    people and found none."""
    assert classify(200, CLOUDFLARE_CHALLENGE) is Outcome.BLOCKED


def test_a_datadome_challenge_is_a_block():
    assert classify(200, DATADOME_CHALLENGE) is Outcome.BLOCKED


def test_a_challenge_header_is_enough_even_when_the_body_looks_fine():
    assert classify(200, REAL_PAGE, {"cf-mitigated": "challenge"}) is Outcome.BLOCKED


def test_an_empty_200_is_a_block():
    assert classify(200, "   ") is Outcome.BLOCKED


def test_a_long_real_page_mentioning_access_denied_is_not_a_block():
    """The false positive the old heuristic produced: plenty of legitimate
    pages carry the words "access denied" in a cookie notice or a help section,
    and throwing those away loses real leads."""
    body = REAL_PAGE + "<p>access denied</p>" + ("<p>filler paragraph</p>" * 500)
    assert classify(200, body) is Outcome.OK


def test_a_5xx_is_the_origins_problem_not_a_block():
    assert classify(502, "<html>bad gateway</html>") is Outcome.SERVER_ERROR


def test_a_503_carrying_a_challenge_is_still_a_block():
    assert classify(503, CLOUDFLARE_CHALLENGE) is Outcome.BLOCKED


# --------------------------------------------------------------------------
# escalation
# --------------------------------------------------------------------------


def test_a_block_escalates_to_the_next_rung():
    cheap = _Scripted("requests", 0, _blocked())
    strong = _Scripted("impersonate", 1, _ok())
    result = TransportLadder([cheap, strong]).fetch("https://example.com/")
    assert result.ok
    assert result.transport == "impersonate"
    assert cheap.calls == 1 and strong.calls == 1


def test_a_success_on_the_cheap_rung_never_pays_for_the_expensive_one():
    cheap = _Scripted("requests", 0, _ok())
    strong = _Scripted("impersonate", 1, _ok())
    result = TransportLadder([cheap, strong]).fetch("https://example.com/")
    assert result.transport == "requests"
    assert strong.calls == 0


def test_a_404_does_not_escalate():
    cheap = _Scripted("requests", 0, _missing())
    strong = _Scripted("impersonate", 1, _ok())
    result = TransportLadder([cheap, strong]).fetch("https://example.com/gone")
    assert result.outcome is Outcome.NOT_FOUND
    assert strong.calls == 0, "a missing page is missing for every client"


def test_a_dns_failure_does_not_escalate():
    """A name that does not resolve will not resolve for a fancier client, and
    at lead-run scale there are thousands of dead domains."""
    cheap = _Scripted("requests", 0, _dns_error())
    strong = _Scripted("impersonate", 1, _ok())
    result = TransportLadder([cheap, strong]).fetch("https://nope.invalid/")
    assert result.outcome is Outcome.ERROR
    assert strong.calls == 0


def test_a_connection_error_does_escalate():
    """Unlike DNS, a reset or a TLS handshake failure can genuinely differ
    between clients — that is much of what the impersonation rung fixes."""
    cheap = _Scripted("requests", 0, (0, "", "SSLError: handshake failure"))
    strong = _Scripted("impersonate", 1, _ok())
    result = TransportLadder([cheap, strong]).fetch("https://example.com/")
    assert result.ok and strong.calls == 1


def test_every_rung_blocked_returns_the_last_block_rather_than_nothing():
    """The caller must still learn it was blocked. Returning None here is the
    original bug."""
    cheap = _Scripted("requests", 0, _blocked())
    strong = _Scripted("impersonate", 1, _blocked(403, CLOUDFLARE_CHALLENGE))
    result = TransportLadder([cheap, strong]).fetch("https://example.com/")
    assert result.outcome is Outcome.BLOCKED
    assert result.transport == "impersonate"


def test_an_unavailable_rung_is_skipped():
    cheap = _Scripted("requests", 0, _blocked())
    missing = _Scripted("impersonate", 1, _ok(), available=False)
    strong = _Scripted("browser", 2, _ok())
    result = TransportLadder([cheap, missing, strong]).fetch("https://example.com/")
    assert result.transport == "browser"
    assert missing.calls == 0


def test_max_rung_keeps_a_caller_off_the_expensive_transport():
    """robots.txt uses this: launching a browser to read a text file is never
    worth it."""
    cheap = _Scripted("requests", 0, _blocked())
    strong = _Scripted("browser", 2, _ok())
    result = TransportLadder([cheap, strong]).fetch("https://example.com/", max_rung=0)
    assert strong.calls == 0
    assert result.outcome is Outcome.BLOCKED


def test_escalation_can_be_switched_off_entirely():
    cheap = _Scripted("requests", 0, _blocked())
    strong = _Scripted("impersonate", 1, _ok())
    ladder = TransportLadder([cheap, strong], escalate=False)
    assert ladder.fetch("https://example.com/").outcome is Outcome.BLOCKED
    assert strong.calls == 0


# --------------------------------------------------------------------------
# memory
# --------------------------------------------------------------------------


def test_the_winning_rung_is_remembered_and_reused(tmp_path):
    """Without this, every URL on a walled domain re-pays the 403 that taught
    us nothing new."""
    memory = TransportMemory(str(tmp_path / "m.sqlite"))
    try:
        cheap = _Scripted("requests", 0, _blocked())
        strong = _Scripted("impersonate", 1, _ok())
        ladder = TransportLadder([cheap, strong], memory)

        ladder.fetch("https://walled.de/one")
        assert cheap.calls == 1 and strong.calls == 1

        ladder.fetch("https://walled.de/two")
        assert cheap.calls == 1, "the second URL must start at the rung that worked"
        assert strong.calls == 2
    finally:
        memory.close()


def test_memory_is_keyed_on_the_registrable_domain_not_the_hostname(tmp_path):
    """One WAF usually fronts every subdomain, so what we learn about
    `www.acme.de` should apply to `careers.acme.de`."""
    memory = TransportMemory(str(tmp_path / "m.sqlite"))
    try:
        cheap = _Scripted("requests", 0, _blocked())
        strong = _Scripted("impersonate", 1, _ok())
        ladder = TransportLadder([cheap, strong], memory)
        ladder.fetch("https://www.acme.de/")
        ladder.fetch("https://careers.acme.de/")
        assert cheap.calls == 1
    finally:
        memory.close()


def test_registrable_domain_collapses_subdomains():
    assert registrable_domain("https://careers.acme.de/jobs") == "acme.de"
    assert registrable_domain("https://www.acme.co.uk/") == "acme.co.uk"


def test_a_ladder_with_no_usable_transport_reports_that_rather_than_crashing():
    dead = _Scripted("requests", 0, _ok(), available=False)
    result = TransportLadder([dead]).fetch("https://example.com/")
    assert result.outcome is Outcome.ERROR
    assert "no transport" in result.error
