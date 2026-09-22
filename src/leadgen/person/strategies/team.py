"""Team and about pages — repeated cards of name, role and sometimes contact.

There is no universal markup here, so the parser finds heading elements whose
text reads like a person's name and takes the role from the nearest following
text inside the same card.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from bs4 import BeautifulSoup, Tag

from ..hit import PersonHit
from ..roles import split_name

log = logging.getLogger(__name__)

_NAME_TAGS = ("h2", "h3", "h4", "h5", "h6", "strong", "b")
_ENTITY_RX = re.compile(
    r"\b(?:gmbh|ag|ltd|inc|llc|bv|nv|team|teams|abteilung|department|group|gruppe|division"
    r"|karriere|kontakt|impressum|newsletter|cookie|datenschutz)\b",
    re.I,
)
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A person's name: two to four Title-Case tokens, allowing particles and
# initials. Each token must be an uppercase letter followed by lowercase ones —
# "RUN YOUR BUSINESS" and "TAKE PAYMENTS" are marketing headings, and a regex
# that merely wants "capitalised" accepts every one of them.
_TOKEN = r"(?:[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’-]+|[A-ZÀ-ÖØ-Þ]\.|van|von|der|den|de|del|di|da|la|le|ter)"
_NAME_RX = re.compile(rf"^{_TOKEN}(?:\s+{_TOKEN}){{1,3}}$", re.UNICODE)
# Nav, marketing and section headings that survive the shape test. "About
# Celonis", "Our Leadership" and "Global Advisory Council" are all Title Case
# two-to-three-word phrases, so no amount of capitalisation logic separates them
# from a person's name — only vocabulary does.
_STOPWORDS = (
    "cookie privacy imprint impressum newsletter blog blogs press news awards award "
    "leadership leaders leader council committee board advisory governance investors "
    "team teams squad crew people careers career jobs job hiring culture values mission "
    "story stories about overview company companies group global regional worldwide "
    "partners partner customers customer clients solutions products product platform "
    "resources resource support help docs documentation pricing plans demo trial "
    "contact office offices location locations headquarters login signin signup "
    "download webinar event events conference summit podcast report reports "
    "policy terms legal compliance security trust "
    "all more recent latest featured popular new full name title role position"
).split()
_STOPWORD_RX = re.compile(r"\b(?:" + "|".join(_STOPWORDS) + r")\b", re.I)
# Possessive forms and stray punctuation that only appear in copy, never a name.
_COPY_RX = re.compile(r"['’]s\b|[:!?&/|]|\.\.\.|…")
_ROLE_MAX_CHARS = 80


def _looks_like_a_person(text: str) -> bool:
    value = (text or "").strip()
    if not value or len(value) > 60:
        return False
    if _ENTITY_RX.search(value) or _STOPWORD_RX.search(value) or _COPY_RX.search(value):
        return False
    if any(char.isdigit() for char in value):
        return False
    if value.isupper():
        return False
    if not _NAME_RX.match(value):
        return False
    return bool(split_name(value)[1])


#: Both conventions are in live use for the same surname, and an address may
#: pick either: Müller appears as both "mueller@" and "muller@".
_EXPANSIONS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "å": "aa", "æ": "ae", "ø": "oe"}


def _name_variants(value: str) -> set[str]:
    """Lowercase ASCII spellings this text could plausibly be written as."""
    lowered = (value or "").lower()
    expanded = "".join(_EXPANSIONS.get(ch, ch) for ch in lowered)
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", lowered) if ch.isalpha() and not unicodedata.combining(ch)
    )
    return {
        "".join(ch for ch in expanded if ch.isalpha()),
        stripped,
    } - {""}


def _echoes_name(haystack: str, name: str) -> bool:
    """Does `haystack` carry a whole name token of at least three letters?

    Three is the floor because shorter tokens ("de", "van", "le") appear inside
    unrelated words often enough to match by accident — "verkauf@" would
    otherwise read as a hit on "Jan de Vries".
    """
    hay = _name_variants(haystack)
    if not hay:
        return False
    for token in name.split():
        for variant in _name_variants(token):
            if len(variant) >= 3 and any(variant in candidate for candidate in hay):
                return True
    return False


def _contacts_between_headings(tag: Tag) -> tuple[str, str]:
    """The email and LinkedIn URL belonging to this heading's person.

    Walks forward in document order and stops at the next heading that reads
    like somebody else's name, so an anchor can only ever be attributed to the
    person it actually follows.
    """
    email = ""
    linkedin = ""
    for node in tag.next_elements:
        if not isinstance(node, Tag):
            continue
        if (
            node.name in _NAME_TAGS
            and node is not tag
            and _looks_like_a_person(node.get_text(" ", strip=True))
        ):
            break
        if node.name != "a":
            continue
        href = str(node.get("href") or "").strip()
        lowered = href.lower()
        if not email and lowered.startswith("mailto:"):
            email = href[7:].split("?")[0]
        elif not linkedin and "linkedin.com/in/" in lowered:
            linkedin = href
    return email, linkedin


def _address_echoes_name(email: str, name: str) -> bool:
    return bool(email) and _echoes_name(email.split("@", 1)[0], name)


def _linkedin_contradicts(url: str, name: str) -> bool:
    """True when the profile slug spells out somebody else.

    A LinkedIn URL is never checked downstream — `build_leads` writes
    `person_linkedin` verbatim — so a wrong one is delivered with no way for
    the buyer to notice. `/in/hans-mueller-8a2b1` under Petra Müller's heading
    is a mis-scoped anchor, and the slug says so outright. Slugs that carry no
    readable name (`/in/p-m-1234`, an opaque id) are left alone: absence of
    evidence is not contradiction.
    """
    if not url or not name:
        return False
    slug = url.lower().split("linkedin.com/in/", 1)[-1].strip("/").split("?")[0]
    words = [part for part in re.split(r"[^a-zA-ZÀ-ɏ]+", slug) if len(part) >= 3]
    if not words:
        return False
    return not _echoes_name(" ".join(words), name)


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

        # Contact details are taken from the span between this heading and the
        # next person's, not from the whole card.
        #
        # `_card_of` climbs three levels, and on a great many real team pages
        # that wrapper holds *everybody* — a flat list, or two sibling cards
        # under one container. `card.find("a", mailto)` then returns the first
        # address in the entire block, so the first person's email and LinkedIn
        # profile were stamped onto every colleague below them. Role assignment
        # already scoped itself against sibling headings; contact details did
        # not, and they are the fields that actually get mailed.
        email, linkedin = _contacts_between_headings(tag)
        if linkedin and _linkedin_contradicts(linkedin, name):
            log.debug("team: %s does not belong to %r — dropping", linkedin, name)
            linkedin = ""
        if not email:
            # Looser fallback for cards that print an address as plain text.
            # It can reach past the boundary, so it must echo the name.
            found = _EMAIL_RX.search(card.get_text(" ", strip=True))
            if found and _address_echoes_name(found.group(0), name):
                email = found.group(0)

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
