"""The named contact inside a job ad.

Company pages yield executives. An Impressum names the Geschäftsführer because
§5 TMG requires it, and a team page names whoever the company chose to show.
Neither reliably names anyone in HR, which is half of what this brief asks for.

Job ads do. A European ad routinely prints "Ihre Ansprechpartnerin: Anna Müller,
a.mueller@firma.de", because an applicant needs somebody to ask. That person is
the recruiting contact for the role by definition, and an address printed beside
them is published rather than inferred.

It also rescues companies the site crawl gives up on: in a measured run 42 of 91
companies had no person anywhere on their website, and the ad text was never
looked at.

Precision beats recall here. A wrong name attached to a guessed address is a
bounced email and a damaged sender reputation, so an address is only attributed
to a person when its local part actually echoes their name.
"""

from __future__ import annotations

import re

from ..email.pattern import _slug
from ..email.validate import is_role_account
from .hit import PersonHit
from .roles import classify_role

# Name shape, as on team pages: Title Case tokens plus the lowercase nobiliary
# particles that are part of a real surname ("van der Berg", "von Weizsäcker").
_TOKEN = r"(?:[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’-]+|[A-ZÀ-ÖØ-Þ]\.|van|von|der|den|de|del|di|da|dos|du|la|le|ter)"
_NAME = rf"{_TOKEN}(?:\s+{_TOKEN}){{1,3}}"

_HONORIFIC = r"(?:Herr|Frau|Mr|Mrs|Ms|Mme|M|Dhr|Mevr|Sr|Sra|Dr|Prof|Ing)\.?"

# The cue that says "a human you may contact follows". Multilingual because the
# ad is written in the language of the country we are targeting.
_CUES: list[str] = [
    r"ihre?\s+ansprechpartner(?:in)?",
    r"ansprechpartner(?:in)?",
    r"kontaktperson",
    r"ihr\s+kontakt(?:\s+bei\s+uns)?",
    r"r[üu]ckfragen[^.]{0,30}?(?:an|bei)",
    r"bei\s+fragen[^.]{0,40}?(?:an|kontaktiere?n?\s+sie)",
    r"contact\s+person",
    r"your\s+contact",
    r"point\s+of\s+contact",
    r"please\s+contact",
    r"reach\s+out\s+to",
    r"questions[^.]{0,30}?contact",
    r"hiring\s+manager",
    r"recruiter",
    r"personne\s+de\s+contact",
    r"votre\s+interlocut(?:eur|rice)",
    r"contactez",
    r"contactpersoon",
    r"neem\s+contact\s+op\s+met",
    r"persona\s+de\s+contacto",
    r"referente",
    r"osoba\s+kontaktowa",
    r"kontakt",
]

_CONTACT_RX = re.compile(
    rf"(?i:{'|'.join(_CUES)})\s*[:\-–—]?\s*(?:(?i:{_HONORIFIC})\s+)?({_NAME})",
)

_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RX = re.compile(r"(?:\+\d{1,3}[\s./-]?)?(?:\(?\d{2,5}\)?[\s./-]?){2,4}\d{2,6}")

# Words that pass the Title Case test but name a function, a team or a document
# rather than a person. Without this "Ansprechpartner: Human Resources" becomes
# a lead called Human Resources.
_STOPWORDS: frozenset[str] = frozenset(
    """
    human resources personal personalabteilung recruiting recruitment talent team teams
    hr people abteilung department bewerbung bewerbungen application applications
    karriere career careers job jobs stelle stellen position kontakt contact
    ansprechpartner ansprechpartnerin kontaktperson unternehmen company gmbh ag kg se
    mbh limited ltd inc bv nv sarl sas spa srl oy ab as aps sp zoo
    datenschutz impressum website email mail telefon phone mobil herr frau
    montag dienstag mittwoch donnerstag freitag monday friday
    """.split()
)

# The window either side of a name in which an address may plausibly belong to
# that person rather than to the next one down the page.
_EMAIL_WINDOW = 220


def _looks_like_a_person(name: str) -> bool:
    tokens = name.split()
    if not 2 <= len(tokens) <= 4:
        return False
    if any(token.lower().strip(".,") in _STOPWORDS for token in tokens):
        return False
    # At least two tokens must be real words, not lone initials.
    return sum(1 for token in tokens if len(token.strip(".")) > 1) >= 2


def _email_belongs_to(email: str, name: str) -> bool:
    """Is this address plausibly this person's, rather than the one beside them?

    An ad often prints several addresses. Requiring the local part to echo a
    name token is what separates "a.mueller@firma.de belongs to Anna Müller"
    from "bewerbung@firma.de happens to sit nearby".
    """
    local = _slug(email.split("@", 1)[0])
    if not local:
        return False
    parts = [_slug(token) for token in name.split() if len(token.strip(".")) > 1]
    return any(part and (part in local or (len(part) > 2 and local.endswith(part))) for part in parts)


def _role_near(text: str, start: int) -> str:
    """An explicit title stated beside the name, if there is one."""
    window = text[start : start + 120]
    for segment in re.split(r"[,;|\n·•\-–—()]", window):
        candidate = segment.strip()
        if 2 < len(candidate) <= 60 and classify_role(candidate) != "other":
            return candidate
    return ""


def contacts_from_ad(text: str, domain: str, source_url: str = "") -> list[PersonHit]:
    """Named contacts in one job ad. Empty is the normal, expected answer."""
    if not text or not domain:
        return []

    hits: list[PersonHit] = []
    seen: set[str] = set()
    for match in _CONTACT_RX.finditer(text):
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        if not _looks_like_a_person(name) or name.lower() in seen:
            continue
        seen.add(name.lower())

        window_start = max(0, match.start() - _EMAIL_WINDOW)
        window = text[window_start : match.end() + _EMAIL_WINDOW]

        email = ""
        for candidate in _EMAIL_RX.findall(window):
            lowered = candidate.strip().lower()
            # A shared mailbox is not this person's address. Leaving it off lets
            # pattern inference produce an individual one instead, which is what
            # the brief actually asks for.
            if (
                lowered.endswith(f"@{domain}")
                and not is_role_account(lowered)
                and _email_belongs_to(lowered, name)
            ):
                email = lowered
                break

        phone_match = _PHONE_RX.search(text[match.end() : match.end() + 160])
        phone = phone_match.group(0).strip() if phone_match else ""

        hits.append(
            PersonHit(
                name=name,
                # The ad names them as its contact, so that is their function
                # here even when no title is printed.
                role=_role_near(text, match.end()) or "Recruiting Contact",
                email=email,
                phone=phone if len(re.sub(r"\D", "", phone)) >= 7 else "",
                source_url=source_url,
                strategy="job_ad",
            )
        )
    return hits
