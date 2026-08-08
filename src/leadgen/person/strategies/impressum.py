"""German-family legal-notice pages.

§5 TMG makes an Impressum mandatory for commercial German sites and requires it
to name the representatives, which is why this is the highest-yield single
source in DACH. The page is a flat run of labelled lines, so the parser looks
for "<label>: <name>" rather than trying to understand the layout.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..hit import PersonHit
from ..roles import split_name

# Labels that introduce a named human. Ordered longest-first so
# "Vertretungsberechtigter Geschäftsführer" wins over "Geschäftsführer".
LABELS: list[str] = [
    r"vertretungsberechtigter?\s+gesch[äa]ftsf[üu]hrer(?:in)?",
    r"gesch[äa]ftsf[üu]hrend(?:er?|e)\s+gesellschafter(?:in)?",
    r"gesch[äa]ftsf[üu]hrer(?:in|innen)?",
    r"vorstand(?:svorsitzender?|smitglied)?",
    r"inhaber(?:in)?",
    r"gr[üu]nder(?:in)?",
    r"technischer?\s+leiter(?:in)?",
    r"it[-\s]?leiter(?:in)?",
    r"personalleiter(?:in)?",
    r"verantwortlich(?:er?)?\s+f[üu]r\s+den\s+inhalt",
    r"redaktionell\s+verantwortlich",
    r"inhaltlich\s+verantwortlich",
    r"directeur\s+de\s+la\s+publication",
    r"responsable\s+de\s+(?:la\s+)?publication",
    r"g[ée]rant(?:e)?",
    r"amministratore\s+(?:unico|delegato)",
    r"legale\s+rappresentante",
    r"administrador(?:a)?\s+[úu]nico",
    r"bestuurder",
    r"directeur",
]

_LABEL_RX = re.compile(
    r"(?P<label>" + "|".join(LABELS) + r")\s*[:–—-]\s*(?P<value>[^\n\r|;]{2,80})",
    re.I,
)

# A legal entity is not a person.
_ENTITY_RX = re.compile(
    r"\b(?:gmbh|ug|ag|kg|ohg|mbh|e\.?\s?v\.?|s\.?a\.?r\.?l|s\.?p\.?a|s\.?l\.?|b\.?v|n\.?v"
    r"|ltd|inc|llc|plc|oy|ab|a\.?s|sp\.?\s?z\.?\s?o\.?\s?o|s\.?r\.?o)\b",
    re.I,
)
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _looks_like_a_person(name: str) -> bool:
    if not name or _ENTITY_RX.search(name):
        return False
    if any(char.isdigit() for char in name):
        return False
    _, last = split_name(name)
    # A person we can use has at least a surname, and a name of five or more
    # whitespace-separated tokens is a sentence, not a name.
    return bool(last) and 1 < len(name.split()) <= 5


def extract(html: str, url: str) -> list[PersonHit]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    page_emails = _EMAIL_RX.findall(text)

    hits: list[PersonHit] = []
    seen: set[str] = set()
    for match in _LABEL_RX.finditer(text):
        raw_name = match.group("value").strip(" ., ")
        # The value may carry a trailing email; keep the name, note the address.
        inline_email = _EMAIL_RX.search(raw_name)
        if inline_email:
            raw_name = raw_name.replace(inline_email.group(0), "").strip(" .,;-")
        # "Anna Schmidt, Peter Wolf" — take the first, the rest are separate
        # labels in practice and splitting risks mangling "Schmidt, Anna".
        if not _looks_like_a_person(raw_name) or raw_name.lower() in seen:
            continue
        seen.add(raw_name.lower())
        hits.append(
            PersonHit(
                name=raw_name,
                role=match.group("label").strip(),
                # Only attribute a page-level address when there is exactly one
                # candidate; more than one and we cannot tell whose it is.
                email=(
                    inline_email.group(0)
                    if inline_email
                    else (page_emails[0] if len(page_emails) == 1 else "")
                ),
                source_url=url,
                strategy="impressum",
            )
        )
    return hits
