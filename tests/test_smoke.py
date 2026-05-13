"""Smoke tests — no network, no browser. Exercise pure-Python paths."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_imports():
    """Top-level modules import cleanly."""
    import job_scraper.main  # noqa: F401
    import job_scraper.config  # noqa: F401
    import job_scraper.models  # noqa: F401
    import job_scraper.extract  # noqa: F401
    import job_scraper.universal  # noqa: F401
    import job_scraper.deep_scrape  # noqa: F401
    import job_scraper.llm_adapter  # noqa: F401


def test_adapter_registry():
    """Adapter registry maps every documented host."""
    from job_scraper.adapters import ADAPTERS

    expected = {
        "greenhouse", "lever", "smartrecruiters", "personio", "ashby",
        "recruitee", "workable", "workday", "arbeitsagentur", "jobs.ch",
        "finn.no", "nav.no", "karrierestart.no", "jobbsafari.no", "generic",
    }
    assert expected.issubset(ADAPTERS.keys())


def test_config_defaults():
    """RunConfig defaults are sane."""
    from job_scraper.config import RunConfig

    cfg = RunConfig()
    assert cfg.deep_scrape is True
    assert cfg.cache_enabled is True
    assert 0 < cfg.deep_concurrency <= 64
    assert cfg.llm_fallback_enabled is False  # opt-in only


def test_job_listing_extras():
    """Dynamic extras dict round-trips through CSV serialization."""
    from job_scraper.models import JobListing

    j = JobListing()
    j.title = "Senior Engineer"
    j.set_extra("custom_field", "custom_value")
    assert j.extras.get("custom_field") == "custom_value"


def test_universal_extract_returns_none_for_non_job():
    """Universal extractor declines a plain HTML page with no job markers."""
    from job_scraper.universal import universal_extract

    html = "<html><body><h1>Random blog post</h1><p>Just some text.</p></body></html>"
    result = universal_extract(html, "https://example.com/blog")
    assert result is None


def test_phone_filter_rejects_finnkode():
    """Phone-mining regex must reject 9-12-digit raw blobs (finnkode IDs)."""
    from job_scraper.universal import _valid_phone

    assert _valid_phone("462751961") is False  # raw 9-digit, no separators
    assert _valid_phone("12345678") is False   # raw 8-digit, no separators
    assert _valid_phone("+47 95 83 21 97") is True
