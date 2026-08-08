"""Infer a domain's address format from known (name, email) pairs.

This is the force multiplier for the whole run. A job ad that names its
recruiter gives us one anchor address; that anchor reveals the format, and the
format turns every other named person on the domain into a deliverable address
instead of a guess across eight candidate layouts.

Conflicting examples deliberately yield nothing. A domain running two formats
cannot be extrapolated safely, and a wrong address is worse than a missing one
because it bounces.
"""

from __future__ import annotations

import unicodedata

from ..person.roles import split_name
from .validate import is_role_account

# Ordered most-specific first: the first pattern consistent with every example
# is the least likely to be a coincidence.
CANDIDATE_PATTERNS: list[str] = [
    "first.last",
    "f.last",
    "flast",
    "firstlast",
    "last.first",
    "first_last",
    "first",
    "last",
]

# German/Nordic transliterations that ASCII folding alone gets wrong: NFKD turns
# "ö" into "o", but the German address convention is "oe".
_TRANSLITERATE = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "å": "aa",
    "æ": "ae",
    "ø": "oe",
}


def _slug(value: str) -> str:
    """Lowercase, transliterate, strip everything that cannot be in a local part."""
    lowered = (value or "").strip().lower()
    for source, target in _TRANSLITERATE.items():
        lowered = lowered.replace(source, target)
    decomposed = unicodedata.normalize("NFKD", lowered)
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return "".join(char for char in ascii_only if char.isalnum())


def apply_pattern(pattern: str, first: str, last: str, domain: str) -> str:
    """Render an address, or "" when the pattern's inputs are not available."""
    if not domain:
        return ""
    first_slug, last_slug = _slug(first), _slug(last)
    both = first_slug and last_slug
    local = {
        "first.last": f"{first_slug}.{last_slug}" if both else "",
        "f.last": f"{first_slug[:1]}.{last_slug}" if both else "",
        "flast": f"{first_slug[:1]}{last_slug}" if both else "",
        "firstlast": f"{first_slug}{last_slug}" if both else "",
        "last.first": f"{last_slug}.{first_slug}" if both else "",
        "first_last": f"{first_slug}_{last_slug}" if both else "",
        "first": first_slug,
        "last": last_slug,
    }.get(pattern, "")
    return f"{local}@{domain.lower()}" if local else ""


def infer_pattern(known: list[tuple[str, str]]) -> tuple[str, str]:
    """Return (pattern, confidence) consistent with every known pair.

    confidence is "high" with two or more agreeing examples, "medium" with
    exactly one, and "" when the examples disagree or none are usable.
    """
    usable: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for full_name, email in known:
        address = (email or "").strip().lower()
        if not full_name or "@" not in address or is_role_account(address):
            continue
        if address in seen:
            continue
        first, last = split_name(full_name)
        if not (first or last):
            continue
        seen.add(address)
        usable.append((first, last, address))

    if not usable:
        return "", ""

    matching = [
        pattern
        for pattern in CANDIDATE_PATTERNS
        if all(
            apply_pattern(pattern, first, last, address.partition("@")[2]) == address
            for first, last, address in usable
        )
    ]
    if not matching:
        return "", ""
    return matching[0], "high" if len(usable) >= 2 else "medium"
