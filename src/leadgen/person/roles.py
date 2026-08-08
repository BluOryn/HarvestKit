r"""Classify a job title into the role families this run targets.

Patterns are data, not code — adding a language is editing a list. Every
alternative is matched under \b word boundaries so "cto" cannot fire inside
"Director" and "hr" cannot fire inside "Thrive", which a naive substring test
gets wrong on real title data.
"""

from __future__ import annotations

import re

HR_PATTERNS: list[str] = [
    r"h\.?r\.?",
    r"human resources",
    r"people (?:operations|ops|team|partner|lead|manager|director)",
    r"talent(?: acquisition| partner| manager| lead)?",
    r"recruit\w*",
    r"rekruter\w*",
    r"personal(?:leiter|leiterin|wesen|abteilung|referent\w*|management|chef\w*)",
    r"personnel",
    r"ressources humaines",
    r"risorse umane",
    r"recursos humanos",
    r"personeelszaken",
    r"rekrytering\w*",
]

TECH_LEADERSHIP_PATTERNS: list[str] = [
    r"c\.?t\.?o\.?",
    r"c\.?i\.?o\.?",
    r"chief technology officer",
    r"chief technical officer",
    r"chief information officer",
    r"v\.?p\.? (?:of )?engineering",
    r"vice president (?:of )?engineering",
    r"head of (?:engineering|technology|technical|development|it|platform|software|product engineering)",
    r"engineering (?:manager|director|lead|head)",
    r"director of (?:engineering|technology|it)",
    r"technischer? leiter(?:in)?",
    r"leiter(?:in)? (?:der )?(?:it|entwicklung|technik|softwareentwicklung)",
    r"it[- ]leiter(?:in)?",
    r"technische[rn]? direktor(?:in)?",
    r"directeur technique",
    r"direttore tecnico",
    r"director t[ée]cnico",
    r"tech(?:nical)? lead",
]


def _compile(patterns: list[str]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.I)


HR_RX = _compile(HR_PATTERNS)
TECH_RX = _compile(TECH_LEADERSHIP_PATTERNS)

# Honorifics and post-nominals that are not part of a person's name.
_TITLE_RX = re.compile(r"^(?:dr|prof|dipl|ing|mag|mr|mrs|ms|herr|frau|m|mme|sr|sra)\.?\s+", re.I)
# Dutch/German/Iberian nobiliary particles belong with the surname.
_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "la", "le", "ter"}


def classify_role(title: str) -> str:
    """Return "hr", "tech_leadership" or "other"."""
    if not title:
        return "other"
    # HR is checked first: "Leiter Personalwesen" contains a leadership word but
    # is unambiguously an HR role.
    if HR_RX.search(title):
        return "hr"
    if TECH_RX.search(title):
        return "tech_leadership"
    return "other"


def is_target_role(title: str) -> bool:
    return classify_role(title) in ("hr", "tech_leadership")


def split_name(full: str) -> tuple[str, str]:
    """Split a display name into (first, last). Particles stay with the surname."""
    cleaned = _TITLE_RX.sub("", (full or "").strip())
    # A second honorific ("Dr. Prof. Anna") is stripped too.
    cleaned = _TITLE_RX.sub("", cleaned)
    parts = cleaned.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    for index in range(1, len(parts)):
        if parts[index].lower() in _PARTICLES:
            return " ".join(parts[:index]), " ".join(parts[index:])
    return " ".join(parts[:-1]), parts[-1]
