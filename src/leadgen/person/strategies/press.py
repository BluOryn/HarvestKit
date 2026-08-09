"""Quote attribution in press and news copy.

Companies that publish nothing resembling a team page still name their
executives in press releases: "…, said Anna Schmidt, CTO of Acme". The
attribution grammar is narrow enough to match reliably in several languages,
and it is often the only place a mid-sized firm names anyone.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..hit import PersonHit
from ..roles import classify_role

# A name: two to four capitalised tokens, particles allowed.
_NAME = r"(?:[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’-]+\s+){1,3}[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’-]+"
# The role phrase runs to the next clause boundary. It is deliberately not cut
# at "of": "Head of HR" would become "Head", which classifies as nothing.
# _trim_company() removes the employer afterwards, when it can tell.
_ROLE = r"[A-Za-zÀ-ÖØ-öø-ÿ][^,.;:!?]{2,70}"

_SAY = r"(?:said|says|explains|explained|adds|added|erkl[äa]rt|sagt|betont|d[ée]clare|afferma|dice)"

PATTERNS: list[str] = [
    # "said Anna Schmidt, CTO of Acme Software."
    rf"{_SAY}\s+(?P<name>{_NAME}),\s*(?P<role>{_ROLE})",
    # "Anna Schmidt, CTO of Acme, said" / "…, Technischer Leiter bei Acme, sagt"
    rf"(?P<name>{_NAME}),\s*(?P<role>{_ROLE}),\s*{_SAY}\b",
]
_COMPILED = [re.compile(pattern, re.UNICODE) for pattern in PATTERNS]

_ENTITY_RX = re.compile(r"\b(?:gmbh|ag|ltd|inc|llc|bv|nv|s\.?a|s\.?r\.?l)\b", re.I)
# Connectors that introduce the employer after a title.
_CONNECTOR_RX = re.compile(r"\s+(?:of|at|bei|von|chez|di|presso|en)\s+", re.I)


def _trim_company(role: str) -> str:
    """Drop the trailing employer clause when the title still classifies without it.

    "Head of HR at Acme" -> "Head of HR"   (cutting at "of" would give "Head")
    "CTO of Acme"        -> "CTO"
    """
    candidates = [role[: match.start()] for match in _CONNECTOR_RX.finditer(role)]
    for candidate in candidates:  # shortest first — finditer is left to right
        if classify_role(candidate) != "other":
            return candidate.strip()
    return role.strip()


def extract(html: str, url: str) -> list[PersonHit]:
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    hits: list[PersonHit] = []
    seen: set[str] = set()
    for pattern in _COMPILED:
        for match in pattern.finditer(text):
            name = match.group("name").strip()
            role = _trim_company(match.group("role").strip(" ,."))
            if _ENTITY_RX.search(name) or name.lower() in seen:
                continue
            # Press copy is noisy, so only keep people whose title is one of the
            # two families this run targets. An unclassifiable phrase here is far
            # more likely to be a sentence fragment than a real job title.
            if classify_role(role) == "other":
                continue
            seen.add(name.lower())
            hits.append(PersonHit(name=name, role=role, source_url=url, strategy="press"))
    return hits
