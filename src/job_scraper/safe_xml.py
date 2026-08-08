"""Hardened XML parsing for untrusted remote documents.

Sitemaps and Personio feeds are fetched from third-party hosts and parsed on our
machine. `xml.etree.ElementTree` does not fetch external entities, but it *does*
expand internal ones — so a hostile or compromised feed can hand us a
"billion laughs" bomb and exhaust memory.

Strategy:
  1. Use `defusedxml` when installed (it blocks entity expansion outright).
  2. Otherwise refuse any document that declares a DOCTYPE or ENTITY, and cap
     the input size, before handing it to the stdlib parser.
"""

from __future__ import annotations

import logging
import re
from xml.etree import ElementTree as ET

__all__ = ["ParseError", "MAX_XML_BYTES", "fromstring"]

ParseError = ET.ParseError

# Well over the largest legitimate sitemap (50k URLs / 50 MB uncompressed is the
# spec cap, but a single sitemap document that big is pathological for our use).
MAX_XML_BYTES = 16 * 1024 * 1024

_DOCTYPE_RX = re.compile(r"<!\s*(DOCTYPE|ENTITY)\b", re.I)

try:  # pragma: no cover - depends on the installed environment
    from defusedxml.ElementTree import fromstring as _defused_fromstring
except ImportError:  # pragma: no cover
    _defused_fromstring = None


def fromstring(text: str, *, source: str = "") -> ET.Element | None:
    """Parse XML from an untrusted source. Returns None if unsafe or malformed."""
    if not text:
        return None
    if len(text) > MAX_XML_BYTES:
        logging.warning("xml: refusing oversized document (%d bytes) from %s", len(text), source or "?")
        return None

    if _defused_fromstring is not None:
        try:
            return _defused_fromstring(text)
        except ET.ParseError as exc:
            logging.debug("xml: parse failed for %s: %s", source or "?", exc)
            return None
        except Exception as exc:
            # defusedxml raises its own DefusedXmlException subclasses when it
            # blocks a bomb. That is an attack signal, not a malformed feed.
            logging.warning("xml: refusing unsafe document from %s: %s", source or "?", exc)
            return None

    # No defusedxml — reject entity/DTD declarations rather than expand them.
    # Scan the whole document, not just the head: the XML prolog may hold
    # arbitrarily long comments, so a bomb can be pushed past any fixed window.
    if _DOCTYPE_RX.search(text):
        logging.warning(
            "xml: refusing document with a DOCTYPE/ENTITY declaration from %s "
            "(install defusedxml to parse these safely)",
            source or "?",
        )
        return None
    try:
        return ET.fromstring(text)  # noqa: S314 - guarded above
    except ET.ParseError as exc:
        logging.debug("xml: parse failed for %s: %s", source or "?", exc)
        return None
