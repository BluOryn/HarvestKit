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
    # "VP, Engineering" is as common as "VP of Engineering" on real team pages,
    # and requiring the "of" silently drops half of them.
    r"v\.?p\.?[,\s]+(?:of\s+)?(?:engineering|technology|platform|infrastructure|data)",
    r"vice president[,\s]+(?:of\s+)?(?:engineering|technology|platform)",
    r"s?vp\s+eng\b",
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


# Company-level decision makers. On EU sites these are the highest-yield names
# available: §5 TMG requires a German Impressum to name the Geschäftsführer, so
# the single most reliable person on any German company site is an executive,
# not an HR manager. Excluding them throws away the best of what the highest-
# yield source produces.
EXECUTIVE_PATTERNS: list[str] = [
    r"c\.?e\.?o\.?",
    r"c\.?o\.?o\.?",
    r"c\.?f\.?o\.?",
    r"chief (?:executive|operating|financial|product|revenue|commercial) officer",
    r"co[-\s]?founder",
    r"founder",
    r"gr[üu]nder(?:in)?",
    r"gesch[äa]ftsf[üu]hrer(?:in)?",
    r"managing director",
    r"general manager",
    r"president",
    r"owner|inhaber(?:in)?",
    r"vorstand(?:svorsitzender?|smitglied)?",
    r"board (?:member|director)",
    r"directeur g[ée]n[ée]ral|direttore generale|director general",
    r"amministratore (?:unico|delegato)",
    r"g[ée]rant(?:e)?",
]


def _compile(patterns: list[str]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.I)


HR_RX = _compile(HR_PATTERNS)
TECH_RX = _compile(TECH_LEADERSHIP_PATTERNS)
EXEC_RX = _compile(EXECUTIVE_PATTERNS)

# Honorifics and post-nominals that are not part of a person's name.
_TITLE_RX = re.compile(r"^(?:dr|prof|dipl|ing|mag|mr|mrs|ms|herr|frau|m|mme|sr|sra)\.?\s+", re.I)
# Dutch/German/Iberian nobiliary particles belong with the surname.
_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "la", "le", "ter"}


TARGET_FAMILIES = ("hr", "tech_leadership", "executive")


def classify_role(title: str) -> str:
    """Return "hr", "tech_leadership", "executive" or "other".

    Order is significance, not string length. HR first, because "Leiter
    Personalwesen" carries a leadership word but is unambiguously HR. Technical
    leadership next, so a "Co-Founder & CTO" is filed under the function the
    buyer actually wants to reach rather than under the equity.
    """
    if not title:
        return "other"
    if HR_RX.search(title):
        return "hr"
    if TECH_RX.search(title):
        return "tech_leadership"
    if EXEC_RX.search(title):
        return "executive"
    return "other"


def is_target_role(title: str) -> bool:
    return classify_role(title) in TARGET_FAMILIES


# How much a title is worth when the same person is found twice under different
# ones. A German managing director who is also the CTO appears as
# "Geschäftsführer" on the Impressum and "Chief Technology Officer" on the team
# page; the buyer asked to reach the CTO, so that is the title to keep.
FAMILY_RANK: dict[str, int] = {"hr": 3, "tech_leadership": 3, "executive": 2, "other": 0}


def role_rank(title: str) -> int:
    return FAMILY_RANK.get(classify_role(title), 0)


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
