"""Universal extractor — works on ANY job page without Schema.org markup.

When extract.py finds no JSON-LD JobPosting, this module kicks in. It
combines:
  1. Title detection (og:title > <h1> > <title>)
  2. Company detection (og:site_name > meta[author] > .company class > footer)
  3. Location detection (postal codes + country-specific city dictionary + Schema-style 'address' classes)
  4. Description extraction (largest text block in <main>/<article>/<body>)
  5. JD section parsing (delegated to extract.py's _parse_jd_sections)
  6. HR contact miner: phone (international + NO/DE/CH/EU regex), email,
     recruiter name from "Kontakt"/"Contact" sections
  7. Apply URL: ATS host match, or "Apply" text anchor

It also exposes a `cluster_anchors` function: given a search/listing page,
group anchors by URL pattern and return the largest cluster — these are
your job detail URLs, regardless of site structure.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .models import JobListing


# ---------------------------------------------------------------------------
# Universal HR contact mining
# ---------------------------------------------------------------------------

# International phone formats — strict enough to avoid false positives.
# E.164: +<country code 1-3 digits> followed by 6-13 more digits with optional separators.
PHONE_RX = re.compile(
    r"(?:\+(?:\d[\s\-\.\(\)]?){6,15}\d|"      # international with +country code
    r"\b(?:0\d{1,3}[\s\-\.\(\)/]?){2,5}\d{2,4}\b)"  # local with leading 0
)
# Norway-specific tighter regex (used for de-noising)
NO_PHONE_RX = re.compile(r"(?:\+47[\s\-]?)?\d{2}[\s\-]?\d{2}[\s\-]?\d{2}[\s\-]?\d{2}\b")
EMAIL_RX = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\.\-]+\.[A-Za-z]{2,}\b")

CONTACT_KEYWORDS = re.compile(
    r"\b(kontakt(?:person)?|contact(?:\s+person)?|kontaktperson|"
    r"for\s+spørsmål|ved\s+spørsmål|spørsmål\s+(?:om|kan\s+rettes\s+til)|"
    r"kontaktperson(?:er)?|HR-kontakt|recruiter|hiring\s+manager|"
    r"questions\s+(?:about|to)|reach\s+out\s+to)\b",
    re.I,
)

NAME_RX = re.compile(
    r"\b([A-ZÆØÅÄÖÜ][a-zæøåäöüß'\-]{2,}(?:[\s\-][A-ZÆØÅÄÖÜ][a-zæøåäöüß'\-]{2,}){1,3})\b"
)

# Hosts/domains we don't trust for "company email"
JUNK_EMAIL_HOSTS = re.compile(
    r"@(sentry\.io|noreply|no-reply|example\.com|donotreply|mailer-daemon|"
    r"localhost|test\.|dev\.|finn\.no|nav\.no|jobsense\.io|jobbnorge\.no)",
    re.I,
)

ATS_HOSTS = re.compile(
    r"(refline\.ch|smartrecruiters\.com|greenhouse\.io|lever\.co|workable\.com|"
    r"workday|myworkdayjobs\.com|ashbyhq\.com|teamtailor\.com|recruitee\.com|"
    r"personio\.|bamboohr\.com|jobvite\.com|webcruiter\.no|recman\.no|jobnow\.no|"
    r"applybyte\.com|jobylon\.com|talentech\.com|easycruit\.com)",
    re.I,
)


def mine_contacts(html: str, *, country_hint: Optional[str] = None) -> Dict[str, str]:
    """Pull every recruiter / HR / applicant contact field we can find.

    Returns a dict that may include:
        recruiter_name, recruiter_email, recruiter_phone,
        application_email, application_phone, contact_section_text
    """
    soup = BeautifulSoup(html, "lxml")
    body_text = soup.get_text(" ", strip=True)
    out: Dict[str, str] = {}

    # Email: all valid, drop junk hosts
    emails: List[str] = []
    for em in EMAIL_RX.findall(html):
        if JUNK_EMAIL_HOSTS.search(em):
            continue
        if em.lower() not in (e.lower() for e in emails):
            emails.append(em)
    if emails:
        # Prefer recruiter-flavored emails
        recruiter = next(
            (e for e in emails if re.search(r"(jobs?|career|talent|recruit|hr|hiring|people|personal)", e, re.I)),
            "",
        )
        if recruiter:
            out["recruiter_email"] = recruiter
        out["application_email"] = recruiter or emails[0]

    # Phone: rely on country regex first, fall back to international
    phones: List[str] = []
    if country_hint == "NO":
        phones = [_normalize_phone(p) for p in NO_PHONE_RX.findall(body_text)]
    if not phones:
        phones = [_normalize_phone(p) for p in PHONE_RX.findall(body_text)]
    phones = [p for p in phones if _valid_phone(p)]
    phones = _uniq_preserve(phones)
    if phones:
        out["application_phone"] = phones[0]

    # Contact section
    section = _find_contact_section(soup)
    if section:
        out["contact_section_text"] = section[:600]
        # NAV often gives us a contact section that ALREADY starts with the
        # person's name (because the heading "Kontaktperson" sits OUTSIDE the
        # block). Two patterns:
        #   (a) "Kontaktperson for stillingen <Name> <Role>"
        #   (b) Bare:                       "<Name> <Role> +47 <phone>"
        m = re.search(r"Kontaktperson(?:\s+for\s+stillingen)?\s+([^\d+@]{5,120}?)(?=\+?\d{2}\s?\d{2}|@|telefon|email|e-post|$)", section, re.I)
        if m:
            head = m.group(1).strip()
        else:
            # Try to parse the section directly — strip leading whitespace + handle
            # case where section begins with the person name.
            head = section
        name, role = _split_name_role(head.strip())
        if name and len(name.split()) >= 2 and not re.search(r"(personvern|cookies|tilgjengeligh)", name, re.I):
            out["recruiter_name"] = name
            if role and len(role) >= 3:
                out["recruiter_title"] = role
        # finn.no & some Norwegian boards use the literal label "Stillingstittel" as
        # a separator: "Kontaktperson : <NAME> Stillingstittel : <ROLE>"
        # Capture the name when this pattern appears.
        for m in re.finditer(r"Kontaktperson\s*:?\s*([^:]{3,60}?)\s+Stillingstittel", section, re.I):
            cand = m.group(1).strip()
            if 2 <= len(cand.split()) <= 5 and not re.search(r"^(Send|Mobil|Telefon|Epost|E[-\s]?post)\b", cand, re.I):
                out["recruiter_name"] = cand
                # If the role/title comes right after, capture it too
                m2 = re.search(rf"Stillingstittel\s*:?\s*([^:]{{3,80}}?)(?:\s+Mobil|\s+Telefon|\s+E[-\s]?post|\s+Send|$)", section[m.end():], re.I)
                if m2:
                    out["recruiter_title"] = m2.group(1).strip()
                break
        # Also try generic "Kontakt:" prefix on EN sites (works without 'Stillingstittel')
        if "recruiter_name" not in out:
            m = re.search(r"(?:Kontaktperson|Contact(?:\s+person)?|Kontakt)\s*:?\s*([A-ZÆØÅÄÖÜ][A-Za-zæøåäöüß'\-]+(?:\s+[A-ZÆØÅÄÖÜ][A-Za-zæøåäöüß'\-]+){1,3})", section)
            if m:
                out["recruiter_name"] = m.group(1).strip()
        # Look for a real person name. Reject:
        #  - common Norwegian section labels (Stillingstittel, Telefonnummer, Mobilnummer, ...)
        #  - company suffix words (Group/AS/ASA/AB/GmbH/...)
        #  - all-uppercase words (company names tend to scream)
        ns = NAME_RX.findall(section)
        # No trailing \b — Norwegian labels suffix (Stillingstittel, Telefonnummer, Mobilnummer)
        bad_label_rx = re.compile(
            r"^(Stilling|Telefon|Mobil|Epost|E[\s\-]?post|Kontakt|Adresse|Søknad|"
            r"Frist|Antall|Søk|Lønn|Søker|Arbeidsgiver|Bedrift|Firma|"
            r"Telephone|Phone|Email|Address|Position|Title|Company|Employer|"
            r"Contact|Apply|Submit|Reach|Tittel|Nummer|"
            # Site-nav junk (karrierestart, NAV)
            r"Partnere|Annonsere|Nyheter|Profil|Karriere|Studier|Skoler|"
            r"Relaterte|Bransje|Yrker|Fagomr|Hjelp|Tilbake|Logg|Logg.inn|"
            r"Personvern|Cookies|Vilk|Brukervilk|Tilgjengelig|"
            # English nav
            r"Login|Register|About|Help|Terms|Privacy|Cookie|Career|Profile|"
            r"News|Industry|Partner|Advertise|Related)",
            re.I,
        )
        company_suffix_rx = re.compile(r"(group|holding|inc|ltd|gmbh|as|asa|ab|s\.a\.|nv|bv|llc|llp|plc|ag|sa|gbr)\b", re.I)
        if "recruiter_name" not in out:  # don't overwrite earlier NAV/finn extraction
            for cand in ns:
                parts = cand.split()
                if len(parts) < 2:
                    continue
                if any(bad_label_rx.match(p) for p in parts):
                    continue
                if company_suffix_rx.search(cand):
                    continue
                out["recruiter_name"] = cand
                break
        # Section-local phone/email
        sec_phones = [
            _normalize_phone(p) for p in (NO_PHONE_RX.findall(section) if country_hint == "NO" else PHONE_RX.findall(section))
        ]
        sec_phones = [p for p in sec_phones if _valid_phone(p)]
        if sec_phones:
            out["recruiter_phone"] = sec_phones[0]
        sec_emails = [e for e in EMAIL_RX.findall(section) if not JUNK_EMAIL_HOSTS.search(e)]
        if sec_emails:
            out["recruiter_email"] = sec_emails[0]
    return out


def _find_contact_section(soup: BeautifulSoup) -> str:
    """Locate a 'Contact / Kontakt' section in the body and return its text."""
    # 1. Look for a heading whose text matches CONTACT_KEYWORDS, collect next siblings.
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "strong", "b"]):
        text = h.get_text(" ", strip=True)
        if not text or len(text) > 120:
            continue
        if CONTACT_KEYWORDS.search(text):
            buf: List[str] = []
            for sib in h.next_siblings:
                if not getattr(sib, "name", None):
                    if isinstance(sib, str) and sib.strip():
                        buf.append(sib.strip())
                    continue
                if sib.name and sib.name.lower() in {"h1", "h2", "h3", "h4", "h5"}:
                    break
                t = sib.get_text(" ", strip=True)
                if t:
                    buf.append(t)
                if sum(len(x) for x in buf) > 2000:
                    break
            joined = " ".join(buf)
            if joined.strip():
                return joined
    # 2. Fallback: nearest 600 chars of text that contains both a phone-like and email-like token.
    text = soup.get_text(" ", strip=True)
    for m in CONTACT_KEYWORDS.finditer(text):
        start = max(0, m.start() - 100)
        end = min(len(text), m.end() + 800)
        return text[start:end]
    return ""


ROLE_HINT_RX = re.compile(
    r"\b(leder|sjef|direktør|direktor|seksjonssjef|avdelingsleder|"
    r"konsulent|spesialist|senioringeniør|ingeniør|rådgiver|"
    r"manager|partner|head|chief|owner|coordinator|recruiter|"
    r"talent|hr|sykepleier|lege|teamleder|prosjektleder|sjefen|"
    r"medarbeider|representant|developer|engineer|architect|"
    r"founder|cto|ceo|cmo|cfo|coo|cio)",
    re.I,
)


def _split_name_role(text: str) -> Tuple[str, str]:
    """Given a contact line like 'Magnus Millenvik Leder digitalisering...',
    split into (name, role). Uses Norwegian role keywords + capitalization
    rules. Returns ('', '') if no name detected.
    """
    text = text.strip()
    if not text:
        return "", ""
    m = re.match(r"^([A-ZÆØÅ][a-zæøå'\-]+(?:\s+[A-ZÆØÅ][a-zæøå'\-]+)*)(?:\s+(.*))?$", text)
    if not m:
        # Names with hyphens (Ann-Helen Holtet)
        m = re.match(r"^([A-ZÆØÅ][a-zæøå'\-]+(?:[\s\-][A-ZÆØÅa-zæøå'\-]+)*)(?:\s+(.*))?$", text)
        if not m:
            return "", ""
    chain = m.group(1).split()
    rest = m.group(2) or ""
    cut = len(chain)
    for i, tok in enumerate(chain):
        if ROLE_HINT_RX.match(tok):
            cut = i
            break
    name = " ".join(chain[:cut]) if cut > 0 else " ".join(chain[:3])
    role_chunks = chain[cut:] + rest.split()
    role_text = " ".join(role_chunks)
    # Truncate role at first phone-like token or trailing icon labels.
    role_m = re.match(r"^(.+?)\s*(?:\+?\d[\d\s\-]{6,}|Kopier|telefon:|tel:|mob:|email|e-post|@)", role_text, re.I)
    role = (role_m.group(1) if role_m else role_text).strip(" ,;:")
    if len(role) > 120:
        role = role[:120]
    return name, role


def _normalize_phone(raw: str) -> str:
    # Strip everything except digits and leading +
    raw = raw.strip()
    keep = ""
    for ch in raw:
        if ch == "+" and not keep:
            keep += "+"
        elif ch.isdigit():
            keep += ch
    return keep


_DATE_LIKE_RX = re.compile(r"^\d{1,4}[\./\-]\d{1,4}[\./\-]\d{2,4}$")


def _valid_phone(p: str) -> bool:
    p = p.strip()
    digits = re.sub(r"\D", "", p)
    if len(digits) < 8 or len(digits) > 15:
        return False
    if digits == digits[0] * len(digits):
        return False
    if digits in ("1234567", "12345678", "123456789", "12345678901"):
        return False
    # Pure digit blob — likely ID
    if re.fullmatch(r"\d{9,12}", p):
        return False
    # Date string
    if _DATE_LIKE_RX.match(p):
        return False
    return True


def _uniq_preserve(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


# ---------------------------------------------------------------------------
# Universal title / company / location / description extraction
# ---------------------------------------------------------------------------

GENERIC_TITLE_SUFFIX = re.compile(
    r"\s*[\|\-–—]\s*(FINN\.no|NAV|arbeidsplassen|LinkedIn|Indeed|Glassdoor|Monster|"
    r"jobs\.ch|Stepstone|Workday|Jobbnorge|Greenhouse|Lever|Workable).*$",
    re.I,
)


def universal_extract(html: str, page_url: str, *, country_hint: Optional[str] = None) -> Optional[JobListing]:
    """Extract a JobListing from a job-detail page without relying on JSON-LD.

    Always returns a listing if title is detectable (heuristic check); returns
    None if the page clearly isn't a job posting.
    """
    soup = BeautifulSoup(html, "lxml")
    if not _looks_like_job_page(soup, page_url):
        return None

    j = JobListing()
    parsed = urlparse(page_url)
    j.source_domain = parsed.netloc

    # Title
    og_t = _meta(soup, "og:title")
    if og_t:
        j.title = GENERIC_TITLE_SUFFIX.sub("", og_t).strip()
    if not j.title:
        h1 = soup.find("h1")
        if h1:
            j.title = h1.get_text(" ", strip=True)
    if not j.title:
        t = soup.find("title")
        if t:
            j.title = GENERIC_TITLE_SUFFIX.sub("", t.get_text(" ", strip=True)).strip()

    # Description (largest text block in main/article)
    desc_node = _main_content_node(soup)
    if desc_node:
        j.description = _normalize(desc_node.get_text(" ", strip=True))[:15000]

    # Company
    org = _meta(soup, "og:site_name")
    if org and parsed.netloc and org.lower() not in parsed.netloc:
        # Often og:site_name = the platform (finn.no, NAV). Filter that.
        if not re.search(r"(finn\.no|nav|arbeidsplassen|jobbnorge|linkedin|indeed|stepstone)", org, re.I):
            j.company = org

    # Try .company class / itemprop="hiringOrganization"
    if not j.company:
        for sel in ("[itemprop='hiringOrganization']", "[itemprop='name']", ".company-name", "[class*='company']", "[class*='employer']"):
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t and len(t) < 100:
                    j.company = t
                    break

    # Location (postal-code heuristic + class match)
    for sel in ("[itemprop='addressLocality']", "[class*='location']", "address"):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if t and len(t) < 200:
                j.location = j.location or t
                break
    if country_hint:
        j.country = j.country or {"NO": "Norway", "DE": "Germany", "CH": "Switzerland", "SE": "Sweden", "DK": "Denmark", "FI": "Finland"}.get(country_hint, "")

    # HR contacts
    contacts = mine_contacts(html, country_hint=country_hint)
    for k, v in contacts.items():
        if k == "contact_section_text":
            j.set_extra("contact_section", v)
            continue
        if v and not getattr(j, k, ""):
            setattr(j, k, v)

    # Apply URL — ATS host preferred
    apply_url = ""
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        try:
            abs_url = urljoin(page_url, href)
        except Exception:
            continue
        if ATS_HOSTS.search(abs_url):
            apply_url = abs_url
            break
        text = a.get_text(" ", strip=True).lower()
        if text in ("apply", "apply now", "søk stillingen", "søk her", "søk", "bewerben", "jetzt bewerben", "postuler", "candidater"):
            if not apply_url:
                apply_url = abs_url
    if apply_url:
        j.apply_url = apply_url
    j.apply_url = j.apply_url or page_url
    j.job_url = j.job_url or page_url

    # ---- Site-specific selectors (improve coverage where universal heuristics miss) ----
    if "arbeidsplassen.nav.no" in parsed.netloc:
        _nav_no_specific(soup, j)
    elif "karrierestart.no" in parsed.netloc:
        _karrierestart_specific(soup, j)

    # Section parsing — delegate to extract.py's parser (already handles EN/DE/FR/IT/NO)
    from .extract import _parse_jd_sections
    if j.description:
        sects = _parse_jd_sections(str(desc_node) if desc_node else html)
        for k, v in sects.items():
            if not v:
                continue
            if k == "responsibilities" and not j.responsibilities:
                j.responsibilities = v
            elif k == "requirements" and not j.requirements:
                j.requirements = v
                if not j.qualifications:
                    j.qualifications = v
            elif k == "benefits" and not j.benefits:
                j.benefits = v
            elif k == "recruiter_name" and not j.recruiter_name:
                j.recruiter_name = v
            elif k == "recruiter_phone" and not j.recruiter_phone:
                j.recruiter_phone = v
            elif k == "recruiter_email" and not j.recruiter_email:
                j.recruiter_email = v

    if not j.seniority and j.title:
        from .extract import SENIORITY
        for name, rx in SENIORITY:
            if rx.search(j.title):
                j.seniority = name
                break
    return j


def _nav_no_specific(soup: BeautifulSoup, j: JobListing) -> None:
    """Pull NAV-specific labelled fields. NAV uses proper <dl>/<dt>/<dd>
    structure on detail pages — that's the primary path. We also fall back to
    text-node label matching for label/value pairs outside <dl>.
    """
    # --- Strategy 1: structured <dl>/<dt>/<dd> pairs (most reliable) ---
    DL_LABEL_MAP = {
        "Stillingstittel": "_role_title",            # role title — different from job title
        "Type ansettelse": "employment_type",
        "Arbeidstid": "_work_hours",
        "Antall stillinger": "_skip",
        "Stillingsbrøk": "_employment_pct",
        "Sektor": "company_industry",
        "Bransje": "company_industry",
        "Stillingsfunksjon": "department",
        "Yrke": "department",
        "Fagområde": "department",
        "Arbeidsspråk": "language",
        "Nettsted": "company_website",
        "Stillingsnummer": "external_id",
        "Sist endret": "posted_date",
        "Hentet fra": "_ats_source",
        "Referanse": "requisition_id",
        "Søknadsfrist": "valid_through",
        "Frist": "valid_through",
        "Tiltreder": "start_date",
        "Oppstart": "start_date",
        "Reisemengde": "travel_required",
    }
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        if not dts or not dds:
            continue
        for dt, dd in zip(dts, dds):
            label = _normalize(dt.get_text(" ", strip=True))
            value = _normalize(dd.get_text(" ", strip=True))
            if not label or not value:
                continue
            field = DL_LABEL_MAP.get(label)
            if not field or field == "_skip":
                # Store any non-mapped label in extras
                if value and len(value) < 500:
                    j.set_extra(f"nav_{label.lower().replace(' ', '_')}", value)
                continue
            if field.startswith("_"):
                # internal — stash in extras
                j.set_extra(field.lstrip("_"), value)
                continue
            cur = getattr(j, field, "")
            if not cur:
                setattr(j, field, value)

    # --- Strategy 2: <h3> heading + next sibling div (NAV sometimes uses this) ---
    for h3 in soup.find_all(["h2", "h3"]):
        label = _normalize(h3.get_text(" ", strip=True))
        if not label or label not in DL_LABEL_MAP:
            continue
        # Walk forward to find a small text block
        for sib in h3.next_siblings:
            if not getattr(sib, "name", None):
                continue
            txt = _normalize(sib.get_text(" ", strip=True))
            if 2 < len(txt) < 200 and not txt.startswith(("Stillingstittel", "Type ansettelse")):
                field = DL_LABEL_MAP[label]
                if field.startswith("_") or field == "_skip":
                    continue
                cur = getattr(j, field, "")
                if not cur:
                    setattr(j, field, txt)
                break


    # Sibling-matcher for header-style labels (Arbeidsgiver, Sted) that NAV
    # renders as a heading text node followed by SVG + nested content blocks.
    # WHITE-LIST only — to avoid the "Del annonsen" leak.
    header_label_map = {
        "Arbeidsgiver": "company",
        "Sted": "location",
    }
    button_words_rx = re.compile(
        r"\b(Del annonsen|Lagre|Følg|Kopier|Send|Søk her|Vis kart|Tilbake|Neste|"
        r"Forrige|Last ned|Skriv ut|Logg inn)\b", re.I,
    )
    for label, field in header_label_map.items():
        if getattr(j, field, ""):
            continue
        rx = re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)
        for el in soup.find_all(string=rx):
            if not el or not el.parent:
                continue
            ancestor = el.parent
            value = None
            for _ in range(4):
                for sib in ancestor.find_next_siblings():
                    if not getattr(sib, "get_text", None):
                        continue
                    txt = re.sub(r"\s+", " ", sib.get_text(" ", strip=True)).strip()
                    if not txt or len(txt) > 250:
                        continue
                    if button_words_rx.search(txt):
                        continue
                    if re.search(r"\b(Stillingstittel|Stillingsnummer|Søknadsfrist|"
                                 r"Kontaktperson|Sist endret|Referansenr|Antall stillinger|"
                                 r"Type ansettelse|Arbeidstid|Sektor|Bransje|Nettsted|"
                                 r"Stillingsfunksjon)\b", txt, re.I):
                        continue
                    value = txt
                    break
                if value:
                    setattr(j, field, value)
                    break
                if ancestor.parent is None:
                    break
                ancestor = ancestor.parent
            break

    # Mine body text for `Label: value` inline pairs NAV uses outside <dl>.
    body_txt = soup.get_text(" ", strip=True)

    # Stop words: another label OR a long word break OR end-of-line
    STOP_LABEL_RX = (
        r"Stillingstype|Heltid|Deltid|Sektor|Bransje|Arbeidsspr(å|a)k|Antall\s+stillinger|"
        r"Stillingsbr(ø|o)k|Publisert|S(ø|o)knadsfrist|Frist|Stillingsprosent|Ansettelsesform|"
        r"Tiltreder|Oppstart|Sted|Beliggenhet|Reisemengde|Hjemmekontor|Kontaktperson|"
        r"Arbeidssted|Arbeidstid|Arbeidsgiver|Stillingsfunksjon|Yrke|Fagomr(å|a)de|"
        r"Referansenr|Referanse|Sektor|Stilling$"
    )
    # Each entry: (label, field, value_pattern). Value pattern guarantees we
    # only accept plausible content (date / pct / role / etc.) — prevents the
    # regex from grabbing the next label's text or button labels like "Del annonsen".
    fallback_specs = [
        ("Søknadsfrist", "valid_through", r"(\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2}|snarest|løpende|fortløpende|umiddelbart)"),
        ("Frist", "valid_through", r"(\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})"),
        ("Publisert", "posted_date", r"(\d{1,2}\.\s*\w+\s*\d{4}|\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})"),
        ("Stillingsprosent", "employment_type", r"(\d{1,3}\s?%\s*(?:fast|midlertidig|vikariat)?[\w\s]{0,30})"),
        ("Ansettelsesform", "employment_type", r"([A-ZÆØÅa-zæøå][^,\n]{2,50})"),
        ("Tiltreder", "start_date", r"([A-ZÆØÅa-zæøå0-9][^\n]{2,50})"),
        ("Oppstart", "start_date", r"([A-ZÆØÅa-zæøå0-9][^\n]{2,50})"),
        ("Arbeidsspråk", "language", r"([A-ZÆØÅa-zæøå][^,\n]{2,50})"),
        ("Stillingsfunksjon", "department", r"([A-ZÆØÅa-zæøå][^\n]{3,80})"),
        ("Yrke", "department", r"([A-ZÆØÅa-zæøå][^\n]{3,80})"),
        ("Fagområde", "department", r"([A-ZÆØÅa-zæøå][^\n]{3,80})"),
        ("Reisemengde", "travel_required", r"([A-ZÆØÅa-zæøå0-9][^\n]{2,80})"),
        ("Referansenr\\.?", "requisition_id", r"([A-Za-z0-9_\-]{3,40})"),
    ]
    for label, field, vp in fallback_specs:
        if getattr(j, field, ""):
            continue
        m = re.search(
            rf"\b{label}\b\s*:?\s*{vp}",
            body_txt,
            re.I,
        )
        if m:
            v = m.group(1).strip(" \t:-—,;")
            if v and len(v) < 100 and v.lower() not in ("none", "null", "n/a"):
                # Reject if the captured value is itself a stop-label
                if not re.search(rf"^(?:{STOP_LABEL_RX})$", v, re.I):
                    setattr(j, field, v)

    # NAV's location string is "<street>, <postal> <city>". Parse for real city.
    if j.location and not j.city:
        # Take last token that starts with 4 digits followed by a city name
        m = re.search(r"\b\d{4}\s+([A-ZÆØÅa-zæøåäöü\-]+)", j.location)
        if m:
            j.city = m.group(1)
        else:
            j.city = j.location.split(",")[-1].strip()
    # Postal code from same string
    if j.location and not j.postal_code:
        m = re.search(r"\b(\d{4})\b", j.location)
        if m:
            j.postal_code = m.group(1)


def _karrierestart_specific(soup: BeautifulSoup, j: JobListing) -> None:
    """Pull karrierestart.no-specific labelled fields. Page uses
    <div class="fact-card-title">Label</div><div class="fact-card-content">Value</div>
    pairs inside .fact-card containers. Labels are Norwegian.
    """
    LABEL_MAP = {
        "Stillingstype": "employment_type",
        "Arbeidssted": "location",
        "Sted": "location",
        "Bransje": "company_industry",
        "Antall stillinger": "_skip",
        "Tiltredelse": "start_date",
        "Tiltreder": "start_date",
        "Oppstart": "start_date",
        "Søknadsfrist": "valid_through",
        "Frist": "valid_through",
        "Publisert": "posted_date",
        "Stillingsfunksjon": "department",
        "Sektor": "company_industry",
        "Yrke": "department",
        "Heltid/Deltid": "employment_type",
        "Stillingsbrøk": "_employment_pct",
        "Arbeidsspråk": "language",
        "Hjemmekontor": "remote_type",
        "Reisemengde": "travel_required",
    }
    for card in soup.select(".fact-card, .fact-grid > div"):
        title_el = card.select_one(".fact-card-title")
        content_el = card.select_one(".fact-card-content")
        if not title_el or not content_el:
            continue
        label = _normalize(title_el.get_text(" ", strip=True))
        value = _normalize(content_el.get_text(" ", strip=True))
        if not label or not value or len(value) > 200:
            continue
        field = LABEL_MAP.get(label)
        if field is None:
            # Stash unmapped labels in extras for transparency
            j.set_extra(f"karrierestart_{label.lower().replace(' ', '_')}", value)
            continue
        if field == "_skip":
            continue
        if field.startswith("_"):
            j.set_extra(field.lstrip("_"), value)
            continue
        if not getattr(j, field, ""):
            setattr(j, field, value)

    # Company name — KS strategies, ranked by reliability:
    # 1. .jobad_company_logo img[alt] — most reliable (KS sets alt = company name)
    # 2. Title prefix before " - " (e.g. "ABB - Local Trade Compliance officer")
    # 3. .company-desc h2/h3 (skip <a> since first <a> often = social link)
    # Skip junk text: nav labels, social-network names, CTA blocks
    BAD = re.compile(
        r"(arbeidsgiverguiden|m.t attraktive|partnere|annonsere|nyheter|relaterte|"
        r"kontaktperson|instagram|facebook|linkedin|twitter|youtube|tiktok|profil)",
        re.I,
    )
    if not j.company or BAD.search(j.company):
        # Strategy 1: company logo img alt
        img = soup.select_one(".jobad_company_logo img, [class*='company_logo'] img")
        if img and img.get("alt"):
            alt = _normalize(img.get("alt", "").strip())
            if alt and 2 <= len(alt) <= 80 and not BAD.search(alt):
                j.company = alt
        # Strategy 2: title-prefix
        if (not j.company or BAD.search(j.company)) and j.title and " - " in j.title:
            prefix = j.title.split(" - ", 1)[0].strip()
            if 2 <= len(prefix) <= 60 and not re.search(r"\d", prefix):
                j.company = prefix
        # Strategy 3: standard selectors
        if not j.company or BAD.search(j.company):
            for sel in (".company-name a", ".company-name", ".jp-company"):
                el = soup.select_one(sel)
                if el:
                    t = _normalize(el.get_text(" ", strip=True))
                    if t and 2 <= len(t) <= 80 and not BAD.search(t):
                        j.company = t
                        break

    # Description in main article
    if not j.description or len(j.description) < 100:
        for sel in (".job-description", ".jp-description", ".job-content", "article", "main"):
            el = soup.select_one(sel)
            if el:
                t = _normalize(el.get_text(" ", strip=True))
                if len(t) > 100:
                    j.description = t[:15000]
                    break

    # City + postal from location string "Oslo, 0123" or similar
    if j.location and not j.city:
        m = re.search(r"\b(\d{4})\s+([A-ZÆØÅa-zæøå\-]+)", j.location)
        if m:
            j.postal_code = j.postal_code or m.group(1)
            j.city = m.group(2)
        else:
            # fallback: first comma-separated token
            j.city = j.location.split(",")[0].strip()
            # No 4-digit postcode → leave blank

    # Deadline: <span class="jobad-deadline-date">DD.MM.YYYY</span> (not a fact-card)
    if not j.valid_through:
        el = soup.select_one(".jobad-deadline-date, .smalljob-deadline-date, [class*='deadline-date']")
        if el:
            t = _normalize(el.get_text(" ", strip=True))
            if re.match(r"^\d{2}\.\d{2}\.\d{2,4}$", t):
                j.valid_through = t


def _looks_like_job_page(soup: BeautifulSoup, page_url: str) -> bool:
    path = urlparse(page_url).path.lower()
    if any(t in path for t in ("/job", "/stilling", "/career", "/vacancy", "/joboffer", "/ad/")):
        return True
    text = soup.get_text(" ", strip=True).lower()
    signals = ["apply", "responsibilities", "requirements", "qualifications",
               "søk", "stillingen", "arbeidsgiver", "aufgaben", "anforderungen",
               "deine aufgaben"]
    return sum(1 for s in signals if s in text) >= 2


def _meta(soup: BeautifulSoup, name: str) -> str:
    el = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
    if el and el.get("content"):
        return el["content"].strip()
    return ""


def _main_content_node(soup: BeautifulSoup) -> Optional[Tag]:
    for sel in ["main", "article", "[role='main']", "[class*='job-detail']",
                "[class*='posting']", "[class*='vacancy']", "[id*='job-detail']",
                "[class*='description']"]:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 100:
            return el
    return soup.find("body")


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------------------
# Anchor-cluster detection: find the job-link cluster on any listing page
# ---------------------------------------------------------------------------

def cluster_anchors(html: str, base_url: str) -> List[str]:
    """Return URLs of the largest anchor cluster that looks like job detail
    pages. Works on any site without per-site selectors.

    Strategy:
      1. Collect every same-domain anchor.
      2. Group by path prefix (everything up to the last /<id>).
      3. Score each group by (count, has-job-keyword-in-path).
      4. Return URLs of the top group.
    """
    soup = BeautifulSoup(html, "lxml")
    parsed_base = urlparse(base_url)
    same_host = parsed_base.netloc.lower()
    by_prefix: Dict[str, List[str]] = defaultdict(list)
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        try:
            abs_url = urljoin(base_url, href)
        except Exception:
            continue
        pu = urlparse(abs_url)
        if pu.netloc.lower() != same_host:
            continue
        # Path prefix = everything before the last URL segment
        parts = [p for p in pu.path.split("/") if p]
        if len(parts) < 2:
            continue
        prefix = "/".join(parts[:-1])
        by_prefix[prefix].append(abs_url.split("#")[0])

    if not by_prefix:
        return []

    JOB_HINT = re.compile(r"(job|stilling|vacanc|career|posting|opening|ad|opportunit|trabajo)", re.I)

    def score(prefix: str, urls: List[str]) -> Tuple[int, int]:
        # Tuple: (count weight, job-keyword bonus)
        kw_bonus = 2 if JOB_HINT.search(prefix) else 0
        return (len(urls) + kw_bonus * 5, kw_bonus)

    ranked = sorted(by_prefix.items(), key=lambda kv: score(kv[0], kv[1]), reverse=True)
    top_prefix, top_urls = ranked[0]
    # Sanity: don't return tiny clusters
    if len(top_urls) < 3:
        return []
    # Dedupe preserving order
    seen = set()
    out: List[str] = []
    for u in top_urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out
