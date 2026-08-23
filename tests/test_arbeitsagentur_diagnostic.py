"""A blocked host and a moved endpoint both look like zero listings.

They need opposite responses -- one is "get permission", the other is "find the
new path" -- so reporting the wrong one sends whoever is on call chasing a
phantom. This happened for real: five Bundesagentur targets in the shipped EU
config returned nothing for as long as they existed, while the log insisted the
service had moved.
"""

from __future__ import annotations

import logging

from job_scraper.adapters import arbeitsagentur


class _Http:
    """Answers nothing, and reports whether robots would have allowed it."""

    def __init__(self, robots_allows: bool) -> None:
        self._allows = robots_allows

    def robots_allows(self, url: str) -> bool:
        return self._allows

    def get_json(self, url, headers=None, use_cache=True):
        return None


def test_a_robots_block_is_reported_as_a_permission_boundary(caplog):
    arbeitsagentur._reset_base_cache()
    with caplog.at_level(logging.WARNING):
        assert arbeitsagentur.resolve_base(_Http(robots_allows=False)) == ""
    message = caplog.text
    assert "robots.txt" in message and "permission boundary" in message
    assert "probably moved" not in message


def test_a_genuinely_dead_endpoint_still_reports_as_moved(caplog):
    arbeitsagentur._reset_base_cache()
    with caplog.at_level(logging.WARNING):
        assert arbeitsagentur.resolve_base(_Http(robots_allows=True)) == ""
    assert "probably moved" in caplog.text


def test_the_shipped_eu_config_declares_no_unreachable_targets():
    """The config used to ship five targets that could never return a row."""
    from job_scraper.config import load_config, resolve_config_path

    config = load_config(resolve_config_path("configs/leads/eu-it.yaml"))
    assert [target.adapter for target in config.targets if target.adapter == "arbeitsagentur"] == []
