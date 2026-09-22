"""The CLI and the extension must canonicalise a URL identically.

Both halves write into the same `id` column, so a disagreement here does not
raise — it silently produces two rows for one posting, and nothing anywhere
reports it. Seven of these vectors used to diverge, all of them on European
URLs: the extension ran the string through the WHATWG serialiser, which
punycodes IDN hosts and percent-encodes accented path bytes, and Python's
urlparse did neither.

The same file is asserted by the extension's own suite
(extension/tests/canonicalUrl.test.mjs), which is the point: one fixture, two
implementations, no way to change one without the other failing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_scraper.models import canonicalize_url

VECTORS_PATH = Path(__file__).resolve().parents[1] / "extension" / "tests" / "canonical-vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("case", VECTORS, ids=[case["why"] for case in VECTORS])
def test_python_matches_the_shared_canonical_form(case):
    assert canonicalize_url(case["in"]) == case["out"]


def test_the_fixture_is_actually_shared_with_the_extension():
    """A vector file the extension does not read would prove nothing."""
    suite = VECTORS_PATH.parent / "canonicalUrl.test.mjs"
    assert suite.is_file(), "the extension-side assertion is missing"
    assert "canonical-vectors.json" in suite.read_text(encoding="utf-8")


def test_canonicalisation_is_idempotent():
    """Running it twice must not move the string, or an id would depend on how
    many times a URL had been through the pipeline."""
    for case in VECTORS:
        once = canonicalize_url(case["in"])
        assert canonicalize_url(once) == once, case["why"]
