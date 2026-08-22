"""The Swiss commercial register as a person source of last resort.

Roughly a fifth of the companies a Swiss run reaches name nobody anywhere on
their own site. Victorinox, FREITAG and Kistler all publish a careers page and
an Impressum and never a human being, because a consumer brand has no reason
to. No better parser recovers those names; they are simply not on the website.

They are, however, on the public record. Swiss law requires every registered
company to file its board, its managing officers and everyone holding signature
authority with the cantonal commercial register, and every change is published
in the Swiss Official Gazette of Commerce (SOGC/SHAB). That record names exactly
the people this pipeline targets -- the board and the executive -- and it is
open government data under the opendata.swiss "Open use. Must provide the
source." terms.

Two things make this safe to use where the ordinary crawl is not:

  1. **Exact linkage.** A company is matched to a register entity by name and
     then everything else keys off the entity's UID. Fuzzy gazette search is
     what turned "FREITAG" into "FREITAGS AG" and "Kistler" into a sole trader
     called Andy Kistler during development; publications reached through the
     UID cannot belong to another company by construction.

  2. **Named source.** Every hit carries the entity's Zefix detail URL, which
     is both the audit trail for the row and the attribution the licence asks
     for.

Access is deliberately gated. The Zefix Public REST API is credentialled --
free, but you have to register -- and this module stays inert unless those
credentials are configured. That is not an obstacle to work around: registering
is how the Federal Registry authorises programmatic use, and it is the reason
this source may be read at all when the site's robots.txt disallows anonymous
crawlers.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import unicodedata

from .hit import PersonHit
from .name import looks_like_person_name, strip_leading_title

log = logging.getLogger(__name__)

API_BASE = "https://www.zefix.admin.ch/ZefixPublicREST"
API_HOST = "www.zefix.admin.ch"
DETAIL_URL = "https://www.zefix.admin.ch/en/search/entity/list/firm/{ehraid}"

#: Environment variables holding the Zefix Public REST credentials.
USER_ENV = "ZEFIX_USER"
PASSWORD_ENV = "ZEFIX_PASSWORD"

#: How many of an entity's gazette publications to read. They arrive newest
#: first and each one only reports a change, so the recent handful covers the
#: current board while older ones increasingly name people who have left.
MAX_PUBLICATIONS = 8


def credentials() -> tuple[str, str]:
    """The configured Zefix credentials, or two empty strings."""
    return os.environ.get(USER_ENV, "").strip(), os.environ.get(PASSWORD_ENV, "").strip()


def is_configured() -> bool:
    user, password = credentials()
    return bool(user and password)


def auth_header() -> dict[str, str]:
    user, password = credentials()
    if not (user and password):
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


# --------------------------------------------------------------------------
# Matching a company to a register entity
# --------------------------------------------------------------------------

# Legal-form words that differ between how a company writes its name and how
# the register does. "Deloitte" and "Deloitte AG" are the same company; the
# suffix carries no identity.
_LEGAL_NOISE = frozenset(
    """
    aktiengesellschaft gmbh mbh ag sa sarl srl spa se scrl kg ohg gbr
    gruppe group holding ltd limited inc llc plc bv nv oy ab
    """.split()
)


def normalize_company(name: str) -> str:
    """Fold a company name to the form used for register matching.

    Accents, punctuation, spacing and legal form all vary between a jobs board
    and the register -- "Gruyere Energie" against "Gruyere Energie S.A.",
    "Elektro Material" against "ELEKTRO-MATERIAL AG" -- and none of that
    variation carries identity.
    """
    folded = unicodedata.normalize("NFKD", (name or "").lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    # Dots close up rather than split, so "S.A." folds to the legal-form token
    # "sa" and is dropped. Splitting on them leaves "s" and "a", which match
    # nothing and make every French company name miss.
    folded = folded.replace(".", "").replace("'", "").replace("’", "")
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    return "".join(token for token in folded.split() if token not in _LEGAL_NOISE)


def _entity_matches(wanted: str, candidate: str) -> bool:
    """Whole-name equality after folding, never a prefix.

    Deliberately strict. A prefix test accepts "Migros Bank AG" for Migros and
    "Andy Kistler" for Kistler, and a lead attributed to the wrong company is
    worse than a company with no lead at all: it is confidently wrong, and
    nothing downstream can tell.
    """
    wanted_key = normalize_company(wanted)
    return bool(wanted_key) and wanted_key == normalize_company(candidate)


def find_entity(company_name: str, http, *, canton: str = "") -> dict | None:
    """The register entity for this company, or None when nothing matches."""
    if not (company_name or "").strip() or not is_configured():
        return None
    payload: dict = {"name": company_name.strip(), "activeOnly": True}
    if canton:
        payload["canton"] = canton
    try:
        result = http.post_json(f"{API_BASE}/api/v1/company/search", payload, headers=auth_header())
    except Exception as exc:
        log.debug("register: search failed for %s: %s", company_name, exc)
        return None
    for entity in _as_entities(result):
        if _entity_matches(company_name, str(entity.get("name") or "")):
            return entity
    return None


def _as_entities(payload) -> list[dict]:
    """The register entities in a response, whichever shape it arrived in.

    `company/uid` answers with a list because one UID can cover a head office
    and its branches, and `company/search` answers with a list of matches.
    Reading either as a single object silently drops everything.
    """
    if isinstance(payload, dict):
        payload = payload.get("list", payload) if "list" in payload else [payload]
    return [item for item in (payload or []) if isinstance(item, dict)]


def _publications(uid: str, http) -> tuple[list[dict], str]:
    """This entity's gazette publications, newest first, and its detail URL.

    Branch offices file their own publications under the same UID, and a branch
    manager is as real a contact as a head-office one, so all of them count.
    """
    try:
        payload = http.get_json(f"{API_BASE}/api/v1/company/uid/{uid}", headers=auth_header())
    except Exception as exc:
        log.debug("register: uid lookup failed for %s: %s", uid, exc)
        return [], ""

    detail = ""
    publications: list[dict] = []
    for entity in _as_entities(payload):
        if not detail and entity.get("ehraid"):
            detail = DETAIL_URL.format(ehraid=entity["ehraid"])
        publications.extend(item for item in (entity.get("sogcPub") or []) if isinstance(item, dict))
    publications.sort(key=lambda item: str(item.get("sogcDate") or ""), reverse=True)
    return publications, detail


# --------------------------------------------------------------------------
# Reading people out of a gazette publication
# --------------------------------------------------------------------------

# A publication lists who joined and who left in the same paragraph. Reading it
# whole hands a departed CFO to the buyer as a current one, so the departure
# section is cut away before anything is parsed.
_CURRENT_RX = re.compile(
    r"(?:Eingetragene\s+Personen(?:\s+neu\s+oder\s+mutierend)?"
    r"|Neu\s+eingetragene\s+Personen"
    r"|Nouvelles\s+personnes\s+inscrites"
    r"|Personnes?\s+inscrites?(?:\s+ou\s+modifi[eé]es?)?"
    r"|Persone\s+iscritte(?:\s+nuove\s+o\s+modificate)?)\s*:",
    re.I,
)
_DEPARTED_RX = re.compile(
    r"(?:Ausgeschiedene\s+Personen|erloschene\s+Unterschriften"
    r"|Personnes?\s+radi[eé]es?|signatures?\s+radi[eé]es?"
    r"|Persone\s+cancellate|firme\s+estinte)",
    re.I,
)

# What an entry says after the name: where the person is from, where they live,
# what they do, how they sign. The first of these ends the name.
_AFTER_NAME_RX = re.compile(
    r"^(?:von|de|da|di|del|della|dello|des|du|in|a|zu"
    r"|genannt|dit|detto|geb\.?"
    r"|mit|ohne|avec|sans|con|senza"
    r"|\w+(?:er|ische[rn]?)\s+Staatsangeh[oö]rige"
    r"|ressortissant|cittadin)\b|^(?:d'|à\s)",
    re.I,
)

# A previous state, a shareholding, or an entry number. None of it is a person
# and all of it confuses the field split, so it goes first.
_BRACKET_RX = re.compile(r"\[[^\]]*\]")
_SHARES_RX = re.compile(r",?\s*(?:mit|avec|con)\s+\d[^,;]*", re.I)

# The register writes a legal entity exactly like a person, but always with its
# own UID in brackets: "BDO AG (CHE-105.952.747), in Zuerich, Revisionsstelle."
_ENTITY_RX = re.compile(r"\(CHE[-\s]?\d", re.I)

#: Register functions worth putting on a row, longest first so "Praesident des
#: Verwaltungsrates" wins over the bare "Verwaltungsrat" inside it. Signature
#: authority ("mit Einzelunterschrift") is deliberately absent: it says how
#: someone signs, not what they do.
_FUNCTION_RX = re.compile(
    r"(?:Pr[aä]sident(?:in)?\s+(?:des\s+)?Verwaltungsrat(?:e?s)?"
    r"|Vizepr[aä]sident(?:in)?\s+(?:des\s+)?Verwaltungsrat(?:e?s)?"
    r"|Mitglied\s+(?:des\s+)?Verwaltungsrat(?:e?s)?"
    r"|Mitglied\s+(?:der\s+)?Gesch[aä]ftsleitung"
    r"|Verwaltungsr[aä]t(?:in|e)?"
    r"|Gesch[aä]ftsf[uü]hrer(?:in)?"
    r"|Vizedirektor(?:in)?|Direktor(?:in)?"
    r"|Liquidator(?:in)?"
    r"|pr[eé]sident(?:e)?\s+du\s+conseil\s+d'administration"
    r"|administrateur(?:-d[eé]l[eé]gu[eé])?|administratrice"
    r"|directeur\s+g[eé]n[eé]ral(?:e)?|directeur|directrice"
    r"|g[eé]rant(?:e)?"
    r"|presidente\s+del\s+consiglio\s+d'amministrazione"
    r"|amministratore(?:\s+(?:unico|delegato))?"
    r"|direttore(?:\s+generale)?)",
    re.I,
)

# A function that describes a firm engaged by the company, not somebody who
# works there. The auditor is a company; the buyer does not want it.
_NOT_STAFF_RX = re.compile(r"Revisionsstelle|organe\s+de\s+r[eé]vision|ufficio\s+di\s+revisione", re.I)

_HONORIFIC_RX = re.compile(r"^(?:dr|prof|dipl|ing|lic|mag|med)\.?\s+", re.I)
_PARTICLES = frozenset("van von der den de del della di da dos du la le les ter ten zu".split())

# A publication ends its person list with a full stop, but a full stop is also
# how "Dr." and "CHE-105.977.463" are written. Only a following section
# heading, or the end of the text, actually terminates the list.
_SECTION_END_RX = re.compile(r"\.\s*(?=[A-Z][a-z]+\s+(?:Personen|personnes|persone))|\.\s*$")


def current_section(message: str) -> str:
    """The part of a publication that names people who are currently in post."""
    match = _CURRENT_RX.search(message or "")
    if not match:
        return ""
    tail = message[match.end() :]
    departed = _DEPARTED_RX.search(tail)
    return tail[: departed.start()] if departed else tail


def _split_entries(section: str) -> list[str]:
    """One string per registered person."""
    head = _SECTION_END_RX.split(section)[0]
    return [part.strip(" .,;") for part in head.split(";") if part.strip(" .,;")]


def _name_and_role(entry: str) -> tuple[str, str]:
    """The person's name in given-surname order, and their register function."""
    entry = _SHARES_RX.sub("", _BRACKET_RX.sub("", entry)).strip(" .,")
    if not entry or _ENTITY_RX.search(entry) or _NOT_STAFF_RX.search(entry):
        return "", ""

    fields = [field.strip() for field in entry.split(",") if field.strip()]
    if not fields:
        return "", ""

    # Everything up to the first "von ..." / "de ..." / "mit ..." is the name.
    # The register writes German as "Surname, Given" and French as "Surname
    # Given", so how many fields that collects is what tells the two apart.
    name_fields: list[str] = []
    for field in fields[:2]:
        if _AFTER_NAME_RX.match(field) or _FUNCTION_RX.match(field):
            break
        name_fields.append(field)
    if not name_fields:
        return "", ""

    if len(name_fields) >= 2:
        surname, given = name_fields[0], _HONORIFIC_RX.sub("", name_fields[1])
    else:
        tokens = name_fields[0].split()
        if len(tokens) < 2:
            return "", ""
        cut = 1
        while cut < len(tokens) and tokens[cut - 1].lower() in _PARTICLES:
            cut += 1
        surname, given = " ".join(tokens[:cut]), " ".join(tokens[cut:])

    name = strip_leading_title(f"{given} {surname}".strip())
    if not looks_like_person_name(name):
        return "", ""

    function = _FUNCTION_RX.search(entry)
    return name, function.group(0).strip() if function else ""


def people_in_publication(message: str) -> list[tuple[str, str]]:
    """(name, role) for everyone currently in post in one publication."""
    found: list[tuple[str, str]] = []
    for entry in _split_entries(current_section(message)):
        name, role = _name_and_role(entry)
        if name:
            found.append((name, role))
    return found


def people_from_register(
    company_name: str,
    http,
    *,
    canton: str = "",
    max_publications: int = MAX_PUBLICATIONS,
) -> list[PersonHit]:
    """Board and officers on the public record for this company. Never raises.

    Returns nothing when no register entity matches the company name exactly,
    which is the intended behaviour: a near-match is a different company.
    """
    if not is_configured():
        return []
    try:
        entity = find_entity(company_name, http, canton=canton)
    except Exception as exc:
        log.debug("register: lookup failed for %s: %s", company_name, exc)
        return []
    if not entity:
        return []
    uid = str(entity.get("uid") or "").strip()
    if not uid:
        return []

    try:
        publications, detail_url = _publications(uid, http)
    except Exception as exc:
        log.debug("register: publications failed for %s: %s", company_name, exc)
        return []

    hits: list[PersonHit] = []
    seen: set[str] = set()
    for publication in publications[: max(0, max_publications)]:
        try:
            people = people_in_publication(str(publication.get("message") or ""))
        except Exception as exc:
            log.debug("register: could not parse a publication for %s: %s", company_name, exc)
            continue
        for name, role in people:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(PersonHit(name=name, role=role, source_url=detail_url, strategy="register"))
    if hits:
        log.info("register: %s -> %d people on the public record", company_name, len(hits))
    return hits
