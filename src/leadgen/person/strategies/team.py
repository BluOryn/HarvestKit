"""Team and about pages — repeated cards of name, role and sometimes contact.

There is no universal markup here, so the parser finds heading elements whose
text reads like a person's name and takes the role from the nearest following
text inside the same card.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from ..hit import PersonHit
from ..roles import split_name

_NAME_TAGS = ("h2", "h3", "h4", "h5", "h6", "strong", "b")
_ENTITY_RX = re.compile(
    r"\b(?:gmbh|ag|ltd|inc|llc|bv|nv|team|teams|abteilung|department|group|gruppe|division"
    r"|karriere|kontakt|impressum|newsletter|cookie|datenschutz)\b",
    re.I,
)
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A person's name: two to four capitalised tokens, allowing particles and
# initials. Unicode-aware so "Jörg Müller" and "Émilie Durand" match.
_NAME_RX = re.compile(
    r"^(?:[A-ZÀ-ÖØ-Þ][\w'’-]*\.?\s+){1,3}[A-ZÀ-ÖØ-Þ][\w'’-]+$",
    re.UNICODE,
)
_ROLE_MAX_CHARS = 80


def _looks_like_a_person(text: str) -> bool:
    value = (text or "").strip()
    if not value or len(value) > 60 or _ENTITY_RX.search(value):
        return False
    if any(char.isdigit() for char in value):
        return False
    if not _NAME_RX.match(value):
        return False
    return bool(split_name(value)[1])


def _card_of(tag: Tag) -> Tag:
    """Climb to the smallest ancestor that plausibly wraps one person."""
    node: Tag = tag
    for _ in range(3):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "main", "[document]"):
            break
        node = parent
    return node


def extract(html: str, url: str) -> list[PersonHit]:
    soup = BeautifulSoup(html, "html.parser")
    hits: list[PersonHit] = []
    seen: set[str] = set()

    for tag in soup.find_all(_NAME_TAGS):
        name = tag.get_text(" ", strip=True)
        if not _looks_like_a_person(name) or name.lower() in seen:
            continue
        card = _card_of(tag)
        lines = [line for line in card.get_text("\n", strip=True).split("\n") if line.strip()]
        # Other headings inside the card are other people, not this person's
        # role. Comparing against the actual heading elements is exact; testing
        # whether the line "looks like a name" is not, because a title-case role
        # such as "Chief Technology Officer" is indistinguishable from one.
        sibling_headings = {
            other.get_text(" ", strip=True) for other in card.find_all(_NAME_TAGS) if other is not tag
        }

        # The role is the first non-empty line after the name inside the card.
        role = ""
        if name in lines:
            index = lines.index(name)
            if index + 1 < len(lines):
                candidate = lines[index + 1].strip()
                if (
                    candidate != name
                    and candidate not in sibling_headings
                    and len(candidate) <= _ROLE_MAX_CHARS
                    and "@" not in candidate
                ):
                    role = candidate

        email = ""
        mailto = card.find("a", href=re.compile(r"^mailto:", re.I))
        if mailto:
            email = str(mailto.get("href", ""))[7:].split("?")[0]
        else:
            found = _EMAIL_RX.search(card.get_text(" ", strip=True))
            if found:
                email = found.group(0)

        linkedin = ""
        profile = card.find("a", href=re.compile(r"linkedin\.com/in/", re.I))
        if profile:
            linkedin = str(profile.get("href", ""))

        seen.add(name.lower())
        hits.append(
            PersonHit(
                name=name,
                role=role,
                email=email.strip(),
                linkedin=linkedin,
                source_url=url,
                strategy="team",
            )
        )
    return hits
