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
from .name import looks_like_person_name
from .roles import classify_role

# Name shape, as on team pages: Title Case tokens plus the lowercase nobiliary
# particles that are part of a real surname ("van der Berg", "von Weizsäcker").
_TOKEN = r"(?:[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’-]+|[A-ZÀ-ÖØ-Þ]\.|van|von|der|den|de|del|di|da|dos|du|la|le|ter)"
_NAME = rf"{_TOKEN}(?:\s+{_TOKEN}){{1,3}}"

_HONORIFIC = r"(?:Herr|Frau|Mr|Mrs|Ms|Mme|M|Dhr|Mevr|Sr|Sra|Dr|Prof|Ing)\.?"

# The cue that says "a human you may contact follows". Multilingual because the
# ad is written in the language of the country we are targeting.
# Cues split by how much they promise. A STRONG cue exists to introduce a
# person, so a name straight after it is the person. A WEAK cue also occurs
# mid-sentence, and German capitalises every noun — "Bei Fragen zur Arbeitsweise
# Darauf ..." reads exactly like a name to any Title-case test. Weak cues
# therefore only count when something corroborates the person: a phone, an
# e-mail marker, or a job title right behind the name.
_STRONG_CUES: list[str] = [
    r"ihre?\s+ansprechpartner(?:in)?",
    # Colon-anchored only. Bare "Ansprechpartner" is also the ordinary German
    # noun for "point of contact", and it turns up inside duty lists —
    # "Ansprechpartner für Kunden rund um die Auslieferung" — where the words
    # after it are responsibilities, not a person.
    r"ansprechpartner(?:in|n)?\s*[:\-–—]",
    r"kontaktperson",
    r"ihr\s+kontakt(?:\s+bei\s+uns)?",
    r"contact\s+person",
    r"your\s+contact",
    r"point\s+of\s+contact",
    r"personne\s+de\s+contact",
    r"votre\s+interlocut(?:eur|rice)",
    r"contactpersoon",
    r"neem\s+contact\s+op\s+met",
    r"persona\s+de\s+contacto",
    r"osoba\s+kontaktowa",
    # Swiss/German ads rarely say "Ansprechpartner". They head the block with the
    # question you would be asking: "Fragen zur Funktion", "Fragen zu Ihrer
    # Bewerbung", "Fachliche Auskünfte". Measured on 66 live jobs.ch ads, these
    # account for most of the contact blocks the older cue list walked past.
    r"fragen\s+zur?\s+(?:stelle|funktion|position|vakanz|aufgabe)",
    r"fragen\s+zu\s+(?:ihrer|deiner)\s+bewerbung",
    r"(?:fachliche|weitere|n[äa]here)\s+ausk[üu]nfte",
    r"ausk[üu]nfte?\s+(?:erteilt|erteilen|gibt)",
    # A bare "Kontakt" is prose; "Kontakt:" is a label introducing a person.
    r"kontakt(?:person)?\s*[:\-–—]",
]

_WEAK_CUES: list[str] = [
    r"ansprechpartner(?:in|n)?",
    r"r[üu]ckfragen[^.]{0,30}?(?:an|bei)",
    r"bei\s+fragen[^.]{0,40}?(?:an|kontaktiere?n?\s+sie)",
    r"bei\s+fragen",
    r"please\s+contact",
    r"reach\s+out\s+to",
    r"questions[^.]{0,30}?contact",
    r"hiring\s+manager",
    r"recruiter",
    r"contacte[rsz]",
    r"noch\s+fragen",
    r"referente",
    r"f[üu]r\s+(?:weitere\s+)?(?:fragen|informationen|ausk[üu]nfte)",
    r"gerne\s+ausk[üu]nfte",
    r"steht\s+ihnen\s+gerne",
    r"freuen\s+uns\s+auf\s+ihre",
    r"kontakt",
]

_CUES: list[str] = _STRONG_CUES + _WEAK_CUES

_STRONG_CUE_RX = re.compile(rf"(?i:{'|'.join(_STRONG_CUES)})")

# Corroboration for a weak cue: a phone, an e-mail marker, or a job title.
_CORROBORATION_RX = re.compile(r"(?:\+?\d[\d\s./-]{6,}|e-?\s?mail|tel\.|telefon|mobile?|@)", re.I)
_CORROBORATION_WINDOW = 80

_CUE_RX = re.compile(rf"(?i:{'|'.join(_CUES)})")

# How far past the cue a name may sit. Wide enough for "Fragen zur Funktion
# <Name>", tight enough that the next paragraph is out of reach.
_NAME_WINDOW = 90
# A weak cue only reaches a name printed right after it. Given a wide window it
# walked into the following sentence and returned capitalised German nouns.
_NAME_WINDOW_WEAK = 24
# Extra characters read beyond the reach so a name starting at its edge is not
# sliced in half.
_NAME_OVERRUN = 60

# A maximal run of name-shaped tokens. Punctuation is not part of any token, so
# a run ends naturally at the comma in "Anna Müller, a.mueller@…" and at the
# uppercase acronym in "… Solutions IT BS".
#
# `_HONORIFIC` is deliberately NOT one of the alternatives: it carries a bare
# "M" (for "M. Dupont"), which matches the leading M of "Max" and cut the run
# after one letter. Honorifics are Title-case words, so `_TOKEN` already admits
# them and `_FURNITURE` strips them off the front.
_RUN_RX = re.compile(rf"{_TOKEN}(?:\s+{_TOKEN}){{0,5}}")

# Filler that sits between the cue and the name, or trails the name. Stripped
# from both ends of a candidate run so "Funktion Philipp Klett" yields the
# person and not the heading above them.
_FURNITURE: frozenset[str] = frozenset(
    """
    fragen frage funktion stelle stellen position vakanz aufgabe bewerbung bewerbungen
    auskunft auskuenfte auskünfte fachliche weitere naehere nähere gerne inserat dossier
    anzeige interesse kontakt kontakte ihr ihre ihrer deine deinem tel telefon fon mobil
    email mail e-mail
    herr frau dr prof dipl ing mag mr mrs ms mme mevr dhr sr sra
    ihnen sie ihr du dir dich uns wir unser unsere euch
    """.split()
)

# The first token of a job title ends the name. This is the actual shape of the
# ads: "<Name> <Role>" with nothing in between, so the role vocabulary is the
# only reliable delimiter — "Philipp Klett Leiter Data Management Solutions" has
# no punctuation to cut on.
_ROLE_START_RX = re.compile(
    r"^(?:leiter(?:in)?|leitung|head|chief|chef(?:in)?|manager(?:in)?|managing|director|direktor(?:in)?|"
    r"verantwortlich\w*|recruit\w*|personal\w*|human|resources|talent|sourcer|hr|people|"
    r"gesch[äa]ftsf[üu]hr\w*|inhaber(?:in)?|gr[üu]nder(?:in)?|vorstand\w*|partner(?:in)?|"
    r"business|senior|junior|team|abteilung\w*|bereich\w*|projektleiter(?:in)?|"
    r"c[teifom]o|vp|svp)$",
    re.I,
)

# Nobiliary particles live *inside* a surname, never at either end of one.
_PARTICLES: frozenset[str] = frozenset("van von der den de del di da dos du la le ter".split())

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

# An honorific is only ever written in front of a person.
_HONORIFIC_NAME_RX = re.compile(rf"(?:Frau|Herr|Mme|Mevr|Dhr)\s+({_NAME})")

# A name printed immediately against a way of reaching it. Swiss ads often skip
# the cue entirely — "Kevin Brosse | +41 22 308 85 00" — and the adjacency is
# itself the evidence, so this needs no cue at all.
_ADJACENT_CONTACT_RX = re.compile(
    r"^\s*[|,·•\-–—]?\s*(?:\+\d{2}|0\d{2}[\s/]|tel\.|telefon|mobil|natel|e-?\s?mail|t[ée]l\.)",
    re.I,
)
_ADJACENCY_REACH = 3

# The window either side of a name in which an address may plausibly belong to
# that person rather than to the next one down the page.
_EMAIL_WINDOW = 220


def _looks_like_a_person(name: str) -> bool:
    tokens = name.split()
    if not 2 <= len(tokens) <= 4:
        return False
    if any(token.lower().strip(".,") in _STOPWORDS for token in tokens):
        return False
    # The shared vocabulary check carries the occupational nouns this module's
    # own list does not — "Workplace Engineering" reads as a name to every
    # capitalisation rule there is. It is also the gate the final cut applies,
    # so rejecting here rather than three stages later keeps the funnel honest.
    if not looks_like_person_name(name):
        return False
    # At least two tokens must be capitalised words of their own. Counting any
    # token longer than one character let "de de" and "Du le" through: a
    # nobiliary particle belongs *inside* a name, it cannot be one of its two
    # halves, and neither can a lone initial.
    return sum(1 for token in tokens if len(token.strip(".")) > 1 and token[:1].isupper()) >= 2


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


def _bare(token: str) -> str:
    return token.strip(".,;:()[]").lower()


def _name_after_cue(window: str, max_start: int) -> tuple[str, int] | None:
    """First person name in `window`, and the offset just past it.

    Walks the run of name-shaped tokens and cuts it at the first job-title word,
    because that is exactly how these blocks read: "Fragen zur Funktion Philipp
    Klett Leiter Data Management Solutions" is heading, person, then title with
    no punctuation anywhere to split on.
    """
    for run in _RUN_RX.finditer(window):
        # `max_start` bounds where a name may *begin*, not where the text ends.
        # Slicing the haystack instead cut "Marion Sägesser" to "Marion Sägess".
        if run.start() > max_start:
            break
        words = run.group(0).split()
        # Drop the heading in front of the name…
        while words and _bare(words[0]) in _FURNITURE:
            words.pop(0)
        # …and cut at the job title behind it.
        for index, word in enumerate(words):
            if _ROLE_START_RX.match(_bare(word)):
                words = words[:index]
                break
        while words and (
            _bare(words[-1]) in _FURNITURE
            or not words[-1][-1:].isalpha()  # "Lanz E-" — a dangling fragment
            or _bare(words[-1]) in _PARTICLES  # a particle cannot end a name
        ):
            words.pop()
        if len(words) < 2:
            continue
        name = " ".join(words[:4])
        if not _looks_like_a_person(name):
            continue
        # Offset just past the name itself, so the role and phone lookups read
        # forward from the person rather than from the cue.
        return name, run.start() + run.group(0).index(words[-1]) + len(words[-1])
    return None


def _corroborated(text: str, name_end: int, name: str) -> bool:
    """Is there evidence just past `name` that it really names a contact?

    A real ad contact is printed with a way of reaching them or with their job
    title. Prose is not.
    """
    tail = text[name_end : name_end + _CORROBORATION_WINDOW]
    if _CORROBORATION_RX.search(tail):
        return True
    return bool(_role_near(text, name_end))


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

    for name, name_end in _cue_free_candidates(text):
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        hits.append(_build_hit(text, name, name_end, domain, source_url))

    for cue in _CUE_RX.finditer(text):
        strong = bool(_STRONG_CUE_RX.match(cue.group(0)))
        reach = _NAME_WINDOW if strong else _NAME_WINDOW_WEAK
        # Read past `reach` so a name that starts inside it can still finish.
        found = _name_after_cue(text[cue.end() : cue.end() + reach + _NAME_OVERRUN], reach)
        if found is None:
            continue
        name, offset = found
        name = re.sub(r"\s+", " ", name).strip()
        if name.lower() in seen:
            continue
        if not strong and not _corroborated(text, cue.end() + offset, name):
            # Weak cue, nothing to confirm a human is being named: this is prose
            # that happens to start with capitalised German nouns.
            continue
        seen.add(name.lower())
        # Absolute offset of the character just past the name, so the role and
        # phone lookups read forward from the person, not from the cue.
        hits.append(_build_hit(text, name, cue.end() + offset, domain, source_url))
    return hits


def _cue_free_candidates(text: str) -> list[tuple[str, int]]:
    """People named without any introducing cue.

    Two shapes, both self-evidencing, so neither needs a cue:
      "Frau Vivianne Schneider, Teamlead HR"   — an honorific precedes a person
      "Kevin Brosse | +41 22 308 85 00"        — a name against its own phone
    """
    out: list[tuple[str, int]] = []
    for match in _HONORIFIC_NAME_RX.finditer(text):
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        if _looks_like_a_person(name):
            out.append((name, match.end()))

    for run in _RUN_RX.finditer(text):
        words = run.group(0).split()
        while words and _bare(words[0]) in _FURNITURE:
            words.pop(0)
        for index, word in enumerate(words):
            if _ROLE_START_RX.match(_bare(word)):
                words = words[:index]
                break
        while words and (
            _bare(words[-1]) in _FURNITURE or not words[-1][-1:].isalpha() or _bare(words[-1]) in _PARTICLES
        ):
            words.pop()
        if len(words) < 2:
            continue
        name = " ".join(words[:4])
        if not _looks_like_a_person(name):
            continue
        end = run.start() + run.group(0).index(words[-1]) + len(words[-1])
        # The contact detail has to sit right against the name. Anything looser
        # matches a sentence that merely happens to precede a phone number.
        if _ADJACENT_CONTACT_RX.match(text[end : end + _ADJACENCY_REACH + 24]):
            out.append((name, end))
    return out


def _build_hit(text: str, name: str, name_end: int, domain: str, source_url: str) -> PersonHit:
    window_start = max(0, name_end - _EMAIL_WINDOW)
    window = text[window_start : name_end + _EMAIL_WINDOW]

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

    phone_match = _PHONE_RX.search(text[name_end : name_end + 160])
    phone = phone_match.group(0).strip() if phone_match else ""

    return PersonHit(
        name=name,
        # The ad names them as its contact, so that is their function here even
        # when no title is printed.
        role=_role_near(text, name_end) or "Recruiting Contact",
        email=email,
        phone=phone if len(re.sub(r"\D", "", phone)) >= 7 else "",
        source_url=source_url,
        strategy="job_ad",
    )
