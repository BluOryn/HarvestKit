# EU IT Lead List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a CSV of exactly 1,000 EU leads — each a named HR or technical-leadership person at a company that employs IT staff, with an individual email address and a source URL for every fact.

**Architecture:** A new `src/leadgen/` package layered on HarvestKit's existing HTTP, crawl, and extraction machinery. Job boards seed companies (a company on an IT board employs IT by construction, and its job ad usually names an HR contact with a direct email). That address anchors email-pattern inference for the CTO found on the company's own Impressum or team page. Leads are over-fetched, scored on field completeness, deduplicated, and cut to exactly 1,000 under a per-country ceiling.

**Tech Stack:** Python 3.11, dataclasses, BeautifulSoup4, dnspython (new), pytest. Reuses `job_scraper.http`, `.crawl`, `.discovery`, `.extract`, `.universal.mine_contacts`, `.config`, `.safe_xml` unchanged.

## Global Constraints

- Target: **exactly 1,000 rows**. A shortfall is reported and the smaller file written — never padded.
- Every row has: named person, role, company, country, email.
- Every row carries `email_status` ∈ `published` > `verified` > `inferred_high` > `inferred_medium` > `unknown` > `catch_all`.
- Every populated field records its source URL in `evidence_json`.
- Role accounts (`info@`, `jobs@`, `hr@`, `karriere@`, …) are **never** emitted as a person's email.
- A named person with no resolvable email is dropped from the CSV but retained in the checkpoint DB.
- Per-country ceiling: **25%** of the final list.
- No source whose terms forbid scraping. No LinkedIn, no Apollo, no ZoomInfo.
- `robots.txt` is honoured — the existing `http` layer already does this; do not bypass it.
- Style: `ruff check` and `black --check` must pass on `src/ tests/`. Line length 110.
- All new modules live under `src/leadgen/`. Do not modify `src/job_scraper/` except where a task says so explicitly.

---

### Task 1: Lead model

**Files:**
- Create: `src/leadgen/__init__.py`
- Create: `src/leadgen/models.py`
- Test: `tests/leadgen/test_models.py`
- Create: `tests/leadgen/__init__.py` (empty)

**Interfaces:**
- Consumes: `job_scraper.models.canonicalize_url`, `job_scraper.models._stringify`
- Produces: `Lead` dataclass, `LEAD_FIELDS: list[str]`, `LEAD_CSV_COLUMNS: list[str]`, `Lead.fingerprint() -> str`, `Lead.to_dict() -> dict[str, str]`, `Lead.set_evidence(field: str, url: str) -> None`, `EMAIL_STATUS_ORDER: list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_models.py
"""Lead schema, fingerprinting and evidence tracking."""

from __future__ import annotations

import json

from leadgen.models import EMAIL_STATUS_ORDER, LEAD_CSV_COLUMNS, LEAD_FIELDS, Lead


def test_csv_columns_cover_every_field():
    assert LEAD_CSV_COLUMNS[0] == "id"
    assert set(LEAD_FIELDS).issubset(LEAD_CSV_COLUMNS)
    assert len(LEAD_CSV_COLUMNS) == len(set(LEAD_CSV_COLUMNS))


def test_to_dict_emits_exactly_the_csv_columns():
    row = Lead(person_name="Jane Doe").to_dict()
    assert set(row) == set(LEAD_CSV_COLUMNS)


def test_fingerprint_keys_on_email_when_present():
    a = Lead(person_name="Jane Doe", person_email="J.Doe@Acme.de", company_domain="acme.de")
    b = Lead(person_name="Jane D.", person_email="j.doe@acme.de", company_domain="acme.de")
    assert a.fingerprint() == b.fingerprint(), "email should dominate identity, case-insensitively"


def test_fingerprint_falls_back_to_name_plus_domain():
    a = Lead(person_name="Jane Doe", company_domain="acme.de")
    b = Lead(person_name="jane  doe", company_domain="acme.de")
    c = Lead(person_name="Jane Doe", company_domain="other.de")
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()


def test_evidence_round_trips_as_json():
    lead = Lead(person_name="Jane Doe", person_role="CTO")
    lead.set_evidence("person_name", "https://acme.de/impressum")
    lead.set_evidence("person_role", "https://acme.de/team")
    row = lead.to_dict()
    evidence = json.loads(row["evidence_json"])
    assert evidence["person_name"] == "https://acme.de/impressum"
    assert evidence["person_role"] == "https://acme.de/team"


def test_empty_evidence_is_not_written():
    assert Lead(person_name="x").to_dict()["evidence_json"] == ""


def test_email_status_order_is_best_first():
    assert EMAIL_STATUS_ORDER[0] == "published"
    assert EMAIL_STATUS_ORDER[-1] == "catch_all"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/leadgen/__init__.py
"""Lead-generation engine layered on the HarvestKit scraping core."""
```

```python
# src/leadgen/models.py
"""Lead schema — one row is one person, not one company.

A company that yields both an HR contact and a CTO produces two Leads that
share the company fields. Identity therefore keys on the person, and the
email dominates when we have one because it is the only globally unique
handle a person has in this dataset.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields

from job_scraper.models import _stringify

# Best first. The quota scorer and the CSV consumer both rely on this order.
EMAIL_STATUS_ORDER: list[str] = [
    "published",
    "verified",
    "inferred_high",
    "inferred_medium",
    "unknown",
    "catch_all",
]

LEAD_FIELDS: list[str] = [
    "person_name",
    "person_first_name",
    "person_last_name",
    "person_role",
    "person_role_family",
    "person_email",
    "email_status",
    "email_confidence",
    "person_linkedin",
    "person_phone",
    "company_name",
    "company_domain",
    "company_website",
    "company_country",
    "company_region",
    "company_city",
    "company_size_hint",
    "tech_stack",
    "seniority",
    "source_seed_url",
    "source_person_url",
    "source_email_url",
    "scraped_at",
]

LEAD_CSV_COLUMNS: list[str] = ["id"] + LEAD_FIELDS + ["evidence_json"]

_WS_RX = re.compile(r"\s+")


@dataclass
class Lead:
    person_name: str = ""
    person_first_name: str = ""
    person_last_name: str = ""
    person_role: str = ""
    person_role_family: str = ""
    person_email: str = ""
    email_status: str = ""
    email_confidence: str = ""
    person_linkedin: str = ""
    person_phone: str = ""
    company_name: str = ""
    company_domain: str = ""
    company_website: str = ""
    company_country: str = ""
    company_region: str = ""
    company_city: str = ""
    company_size_hint: str = ""
    tech_stack: str = ""
    seniority: str = ""
    source_seed_url: str = ""
    source_person_url: str = ""
    source_email_url: str = ""
    scraped_at: str = ""
    evidence: dict[str, str] = field(default_factory=dict)

    def set_evidence(self, field_name: str, url: str) -> None:
        """Record where a field came from. Silently ignores empty URLs."""
        if url:
            self.evidence[field_name] = url

    def fingerprint(self) -> str:
        email = (self.person_email or "").strip().lower()
        if email:
            basis = email
        else:
            name = _WS_RX.sub(" ", (self.person_name or "").strip().lower())
            basis = f"{name}@@{(self.company_domain or '').strip().lower()}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        row = {name: _stringify(getattr(self, name)) for name in LEAD_FIELDS}
        row["id"] = self.fingerprint()
        row["evidence_json"] = json.dumps(self.evidence, ensure_ascii=False) if self.evidence else ""
        return row

    def to_row(self) -> list[str]:
        row = self.to_dict()
        return [row[column] for column in LEAD_CSV_COLUMNS]


# Guard against a field being added to the dataclass but not to LEAD_FIELDS.
_DATACLASS_FIELDS = {f.name for f in fields(Lead)} - {"evidence"}
assert _DATACLASS_FIELDS == set(LEAD_FIELDS), (
    f"LEAD_FIELDS out of sync with the dataclass: {_DATACLASS_FIELDS ^ set(LEAD_FIELDS)}"
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_models.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/ tests/leadgen/
git commit -m "leadgen: person-centric Lead schema with per-field provenance"
```

---

### Task 2: Multilingual role classification

**Files:**
- Create: `src/leadgen/person/__init__.py` (empty)
- Create: `src/leadgen/person/roles.py`
- Test: `tests/leadgen/test_roles.py`

**Interfaces:**
- Consumes: nothing
- Produces: `classify_role(title: str) -> str` returning `"hr"` / `"tech_leadership"` / `"other"`; `is_target_role(title: str) -> bool`; `split_name(full: str) -> tuple[str, str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_roles.py
"""Role-family classification across EU languages."""

from __future__ import annotations

import pytest

from leadgen.person.roles import classify_role, is_target_role, split_name


@pytest.mark.parametrize(
    "title",
    [
        "Head of HR",
        "HR Manager",
        "People Operations Lead",
        "Talent Acquisition Partner",
        "Recruiter",
        "Personalleiterin",
        "Leiter Personalwesen",
        "Responsable Ressources Humaines",
        "Responsabile Risorse Umane",
        "Director de Recursos Humanos",
        "HR-sjef",
    ],
)
def test_hr_titles_classify_as_hr(title):
    assert classify_role(title) == "hr"


@pytest.mark.parametrize(
    "title",
    [
        "CTO",
        "Chief Technology Officer",
        "VP of Engineering",
        "Head of Engineering",
        "Engineering Manager",
        "Technischer Leiter",
        "IT-Leiter",
        "Directeur Technique",
        "Direttore Tecnico",
    ],
)
def test_tech_leadership_titles_classify(title):
    assert classify_role(title) == "tech_leadership"


@pytest.mark.parametrize("title", ["Software Engineer", "Sales Director", "Barista", ""])
def test_non_target_titles_are_other(title):
    assert classify_role(title) == "other"


def test_hr_wins_over_a_generic_leader_token():
    # "Leiter Personalwesen" contains a leadership word but is unambiguously HR.
    assert classify_role("Leiter Personalwesen") == "hr"


def test_substring_false_positives_are_rejected():
    # "cto" must not fire inside "Director"; "hr" must not fire inside "Thrive".
    assert classify_role("Director of Sales") == "other"
    assert classify_role("Thrive Coach") == "other"


def test_is_target_role():
    assert is_target_role("CTO") is True
    assert is_target_role("HR Manager") is True
    assert is_target_role("Software Engineer") is False


def test_split_name_handles_particles_and_titles():
    assert split_name("Jane Doe") == ("Jane", "Doe")
    assert split_name("Dr. Anna Schmidt") == ("Anna", "Schmidt")
    assert split_name("Jan van der Berg") == ("Jan", "van der Berg")
    assert split_name("Müller") == ("", "Müller")
    assert split_name("") == ("", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_roles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.person'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/leadgen/person/roles.py
"""Classify a job title into the role families this run targets.

Patterns are data, not code — adding a language is editing a list. Every
alternative is matched under \\b word boundaries so "cto" cannot fire inside
"Director" and "hr" cannot fire inside "Thrive", which a naive substring
test gets wrong on real title data.
"""

from __future__ import annotations

import re

HR_PATTERNS: list[str] = [
    r"h\.?r\.?",
    r"human resources",
    r"people (?:operations|ops|team|partner|lead|manager|director)",
    r"talent(?: acquisition| partner| manager| lead)?",
    r"recruit\w*",
    r"personal(?:leiter|leiterin|wesen|abteilung|referent\w*|management)",
    r"ressources humaines",
    r"risorse umane",
    r"recursos humanos",
    r"personeelszaken",
    r"rekrytering\w*",
]

TECH_LEADERSHIP_PATTERNS: list[str] = [
    r"c\.?t\.?o\.?",
    r"chief technology officer",
    r"chief technical officer",
    r"v\.?p\.? (?:of )?engineering",
    r"head of (?:engineering|technology|development|it|platform)",
    r"engineering (?:manager|director|lead)",
    r"director of engineering",
    r"technischer leiter(?:in)?",
    r"it[- ]leiter(?:in)?",
    r"technische[rn]? direktor(?:in)?",
    r"directeur technique",
    r"direttore tecnico",
    r"director t[ée]cnico",
]


def _compile(patterns: list[str]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.I)


HR_RX = _compile(HR_PATTERNS)
TECH_RX = _compile(TECH_LEADERSHIP_PATTERNS)

# Honorifics and post-nominals that are not part of a person's name.
_TITLE_RX = re.compile(r"^(?:dr|prof|dipl|ing|mag|mr|mrs|ms|herr|frau|m|mme)\.?\s+", re.I)
# Dutch/German/Iberian nobiliary particles belong with the surname.
_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "la", "le", "ter"}


def classify_role(title: str) -> str:
    """Return "hr", "tech_leadership" or "other"."""
    if not title:
        return "other"
    # HR is checked first: "Leiter Personalwesen" contains a leadership word
    # but is unambiguously an HR role.
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
    parts = cleaned.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    for index in range(1, len(parts)):
        if parts[index].lower() in _PARTICLES:
            return " ".join(parts[:index]), " ".join(parts[index:])
    return " ".join(parts[:-1]), parts[-1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_roles.py -v`
Expected: PASS, 26 parametrised cases

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/person/ tests/leadgen/test_roles.py
git commit -m "leadgen: multilingual HR and tech-leadership title classification"
```

---

### Task 3: Email pattern inference

**Files:**
- Create: `src/leadgen/email/__init__.py` (empty)
- Create: `src/leadgen/email/pattern.py`
- Test: `tests/leadgen/test_pattern.py`

**Interfaces:**
- Consumes: `leadgen.person.roles.split_name`
- Produces: `CANDIDATE_PATTERNS: list[str]`, `apply_pattern(pattern: str, first: str, last: str, domain: str) -> str`, `infer_pattern(known: list[tuple[str, str]]) -> tuple[str, str]` returning `(pattern_name, confidence)` where confidence ∈ `"high" | "medium" | ""`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_pattern.py
"""Email-format inference from known (name, email) pairs on a domain."""

from __future__ import annotations

from leadgen.email.pattern import apply_pattern, infer_pattern


def test_apply_each_pattern():
    assert apply_pattern("first.last", "Anna", "Schmidt", "acme.de") == "anna.schmidt@acme.de"
    assert apply_pattern("f.last", "Anna", "Schmidt", "acme.de") == "a.schmidt@acme.de"
    assert apply_pattern("first", "Anna", "Schmidt", "acme.de") == "anna@acme.de"
    assert apply_pattern("firstlast", "Anna", "Schmidt", "acme.de") == "annaschmidt@acme.de"
    assert apply_pattern("last.first", "Anna", "Schmidt", "acme.de") == "schmidt.anna@acme.de"
    assert apply_pattern("flast", "Anna", "Schmidt", "acme.de") == "aschmidt@acme.de"


def test_apply_pattern_folds_accents_and_strips_particles_spaces():
    assert apply_pattern("first.last", "Jörg", "Müller", "acme.de") == "joerg.mueller@acme.de"
    assert apply_pattern("first.last", "Jan", "van der Berg", "acme.nl") == "jan.vanderberg@acme.nl"


def test_apply_pattern_needs_both_parts_except_first_only():
    assert apply_pattern("first.last", "", "Schmidt", "acme.de") == ""
    assert apply_pattern("first", "Anna", "", "acme.de") == "anna@acme.de"


def test_two_consistent_examples_give_high_confidence():
    pattern, confidence = infer_pattern(
        [("Anna Schmidt", "anna.schmidt@acme.de"), ("Peter Wolf", "peter.wolf@acme.de")]
    )
    assert pattern == "first.last"
    assert confidence == "high"


def test_one_example_gives_medium_confidence():
    pattern, confidence = infer_pattern([("Anna Schmidt", "a.schmidt@acme.de")])
    assert pattern == "f.last"
    assert confidence == "medium"


def test_conflicting_examples_yield_no_pattern():
    """Two formats on one domain means we cannot safely extrapolate."""
    pattern, confidence = infer_pattern(
        [("Anna Schmidt", "anna.schmidt@acme.de"), ("Peter Wolf", "p.wolf@acme.de")]
    )
    assert pattern == ""
    assert confidence == ""


def test_role_accounts_are_not_treated_as_examples():
    pattern, confidence = infer_pattern([("Anna Schmidt", "info@acme.de")])
    assert pattern == ""


def test_no_examples_yields_nothing():
    assert infer_pattern([]) == ("", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_pattern.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.email'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/leadgen/email/pattern.py
"""Infer a domain's address format from known (name, email) pairs.

This is the force multiplier for the whole run. A job ad that names its
recruiter gives us one anchor address; that anchor reveals the format, and
the format turns every other named person on the domain into a deliverable
address instead of a guess across six candidate layouts.

Conflicting examples deliberately yield nothing. A domain running two
formats cannot be extrapolated safely, and a wrong address is worse than a
missing one because it bounces.
"""

from __future__ import annotations

import unicodedata

from ..person.roles import split_name

CANDIDATE_PATTERNS: list[str] = [
    "first.last",
    "f.last",
    "flast",
    "firstlast",
    "first",
    "last.first",
    "first_last",
    "last",
]

# German/Nordic transliterations that ASCII folding alone gets wrong:
# NFKD turns "ö" into "o", but German address convention is "oe".
_TRANSLITERATE = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "å": "aa", "æ": "ae", "ø": "oe"}

_ROLE_LOCALPARTS = {
    "info",
    "office",
    "kontakt",
    "contact",
    "hello",
    "mail",
    "jobs",
    "job",
    "karriere",
    "career",
    "careers",
    "hr",
    "bewerbung",
    "recruiting",
    "sales",
    "support",
    "admin",
    "team",
    "welcome",
    "hallo",
    "empleo",
    "lavoro",
}


def _slug(value: str) -> str:
    """Lowercase, transliterate, strip everything that cannot be in a local part."""
    lowered = (value or "").strip().lower()
    for source, target in _TRANSLITERATE.items():
        lowered = lowered.replace(source, target)
    decomposed = unicodedata.normalize("NFKD", lowered)
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return "".join(char for char in ascii_only if char.isalnum())


def apply_pattern(pattern: str, first: str, last: str, domain: str) -> str:
    """Render an address, or "" when the pattern's inputs are not available."""
    first_slug, last_slug = _slug(first), _slug(last)
    if not domain:
        return ""
    local = {
        "first.last": f"{first_slug}.{last_slug}" if first_slug and last_slug else "",
        "f.last": f"{first_slug[:1]}.{last_slug}" if first_slug and last_slug else "",
        "flast": f"{first_slug[:1]}{last_slug}" if first_slug and last_slug else "",
        "firstlast": f"{first_slug}{last_slug}" if first_slug and last_slug else "",
        "first": first_slug,
        "last": last_slug,
        "last.first": f"{last_slug}.{first_slug}" if first_slug and last_slug else "",
        "first_last": f"{first_slug}_{last_slug}" if first_slug and last_slug else "",
    }.get(pattern, "")
    return f"{local}@{domain.lower()}" if local else ""


def infer_pattern(known: list[tuple[str, str]]) -> tuple[str, str]:
    """Return (pattern, confidence) consistent with every known pair.

    confidence is "high" with two or more agreeing examples, "medium" with
    exactly one, and "" when the examples disagree or none are usable.
    """
    usable: list[tuple[str, str, str]] = []
    for full_name, email in known:
        if not full_name or "@" not in (email or ""):
            continue
        local, _, domain = email.strip().lower().partition("@")
        if local in _ROLE_LOCALPARTS:
            continue
        first, last = split_name(full_name)
        if not (first or last):
            continue
        usable.append((first, last, email.strip().lower()))

    if not usable:
        return "", ""

    matching = [
        pattern
        for pattern in CANDIDATE_PATTERNS
        if all(
            apply_pattern(pattern, first, last, email.partition("@")[2]) == email
            for first, last, email in usable
        )
    ]
    if not matching:
        return "", ""
    # CANDIDATE_PATTERNS is ordered most-specific first, so the first match is
    # the least likely to be a coincidence (e.g. "first" also matches a
    # one-word name that "first.last" would reject).
    return matching[0], "high" if len(usable) >= 2 else "medium"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_pattern.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/email/ tests/leadgen/test_pattern.py
git commit -m "leadgen: infer a domain's email format from anchor addresses"
```

---

### Task 4: Email validation

**Files:**
- Create: `src/leadgen/email/validate.py`
- Modify: `requirements.txt` — add `dnspython>=2.6.1`
- Modify: `pyproject.toml:28-35` — add `"dnspython>=2.6.1"` to `dependencies`
- Test: `tests/leadgen/test_validate.py`

**Interfaces:**
- Consumes: `leadgen.email.pattern` (nothing at runtime — separate concern)
- Produces: `is_role_account(email: str) -> bool`, `valid_syntax(email: str) -> bool`, `has_mx(domain: str) -> bool`, `is_catch_all(domain: str) -> bool | None`, `EmailVerdict` dataclass with `.status: str` and `.reason: str`, `validate(email: str, *, smtp: bool = True) -> EmailVerdict`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_validate.py
"""Email validation ladder: syntax, role accounts, MX, catch-all."""

from __future__ import annotations

import pytest

from leadgen.email import validate as V


@pytest.mark.parametrize(
    "email",
    [
        "info@acme.de",
        "jobs@acme.de",
        "karriere@acme.de",
        "hr@acme.de",
        "bewerbung@acme.de",
        "contact@acme.fr",
        "empleo@acme.es",
        "no-reply@acme.de",
    ],
)
def test_role_accounts_are_rejected(email):
    assert V.is_role_account(email) is True


@pytest.mark.parametrize("email", ["anna.schmidt@acme.de", "j.doe@acme.co.uk", "a@b.io"])
def test_personal_addresses_are_not_role_accounts(email):
    assert V.is_role_account(email) is False


@pytest.mark.parametrize(
    "email", ["anna.schmidt@acme.de", "a+tag@sub.example.co.uk", "x_y@d-e.io"]
)
def test_valid_syntax(email):
    assert V.valid_syntax(email) is True


@pytest.mark.parametrize(
    "email", ["", "no-at-sign", "@acme.de", "a@", "a@b", "a b@acme.de", "a@acme..de"]
)
def test_invalid_syntax(email):
    assert V.valid_syntax(email) is False


def test_validate_rejects_role_account_before_touching_the_network(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("network must not be touched for a role account")

    monkeypatch.setattr(V, "has_mx", explode)
    verdict = V.validate("info@acme.de", smtp=False)
    assert verdict.status == "rejected"
    assert "role account" in verdict.reason


def test_validate_drops_a_domain_with_no_mx(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: False)
    verdict = V.validate("anna@dead.de", smtp=False)
    assert verdict.status == "rejected"
    assert "mx" in verdict.reason.lower()


def test_validate_returns_unknown_when_smtp_is_disabled(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    verdict = V.validate("anna@acme.de", smtp=False)
    assert verdict.status == "unknown"


def test_validate_flags_catch_all(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    monkeypatch.setattr(V, "is_catch_all", lambda domain: True)
    verdict = V.validate("anna@acme.de", smtp=True)
    assert verdict.status == "catch_all"


def test_validate_passes_a_clean_domain(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    monkeypatch.setattr(V, "is_catch_all", lambda domain: False)
    verdict = V.validate("anna@acme.de", smtp=True)
    assert verdict.status == "ok"


def test_mx_lookups_are_cached(monkeypatch):
    calls = []

    def fake_resolve(domain, record_type):
        calls.append(domain)
        return ["mx1"]

    monkeypatch.setattr(V, "_resolve", fake_resolve)
    V.has_mx.cache_clear()
    V.has_mx("acme.de")
    V.has_mx("acme.de")
    assert calls == ["acme.de"], "second lookup must come from the cache"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_validate.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/leadgen/email/validate.py
"""Validation ladder, cheapest check first, stopping at the first failure.

Order matters for cost and for politeness: a role account is rejected from a
frozenset before any DNS query, and a domain with no MX is dropped before any
SMTP connection. Catch-all detection costs one connection per *domain*, not
per address, and its result is cached.
"""

from __future__ import annotations

import logging
import re
import smtplib
import socket
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

# Local parts that are a function, not a person. Emitting one of these as
# "the CTO's email" would be wrong in a way the buyer notices immediately.
ROLE_LOCALPARTS: frozenset[str] = frozenset(
    {
        "info", "information", "office", "buero", "bureau",
        "kontakt", "contact", "contacto", "contatto", "contato",
        "hello", "hallo", "hi", "mail", "email", "post", "postmaster",
        "jobs", "job", "karriere", "karriera", "career", "careers", "cariere",
        "bewerbung", "bewerbungen", "recruiting", "recruitment", "hiring",
        "empleo", "trabajo", "lavoro", "emploi", "praca", "vacatures",
        "hr", "people", "talent", "personal", "personel",
        "sales", "vertrieb", "ventas", "vendite", "verkoop",
        "support", "help", "helpdesk", "service", "kundenservice",
        "admin", "administration", "webmaster", "hostmaster", "abuse",
        "team", "welcome", "willkommen", "presse", "press", "media",
        "noreply", "no-reply", "donotreply", "do-not-reply",
        "marketing", "newsletter", "invoice", "rechnung", "billing",
        "datenschutz", "privacy", "legal", "impressum",
    }
)

# Deliberately not full RFC 5322 — that grammar accepts addresses no mail
# system in this dataset will ever use. This is the practical subset.
_SYNTAX_RX = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,}$"
)

SMTP_TIMEOUT = 8.0
_PROBE_LOCALPART = "zz-harvestkit-probe-9f3a1c"


@dataclass
class EmailVerdict:
    status: str  # "ok" | "catch_all" | "unknown" | "rejected"
    reason: str = ""


def is_role_account(email: str) -> bool:
    local = (email or "").strip().lower().partition("@")[0]
    if not local:
        return False
    if local in ROLE_LOCALPARTS:
        return True
    # "no-reply", "info-de", "jobs2024" — a role word plus a suffix.
    stem = re.split(r"[-_.+0-9]", local)[0]
    return stem in ROLE_LOCALPARTS


def valid_syntax(email: str) -> bool:
    value = (email or "").strip()
    return bool(value) and len(value) <= 254 and bool(_SYNTAX_RX.match(value))


def _resolve(domain: str, record_type: str) -> list:
    """Thin seam over dnspython so tests can substitute it."""
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    resolver.timeout = 5.0
    return list(resolver.resolve(domain, record_type))


@lru_cache(maxsize=20000)
def has_mx(domain: str) -> bool:
    """True when the domain can receive mail. Cached — one lookup per domain."""
    if not domain:
        return False
    try:
        return bool(_resolve(domain, "MX"))
    except Exception:
        # No MX is common for parked or dead domains. Some domains accept mail
        # on their A record, but for prospecting purposes a missing MX is a
        # strong enough signal to drop the lead.
        return False


def _mx_host(domain: str) -> str:
    try:
        records = _resolve(domain, "MX")
    except Exception:
        return ""
    hosts = sorted((getattr(r, "preference", 0), str(getattr(r, "exchange", "")).rstrip(".")) for r in records)
    return hosts[0][1] if hosts else ""


@lru_cache(maxsize=20000)
def is_catch_all(domain: str) -> bool | None:
    """One probe per domain. None means the server refused to tell us."""
    host = _mx_host(domain)
    if not host:
        return None
    try:
        with smtplib.SMTP(host, 25, timeout=SMTP_TIMEOUT) as server:
            server.ehlo_or_helo_if_needed()
            server.mail("verify@example.com")
            code, _ = server.rcpt(f"{_PROBE_LOCALPART}@{domain}")
        return code in (250, 251)
    except (smtplib.SMTPException, socket.error, OSError) as exc:
        log.debug("catch-all probe failed for %s: %s", domain, exc)
        return None


def validate(email: str, *, smtp: bool = True) -> EmailVerdict:
    """Run the ladder. Never raises."""
    value = (email or "").strip().lower()
    if not valid_syntax(value):
        return EmailVerdict("rejected", "invalid syntax")
    if is_role_account(value):
        return EmailVerdict("rejected", "role account, not a person")
    domain = value.partition("@")[2]
    if not has_mx(domain):
        return EmailVerdict("rejected", "domain has no MX record")
    if not smtp:
        return EmailVerdict("unknown", "smtp probing disabled")
    catch_all = is_catch_all(domain)
    if catch_all is True:
        return EmailVerdict("catch_all", "domain accepts all recipients")
    if catch_all is None:
        return EmailVerdict("unknown", "server would not answer the probe")
    return EmailVerdict("ok", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_validate.py -v`
Expected: PASS, 25 parametrised cases

- [ ] **Step 5: Add the dependency and commit**

```bash
python -m pip install "dnspython>=2.6.1"
git add src/leadgen/email/validate.py tests/leadgen/test_validate.py requirements.txt pyproject.toml
git commit -m "leadgen: email validation ladder with per-domain catch-all detection"
```

---

### Task 5: Completeness scoring and the quota cut

**Files:**
- Create: `src/leadgen/score/__init__.py` (empty)
- Create: `src/leadgen/score/completeness.py`
- Create: `src/leadgen/score/quota.py`
- Test: `tests/leadgen/test_score.py`

**Interfaces:**
- Consumes: `leadgen.models.Lead`, `leadgen.models.EMAIL_STATUS_ORDER`
- Produces: `score_lead(lead: Lead) -> float`; `QuotaReport` dataclass with `.requested: int`, `.selected: int`, `.shortfall: int`, `.dropped: dict[str, int]`, `.by_country: dict[str, int]`; `select(leads: list[Lead], *, target: int, country_ceiling: float = 0.25) -> tuple[list[Lead], QuotaReport]`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_score.py
"""Completeness scoring and exact-N selection."""

from __future__ import annotations

from leadgen.models import Lead
from leadgen.score.completeness import score_lead
from leadgen.score.quota import select


def _lead(**kwargs) -> Lead:
    base = dict(
        person_name="Jane Doe",
        person_role="CTO",
        person_role_family="tech_leadership",
        person_email="j.doe@acme.de",
        email_status="verified",
        company_name="Acme",
        company_domain="acme.de",
        company_country="DE",
    )
    base.update(kwargs)
    return Lead(**base)


def test_published_email_outscores_inferred():
    published = _lead(email_status="published")
    inferred = _lead(email_status="inferred_medium")
    assert score_lead(published) > score_lead(inferred)


def test_catch_all_scores_lowest_of_the_statuses():
    assert score_lead(_lead(email_status="catch_all")) < score_lead(_lead(email_status="unknown"))


def test_target_role_outscores_other():
    assert score_lead(_lead(person_role_family="hr")) > score_lead(_lead(person_role_family="other"))


def test_more_populated_fields_score_higher():
    sparse = _lead()
    rich = _lead(person_phone="+49 89 1234", person_linkedin="https://x/in/j", company_city="Munich")
    assert score_lead(rich) > score_lead(sparse)


def test_select_returns_exactly_the_target():
    leads = [_lead(person_email=f"p{i}@acme{i}.de", company_domain=f"acme{i}.de") for i in range(50)]
    selected, report = select(leads, target=10)
    assert len(selected) == 10
    assert report.selected == 10
    assert report.shortfall == 0


def test_select_reports_shortfall_rather_than_padding():
    leads = [_lead(person_email=f"p{i}@acme{i}.de", company_domain=f"acme{i}.de") for i in range(4)]
    selected, report = select(leads, target=10)
    assert len(selected) == 4
    assert report.shortfall == 6


def test_select_drops_leads_without_an_email():
    leads = [_lead(person_email="", email_status=""), _lead()]
    selected, report = select(leads, target=10)
    assert len(selected) == 1
    assert report.dropped["no_email"] == 1


def test_select_deduplicates_the_same_person_found_twice():
    a = _lead(source_person_url="https://acme.de/team")
    b = _lead(source_person_url="https://acme.de/impressum")
    selected, report = select([a, b], target=10)
    assert len(selected) == 1
    assert report.dropped["duplicate"] == 1


def test_country_ceiling_caps_a_dominant_country():
    german = [
        _lead(person_email=f"d{i}@de{i}.de", company_domain=f"de{i}.de", company_country="DE")
        for i in range(50)
    ]
    french = [
        _lead(person_email=f"f{i}@fr{i}.fr", company_domain=f"fr{i}.fr", company_country="FR")
        for i in range(50)
    ]
    selected, report = select(german + french, target=20, country_ceiling=0.25)
    assert report.by_country["DE"] <= 5, "25% of 20 is 5"


def test_ceiling_relaxes_rather_than_under_delivering():
    """If honouring the ceiling would miss the target, fill from what is left."""
    german = [
        _lead(person_email=f"d{i}@de{i}.de", company_domain=f"de{i}.de", company_country="DE")
        for i in range(20)
    ]
    selected, report = select(german, target=10, country_ceiling=0.25)
    assert len(selected) == 10, "one country only — the ceiling must not block delivery"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_score.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.score'`

- [ ] **Step 3: Write the implementation**

```python
# src/leadgen/score/completeness.py
"""Score a lead on how useful it will be to whoever works the list.

Email quality dominates because a lead you cannot contact is not a lead;
role-family match comes next because the brief asked for HR and CTOs
specifically; everything else is incremental context.
"""

from __future__ import annotations

from ..models import Lead

EMAIL_STATUS_WEIGHTS: dict[str, float] = {
    "published": 40.0,
    "verified": 36.0,
    "inferred_high": 26.0,
    "inferred_medium": 16.0,
    "unknown": 10.0,
    "catch_all": 4.0,
}

ROLE_FAMILY_WEIGHTS: dict[str, float] = {
    "hr": 20.0,
    "tech_leadership": 20.0,
    "other": 2.0,
}

# Everything else contributes a flat amount when populated.
FIELD_WEIGHTS: dict[str, float] = {
    "person_name": 10.0,
    "person_role": 6.0,
    "company_name": 5.0,
    "company_domain": 4.0,
    "company_country": 4.0,
    "person_linkedin": 3.0,
    "person_phone": 3.0,
    "company_city": 2.0,
    "company_region": 1.0,
    "tech_stack": 2.0,
    "seniority": 1.0,
    "company_size_hint": 1.0,
    "source_person_url": 1.0,
}


def score_lead(lead: Lead) -> float:
    total = EMAIL_STATUS_WEIGHTS.get(lead.email_status, 0.0)
    total += ROLE_FAMILY_WEIGHTS.get(lead.person_role_family, 0.0)
    for name, weight in FIELD_WEIGHTS.items():
        if str(getattr(lead, name, "") or "").strip():
            total += weight
    # Evidence is cheap to carry and makes a row defensible; reward it lightly
    # so a traceable lead beats an identical untraceable one.
    total += min(len(lead.evidence), 5) * 0.5
    return total
```

```python
# src/leadgen/score/quota.py
"""Turn a pile of candidate leads into exactly N, or say why it could not.

The engine over-fetches deliberately, so this is where the run's promise is
kept. Padding a short list with leads that fail the criteria would poison
the buyer's trust in the rows that *are* good, so a shortfall is reported
and the smaller file written.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ..models import Lead
from .completeness import score_lead

_WS_RX = re.compile(r"\s+")


@dataclass
class QuotaReport:
    requested: int = 0
    candidates: int = 0
    selected: int = 0
    shortfall: int = 0
    dropped: Counter = field(default_factory=Counter)
    by_country: Counter = field(default_factory=Counter)

    def summary(self) -> str:
        lines = [
            f"requested {self.requested} · candidates {self.candidates} · selected {self.selected}",
        ]
        if self.shortfall:
            lines.append(f"SHORTFALL {self.shortfall} — the run could not find enough qualifying leads")
        if self.dropped:
            lines.append("dropped: " + ", ".join(f"{k}={v}" for k, v in self.dropped.most_common()))
        if self.by_country:
            lines.append("by country: " + ", ".join(f"{k}={v}" for k, v in self.by_country.most_common()))
        return "\n".join(lines)


def _person_key(lead: Lead) -> str:
    """Secondary dedupe key: the same human found by two strategies."""
    name = _WS_RX.sub(" ", (lead.person_name or "").strip().lower())
    return f"{name}@@{(lead.company_domain or '').strip().lower()}"


def select(
    leads: list[Lead], *, target: int, country_ceiling: float = 0.25
) -> tuple[list[Lead], QuotaReport]:
    report = QuotaReport(requested=target, candidates=len(leads))

    qualified: list[Lead] = []
    seen_ids: set[str] = set()
    seen_people: set[str] = set()
    for lead in leads:
        if not (lead.person_email or "").strip():
            report.dropped["no_email"] += 1
            continue
        if not (lead.person_name or "").strip():
            report.dropped["no_name"] += 1
            continue
        fingerprint, person = lead.fingerprint(), _person_key(lead)
        if fingerprint in seen_ids or person in seen_people:
            report.dropped["duplicate"] += 1
            continue
        seen_ids.add(fingerprint)
        seen_people.add(person)
        qualified.append(lead)

    qualified.sort(key=score_lead, reverse=True)

    cap = max(1, int(target * country_ceiling))
    selected: list[Lead] = []
    per_country: Counter = Counter()
    overflow: list[Lead] = []
    for lead in qualified:
        if len(selected) >= target:
            break
        country = (lead.company_country or "??").upper()
        if per_country[country] >= cap:
            overflow.append(lead)
            continue
        selected.append(lead)
        per_country[country] += 1

    # The ceiling is a spread preference, not a reason to under-deliver. If
    # honouring it leaves the list short, fill from the highest-scoring
    # leads it excluded.
    if len(selected) < target and overflow:
        needed = target - len(selected)
        selected.extend(overflow[:needed])
        for lead in overflow[:needed]:
            per_country[(lead.company_country or "??").upper()] += 1

    report.selected = len(selected)
    report.shortfall = max(0, target - len(selected))
    report.by_country = per_country
    return selected, report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_score.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/score/ tests/leadgen/test_score.py
git commit -m "leadgen: completeness scoring and exact-N selection with country ceiling"
```

---

### Task 6: Person extraction strategies

**Files:**
- Create: `src/leadgen/person/paths.py`
- Create: `src/leadgen/person/hit.py`
- Create: `src/leadgen/person/strategies/__init__.py`
- Create: `src/leadgen/person/strategies/jsonld_person.py`
- Create: `src/leadgen/person/strategies/impressum.py`
- Create: `src/leadgen/person/strategies/team.py`
- Test: `tests/leadgen/test_person_strategies.py`

**Interfaces:**
- Consumes: `leadgen.person.roles.classify_role`, `leadgen.person.roles.split_name`, `bs4.BeautifulSoup`
- Produces: `PersonHit` dataclass with `.name`, `.role`, `.email`, `.phone`, `.linkedin`, `.source_url`, `.strategy`; `candidate_paths(country: str) -> list[str]`; each strategy exposes `extract(html: str, url: str) -> list[PersonHit]`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_person_strategies.py
"""Each person-extraction strategy against a realistic fixture."""

from __future__ import annotations

from leadgen.person.paths import candidate_paths
from leadgen.person.strategies import impressum, jsonld_person, team

IMPRESSUM_DE = """
<html><body><main>
<h1>Impressum</h1>
<p>Acme Software GmbH<br/>Maximilianstrasse 12<br/>80539 M&uuml;nchen</p>
<p>Vertretungsberechtigter Gesch&auml;ftsf&uuml;hrer: Dr. Anna Schmidt</p>
<p>Technischer Leiter: Peter Wolf</p>
<p>E-Mail: anna.schmidt@acme.de</p>
<p>Registergericht: Amtsgericht M&uuml;nchen HRB 123456</p>
</main></body></html>
"""

JSONLD_PEOPLE = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization","name":"Acme",
 "employee":[
   {"@type":"Person","name":"Anna Schmidt","jobTitle":"CTO","email":"anna.schmidt@acme.de"},
   {"@type":"Person","name":"Peter Wolf","jobTitle":"Head of People"}
 ]}
</script></head><body></body></html>
"""

TEAM_PAGE = """
<html><body><main>
<h1>Our team</h1>
<div class="member"><h3>Anna Schmidt</h3><p class="role">Chief Technology Officer</p>
  <a href="mailto:anna.schmidt@acme.de">Email</a>
  <a href="https://www.linkedin.com/in/annaschmidt">LinkedIn</a></div>
<div class="member"><h3>Peter Wolf</h3><p class="role">Head of HR</p></div>
<div class="member"><h3>Sales Team</h3><p class="role">Sales</p></div>
</main></body></html>
"""


def test_impressum_finds_the_managing_director_and_role():
    hits = impressum.extract(IMPRESSUM_DE, "https://acme.de/impressum")
    by_name = {h.name: h for h in hits}
    assert "Anna Schmidt" in by_name
    assert "Geschäftsführer" in by_name["Anna Schmidt"].role
    assert by_name["Anna Schmidt"].source_url == "https://acme.de/impressum"


def test_impressum_finds_a_second_labelled_person():
    hits = impressum.extract(IMPRESSUM_DE, "https://acme.de/impressum")
    assert any(h.name == "Peter Wolf" and "Technischer Leiter" in h.role for h in hits)


def test_impressum_does_not_return_the_company_as_a_person():
    hits = impressum.extract(IMPRESSUM_DE, "https://acme.de/impressum")
    assert not any("GmbH" in h.name for h in hits)


def test_jsonld_person_reads_employee_array():
    hits = jsonld_person.extract(JSONLD_PEOPLE, "https://acme.de/about")
    assert {h.name for h in hits} == {"Anna Schmidt", "Peter Wolf"}
    anna = next(h for h in hits if h.name == "Anna Schmidt")
    assert anna.role == "CTO"
    assert anna.email == "anna.schmidt@acme.de"


def test_team_page_pairs_names_roles_emails_and_linkedin():
    hits = team.extract(TEAM_PAGE, "https://acme.de/team")
    anna = next(h for h in hits if h.name == "Anna Schmidt")
    assert anna.role == "Chief Technology Officer"
    assert anna.email == "anna.schmidt@acme.de"
    assert anna.linkedin.endswith("/in/annaschmidt")


def test_team_page_rejects_a_non_person_card():
    hits = team.extract(TEAM_PAGE, "https://acme.de/team")
    assert not any(h.name == "Sales Team" for h in hits)


def test_every_hit_records_its_strategy_and_source():
    for module, url in ((impressum, "https://acme.de/impressum"), (team, "https://acme.de/team")):
        html = IMPRESSUM_DE if module is impressum else TEAM_PAGE
        for hit in module.extract(html, url):
            assert hit.strategy
            assert hit.source_url == url


def test_candidate_paths_are_country_aware_and_always_include_english():
    german = candidate_paths("DE")
    assert "/impressum" in german
    assert "/team" in german
    french = candidate_paths("FR")
    assert "/mentions-legales" in french
    assert "/equipe" in french
    unknown = candidate_paths("XX")
    assert "/team" in unknown and "/about" in unknown


def test_candidate_paths_have_no_duplicates():
    paths = candidate_paths("DE")
    assert len(paths) == len(set(paths))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_person_strategies.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.person.paths'`

- [ ] **Step 3: Write the implementation**

```python
# src/leadgen/person/hit.py
"""One extracted person, before it is merged into a Lead."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PersonHit:
    name: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    source_url: str = ""
    strategy: str = ""
```

```python
# src/leadgen/person/paths.py
"""Country -> the paths on a company site that tend to name people.

Data, not code: adding a market is editing a list. English paths are always
appended because a great many EU companies run an English site regardless of
where they are registered.
"""

from __future__ import annotations

UNIVERSAL: list[str] = [
    "/team",
    "/about",
    "/about-us",
    "/company",
    "/people",
    "/leadership",
    "/management",
    "/contact",
    "/imprint",
]

BY_COUNTRY: dict[str, list[str]] = {
    "DE": ["/impressum", "/ueber-uns", "/unternehmen", "/kontakt", "/karriere", "/das-team"],
    "AT": ["/impressum", "/ueber-uns", "/unternehmen", "/kontakt"],
    "CH": ["/impressum", "/ueber-uns", "/unternehmen", "/kontakt", "/a-propos"],
    "FR": ["/mentions-legales", "/equipe", "/a-propos", "/notre-equipe", "/contact"],
    "IT": ["/note-legali", "/chi-siamo", "/il-team", "/contatti", "/azienda"],
    "ES": ["/aviso-legal", "/quienes-somos", "/equipo", "/nosotros", "/contacto"],
    "PT": ["/aviso-legal", "/quem-somos", "/equipa", "/contactos"],
    "NL": ["/colofon", "/over-ons", "/ons-team", "/contact"],
    "BE": ["/over-ons", "/equipe", "/contact"],
    "PL": ["/o-nas", "/zespol", "/nasz-zespol", "/kontakt"],
    "SE": ["/om-oss", "/vart-team", "/kontakt"],
    "NO": ["/om-oss", "/vart-team", "/kontakt"],
    "DK": ["/om-os", "/vores-team", "/kontakt"],
    "FI": ["/tietoa-meista", "/tiimi", "/yhteystiedot"],
    "CZ": ["/o-nas", "/tym", "/kontakt"],
    "RO": ["/despre-noi", "/echipa", "/contact"],
}


def candidate_paths(country: str) -> list[str]:
    """Localised paths first — they are the higher-yield pages — then English."""
    ordered = list(BY_COUNTRY.get((country or "").upper(), [])) + UNIVERSAL
    seen: set[str] = set()
    return [path for path in ordered if not (path in seen or seen.add(path))]
```

```python
# src/leadgen/person/strategies/jsonld_person.py
"""schema.org Person objects — the cleanest source when a site publishes them."""

from __future__ import annotations

import json
import logging

from bs4 import BeautifulSoup

from ..hit import PersonHit

log = logging.getLogger(__name__)

_PERSON_KEYS = ("employee", "employees", "founder", "founders", "member", "members", "author")


def _walk(node: object, out: list[dict]) -> None:
    if isinstance(node, dict):
        if str(node.get("@type", "")).lower() == "person":
            out.append(node)
        for key, value in node.items():
            if key in _PERSON_KEYS or isinstance(value, (dict, list)):
                _walk(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk(item, out)


def extract(html: str, url: str) -> list[PersonHit]:
    soup = BeautifulSoup(html, "html.parser")
    people: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            _walk(json.loads(raw), people)
        except (json.JSONDecodeError, TypeError) as exc:
            log.debug("jsonld_person: unparseable block on %s: %s", url, exc)

    hits: list[PersonHit] = []
    for person in people:
        name = str(person.get("name") or "").strip()
        if not name:
            continue
        email = str(person.get("email") or "").replace("mailto:", "").strip()
        same_as = person.get("sameAs") or []
        if isinstance(same_as, str):
            same_as = [same_as]
        linkedin = next((s for s in same_as if "linkedin.com/in/" in str(s)), "")
        hits.append(
            PersonHit(
                name=name,
                role=str(person.get("jobTitle") or "").strip(),
                email=email,
                phone=str(person.get("telephone") or "").strip(),
                linkedin=str(linkedin),
                source_url=url,
                strategy="jsonld_person",
            )
        )
    return hits
```

```python
# src/leadgen/person/strategies/impressum.py
"""German-family legal-notice pages.

§5 TMG makes an Impressum mandatory for commercial German sites and requires
it to name the representatives, which is why this is the highest-yield single
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
    r"gesch[äa]ftsf[üu]hrer(?:in)?",
    r"vorstand(?:svorsitzender?)?",
    r"inhaber(?:in)?",
    r"technischer?\s+leiter(?:in)?",
    r"it[-\s]?leiter(?:in)?",
    r"personalleiter(?:in)?",
    r"verantwortlich(?:er?)?\s+f[üu]r\s+den\s+inhalt",
    r"redaktionell\s+verantwortlich",
    r"directeur\s+de\s+la\s+publication",
    r"responsabile\s+del\s+trattamento",
]

_LABEL_RX = re.compile(
    r"(?P<label>" + "|".join(LABELS) + r")\s*[:\u2013-]\s*(?P<value>[^\n\r|;]{2,80})",
    re.I,
)

# A legal entity is not a person.
_ENTITY_RX = re.compile(
    r"\b(?:gmbh|ug|ag|kg|ohg|mbh|e\.?v\.?|s\.?a\.?r\.?l|s\.?p\.?a|b\.?v|n\.?v|ltd|inc|llc|oy|ab|as)\b",
    re.I,
)
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _looks_like_a_person(name: str) -> bool:
    if not name or _ENTITY_RX.search(name):
        return False
    first, last = split_name(name)
    # A person we can use has at least a surname, and a name of five or more
    # whitespace-separated tokens is a sentence, not a name.
    return bool(last) and len(name.split()) <= 5


def extract(html: str, url: str) -> list[PersonHit]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    page_emails = _EMAIL_RX.findall(text)

    hits: list[PersonHit] = []
    seen: set[str] = set()
    for match in _LABEL_RX.finditer(text):
        raw_name = match.group("value").strip(" .,\u00a0")
        # The value may carry a trailing email; keep the name, note the address.
        inline_email = _EMAIL_RX.search(raw_name)
        if inline_email:
            raw_name = raw_name.replace(inline_email.group(0), "").strip(" .,;-")
        if not _looks_like_a_person(raw_name) or raw_name.lower() in seen:
            continue
        seen.add(raw_name.lower())
        hits.append(
            PersonHit(
                name=raw_name,
                role=match.group("label").strip(),
                # Only attribute a page-level address when there is exactly one
                # candidate; more than one and we cannot tell whose it is.
                email=inline_email.group(0) if inline_email else (page_emails[0] if len(page_emails) == 1 else ""),
                source_url=url,
                strategy="impressum",
            )
        )
    return hits
```

```python
# src/leadgen/person/strategies/team.py
"""Team and about pages — repeated cards of name, role and sometimes contact.

There is no universal markup here, so the parser finds heading elements whose
text reads like a person's name and takes the role from the nearest following
text within the same card.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from ..hit import PersonHit
from ..roles import split_name

_NAME_TAGS = ("h2", "h3", "h4", "h5", "strong", "b")
_ENTITY_RX = re.compile(
    r"\b(?:gmbh|ag|ltd|inc|llc|bv|nv|team|abteilung|department|group|gruppe|division)\b", re.I
)
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A person's name: two to four capitalised tokens, allowing particles.
_NAME_RX = re.compile(r"^(?:[A-ZÄÖÜÀ-Þ][\w'’-]+\.?\s+){1,3}[A-ZÄÖÜÀ-Þ][\w'’-]+$")


def _looks_like_a_person(text: str) -> bool:
    value = (text or "").strip()
    if not value or _ENTITY_RX.search(value):
        return False
    if not _NAME_RX.match(value):
        return False
    return bool(split_name(value)[1])


def _card_of(tag: Tag) -> Tag:
    """Climb to the smallest ancestor that plausibly wraps one person."""
    node = tag
    for _ in range(3):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "main"):
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
        card_text = card.get_text("\n", strip=True)
        # The role is the first non-empty line after the name inside the card.
        lines = [line for line in card_text.split("\n") if line.strip()]
        role = ""
        if name in lines:
            index = lines.index(name)
            role = lines[index + 1] if index + 1 < len(lines) else ""
        if role == name:
            role = ""

        email = ""
        mailto = card.find("a", href=re.compile(r"^mailto:", re.I))
        if mailto:
            email = str(mailto.get("href", ""))[7:].split("?")[0]
        elif (found := _EMAIL_RX.search(card_text)) is not None:
            email = found.group(0)

        linkedin = ""
        profile = card.find("a", href=re.compile(r"linkedin\.com/in/", re.I))
        if profile:
            linkedin = str(profile.get("href", ""))

        seen.add(name.lower())
        hits.append(
            PersonHit(
                name=name,
                role=role.strip(),
                email=email.strip(),
                linkedin=linkedin,
                source_url=url,
                strategy="team",
            )
        )
    return hits
```

```python
# src/leadgen/person/strategies/__init__.py
"""Person-extraction strategies, cheapest and cleanest first."""

from . import impressum, jsonld_person, team

__all__ = ["jsonld_person", "impressum", "team"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_person_strategies.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/person/ tests/leadgen/test_person_strategies.py
git commit -m "leadgen: Impressum, schema.org Person and team-page extraction"
```

---

### Task 7: Company domain resolution

**Files:**
- Create: `src/leadgen/company/__init__.py` (empty)
- Create: `src/leadgen/company/domain.py`
- Test: `tests/leadgen/test_domain.py`

**Interfaces:**
- Consumes: `job_scraper.http.HttpClient` (duck-typed — tests pass a stub with `.get(url) -> tuple[str, str] | None`)
- Produces: `ATS_HOSTS: frozenset[str]`, `is_company_site(url: str) -> bool`, `resolve_domain(company_name: str, hints: list[str], http) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_domain.py
"""Resolving a company to its own website, not its ATS or an aggregator."""

from __future__ import annotations

from leadgen.company.domain import is_company_site, resolve_domain


class StubHttp:
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for fragment, body in self.pages.items():
            if fragment in url:
                return url, body
        return None


def test_ats_and_aggregator_hosts_are_not_company_sites():
    for url in [
        "https://boards.greenhouse.io/acme",
        "https://jobs.lever.co/acme",
        "https://acme.jobs.personio.de/",
        "https://www.linkedin.com/company/acme",
        "https://www.indeed.com/cmp/Acme",
        "https://acme.recruitee.com/",
        "https://apply.workable.com/acme/",
    ]:
        assert is_company_site(url) is False, url


def test_a_real_company_site_is_accepted():
    assert is_company_site("https://www.acme.de/karriere") is True


def test_hints_are_preferred_over_guessing():
    http = StubHttp(pages={"acme.de": "<html><title>Acme</title></html>"})
    domain = resolve_domain("Acme GmbH", ["https://boards.greenhouse.io/acme", "https://acme.de/jobs"], http)
    assert domain == "acme.de"


def test_ats_hints_are_skipped():
    http = StubHttp(pages={"acme.de": "<html></html>"})
    resolve_domain("Acme GmbH", ["https://boards.greenhouse.io/acme"], http)
    assert not any("greenhouse" in call for call in http.calls)


def test_unreachable_candidates_yield_empty_string():
    assert resolve_domain("Nowhere Ltd", [], StubHttp()) == ""


def test_www_prefix_is_normalised_away():
    http = StubHttp(pages={"acme.de": "<html></html>"})
    assert resolve_domain("Acme", ["https://www.acme.de/about"], http) == "acme.de"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.company'`

- [ ] **Step 3: Write the implementation**

```python
# src/leadgen/company/domain.py
"""Find the company's *own* website.

A job ad's URL is usually on an ATS or an aggregator, and crawling those for
an Impressum finds the ATS vendor's legal notice rather than the employer's.
Everything in ATS_HOSTS is therefore rejected before any fetch.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ATS_HOSTS: frozenset[str] = frozenset(
    {
        "greenhouse.io", "boards.greenhouse.io", "lever.co", "jobs.lever.co",
        "ashbyhq.com", "jobs.ashbyhq.com", "myworkdayjobs.com", "workday.com",
        "personio.de", "personio.com", "jobs.personio.de", "recruitee.com",
        "workable.com", "apply.workable.com", "smartrecruiters.com",
        "successfactors.com", "taleo.net", "icims.com", "bamboohr.com",
        "teamtailor.com", "jobvite.com", "breezy.hr", "join.com", "softgarden.io",
        "linkedin.com", "indeed.com", "glassdoor.com", "xing.com", "stepstone.de",
        "monster.com", "jobs.ch", "finn.no", "arbeitsagentur.de", "welcometothejungle.com",
        "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
        "google.com", "github.com", "medium.com", "notion.site", "wixsite.com",
    }
)


def _registrable(host: str) -> str:
    host = (host or "").lower().strip().removeprefix("www.")
    return host


def is_company_site(url: str) -> bool:
    """False for ATS vendors, job aggregators and social platforms."""
    host = _registrable(urlparse(url or "").netloc)
    if not host or "." not in host:
        return False
    return not any(host == bad or host.endswith("." + bad) for bad in ATS_HOSTS)


def resolve_domain(company_name: str, hints: list[str], http) -> str:
    """Return the registrable domain of the company's own site, or "".

    `hints` are URLs seen alongside the company — the ad's apply link, a
    website field, a logo link. They are tried in order; the first that is
    not an ATS host and actually responds wins. No search-engine querying:
    it is rate-limited, unreliable, and against most engines' terms.
    """
    tried: set[str] = set()
    for hint in hints:
        if not hint or not is_company_site(hint):
            continue
        host = _registrable(urlparse(hint).netloc)
        if not host or host in tried:
            continue
        tried.add(host)
        for scheme in ("https", "http"):
            try:
                if http.get(f"{scheme}://{host}/") is not None:
                    return host
            except Exception as exc:
                log.debug("domain: %s://%s failed: %s", scheme, host, exc)
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_domain.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/company/ tests/leadgen/test_domain.py
git commit -m "leadgen: resolve a company to its own site, never its ATS"
```

---

### Task 8: The person cascade

**Files:**
- Create: `src/leadgen/person/cascade.py`
- Test: `tests/leadgen/test_cascade.py`

**Interfaces:**
- Consumes: `leadgen.person.paths.candidate_paths`, `leadgen.person.strategies.*`, `leadgen.person.hit.PersonHit`, `leadgen.person.roles.classify_role`
- Produces: `resolve_people(domain: str, country: str, http, *, max_pages: int = 8) -> list[PersonHit]`, `merge_hits(hits: list[PersonHit]) -> list[PersonHit]`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_cascade.py
"""Cascade orchestration: fetch candidate pages, run strategies, merge."""

from __future__ import annotations

from leadgen.person.cascade import merge_hits, resolve_people
from leadgen.person.hit import PersonHit

IMPRESSUM = """
<html><body><p>Gesch&auml;ftsf&uuml;hrer: Anna Schmidt</p>
<p>E-Mail: anna.schmidt@acme.de</p></body></html>
"""
TEAM = """
<html><body><main>
<h3>Anna Schmidt</h3><p>Chief Technology Officer</p>
<h3>Peter Wolf</h3><p>Head of HR</p>
</main></body></html>
"""


class StubHttp:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for fragment, body in self.pages.items():
            if url.endswith(fragment):
                return url, body
        return None


def test_cascade_visits_localised_paths_and_collects_people():
    http = StubHttp({"/impressum": IMPRESSUM, "/team": TEAM})
    hits = resolve_people("acme.de", "DE", http)
    names = {h.name for h in hits}
    assert names == {"Anna Schmidt", "Peter Wolf"}


def test_cascade_stops_at_max_pages():
    http = StubHttp({})
    resolve_people("acme.de", "DE", http, max_pages=3)
    assert len(http.calls) == 3


def test_merge_prefers_the_richer_record_for_the_same_person():
    sparse = PersonHit(name="Anna Schmidt", role="CTO", strategy="team", source_url="u1")
    rich = PersonHit(
        name="Anna Schmidt", role="Geschäftsführer", email="a@acme.de", strategy="impressum", source_url="u2"
    )
    merged = merge_hits([sparse, rich])
    assert len(merged) == 1
    assert merged[0].email == "a@acme.de"
    # A target-role title beats a generic legal one — CTO is what the buyer wants.
    assert merged[0].role == "CTO"


def test_merge_keeps_distinct_people():
    merged = merge_hits([PersonHit(name="Anna Schmidt"), PersonHit(name="Peter Wolf")])
    assert len(merged) == 2


def test_merge_is_case_and_whitespace_insensitive():
    merged = merge_hits([PersonHit(name="Anna Schmidt"), PersonHit(name="anna  schmidt")])
    assert len(merged) == 1


def test_a_dead_site_yields_nothing_without_raising():
    assert resolve_people("dead.de", "DE", StubHttp({})) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_cascade.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.person.cascade'`

- [ ] **Step 3: Write the implementation**

```python
# src/leadgen/person/cascade.py
"""Fetch the pages that name people, run every strategy, merge the results.

Bounded on purpose: eight pages per company across thousands of companies is
the difference between a run that finishes tonight and one that does not.
Localised paths come first because they are the higher-yield ones.
"""

from __future__ import annotations

import logging
import re

from .hit import PersonHit
from .paths import candidate_paths
from .roles import classify_role
from .strategies import impressum, jsonld_person, team

log = logging.getLogger(__name__)

_WS_RX = re.compile(r"\s+")

# Order matters only for tie-breaking; every strategy runs on every page.
STRATEGIES = (jsonld_person, impressum, team)


def _key(name: str) -> str:
    return _WS_RX.sub(" ", (name or "").strip().lower())


def merge_hits(hits: list[PersonHit]) -> list[PersonHit]:
    """One record per person, taking the best value for each field."""
    merged: dict[str, PersonHit] = {}
    for hit in hits:
        key = _key(hit.name)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = PersonHit(**vars(hit))
            continue
        # Fill blanks from the newcomer.
        for attribute in ("email", "phone", "linkedin", "source_url"):
            if not getattr(existing, attribute) and getattr(hit, attribute):
                setattr(existing, attribute, getattr(hit, attribute))
        # A title the buyer asked for beats one they did not. "CTO" is more
        # useful on the row than "Geschäftsführer" even though both are true.
        incoming_is_target = classify_role(hit.role) != "other"
        existing_is_target = classify_role(existing.role) != "other"
        if hit.role and (not existing.role or (incoming_is_target and not existing_is_target)):
            existing.role = hit.role
    return list(merged.values())


def resolve_people(domain: str, country: str, http, *, max_pages: int = 8) -> list[PersonHit]:
    """Crawl a company's own site for named people. Never raises."""
    if not domain:
        return []
    hits: list[PersonHit] = []
    for path in candidate_paths(country)[:max_pages]:
        url = f"https://{domain}{path}"
        try:
            response = http.get(url)
        except Exception as exc:
            log.debug("cascade: %s failed: %s", url, exc)
            continue
        if not response:
            continue
        final_url, html = response
        if not html:
            continue
        for strategy in STRATEGIES:
            try:
                hits.extend(strategy.extract(html, final_url))
            except Exception as exc:
                log.debug("cascade: %s raised on %s: %s", strategy.__name__, final_url, exc)
    return merge_hits(hits)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_cascade.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/person/cascade.py tests/leadgen/test_cascade.py
git commit -m "leadgen: person cascade over localised company pages"
```

---

### Task 9: Lead assembly — turn a company plus its people into Leads

**Files:**
- Create: `src/leadgen/assemble.py`
- Test: `tests/leadgen/test_assemble.py`

**Interfaces:**
- Consumes: `leadgen.models.Lead`, `leadgen.person.hit.PersonHit`, `leadgen.person.roles.classify_role`, `leadgen.person.roles.split_name`, `leadgen.email.pattern.infer_pattern`, `leadgen.email.pattern.apply_pattern`, `leadgen.email.validate.validate`
- Produces: `CompanyContext` dataclass with `.name`, `.domain`, `.website`, `.country`, `.region`, `.city`, `.size_hint`, `.tech_stack`, `.seed_url`; `build_leads(company: CompanyContext, hits: list[PersonHit], *, smtp: bool = True) -> list[Lead]`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_assemble.py
"""Company + person hits -> Leads, with email resolution and provenance."""

from __future__ import annotations

import pytest

from leadgen.assemble import CompanyContext, build_leads
from leadgen.email import validate as V
from leadgen.person.hit import PersonHit


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    monkeypatch.setattr(V, "is_catch_all", lambda domain: False)


COMPANY = CompanyContext(
    name="Acme GmbH",
    domain="acme.de",
    website="https://acme.de",
    country="DE",
    city="Munich",
    seed_url="https://boards.greenhouse.io/acme/jobs/1",
)


def test_published_email_is_marked_published():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="anna.schmidt@acme.de", source_url="u")]
    leads = build_leads(COMPANY, hits)
    assert leads[0].email_status == "published"
    assert leads[0].person_email == "anna.schmidt@acme.de"


def test_pattern_is_inferred_from_an_anchor_and_applied_to_the_others():
    hits = [
        PersonHit(name="Anna Schmidt", role="CTO", email="anna.schmidt@acme.de", source_url="u1"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u2"),
    ]
    leads = {lead.person_name: lead for lead in build_leads(COMPANY, hits)}
    assert leads["Peter Wolf"].person_email == "peter.wolf@acme.de"
    assert leads["Peter Wolf"].email_status == "inferred_medium"


def test_two_anchors_give_high_confidence():
    hits = [
        PersonHit(name="Anna Schmidt", email="anna.schmidt@acme.de", role="CTO", source_url="u1"),
        PersonHit(name="Klaus Berg", email="klaus.berg@acme.de", role="CEO", source_url="u2"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u3"),
    ]
    leads = {lead.person_name: lead for lead in build_leads(COMPANY, hits)}
    assert leads["Peter Wolf"].email_status == "inferred_high"


def test_a_role_account_is_never_attached_to_a_person():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="info@acme.de", source_url="u")]
    leads = build_leads(COMPANY, hits)
    assert leads[0].person_email == "", "info@ must not become Anna's address"


def test_role_family_is_classified():
    hits = [
        PersonHit(name="Anna Schmidt", role="CTO", email="a.s@acme.de", source_url="u"),
        PersonHit(name="Peter Wolf", role="Head of HR", email="p.w@acme.de", source_url="u"),
    ]
    families = {lead.person_name: lead.person_role_family for lead in build_leads(COMPANY, hits)}
    assert families["Anna Schmidt"] == "tech_leadership"
    assert families["Peter Wolf"] == "hr"


def test_company_fields_are_copied_onto_every_lead():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="a@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.company_name == "Acme GmbH"
    assert lead.company_country == "DE"
    assert lead.company_city == "Munich"
    assert lead.source_seed_url == COMPANY.seed_url


def test_evidence_records_where_each_fact_came_from():
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="a@acme.de", source_url="https://acme.de/team")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.evidence["person_name"] == "https://acme.de/team"
    assert lead.evidence["person_email"] == "https://acme.de/team"


def test_inferred_email_evidence_points_at_the_anchor_not_a_page():
    hits = [
        PersonHit(name="Anna Schmidt", email="anna.schmidt@acme.de", role="CTO", source_url="u1"),
        PersonHit(name="Peter Wolf", role="Head of HR", source_url="u2"),
    ]
    wolf = next(lead for lead in build_leads(COMPANY, hits) if lead.person_name == "Peter Wolf")
    assert wolf.evidence["person_email"].startswith("inferred:")


def test_a_dead_domain_drops_the_email_but_keeps_the_person(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: False)
    hits = [PersonHit(name="Anna Schmidt", role="CTO", email="a@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.person_name == "Anna Schmidt"
    assert lead.person_email == ""


def test_names_are_split_for_downstream_use():
    hits = [PersonHit(name="Dr. Anna Schmidt", role="CTO", email="a@acme.de", source_url="u")]
    lead = build_leads(COMPANY, hits)[0]
    assert lead.person_first_name == "Anna"
    assert lead.person_last_name == "Schmidt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_assemble.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.assemble'`

- [ ] **Step 3: Write the implementation**

```python
# src/leadgen/assemble.py
"""Turn one company and its extracted people into Lead rows.

This is where the pattern anchor pays off: any person whose address was
published on the site anchors the domain's format, and every other named
person on that domain inherits a derived address with an honest confidence
label rather than being dropped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .email.pattern import apply_pattern, infer_pattern
from .email.validate import is_role_account, validate
from .models import Lead
from .person.hit import PersonHit
from .person.roles import classify_role, split_name

# validate() returns a transport-level verdict; these map it onto the
# provenance-level status the CSV carries.
_VERDICT_TO_STATUS = {
    "ok": "verified",
    "catch_all": "catch_all",
    "unknown": "unknown",
}


@dataclass
class CompanyContext:
    name: str = ""
    domain: str = ""
    website: str = ""
    country: str = ""
    region: str = ""
    city: str = ""
    size_hint: str = ""
    tech_stack: str = ""
    seed_url: str = ""
    extra_anchors: list[tuple[str, str]] = field(default_factory=list)


def build_leads(company: CompanyContext, hits: list[PersonHit], *, smtp: bool = True) -> list[Lead]:
    anchors: list[tuple[str, str]] = list(company.extra_anchors)
    for hit in hits:
        if hit.name and hit.email and not is_role_account(hit.email):
            anchors.append((hit.name, hit.email))

    pattern, pattern_confidence = infer_pattern(anchors)

    leads: list[Lead] = []
    for hit in hits:
        if not hit.name:
            continue
        first, last = split_name(hit.name)
        lead = Lead(
            person_name=hit.name,
            person_first_name=first,
            person_last_name=last,
            person_role=hit.role,
            person_role_family=classify_role(hit.role),
            person_linkedin=hit.linkedin,
            person_phone=hit.phone,
            company_name=company.name,
            company_domain=company.domain,
            company_website=company.website,
            company_country=company.country,
            company_region=company.region,
            company_city=company.city,
            company_size_hint=company.size_hint,
            tech_stack=company.tech_stack,
            source_seed_url=company.seed_url,
            source_person_url=hit.source_url,
            scraped_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        lead.set_evidence("person_name", hit.source_url)
        if hit.role:
            lead.set_evidence("person_role", hit.source_url)

        candidate, status, evidence_url = "", "", ""
        if hit.email and not is_role_account(hit.email):
            candidate, status, evidence_url = hit.email, "published", hit.source_url
        elif pattern and company.domain:
            derived = apply_pattern(pattern, first, last, company.domain)
            if derived:
                candidate = derived
                status = "inferred_high" if pattern_confidence == "high" else "inferred_medium"
                evidence_url = f"inferred:{pattern}"

        if candidate:
            verdict = validate(candidate, smtp=smtp)
            if verdict.status == "rejected":
                candidate, status = "", ""
            elif status == "published":
                # A published address that the server also confirms is still
                # "published" — that is the strongest provenance we have.
                if verdict.status in ("catch_all", "unknown"):
                    status = "published"
            else:
                status = _VERDICT_TO_STATUS.get(verdict.status, status)
                # Never let a probe *upgrade* an inference beyond what the
                # pattern evidence supports.
                if status == "verified" and pattern_confidence != "high":
                    status = "inferred_high"

        lead.person_email = candidate
        lead.email_status = status
        lead.email_confidence = pattern_confidence if status.startswith("inferred") else ""
        if candidate:
            lead.set_evidence("person_email", evidence_url)
            lead.set_evidence("source_email_url", evidence_url)
            lead.source_email_url = evidence_url
        leads.append(lead)

    return leads
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_assemble.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/assemble.py tests/leadgen/test_assemble.py
git commit -m "leadgen: assemble Leads with anchored email inference and provenance"
```

---

### Task 10: Job-board seed provider

**Files:**
- Create: `src/leadgen/seed/__init__.py`
- Create: `src/leadgen/seed/jobboard.py`
- Test: `tests/leadgen/test_seed.py`

**Interfaces:**
- Consumes: `job_scraper.adapters.get_adapter`, `job_scraper.config.TargetConfig`, `job_scraper.config.RunConfig`, `job_scraper.models.JobListing`, `job_scraper.universal.mine_contacts`, `leadgen.assemble.CompanyContext`, `leadgen.company.domain.resolve_domain`
- Produces: `IT_TITLE_RX: re.Pattern`, `looks_like_it_role(title: str) -> bool`, `seed_companies(targets: list[TargetConfig], run_config, http) -> list[CompanyContext]`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_seed.py
"""Job boards as a company seed, with the recruiter address as an anchor."""

from __future__ import annotations

from job_scraper.models import JobListing
from leadgen.seed.jobboard import companies_from_listings, looks_like_it_role


class StubHttp:
    def __init__(self, reachable=("acme.de",)):
        self.reachable = reachable

    def get(self, url, **kwargs):
        return (url, "<html></html>") if any(host in url for host in self.reachable) else None


def test_it_role_detection():
    for title in ["Backend Engineer", "DevOps Engineer", "Softwareentwickler", "Data Engineer",
                  "IT Administrator", "Développeur Full Stack", "QA Engineer"]:
        assert looks_like_it_role(title) is True, title
    for title in ["Barista", "Warehouse Picker", "Sales Representative", "Krankenpfleger"]:
        assert looks_like_it_role(title) is False, title


def test_listings_collapse_to_one_company():
    listings = [
        JobListing(title="Backend Engineer", company="Acme GmbH", job_url="https://acme.de/jobs/1"),
        JobListing(title="Frontend Engineer", company="Acme GmbH", job_url="https://acme.de/jobs/2"),
    ]
    companies = companies_from_listings(listings, StubHttp())
    assert len(companies) == 1
    assert companies[0].name == "Acme GmbH"


def test_non_it_listings_are_skipped():
    listings = [JobListing(title="Barista", company="Cafe", job_url="https://cafe.de/jobs/1")]
    assert companies_from_listings(listings, StubHttp(reachable=("cafe.de",))) == []


def test_recruiter_address_becomes_a_pattern_anchor():
    listing = JobListing(
        title="Backend Engineer",
        company="Acme GmbH",
        job_url="https://acme.de/jobs/1",
        recruiter_name="Anna Schmidt",
        recruiter_email="anna.schmidt@acme.de",
    )
    company = companies_from_listings([listing], StubHttp())[0]
    assert ("Anna Schmidt", "anna.schmidt@acme.de") in company.extra_anchors


def test_company_country_and_city_carry_through():
    listing = JobListing(
        title="DevOps Engineer",
        company="Acme GmbH",
        job_url="https://acme.de/jobs/1",
        country="DE",
        city="Munich",
    )
    company = companies_from_listings([listing], StubHttp())[0]
    assert company.country == "DE"
    assert company.city == "Munich"


def test_a_company_whose_domain_cannot_be_resolved_is_dropped():
    listing = JobListing(title="Backend Engineer", company="Ghost", job_url="https://ghost.xyz/jobs/1")
    assert companies_from_listings([listing], StubHttp(reachable=())) == []


def test_tech_stack_is_unioned_across_a_company_listings():
    listings = [
        JobListing(title="Backend Engineer", company="Acme", job_url="https://acme.de/1", tech_stack="python"),
        JobListing(title="SRE", company="Acme", job_url="https://acme.de/2", tech_stack="kubernetes"),
    ]
    company = companies_from_listings(listings, StubHttp())[0]
    assert "python" in company.tech_stack and "kubernetes" in company.tech_stack
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.seed'`

- [ ] **Step 3: Write the implementation**

```python
# src/leadgen/seed/__init__.py
"""Seed providers — sources that produce candidate companies."""
```

```python
# src/leadgen/seed/jobboard.py
"""Job boards as the company seed.

A company on an IT job board employs IT staff by construction, so the
targeting criterion needs no classifier. Better, the ad often names a
recruiter and gives their direct address, which becomes the anchor that
email-pattern inference needs.
"""

from __future__ import annotations

import logging
import re

from job_scraper.models import JobListing

from ..assemble import CompanyContext
from ..company.domain import resolve_domain

log = logging.getLogger(__name__)

# Deliberately broad: the brief is "any sector which employs IT", so a
# logistics firm hiring one sysadmin qualifies.
IT_TITLE_PATTERNS: list[str] = [
    r"software",
    r"developer",
    r"entwickler(?:in)?",
    r"d[ée]veloppeur(?:se)?",
    r"sviluppatore",
    r"desarrollador(?:a)?",
    r"programmer",
    r"programmier\w*",
    r"engineer(?:ing)?",
    r"ingenieur|ingénieur|ingegnere",
    r"devops|sre|site reliability",
    r"data (?:engineer|scientist|analyst)",
    r"machine learning|ml engineer",
    r"cloud|kubernetes|aws|azure",
    r"backend|back-end|frontend|front-end|fullstack|full-stack",
    r"\bit\b|informatik\w*|informatique|informatica",
    r"sysadmin|system(?:s)? admin\w*|administrator(?:in)?",
    r"database|datenbank|dba",
    r"security engineer|cyber ?security|it[- ]security",
    r"qa engineer|test engineer|quality assurance",
    r"architect(?:ure)?",
    r"technical lead|tech lead",
]
IT_TITLE_RX = re.compile(r"(?:" + "|".join(IT_TITLE_PATTERNS) + r")", re.I)


def looks_like_it_role(title: str) -> bool:
    return bool(title) and bool(IT_TITLE_RX.search(title))


def _company_key(listing: JobListing) -> str:
    return (listing.company or "").strip().lower()


def companies_from_listings(listings: list[JobListing], http) -> list[CompanyContext]:
    """Collapse listings to unique companies with resolved domains.

    One fetch per company for domain resolution, not one per listing — a
    company with forty open roles must not cost forty probes.
    """
    grouped: dict[str, list[JobListing]] = {}
    for listing in listings:
        if not looks_like_it_role(listing.title) or not _company_key(listing):
            continue
        grouped.setdefault(_company_key(listing), []).append(listing)

    companies: list[CompanyContext] = []
    for group in grouped.values():
        first = group[0]
        hints: list[str] = []
        for listing in group:
            hints.extend(
                url for url in (listing.company_url, listing.apply_url, listing.job_url) if url
            )
        domain = resolve_domain(first.company, hints, http)
        if not domain:
            log.debug("seed: no domain for %r, dropping", first.company)
            continue

        anchors: list[tuple[str, str]] = []
        tech: set[str] = set()
        for listing in group:
            if listing.recruiter_name and listing.recruiter_email:
                anchors.append((listing.recruiter_name, listing.recruiter_email))
            for token in (listing.tech_stack or "").split(","):
                if token.strip():
                    tech.add(token.strip())

        companies.append(
            CompanyContext(
                name=first.company,
                domain=domain,
                website=f"https://{domain}",
                country=next((listing.country for listing in group if listing.country), ""),
                region=next((listing.region for listing in group if listing.region), ""),
                city=next((listing.city for listing in group if listing.city), ""),
                tech_stack=", ".join(sorted(tech)),
                seed_url=first.job_url or first.apply_url,
                extra_anchors=anchors,
            )
        )
    return companies
```

**Note for the implementer:** `JobListing` may not have every attribute referenced above (`company_url`, `region`, `country`, `city`, `recruiter_name`, `recruiter_email`, `tech_stack`). Before writing this file, run `python -c "import sys; sys.path.insert(0,'src'); from job_scraper.models import JOB_FIELDS; print(JOB_FIELDS)"` and use `getattr(listing, name, "")` for any field that is absent rather than assuming it exists.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_seed.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add src/leadgen/seed/ tests/leadgen/test_seed.py
git commit -m "leadgen: job-board seed provider with recruiter-address anchors"
```

---

### Task 11: Pipeline, checkpointing and CLI

**Files:**
- Create: `src/leadgen/checkpoint.py`
- Create: `src/leadgen/pipeline.py`
- Create: `src/leadgen/cli.py`
- Create: `src/leadgen/export.py`
- Create: `configs/leads/eu-it.yaml`
- Test: `tests/leadgen/test_pipeline.py`

**Interfaces:**
- Consumes: everything above, plus `job_scraper.http.HttpClient`, `job_scraper.config.load_config`
- Produces: `Checkpoint` class with `.seen_company(domain) -> bool`, `.record_company(domain)`, `.save_leads(leads)`, `.all_leads() -> list[Lead]`; `run(config, *, target: int, smtp: bool, resume: bool) -> tuple[list[Lead], QuotaReport]`; `write_csv(leads, path) -> None`; `main(argv=None) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/leadgen/test_pipeline.py
"""End-to-end assembly and CSV output, no network."""

from __future__ import annotations

import csv

import pytest

from leadgen.checkpoint import Checkpoint
from leadgen.email import validate as V
from leadgen.export import write_csv
from leadgen.models import LEAD_CSV_COLUMNS, Lead


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(V, "has_mx", lambda domain: True)
    monkeypatch.setattr(V, "is_catch_all", lambda domain: False)


def test_write_csv_emits_the_schema_header(tmp_path):
    out = tmp_path / "leads.csv"
    write_csv([Lead(person_name="Jane Doe", person_email="j@acme.de")], out)
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert list(rows[0]) == LEAD_CSV_COLUMNS
    assert rows[0]["person_name"] == "Jane Doe"


def test_write_csv_is_atomic_on_failure(tmp_path):
    out = tmp_path / "leads.csv"
    out.write_text("previous,data\n", encoding="utf-8")

    class Exploding(Lead):
        def to_dict(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_csv([Exploding(person_name="x")], out)
    assert out.read_text(encoding="utf-8") == "previous,data\n"


def test_checkpoint_remembers_companies(tmp_path):
    checkpoint = Checkpoint(tmp_path / "run.sqlite")
    assert checkpoint.seen_company("acme.de") is False
    checkpoint.record_company("acme.de")
    assert checkpoint.seen_company("acme.de") is True


def test_checkpoint_survives_reopen(tmp_path):
    path = tmp_path / "run.sqlite"
    first = Checkpoint(path)
    first.record_company("acme.de")
    first.save_leads([Lead(person_name="Jane Doe", person_email="j@acme.de", company_domain="acme.de")])
    first.close()

    second = Checkpoint(path)
    assert second.seen_company("acme.de") is True
    assert len(second.all_leads()) == 1
    assert second.all_leads()[0].person_name == "Jane Doe"


def test_checkpoint_deduplicates_saved_leads(tmp_path):
    checkpoint = Checkpoint(tmp_path / "run.sqlite")
    lead = Lead(person_name="Jane Doe", person_email="j@acme.de", company_domain="acme.de")
    checkpoint.save_leads([lead])
    checkpoint.save_leads([lead])
    assert len(checkpoint.all_leads()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/leadgen/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'leadgen.checkpoint'`

- [ ] **Step 3: Write the implementation**

```python
# src/leadgen/export.py
"""CSV output. Atomic, so a crash never destroys a previous deliverable."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

from .models import LEAD_CSV_COLUMNS, Lead


def write_csv(leads: list[Lead], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=destination.parent,
        prefix=".leadgen-",
        suffix=".csv",
        newline="",
        # BOM so Excel and Google Sheets read the UTF-8 accents correctly on
        # import, which matters for a list full of European names.
        encoding="utf-8-sig",
    )
    try:
        with handle:
            writer = csv.DictWriter(handle, fieldnames=LEAD_CSV_COLUMNS)
            writer.writeheader()
            for lead in leads:
                writer.writerow(lead.to_dict())
        os.replace(handle.name, destination)
    except BaseException:
        with contextlib_suppress():
            os.unlink(handle.name)
        raise


class contextlib_suppress:
    """Local suppress so a cleanup failure never masks the real exception."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc_info) -> bool:
        return True
```

```python
# src/leadgen/checkpoint.py
"""SQLite run state so an interrupted run resumes instead of restarting.

A full run is hours long. Losing it to a dropped connection at hour three
is not acceptable, and this is also the foundation the scheduled version
will need for its cross-run ledger.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import LEAD_FIELDS, Lead

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (domain TEXT PRIMARY KEY, seen_at REAL DEFAULT (julianday('now')));
CREATE TABLE IF NOT EXISTS leads (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
"""


class Checkpoint:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def seen_company(self, domain: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM companies WHERE domain = ?", (domain,)).fetchone()
        return row is not None

    def record_company(self, domain: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO companies (domain) VALUES (?)", (domain,))
        self.conn.commit()

    def save_leads(self, leads: list[Lead]) -> None:
        rows = [
            (lead.fingerprint(), json.dumps({name: getattr(lead, name) for name in LEAD_FIELDS} | {"evidence": lead.evidence}))
            for lead in leads
        ]
        self.conn.executemany("INSERT OR REPLACE INTO leads (id, payload) VALUES (?, ?)", rows)
        self.conn.commit()

    def all_leads(self) -> list[Lead]:
        leads: list[Lead] = []
        for (payload,) in self.conn.execute("SELECT payload FROM leads"):
            data = json.loads(payload)
            evidence = data.pop("evidence", {})
            lead = Lead(**{name: data.get(name, "") for name in LEAD_FIELDS})
            lead.evidence = evidence
            leads.append(lead)
        return leads

    def close(self) -> None:
        self.conn.close()
```

```python
# src/leadgen/pipeline.py
"""Orchestrate: seed -> resolve people -> assemble -> checkpoint.

Company processing is embarrassingly parallel and network-bound, so it runs
in a thread pool. Each company is isolated: one dead site never aborts the
run, and every failure is counted so the funnel stays inspectable.
"""

from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from .assemble import CompanyContext, build_leads
from .checkpoint import Checkpoint
from .person.cascade import resolve_people

log = logging.getLogger(__name__)


def process_companies(
    companies: list[CompanyContext],
    http,
    checkpoint: Checkpoint,
    *,
    concurrency: int = 8,
    smtp: bool = True,
    max_pages: int = 8,
) -> Counter:
    """Resolve people for each company and persist the resulting leads."""
    funnel: Counter = Counter()

    def handle(company: CompanyContext) -> int:
        if checkpoint.seen_company(company.domain):
            funnel["already_done"] += 1
            return 0
        hits = resolve_people(company.domain, company.country, http, max_pages=max_pages)
        checkpoint.record_company(company.domain)
        if not hits:
            funnel["no_person_found"] += 1
            return 0
        leads = build_leads(company, hits, smtp=smtp)
        with_email = [lead for lead in leads if lead.person_email]
        funnel["people_found"] += len(leads)
        funnel["with_email"] += len(with_email)
        checkpoint.save_leads(leads)
        return len(with_email)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(handle, company): company for company in companies}
        for index, future in enumerate(as_completed(futures), 1):
            company = futures[future]
            try:
                future.result()
            except Exception as exc:
                funnel["company_error"] += 1
                log.warning("pipeline: %s failed: %s", company.domain, exc)
            if index % 50 == 0:
                log.info(
                    "pipeline: %d/%d companies · %d leads with an email",
                    index,
                    len(companies),
                    funnel["with_email"],
                )
    return funnel
```

```python
# src/leadgen/cli.py
"""Single command: seed, harvest, score, cut, write."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from job_scraper.config import load_config, resolve_config_path
from job_scraper.http import HttpClient

from .checkpoint import Checkpoint
from .export import write_csv
from .pipeline import process_companies
from .score.quota import select
from .seed.jobboard import companies_from_listings


def _collect_listings(config, http) -> list:
    from job_scraper.adapters import get_adapter

    listings = []
    for target in config.targets:
        try:
            found = get_adapter(target).fetch_jobs(target, config.run, http)
            logging.info("seed: %s -> %d listings", target.name, len(found))
            listings.extend(found)
        except Exception as exc:
            logging.warning("seed: %s failed: %s", target.name, exc)
    return listings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leadgen", description="Harvest a lead list.")
    parser.add_argument("--config", required=True, help="config name or path")
    parser.add_argument("--target", type=int, default=1000, help="exact number of rows wanted")
    parser.add_argument("--output", default="output/leads.csv")
    parser.add_argument("--checkpoint", default=".cache/leadgen.sqlite")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=8, help="pages fetched per company")
    parser.add_argument("--country-ceiling", type=float, default=0.25)
    parser.add_argument("--no-smtp", action="store_true", help="skip catch-all probing")
    parser.add_argument("--select-only", action="store_true", help="skip harvesting, re-cut the checkpoint")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = load_config(resolve_config_path(args.config))
    checkpoint = Checkpoint(args.checkpoint)

    if not args.select_only:
        with HttpClient(config.run) as http:
            listings = _collect_listings(config, http)
            logging.info("seed: %d listings total", len(listings))
            companies = companies_from_listings(listings, http)
            logging.info("seed: %d unique companies with a resolved domain", len(companies))
            funnel = process_companies(
                companies,
                http,
                checkpoint,
                concurrency=args.concurrency,
                smtp=not args.no_smtp,
                max_pages=args.max_pages,
            )
            logging.info("funnel: %s", dict(funnel))

    leads, report = select(
        checkpoint.all_leads(), target=args.target, country_ceiling=args.country_ceiling
    )
    write_csv(leads, args.output)
    checkpoint.close()

    print(report.summary())
    print(f"\nwrote {len(leads)} rows to {Path(args.output).resolve()}")
    if report.shortfall:
        print(f"\nSHORTFALL: {report.shortfall} rows short of {args.target}.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```yaml
# configs/leads/eu-it.yaml
# EU IT-sector lead seed. Every target is a job board; a company appearing
# on one employs IT staff by construction.
run:
  confirm_permission: true
  deep_scrape: true
  deep_concurrency: 8
  per_host_delay_seconds: 1.0
  timeout_seconds: 30
  max_retries: 2
  cache_enabled: true

targets:
  - name: greenhouse-eu
    url: https://boards.greenhouse.io
    adapter: greenhouse
  - name: lever-eu
    url: https://jobs.lever.co
    adapter: lever
  - name: personio-de
    url: https://www.personio.de
    adapter: personio
  - name: arbeitsagentur
    url: https://www.arbeitsagentur.de/jobsuche/
    adapter: arbeitsagentur
```

**Note for the implementer:** the four targets above are placeholders in shape — each adapter needs the specific board/company URL form it expects. Before running, check what each adapter's `fetch_jobs` actually requires by reading `src/job_scraper/adapters/<name>.py`, and fix the `url` values accordingly. A target whose URL is wrong logs a seed failure and contributes nothing, which is visible in the run output.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/leadgen/test_pipeline.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Run the whole suite and the linters**

Run:
```bash
python -m pytest tests/ -q
python -m ruff check src/ tests/
python -m black --check src/ tests/
```
Expected: all pass. Fix anything that does not before committing.

- [ ] **Step 6: Commit**

```bash
git add src/leadgen/ tests/leadgen/ configs/leads/
git commit -m "leadgen: pipeline, resumable checkpoint, atomic CSV and CLI"
```

---

### Task 12: Live smoke run and delivery

**Files:**
- Modify: `configs/leads/eu-it.yaml` (correct the target URLs found in Task 11)
- Create: `output/leads.csv` (the deliverable — not committed; `output/` is gitignored)

- [ ] **Step 1: Verify each adapter's target URL form**

Run:
```bash
python - <<'PY'
import sys; sys.path.insert(0, "src")
from job_scraper.adapters import ADAPTERS
for name, adapter in sorted(ADAPTERS.items()):
    print(f"{name:20} {type(adapter).__name__}")
PY
```
Read `src/job_scraper/adapters/<name>.py` for each adapter used in the config and confirm what `target.url` must look like. Correct `configs/leads/eu-it.yaml`.

- [ ] **Step 2: Tiny smoke run**

Run: `python -m leadgen.cli --config configs/leads/eu-it.yaml --target 10 --max-pages 3 --no-smtp -v`
Expected: a `output/leads.csv` with up to 10 rows. Inspect it by eye — do the names look like real people, do the roles look like HR or engineering leadership, do the domains match the companies?

- [ ] **Step 3: Fix what the smoke run reveals**

The funnel line in the output shows where leads are lost: `no_person_found`, `with_email`, `company_error`. If `no_person_found` dominates, the path list or the team-page parser needs work on the sites actually being hit. Add a regression test for each real failure found before fixing it.

- [ ] **Step 4: Full run**

Run: `python -m leadgen.cli --config configs/leads/eu-it.yaml --target 1000 --concurrency 8 -v`
Expected: runs for 1–4 hours, resumable via the checkpoint if interrupted (re-running skips companies already processed).

- [ ] **Step 5: Verify the deliverable**

Run:
```bash
python - <<'PY'
import csv, collections
rows = list(csv.DictReader(open("output/leads.csv", encoding="utf-8-sig")))
print("rows:", len(rows))
print("unique emails:", len({r["person_email"].lower() for r in rows}))
print("missing email:", sum(1 for r in rows if not r["person_email"]))
print("missing name:", sum(1 for r in rows if not r["person_name"]))
print("status:", collections.Counter(r["email_status"] for r in rows).most_common())
print("role family:", collections.Counter(r["person_role_family"] for r in rows).most_common())
print("countries:", collections.Counter(r["company_country"] for r in rows).most_common(10))
PY
```
Expected: row count equals the target (or the reported shortfall), zero missing emails, zero missing names, no single country above 25%.

- [ ] **Step 6: Commit the config fixes**

```bash
git add configs/leads/eu-it.yaml
git commit -m "leadgen: correct board target URLs after the smoke run"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Lead data model, evidence | 1 |
| Role classification | 2 |
| Email pattern inference | 3 |
| Validation ladder, catch-all, role accounts | 4 |
| Completeness scoring, quota cut, country ceiling, shortfall | 5 |
| Person cascade strategies (impressum, jsonld, team) | 6 |
| Domain resolution, ATS rejection | 7 |
| Cascade orchestration, multilingual paths | 8 |
| Anchored inference, provenance | 9 |
| Job-board seed | 10 |
| Checkpoint, CSV, CLI, config | 11 |
| Live run, delivery verification | 12 |

**Deliberately deferred from the spec, with reasons:** sitemap-mining and press-attribution strategies (Tasks 6/8 cover the three highest-yield sources; these two are additive and can be added without touching any interface), the suppression-list hook (empty for this run — a `--suppress FILE` flag filtering on domain and email is a ten-line addition to `cli.py` when a first objection arrives), and PDF/Wayback recovery (already scoped out in the spec).

**Type consistency:** `PersonHit` fields are identical across Tasks 6, 8, 9, 10. `CompanyContext` is defined once in Task 9 and imported by Task 10. `validate()` returns `EmailVerdict` with `.status`/`.reason` in Task 4 and is consumed with those names in Task 9. `select()` returns `(list[Lead], QuotaReport)` in Task 5 and is unpacked that way in Task 11. `EMAIL_STATUS_ORDER` in Task 1 matches the keys of `EMAIL_STATUS_WEIGHTS` in Task 5.

**Known risk:** Task 10 assumes `JobListing` attribute names that must be verified against `JOB_FIELDS` first — the task says so explicitly and prescribes `getattr(..., "")`. Task 11's config target URLs are shape-only and Task 12 Step 1 corrects them.
