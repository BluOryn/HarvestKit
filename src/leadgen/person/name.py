"""Is this string a person's name, or something a parser mistook for one?

Every extraction strategy reads human-authored HTML, where a person's name and
a navigation label look identical: both are short runs of Title Case words. So
"Account Manager", "Getting Started" and "Our Guiding Principles" all arrive
shaped exactly like "Anna Dahlfors".

Getting this wrong is expensive in a way a missing row is not. A junk name
becomes a junk address -- account.manager@company.com -- which bounces, and
enough bounces damage the sender's domain reputation for every later campaign.
One fabricated contact also costs more trust with whoever works the list than
ten missing ones.

The test is deliberately a vocabulary check rather than anything cleverer: role
nouns, page furniture and marketing words are a closed set that real surnames
almost never intersect. Where they do -- Page, Baker, Fox, Mason, Hunter, Cook
are all real surnames -- the word is deliberately absent below.
"""

from __future__ import annotations

import re

# Words that mark a token as part of a job title, a menu or a slogan rather
# than a person. Kept lowercase; matched per token, not as a substring, so
# "Head" is caught while "Headley" is not.
_NOT_A_NAME: frozenset[str] = frozenset(
    """
    manager managers director directors officer officers owner owners lead leads
    leader leaders head heads specialist specialists engineer engineers developer
    developers consultant consultants analyst analysts coordinator assistant
    assistants executive executives recruiter recruiters representative advisor
    adviser architect designer scientist administrator supervisor intern trainee
    president vice chief senior junior principal staff associate associates
    account accounts product products project projects programme program sales
    marketing support service services operations finance financial legal talent
    people human resources customer customers business technical technology
    engineering data security quality delivery success growth strategy solutions
    solution platform platforms agency agencies group team teams company companies
    global international regional national corporate enterprise
    read more learn view details explore follow register login signup subscribe
    search menu home blog news press careers career jobs job vacancy vacancies
    cookie cookies privacy terms imprint impressum contact contacts about
    getting started overview introduction welcome discover download demo
    partner partners partnership investor investors board advisory council
    committee governance leadership management department division unit
    digital banking media social cash payments payment commerce retail insurance
    software hardware cloud mobile network networks inventory tools funding
    innovation principles guiding values mission vision story journey together
    beginning launches launch industry industries manufacturing logistics
    consulting training academy events webinar webinars ebook whitepaper
    pricing features integrations documentation roadmap changelog faq
    """.split()
)

# The trailing dot matters: an academic title is part of how a German Impressum
# writes a director's name ("Dr. Stefan Strobl"), and rejecting the token loses
# the whole person.
_TOKEN_RX = re.compile(r"^[A-ZÀ-ÖØ-Þ][\wÀ-ÿ'’-]*\.?$", re.UNICODE)

# A name never opens with a determiner or pronoun. This is what separates a
# slogan -- "Our Guiding Principles", "The Beginning" -- from a person, without
# needing every marketing noun in the vocabulary below.
_LEADING_STOPWORDS: frozenset[str] = frozenset(
    "our the your my we us this that these those all new more why how what "
    "who when where a an is are be it its their his her".split()
)
# Lowercase particles that legitimately sit inside a surname.
_PARTICLES: frozenset[str] = frozenset(
    "van von der den de del di da dos du la le les ter ten af av zu zum om na bin al".split()
)
_WS_RX = re.compile(r"\s+")


def _tokens(name: str) -> list[str]:
    return _WS_RX.sub(" ", (name or "").strip()).split()


def strip_leading_title(name: str) -> str:
    """Drop a job title prefixed to a name.

    Ads write "Kontakt: Product Owner Line Benzin", where the person is Line
    Benzin. Removing the title recovers a real contact instead of discarding
    one, and leaves nothing behind when the whole string was a title.
    """
    tokens = _tokens(name)
    while tokens and tokens[0].lower().strip(".,") in _NOT_A_NAME:
        tokens.pop(0)
    return " ".join(tokens)


# A German Impressum names every managing director, and §5 TMG gives it no
# reason to put them on separate lines: "Geschäftsführer: Dana Aleff, Erik
# Müller". Read as one person that becomes danaalefferik.mueller@ -- a fabricated
# address for a person who does not exist, in place of two real executives.
_SPLIT_RX = re.compile(r"\s*(?:,|;|/|\||&|\bund\b|\band\b|\bet\b)\s*", re.I)


def split_people(name: str) -> list[str]:
    """One name, or the several that were printed on one line.

    Splits only when every part is independently a plausible name. That keeps
    "Smith, John" -- a single person written surname-first, whose parts are one
    token each -- from being torn into two unusable halves.
    """
    parts = [part.strip() for part in _SPLIT_RX.split(name or "") if part.strip()]
    if len(parts) < 2:
        return [name.strip()] if (name or "").strip() else []
    if all(looks_like_person_name(part) for part in parts):
        return parts
    return [name.strip()]


def looks_like_person_name(name: str) -> bool:
    tokens = _tokens(name)
    if not 2 <= len(tokens) <= 4:
        return False
    if tokens[0].lower().strip(".,") in _LEADING_STOPWORDS:
        return False
    for token in tokens:
        bare = token.lower().strip(".,'’-")
        if bare in _NOT_A_NAME:
            return False
        if bare in _PARTICLES:
            continue
        if not _TOKEN_RX.match(token):
            return False
    # A lone pair of initials ("J. P.") is not a usable contact.
    return sum(1 for token in tokens if len(token.strip(".")) > 1) >= 2
