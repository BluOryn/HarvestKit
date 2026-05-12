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
            r"Contact|Apply|Submit|Reach|Tittel|Nummer)",
            re.I,
        )
        company_suffix_rx = re.compile(r"(group|holding|inc|ltd|gmbh|as|asa|ab|s\.a\.|nv|bv|llc|llp|plc|ag|sa|gbr)\b", re.I)
        for cand in ns:
            parts = cand.split()
            if len(parts) < 2:
                continue
            # Reject if ANY token is a label like "Stillingstittel" / "Telefonnummer".
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
    return j


def _nav_no_specific(soup: BeautifulSoup, j: JobListing) -> None:
    """Pull NAV-specific labelled fields like Arbeidsgiver / Sted / Søknadsfrist.

    NAV renders fields as a dt/dd-style pattern where the label heading is a
    text node followed by an SVG icon, then the value sits in a sibling div.
    """
    text_blocks = soup.find_all(["div", "dl", "dd", "section"])
    label_map = {
        "Arbeidsgiver": "company",
        "Sted": "location",
        "Stillingstype": "employment_type",
        "Heltid/Deltid": "employment_type",
        "Stillingsbrøk": "employment_type",
        "Søknadsfrist": "valid_through",
        "Publisert": "posted_date",
        "Sektor": "company_industry",
        "Bransje": "company_industry",
    }
    # JS / framework noise patterns to reject
    junk_rx = re.compile(r"^(self\.__next_f|window\.|var\s|function\s|null\s*$|undefined\s*$)", re.I)

    def _is_plausible_value(txt: str) -> bool:
        if not txt or len(txt) > 200:
            return False
        if junk_rx.match(txt):
            return False
        if txt.startswith("{") or txt.startswith("["):
            return False
        return True

    for label, field in label_map.items():
        # Match label as a string node — allow surrounding whitespace + optional colon
        rx = re.compile(rf"^\s*{re.escape(label)}\s*:?\s*$", re.I)
        # First try exact-string match (best precision)
        targets = list(soup.find_all(string=rx))
        # Fall back to startswith match for cases where label is followed by inline content
        if not targets:
            rx2 = re.compile(rf"^\s*{re.escape(label)}\b", re.I)
            targets = [s for s in soup.find_all(string=rx2) if len((s or "").strip()) < 80]
        for el in targets:
            if not el or not el.parent:
                continue
            ancestor = el.parent
            value = None
            for _ in range(4):
                for sib in ancestor.find_next_siblings():
                    if not getattr(sib, "get_text", None):
                        continue
                    txt = sib.get_text(" ", strip=True)
                    if _is_plausible_value(txt):
                        value = txt
                        break
                if value:
                    cur = getattr(j, field, "")
                    if not cur:
                        setattr(j, field, value)
                    break
                if ancestor.parent is None:
                    break
                ancestor = ancestor.parent
            break
    # Also mine the body text for `Label: value` inline pairs NAV sometimes uses.
    body_txt = soup.get_text(" ", strip=True)

    # Stop words: another label OR a long word break OR end-of-line
    STOP_LABEL_RX = (
        r"Stillingstype|Heltid|Deltid|Sektor|Bransje|Arbeidsspr(å|a)k|Antall\s+stillinger|"
        r"Stillingsbr(ø|o)k|Publisert|S(ø|o)knadsfrist|Frist|Stillingsprosent|Ansettelsesform|"
        r"Tiltreder|Oppstart|Sted|Beliggenhet|Reisemengde|Hjemmekontor|Kontaktperson|"
        r"Arbeidssted|Arbeidstid|Arbeidsgiver|Stillingsfunksjon|Yrke|Fagomr(å|a)de|"
        r"Referansenr|Referanse|Sektor|Stilling$"
    )
    for label, field in [
        ("Søknadsfrist", "valid_through"),
        ("Frist", "valid_through"),
        ("Publisert", "posted_date"),
        ("Stillingsprosent", "employment_type"),
        ("Ansettelsesform", "employment_type"),
        ("Tiltreder", "start_date"),
        ("Oppstart", "start_date"),
        ("Arbeidsspråk", "language"),
        ("Heltid/Deltid", "employment_type"),
        ("Stillingstype", "employment_type"),
        ("Reisemengde", "travel_required"),
        ("Stillingsfunksjon", "department"),
        ("Yrke", "department"),
        ("Fagområde", "department"),
        ("Referansenr.", "requisition_id"),
        ("Referansenr", "requisition_id"),
        ("Antall stillinger", "_skip"),  # not in schema
    ]:
        if field == "_skip" or getattr(j, field, ""):
            continue
        m = re.search(
            rf"\b{re.escape(label)}\b\s*:?\s*([^\n\r]{{2,80}}?)(?:\s+(?:{STOP_LABEL_RX})\b|$)",
            body_txt,
            re.I,
        )
        if m:
            v = m.group(1).strip(" \t:-—,;")
            if v and len(v) < 100 and v.lower() not in ("none", "null", "n/a"):
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
