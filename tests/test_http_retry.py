"""A remote host must never be able to stall the run indefinitely."""

from __future__ import annotations

from job_scraper.http import _MAX_RETRY_AFTER_SECONDS, HttpClient, _retry_after_seconds


def _client() -> HttpClient:
    return HttpClient(user_agent="test", delay_seconds=0.0, obey_robots=False, cache_enabled=False)


def _retry_config(client: HttpClient):
    return client._session.get_adapter("https://example.com").max_retries


def test_urllib3_is_not_allowed_to_honour_retry_after_unboundedly():
    """urllib3 sleeps for Retry-After *inside the adapter*, uncapped, while the
    caller still holds its per-host slot. A host answering "Retry-After: 3600"
    would park every worker behind it: no CPU, no log line, no error.

    Observed against a search API after a burst — the run stopped dead.
    """
    client = _client()
    try:
        assert _retry_config(client).respect_retry_after_header is False
    finally:
        client.close()


def test_429_is_handled_here_not_swallowed_by_urllib3():
    """With 429 in urllib3's forcelist it retries, then raises MaxRetryError,
    which arrives as a generic RequestException — so the branch that logs the
    rate limit never runs and a blocked host looks like one with no results.

    Observed live: 122 of 166 search queries returned "0 pages" and not one
    line said why.
    """
    client = _client()
    try:
        assert 429 not in (_retry_config(client).status_forcelist or ())
    finally:
        client.close()


def test_urllib3_backoff_is_bounded():
    client = _client()
    try:
        assert _retry_config(client).backoff_max <= _MAX_RETRY_AFTER_SECONDS
    finally:
        client.close()


def test_our_own_retry_after_parsing_caps_absurd_values():
    """The header is still respected, just never unboundedly."""
    assert _retry_after_seconds("3600") == _MAX_RETRY_AFTER_SECONDS
    assert _retry_after_seconds("5") == 5.0
    assert _retry_after_seconds("Wed, 21 Oct 2099 07:28:00 GMT") == _MAX_RETRY_AFTER_SECONDS
    assert _retry_after_seconds("garbage") > 0
    assert _retry_after_seconds(None) > 0


def test_a_negative_or_past_retry_after_does_not_go_negative():
    assert _retry_after_seconds("-10") == 0.0
    assert _retry_after_seconds("Wed, 21 Oct 1999 07:28:00 GMT") == 0.0
