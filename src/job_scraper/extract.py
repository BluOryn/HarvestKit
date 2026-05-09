"""HTML/JSON-LD job-page extractor — local-Python mirror of extension/app/src/content/extractor.ts.

Strategy stack (each contributes a partial JobListing; longer string wins on merge):
  1. JSON-LD JobPosting (Schema.org)
  2. Microdata (itemtype=schema.org/JobPosting)
  3. OpenGraph + meta tags
  4. Heuristics (regex over body text → salary, tech stack, seniority, etc.)
  5. Recruiter section (name/title/email/phone/LinkedIn)

Public API:
  - extract_job_postings(html, url) — for backwards-compatibility with old call sites
  - extract_job_from_page(html, url) — full single-job extraction with all 60 fields
  - extract_apply_url_from_html(html, url) — apply-URL anchor scan
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .models import JOB_FIELDS, JobListing


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def extract_job_postings(html: str, page_url: str) -> List[JobListing]:
    """Backwards-compat: returns a list (possibly empty) of JobListing for a page.

    For multiple JobPosting blocks (rare; some sites embed several), returns each.
    For a single page, returns one fully-populated JobListing.
    """
    soup = BeautifulSoup(html, "lxml")
    jsonld_blocks = list(_iter_json_ld(soup))
    job_postings = [b for b in jsonld_blocks if _is_job_posting(b)]

    listings: List[JobListing] = []
    if job_postings:
        for ld in job_postings:
            listing = JobListing()
            _apply_jsonld(listing, ld)
            _apply_microdata(listing, soup)
            _apply_opengraph(listing, soup)
            _apply_heuristics(listing, soup)
            _apply_recruiter(listing, soup)
            _finalize(listing, html, page_url, soup, ld)
            listings.append(listing)
        return listings

    # No JSON-LD JobPosting — single best-effort listing
    if _is_likely_job_page(page_url, soup):
        listing = JobListing()
        _apply_microdata(listing, soup)
        _apply_opengraph(listing, soup)
        _apply_heuristics(listing, soup)
        _apply_recruiter(listing, soup)
        _finalize(listing, html, page_url, soup, None)
        if listing.title or listing.description:
            return [listing]
    return []


def extract_job_from_page(html: str, page_url: str) -> Optional[JobListing]:
    """Single-page extraction, merging all strategies. Returns None if nothing found."""
    listings = extract_job_postings(html, page_url)
    return listings[0] if listings else None


def extract_apply_url_from_html(html: str, page_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    return _find_apply_url(soup, page_url)


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

def _iter_json_ld(soup: BeautifulSoup) -> Iterable[Dict[str, Any]]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text(strip=False)
        if not text:
            continue
        text = text.strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            # Some sites embed multiple JSON objects concatenated. Try repairing trailing commas.
            try:
                data = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
            except Exception:
                continue
        for item in _flatten_json_ld(data):
            if isinstance(item, dict):
                yield item


def _flatten_json_ld(data: Any) -> Iterable[Any]:
    if isinstance(data, list):
        for item in data:
            yield from _flatten_json_ld(item)
        return
    if isinstance(data, dict):
        graph = data.get("@graph")
        if graph:
            yield from _flatten_json_ld(graph)
            return
        yield data


def _is_job_posting(item: Dict[str, Any]) -> bool:
    t = item.get("@type")
    if isinstance(t, list):
        return any(str(x).lower() == "jobposting" for x in t)
    return str(t or "").lower() == "jobposting"


def _apply_jsonld(j: JobListing, ld: Dict[str, Any]) -> None:
    j.title = j.title or _str(ld.get("title") or ld.get("name"))
    raw_desc_html = ld.get("description") or ""
    j.description = j.description or _html_to_text(raw_desc_html)
    # Run section parser on the raw HTML — much richer than DOM siblings.
    if raw_desc_html and isinstance(raw_desc_html, str):
        sects = _parse_jd_sections(raw_desc_html)
        if sects.get("responsibilities") and not j.responsibilities:
            j.responsibilities = sects["responsibilities"]
        if sects.get("requirements") and not j.requirements:
            j.requirements = sects["requirements"]
            if not j.qualifications:
                j.qualifications = sects["requirements"]
        if sects.get("benefits") and not j.benefits:
            j.benefits = sects["benefits"]
        if sects.get("recruiter_name") and not j.recruiter_name:
            j.recruiter_name = sects["recruiter_name"]
        if sects.get("recruiter_title") and not j.recruiter_title:
            j.recruiter_title = sects["recruiter_title"]
        if sects.get("recruiter_phone") and not j.recruiter_phone:
            j.recruiter_phone = sects["recruiter_phone"]
        if sects.get("recruiter_email") and not j.recruiter_email:
            j.recruiter_email = sects["recruiter_email"]
        if sects.get("apply_url") and not j.apply_url:
            j.apply_url = sects["apply_url"]
        if sects.get("education_required") and not j.education_required:
            j.education_required = sects["education_required"]
        if sects.get("experience_years") and not j.experience_years:
            j.experience_years = sects["experience_years"]
        if sects.get("hiring_manager") and not j.hiring_manager:
            j.hiring_manager = sects["hiring_manager"]

    org = ld.get("hiringOrganization")
    if isinstance(org, dict):
        j.company = j.company or _str(org.get("name"))
        logo = org.get("logo")
        if isinstance(logo, dict):
            j.company_logo = j.company_logo or _str(logo.get("url") or logo.get("@id"))
        elif isinstance(logo, str):
            j.company_logo = j.company_logo or logo
        j.company_website = j.company_website or _str(org.get("sameAs") or org.get("url"))
    elif isinstance(org, str):
        j.company = j.company or org

    locs = ld.get("jobLocation")
    if not isinstance(locs, list):
        locs = [locs] if locs else []
    loc_strs: List[str] = []
    for loc in locs:
        if not loc:
            continue
        if isinstance(loc, str):
            loc_strs.append(loc)
            continue
        addr = loc.get("address") if isinstance(loc, dict) else None
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry"), addr.get("postalCode")]
            country = addr.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            loc_strs.append(", ".join([str(p) for p in parts if p]))
            j.city = j.city or _str(addr.get("addressLocality"))
            j.region = j.region or _str(addr.get("addressRegion"))
            j.country = j.country or _str(country)
            j.postal_code = j.postal_code or _str(addr.get("postalCode"))
            street = _str(addr.get("streetAddress"))
            if street and not j.location:
                # streetAddress alone isn't useful; only include if we have anything else
                pass
    if loc_strs:
        j.location = j.location or " | ".join([s for s in loc_strs if s])

    loc_type = ld.get("jobLocationType")
    if loc_type and re.search(r"telecommute|remote", json.dumps(loc_type), re.I):
        j.remote_type = j.remote_type or "remote"
    elif ld.get("applicantLocationRequirements"):
        j.remote_type = j.remote_type or "remote"

    et = ld.get("employmentType")
    if isinstance(et, list):
        j.employment_type = j.employment_type or ", ".join(str(x) for x in et if x)
    elif et:
        j.employment_type = j.employment_type or _str(et)

    j.posted_date = j.posted_date or _str(ld.get("datePosted"))
    j.valid_through = j.valid_through or _str(ld.get("validThrough"))
    j.start_date = j.start_date or _str(ld.get("jobStartDate"))
    lang = ld.get("inLanguage")
    if isinstance(lang, list):
        j.language = j.language or ", ".join(str(x) for x in lang if x)
    elif lang:
        j.language = j.language or _str(lang)

    sal = ld.get("baseSalary")
    if isinstance(sal, dict):
        j.salary_currency = j.salary_currency or _str(sal.get("currency"))
        v = sal.get("value")
        if isinstance(v, dict):
            j.salary_min = j.salary_min or _str(v.get("minValue"))
            j.salary_max = j.salary_max or _str(v.get("maxValue") or v.get("value"))
            j.salary_period = j.salary_period or _str(v.get("unitText"))
        elif v:
            j.salary_max = j.salary_max or _str(v)

    edu = ld.get("educationRequirements")
    if isinstance(edu, dict):
        j.education_required = j.education_required or _str(edu.get("credentialCategory") or edu.get("name"))
    elif edu:
        j.education_required = j.education_required or _str(edu)

    exp = ld.get("experienceRequirements")
    if isinstance(exp, dict):
        months = exp.get("monthsOfExperience")
        if months:
            try:
                j.experience_years = j.experience_years or str(int(months) // 12)
            except Exception:
                j.experience_years = j.experience_years or str(months)
        else:
            j.experience_years = j.experience_years or _str(exp.get("name"))
    elif exp:
        j.experience_years = j.experience_years or _str(exp)

    ident = ld.get("identifier")
    if isinstance(ident, dict):
        j.external_id = j.external_id or _str(ident.get("value") or ident.get("name"))
    elif ident:
        j.external_id = j.external_id or _str(ident)

    apply_action = _resolve_apply_action(ld)
    if apply_action:
        j.apply_url = j.apply_url or apply_action

    j.job_url = j.job_url or _str(ld.get("url"))

    # Schema.org distinguishes occupationalCategory (job function) from industry (employer's sector).
    # Map them to department vs company_industry respectively.
    occ = ld.get("occupationalCategory")
    if occ:
        j.department = j.department or _str(occ if not isinstance(occ, list) else ", ".join(map(str, occ)))
    ind = ld.get("industry")
    if ind:
        j.company_industry = j.company_industry or _str(ind if not isinstance(ind, list) else ", ".join(map(str, ind)))

    if not j.raw_jsonld:
        try:
            j.raw_jsonld = json.dumps(ld, ensure_ascii=False)[:8000]
        except Exception:
            j.raw_jsonld = ""


# Section header regexes — broad coverage of EN/DE/FR/IT.
SECTION_PATTERNS: Dict[str, re.Pattern] = {
    "responsibilities": re.compile(
        r"\b("
        r"responsibilit|what\s+you'?ll\s+do|your\s+role|key\s+(tasks|duties)|"
        r"deine\s+aufgabe|ihre\s+aufgabe|aufgaben|aufgabengebiet|hauptaufgaben|"
        r"das\s+erwartet\s+dich|was\s+(dich|sie)\s+erwartet|was\s+du\s+bewegst|"
        r"damit\s+unterst(ü|u)tzt\s+du\s+uns|dein\s+aufgabenfeld|"
        r"mission|tätigkeitsbereich|"
        r"vos\s+missions|vos\s+responsabilit|tes\s+missions|"
        r"le\s+tue\s+responsabilit"
        r")\b", re.I),
    "requirements": re.compile(
        r"\b("
        r"requirement|qualification|what\s+you'?ll\s+need|must[-\s]?have|"
        r"dein\s+profil|ihr\s+profil|das\s+bringst\s+du\s+mit|"
        r"das\s+(zeichnet\s+dich\s+aus|sind\s+sie)|so\s+machst\s+du\s+uns\s+happy|"
        r"damit\s+(begeisterst\s+du|kannst\s+du\s+uns)|"
        r"das\s+wünschen\s+wir\s+uns|anforderung|profil|qualifikation|"
        r"was\s+sie\s+(daf(ü|u)r\s+)?mitbringen|"
        r"votre\s+profil|tu\s+es|nous\s+recherchons|"
        r"il\s+tuo\s+profilo"
        r")\b", re.I),
    "benefits": re.compile(
        r"\b("
        r"benefits?|perks|what\s+we\s+offer|wir\s+bieten|deine\s+vorteile|"
        r"freue\s+dich\s+auf|damit\s+begeistern\s+wir\s+dich|"
        r"das\s+bieten\s+wir|haben\s+wir\s+dein\s+interesse|"
        r"unser\s+angebot|deine\s+benefits|wir\s+sind\s+|"
        r"nous\s+offrons|nos\s+avantages|"
        r"ti\s+offriamo|i\s+nostri\s+vantaggi"
        r")\b", re.I),
    "contact": re.compile(
        r"\b("
        r"contact|kontakt|deine\s+kontakte|haben\s+wir\s+(dein|ihr)\s+interesse|"
        r"bist\s+du\s+(bereit|interessiert)|fragen\s+beantwortet|"
        r"bewerbung|bewerben|jetzt\s+bewerben|interesse\s+geweckt|"
        r"contactez|postuler"
        r")\b", re.I),
}

# Recruiter name patterns (EN/DE/FR/IT prefixes + bare First Last forms)
RECRUITER_NAME_RX = re.compile(
    r"\b(Frau|Herr|Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Madame|Monsieur|Mme|Mr|Sig\.?|Sig\.ra|Sr\.?|Sra\.?)\s+"
    r"([A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+){1,3})\b"
)
# Contextual: name followed by title hint or vice-versa, e.g.
#   "Anna Müller, HR Manager"  /  "Sebastian Gigla (Recruiter)"
#   "Talent Acquisition Partner Anna Müller"
RECRUITER_NAME_TITLE_RX = re.compile(
    r"([A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+){1,3})"  # name
    r"\s*[,(–\-\|/]\s*"
    r"(Recruiter|Talent\s+(?:Acquisition|Partner|Manager|Lead)|HR(?:[-\s]Manager| Business Partner| Generalist)?|"
    r"People\s+(?:Partner|Operations|Manager)|Hiring\s+Manager|Sourcer|Personalleit|"
    r"HR-Team|Personalabteilung|Personalverantwort|Leiter[\s\w]{0,30}|Head\s+of\s+\w+)"
)
# Job-function keywords near recruiter (for backfilling title when only name found)
RECRUITER_TITLE_HINTS = re.compile(
    r"\b(Recruiter|Talent\s+(?:Acquisition|Partner|Manager|Lead)|HR\s*Manager|HR\s*Business\s*Partner|"
    r"HR\s*Generalist|People\s+Partner|People\s+Operations|Hiring\s+Manager|Sourcer|"
    r"Personalleit|Personalverantwort|HR-Team|Personalabteilung|Leiter\s+HR|"
    r"Head\s+of\s+(?:HR|People|Talent)|Chef\s+(?:du|de)\s+personnel)\b",
    re.I,
)
# Swiss/EU phone: +41 44 215 15 78 / +49 30 …
PHONE_INTL_RX = re.compile(r"\+\d{1,3}\s?\d{1,4}\s?\d{2,4}\s?\d{2,4}\s?\d{2,4}")


def _parse_jd_sections(html: str) -> Dict[str, str]:
    """Parse a job-description HTML blob into structured sections.

    Walks the DOM looking for h1-h4/strong/b headings whose text matches the
    section regexes, then collects the *next* sibling block (typically a <ul>
    or set of <p>) until the next heading. Works on jobs.ch-style nested div
    layouts where the heading is wrapped in <strong> inside its own div.
    """
    soup = BeautifulSoup(html, "lxml")
    out: Dict[str, str] = {}
    headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"])

    for h in headings:
        text = h.get_text(" ", strip=True)
        if not text or len(text) > 120:
            continue
        for kind, rx in SECTION_PATTERNS.items():
            if not rx.search(text):
                continue
            block = _collect_section_body(h)
            body = _normalize(block)
            if not body or len(body) < 20:
                continue
            if kind == "contact":
                _extract_contact_info(block, out)
            else:
                if kind not in out or len(body) > len(out[kind]):
                    out[kind] = body
            break

    # Education / experience / hiring manager — scan whole HTML body, not just sections
    full_text = _normalize(soup.get_text(" ", strip=True))
    _extract_education_experience(full_text, out)

    # Apply URL: scan all anchors for "Apply"/"Bewerben" or external ATS host.
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        atext = a.get_text(" ", strip=True)
        if not href:
            continue
        if APPLY_HOST_RX.search(href):
            out.setdefault("apply_url", href)
            break
        if APPLY_TEXT_RX.search(atext):
            out.setdefault("apply_url", href)

    return out


def _collect_section_body(heading: Any) -> str:
    """Walk forward from `heading` capturing sibling text until the next heading
    of equal/higher rank. Handles nested div containers where heading is wrapped.
    """
    # Climb up to a reasonable container — the heading's parent <div> or <p>.
    container = heading
    for _ in range(3):
        parent = getattr(container, "parent", None)
        if parent is None:
            break
        # If parent is the description root (multiple section siblings), stop.
        sibs = [s for s in parent.find_next_siblings() if getattr(s, "name", None)]
        if sibs:
            container = parent
            break
        container = parent

    buf: List[str] = []
    found_self = False
    for sib in container.find_next_siblings():
        if not getattr(sib, "name", None):
            continue
        # Stop on next heading-like sibling.
        sib_headings = sib.find_all(["h1","h2","h3","h4","h5","h6","strong","b"])
        if sib_headings:
            # If the first heading inside is our text, skip.
            first_text = sib_headings[0].get_text(" ", strip=True)
            for rx in SECTION_PATTERNS.values():
                if rx.search(first_text):
                    return _normalize(" • ".join(buf)) if buf else ""
        items = sib.find_all(["li", "p"])
        if items:
            for it in items:
                t = it.get_text(" ", strip=True)
                if t:
                    buf.append(t)
        else:
            t = sib.get_text(" ", strip=True)
            if t:
                buf.append(t)
        if sum(len(x) for x in buf) > 4000:
            break
    if buf:
        return " • ".join(buf)

    # Fallback: collect heading's parent block's body.
    parent = heading.parent
    if parent is not None:
        items = parent.find_all(["li", "p"])
        if items:
            return " • ".join(it.get_text(" ", strip=True) for it in items if it.get_text(strip=True))
    return ""


def _extract_contact_info(html_or_text: str, out: Dict[str, str]) -> None:
    """From a 'Contact' section (string or HTML), pull recruiter name/title/email/phone."""
    text = _normalize(html_or_text)

    # 1. Try Name + Title combo first (richest)
    nt = RECRUITER_NAME_TITLE_RX.search(text)
    if nt:
        out.setdefault("recruiter_name", _normalize(nt.group(1)))
        out.setdefault("recruiter_title", _normalize(nt.group(2)))
    else:
        # 2. Bare title hint (no name attached) — e.g. "HR Manager" alone
        th = RECRUITER_TITLE_HINTS.search(text)
        if th:
            out.setdefault("recruiter_title", _normalize(th.group(0)))

    # 3. Frau/Herr-prefixed name
    nm = RECRUITER_NAME_RX.search(text)
    if nm and not out.get("recruiter_name"):
        # "Frau Anna Müller" — keep prefix only if it's a real title (Dr.)
        prefix = nm.group(1)
        if prefix.startswith("Dr"):
            out["recruiter_name"] = f"Dr. {nm.group(2)}"
        else:
            out["recruiter_name"] = nm.group(2)

    # 4. Phone — prefer international format, fall back to plain
    ph = PHONE_INTL_RX.search(text) or PHONE_RX.search(text)
    if ph:
        candidate = ph.group(0)
        if len(re.sub(r"\D", "", candidate)) >= 7:
            out.setdefault("recruiter_phone", _normalize(candidate))

    # 5. Email
    for cand in EMAIL_RX.findall(text):
        if re.search(r"noreply|no-reply|example\.com|@sentry", cand, re.I):
            continue
        out.setdefault("recruiter_email", cand)
        break


EDUCATION_RX = re.compile(
    r"\b("
    r"Bachelor(?:'?s)?(?:\s+(?:degree|of\s+(?:Science|Arts|Engineering)))?|"
    r"Master(?:'?s)?(?:\s+(?:degree|of\s+(?:Science|Arts|Engineering)))?|"
    r"M\.?Sc\.?|M\.?Eng\.?|M\.?A\.?|B\.?Sc\.?|B\.?Eng\.?|B\.?A\.?|"
    r"Ph\.?D\.?|Doctorate|"
    r"Studium|Diplom|Promotion|Hochschulabschluss|Universitätsabschluss|"
    r"FH(?:-Abschluss)?|ETH-Abschluss|"
    r"Lehre|Berufsausbildung|Berufsmatur|Fachhochschule|"
    r"Apprenticeship|EFZ|Eidg\.?\s+Fähigkeitszeugnis|"
    r"Licence|Master\s+pro|DEA|DESS|"
    r"diploma|degree"
    r")\b",
    re.I,
)
EXPERIENCE_RX = re.compile(
    r"\b("
    r"(\d+)\s?\+?\s?(?:-\s?\d+\s?)?(years?|jahre?n?|jahresberufserfahrung|ans|anni)|"
    r"(?:mind\.?|min(?:imum)?|at\s+least|wenigstens|au\s+moins)\s+(\d+)\s+(?:jahre?|years?|ans|anni)|"
    r"(\d+)\s+years?\s+(?:of\s+)?(?:experience|erfahrung|exp\.?)|"
    r"(\d+)\s?\+?\s?(?:jährige|years?\b)"
    r")",
    re.I,
)
HIRING_MANAGER_RX = re.compile(
    r"\b(hiring\s+manager|reports?\s+to|berichtet\s+an|vorgesetzt|line\s+manager|"
    r"direct\s+supervisor|supervisor)\s*[:\-]?\s*"
    r"([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+){1,3})",
    re.I,
)


def _extract_education_experience(text: str, out: Dict[str, str]) -> None:
    if "education_required" not in out:
        m = EDUCATION_RX.search(text)
        if m:
            out["education_required"] = _normalize(m.group(0))
    if "experience_years" not in out:
        m = EXPERIENCE_RX.search(text)
        if m:
            for grp in m.groups()[1:]:
                if grp and grp.isdigit():
                    out["experience_years"] = grp
                    break
    if "hiring_manager" not in out:
        m = HIRING_MANAGER_RX.search(text)
        if m:
            out["hiring_manager"] = _normalize(m.group(2))


def _resolve_apply_action(ld: Dict[str, Any]) -> str:
    pa = ld.get("potentialAction")
    if pa is None:
        if isinstance(ld.get("directApply"), str):
            return ld["directApply"]
        ac = ld.get("applicationContact")
        if isinstance(ac, dict):
            return _str(ac.get("url") or ac.get("email"))
        return ""
    arr = pa if isinstance(pa, list) else [pa]
    for a in arr:
        if not a:
            continue
        if isinstance(a, str):
            return a
        if isinstance(a, dict):
            t = a.get("target")
            if isinstance(t, str):
                return t
            if isinstance(t, dict):
                return _str(t.get("urlTemplate") or t.get("url"))
    return ""


# ---------------------------------------------------------------------------
# Microdata
# ---------------------------------------------------------------------------

def _apply_microdata(j: JobListing, soup: BeautifulSoup) -> None:
    root = soup.find(attrs={"itemtype": re.compile(r"schema\.org/JobPosting", re.I)})
    if not root or not isinstance(root, Tag):
        return
    def get_prop(name: str) -> str:
        el = root.find(attrs={"itemprop": name})
        if not el:
            return ""
        if el.has_attr("content"):
            return el["content"]
        if el.name == "meta":
            return el.get("content", "")
        if el.name in ("a", "link"):
            return el.get("href", "") or el.get_text(" ", strip=True)
        return el.get_text(" ", strip=True)
    j.title = j.title or get_prop("title")
    j.description = j.description or _html_to_text(get_prop("description"))
    j.employment_type = j.employment_type or get_prop("employmentType")
    j.posted_date = j.posted_date or get_prop("datePosted")
    j.valid_through = j.valid_through or get_prop("validThrough")


# ---------------------------------------------------------------------------
# OpenGraph
# ---------------------------------------------------------------------------

def _apply_opengraph(j: JobListing, soup: BeautifulSoup) -> None:
    def og(name: str) -> str:
        el = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if el and el.get("content"):
            return el["content"].strip()
        return ""
    j.title = j.title or og("og:title")
    j.description = j.description or og("og:description") or og("description")
    j.company_logo = j.company_logo or og("og:image")


# ---------------------------------------------------------------------------
# Heuristics — body text scan
# ---------------------------------------------------------------------------

TECH_DICT = [
    "python", "java", "kotlin", "scala", "golang", "rust", "c++", "c#", "typescript", "javascript",
    "react", "vue", "angular", "svelte", "next.js", "nuxt", "node.js", "express", "fastapi",
    "django", "flask", "spring", "spring boot",
    "aws", "gcp", "azure", "kubernetes", "k8s", "docker", "terraform", "ansible", "helm", "istio",
    "postgres", "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "clickhouse", "snowflake",
    "bigquery", "redshift", "databricks",
    "kafka", "rabbitmq", "spark", "airflow", "dbt", "flink", "hadoop",
    "tensorflow", "pytorch", "jax", "scikit-learn", "huggingface", "transformers", "langchain", "llamaindex",
    "graphql", "rest", "grpc", "openapi", "swagger",
    "ci/cd", "jenkins", "github actions", "gitlab ci", "circleci", "argocd",
    "linux", "bash",
    "ios", "swift", "objective-c", "android", "jetpack compose", "flutter", "react native",
    "salesforce", "sap", "servicenow", "oracle", "datadog", "new relic", "grafana", "prometheus", "sentry",
    "tableau", "power bi", "looker", "metabase", "mixpanel",
    "openai", "anthropic", "gemini", "llm", "rag", "embeddings", "vector db", "pinecone", "weaviate", "chroma", "qdrant",
]

SENIORITY = [
    ("principal", re.compile(r"\bprincipal\b", re.I)),
    ("staff", re.compile(r"\bstaff\b", re.I)),
    ("senior", re.compile(r"\bsenior\b|\bsr\.?\b", re.I)),
    ("lead", re.compile(r"\blead\b|\btech\s+lead\b", re.I)),
    ("mid", re.compile(r"\bmid[-\s]?level\b", re.I)),
    ("junior", re.compile(r"\bjunior\b|\bjr\.?\b|\bgraduate\b|\bentry[-\s]?level\b", re.I)),
    ("intern", re.compile(r"\bintern\b|\bpraktikum\b|\bwerkstudent\b", re.I)),
    ("director", re.compile(r"\bdirector\b", re.I)),
    ("head", re.compile(r"\bhead\s+of\b|\bvp\b|\bcto\b", re.I)),
]

# Salary regex — covers EUR/USD/GBP/CHF/SEK/DKK/NOK/PLN/CZK/HUF in CH-formatted numbers
# (apostrophe / space / narrow-no-break-space / no-break-space thousands separators).
# Examples it matches:
#   "CHF 80'000 - 110'000 / Jahr"
#   "EUR 60.000 – 75.000 pro Jahr"
#   "$120,000 - $150,000 per year"
#   "CHF 80 000.– bis 110 000.–"
_NUM = r"\d{2,3}(?:[.,'\s  ]\d{3})*(?:[.,]\d+)?"
SALARY_RX = re.compile(
    r"(?:€|EUR|USD|US\$|\$|£|GBP|CHF|Fr\.|SFr\.|SEK|DKK|NOK|PLN|CZK|HUF)\s?"
    rf"({_NUM})\s?(?:[.,]–|–|–|\.\-|\-|—)?\s?(?:k\b|tausend|thousand)?\s?"
    r"(?:-|–|—|to|bis|à|a)?\s?"
    r"(?:€|EUR|USD|US\$|\$|£|GBP|CHF|Fr\.|SFr\.|SEK|DKK|NOK|PLN|CZK|HUF)?\s?"
    rf"({_NUM})?\s?"
    r"(?:[.,]–|–|—|\.\-)?\s?"
    r"(per|/|pro|par|al)?\s?"
    r"(year|yr|p\.a\.|annual|jahr|jährlich|annee|année|month|mo|monat|monatlich|mois|hour|hr|stunde|heure|ora)?",
    re.I,
)
EMAIL_RX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RX = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{2,4}")

REMOTE_RX = re.compile(r"\b(remote|home\s?office|work\s+from\s+home|fully\s+remote|telecommut|distributed)\b", re.I)
HYBRID_RX = re.compile(r"\b(hybrid|flex(?:ible)?\s+location)\b", re.I)
ONSITE_RX = re.compile(r"\b(on[-\s]?site|in[-\s]?office|vor\s+ort)\b", re.I)
VISA_RX = re.compile(r"\b(visa\s+sponsorship|sponsor\s+(?:your)?\s?visa|h-?1b|blue\s+card)\b", re.I)
RELOC_RX = re.compile(r"relocation\s+(?:support|assistance|package|paid|covered|provided)", re.I)
EQUITY_RX = re.compile(r"\b(equity|stock\s+options?|rsu|esop|share\s+options?)\b", re.I)


def _apply_heuristics(j: JobListing, soup: BeautifulSoup) -> None:
    body = soup.find("body") or soup
    text = body.get_text(" ", strip=True)[:200000]
    lower = text.lower()

    techs: List[str] = []
    for t in TECH_DICT:
        escaped = re.escape(t)
        if re.search(rf"(^|[^a-z0-9]){escaped}([^a-z0-9]|$)", lower):
            techs.append(t)
    if techs and not j.tech_stack:
        j.tech_stack = ", ".join(_uniq(techs))

    if not j.seniority:
        for name, rx in SENIORITY:
            if rx.search(text):
                j.seniority = name
                break

    if not j.remote_type:
        if REMOTE_RX.search(text):
            j.remote_type = "remote"
        elif HYBRID_RX.search(text):
            j.remote_type = "hybrid"
        elif ONSITE_RX.search(text):
            j.remote_type = "onsite"

    if not (j.salary_min or j.salary_max):
        m = SALARY_RX.search(text)
        if m:
            whole = m.group(0)
            lo, hi, period = m.group(1), m.group(2), m.group(4)
            if lo:
                j.salary_min = _normalize_amount(lo)
            if hi:
                j.salary_max = _normalize_amount(hi)
            if not j.salary_currency:
                if re.search(r"€|EUR", whole, re.I):
                    j.salary_currency = "EUR"
                elif re.search(r"\$|USD", whole, re.I):
                    j.salary_currency = "USD"
                elif re.search(r"£|GBP", whole, re.I):
                    j.salary_currency = "GBP"
                elif re.search(r"CHF|Fr\.|SFr\.", whole, re.I):
                    j.salary_currency = "CHF"
                elif re.search(r"SEK", whole, re.I):
                    j.salary_currency = "SEK"
            if period and not j.salary_period:
                p = period.lower()
                if "month" in p or "monat" in p or "mois" in p:
                    j.salary_period = "month"
                elif "hour" in p or "hr" in p or "stunde" in p or "heure" in p or "ora" in p:
                    j.salary_period = "hour"
                else:
                    j.salary_period = "year"

    if VISA_RX.search(text) and not j.visa_sponsorship:
        j.visa_sponsorship = "yes"
    if RELOC_RX.search(text) and not j.relocation:
        j.relocation = "yes"
    if EQUITY_RX.search(text) and not j.equity:
        j.equity = "yes"

    emails = _uniq([e for e in EMAIL_RX.findall(text) if not re.search(r"noreply|no-reply|example\.com", e, re.I)])
    if emails:
        recruiter = next((e for e in emails if re.search(r"jobs?|career|talent|recruit|hr|hiring|people", e, re.I)), "")
        if recruiter and not j.recruiter_email:
            j.recruiter_email = recruiter
        if not j.application_email:
            j.application_email = recruiter or emails[0]
    phones = _uniq([p for p in PHONE_RX.findall(text) if len(re.sub(r"\D", "", p)) >= 7])
    if phones and not j.application_phone:
        j.application_phone = phones[0]

    j.responsibilities = j.responsibilities or _section_text(soup, re.compile(r"responsibilit|aufgaben|what\s+you'?ll\s+do|deine\s+aufgaben|tasks", re.I))
    j.requirements = j.requirements or _section_text(soup, re.compile(r"requirement|qualific|profil|what\s+you'?ll\s+need|dein\s+profil|skills?\s*we|must[-\s]?have", re.I))
    j.benefits = j.benefits or _section_text(soup, re.compile(r"benefits?|perks|wir\s+bieten|what\s+we\s+offer|deine\s+vorteile", re.I))
    if not j.qualifications:
        j.qualifications = j.requirements

    # Skills: dedupe of tech_stack + leading bullet phrases from requirements section
    if not j.skills:
        skill_parts: List[str] = []
        if j.tech_stack:
            skill_parts.extend([s.strip() for s in j.tech_stack.split(",") if s.strip()])
        if j.requirements:
            for bullet in j.requirements.split(" • "):
                bullet = bullet.strip()
                if 4 < len(bullet) < 80 and not bullet[0].islower():
                    skill_parts.append(bullet)
        if skill_parts:
            seen_s = set()
            uniq_skills = []
            for s in skill_parts:
                k = s.lower()
                if k in seen_s:
                    continue
                seen_s.add(k)
                uniq_skills.append(s)
            j.skills = ", ".join(uniq_skills[:20])

    # Education / experience / hiring manager — fallback regex on full body if not set yet
    if not (j.education_required and j.experience_years and j.hiring_manager):
        body_text = (soup.find("body") or soup).get_text(" ", strip=True)[:50000]
        tmp: Dict[str, str] = {}
        if j.education_required: tmp["education_required"] = j.education_required
        if j.experience_years: tmp["experience_years"] = j.experience_years
        if j.hiring_manager: tmp["hiring_manager"] = j.hiring_manager
        _extract_education_experience(body_text, tmp)
        if tmp.get("education_required") and not j.education_required:
            j.education_required = tmp["education_required"]
        if tmp.get("experience_years") and not j.experience_years:
            j.experience_years = tmp["experience_years"]
        if tmp.get("hiring_manager") and not j.hiring_manager:
            j.hiring_manager = tmp["hiring_manager"]


def _section_text(soup: BeautifulSoup, header_rx: re.Pattern) -> str:
    headings = soup.find_all(["h1", "h2", "h3", "h4", "strong", "b"])
    stop = {"h1", "h2", "h3", "h4", "aside", "section", "footer", "nav", "form"}
    for h in headings:
        if not header_rx.search(h.get_text(" ", strip=True) or ""):
            continue
        buf: List[str] = []
        n = h.next_sibling
        while n is not None:
            if hasattr(n, "name") and n.name and n.name.lower() in stop:
                break
            if hasattr(n, "find_all"):
                items = n.find_all(["li", "p"])
                if items:
                    buf.extend(it.get_text(" ", strip=True) for it in items)
                else:
                    buf.append(n.get_text(" ", strip=True) if hasattr(n, "get_text") else str(n))
            elif isinstance(n, str):
                buf.append(n.strip())
            n = n.next_sibling
            if sum(len(s) for s in buf) > 4000:
                break
        joined = " • ".join([s for s in buf if s])
        return _normalize(joined)
    return ""


# ---------------------------------------------------------------------------
# Recruiter
# ---------------------------------------------------------------------------

RECRUITER_TITLE_RX = re.compile(r"(recruiter|talent\s+(?:acquisition|partner|manager)|hr\s+manager|people\s+(?:partner|operations)|hiring\s+manager|sourcer)", re.I)
PERSON_NAME_RX = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+){1,3})\b")
LINKEDIN_RX = re.compile(r"https?://(?:[a-z]+\.)?linkedin\.com/in/[A-Za-z0-9-_%]+", re.I)


def _apply_recruiter(j: JobListing, soup: BeautifulSoup) -> None:
    candidates = soup.find_all(["section", "aside", "footer", "div", "article"])
    for el in candidates:
        text = el.get_text(" ", strip=True)
        if len(text) > 1000:
            text = text[:1000]
        if not RECRUITER_TITLE_RX.search(text):
            continue
        m = RECRUITER_TITLE_RX.search(text)
        if m and not j.recruiter_title:
            j.recruiter_title = m.group(0)
        stripped = re.sub(r"^(your|our|meet)\s+(talent|recruiting|hiring)\s+(partner|team|contact)", "", text, flags=re.I)
        stripped = RECRUITER_TITLE_RX.sub("", stripped)
        nm = PERSON_NAME_RX.search(stripped)
        if nm and not j.recruiter_name:
            j.recruiter_name = nm.group(1)
        li = el.find("a", href=re.compile(r"linkedin\.com/in/", re.I))
        if li and not j.recruiter_linkedin:
            href = li.get("href", "")
            mm = LINKEDIN_RX.search(href)
            if mm:
                j.recruiter_linkedin = mm.group(0)
        mailto = el.find("a", href=re.compile(r"^mailto:", re.I))
        if mailto and not j.recruiter_email:
            j.recruiter_email = mailto.get("href", "")[7:].split("?")[0]
        tel = el.find("a", href=re.compile(r"^tel:", re.I))
        if tel and not j.recruiter_phone:
            j.recruiter_phone = tel.get("href", "")[4:]
        if j.recruiter_email or j.recruiter_name or j.recruiter_linkedin:
            break


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------

NON_JOB_PATH_RX = re.compile(
    r"/(account|profile|recommendations?|saved|applications?|login|signin|signup|register|home|feed|search\?|results\??|index)(/|$|\?)",
    re.I,
)
APPLY_HOST_RX = re.compile(
    r"(refline\.ch|smartrecruiters\.com|greenhouse\.io|lever\.co|workable\.com|workday|myworkdayjobs\.com|"
    r"ashbyhq\.com|teamtailor\.com|recruitee\.com|personio\.|bamboohr\.com|jobvite\.com)",
    re.I,
)
APPLY_TEXT_RX = re.compile(r"^(apply|apply now|jetzt bewerben|bewerben|application|submit application|postuler|candidater)\b", re.I)
APPLY_PATH_RX = re.compile(r"/(apply|application|bewerb|postuler|candidat|submission)", re.I)


def _is_junk_url(u: str) -> bool:
    if not u:
        return True
    try:
        p = urlparse(u)
        if p.path == "" or p.path == "/":
            return True
        if NON_JOB_PATH_RX.search(p.path):
            return True
        return False
    except Exception:
        return True


def _finalize(j: JobListing, html: str, page_url: str, soup: BeautifulSoup, ld: Optional[Dict[str, Any]]) -> None:
    parsed = urlparse(page_url)
    j.source_domain = j.source_domain or parsed.netloc

    # Canonical job URL: JSON-LD url > <link rel=canonical> > og:url > page_url
    cands: List[str] = []
    if j.job_url:
        cands.append(j.job_url)
    canon = soup.find("link", rel=re.compile(r"^canonical$", re.I))
    if canon and canon.get("href"):
        cands.append(urljoin(page_url, canon.get("href")))
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and og.get("content"):
        cands.append(og["content"].strip())
    cands.append(page_url)
    for c in cands:
        if c and not _is_junk_url(c):
            j.job_url = c
            break
    if not j.job_url:
        j.job_url = page_url

    # Apply URL: prefer external ATS host
    if not j.apply_url or _is_junk_url(j.apply_url):
        better = _find_apply_url(soup, page_url)
        if better:
            j.apply_url = better
    if not j.apply_url:
        j.apply_url = j.job_url

    # Description fallback
    if not j.description or len(j.description) < 50:
        for sel in ["main", "article", "[role='main']"]:
            el = soup.select_one(sel)
            if el:
                txt = el.get_text(" ", strip=True)
                if len(txt) > 50:
                    j.description = txt[:10000]
                    break
        if not j.description or len(j.description) < 50:
            for sel in ["[class*='description']", "[class*='job-detail']", "[class*='posting']", "[class*='vacancy']"]:
                el = soup.select_one(sel)
                if el:
                    txt = el.get_text(" ", strip=True)
                    if len(txt) > 50:
                        j.description = txt[:10000]
                        break

    # Title fallback
    if not j.title:
        h1 = soup.find("h1")
        if h1:
            j.title = h1.get_text(" ", strip=True)
        else:
            t = soup.find("title")
            if t:
                j.title = t.get_text(" ", strip=True)


def _find_apply_url(soup: BeautifulSoup, page_url: str) -> str:
    in_site = ""
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        try:
            absolute = urljoin(page_url, href)
        except Exception:
            continue
        text = a.get_text(" ", strip=True)
        if APPLY_HOST_RX.search(absolute):
            return absolute
        match_text = APPLY_TEXT_RX.search(text or "")
        match_path = APPLY_PATH_RX.search(absolute or "")
        if (match_text or match_path) and not in_site and not _is_junk_url(absolute):
            in_site = absolute
    return in_site


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_amount(s: str) -> str:
    """Strip thousands separators (apostrophe, space, narrow-no-break-space, no-break-space,
    period) from a salary number. Keeps decimal point if it's clearly the cents separator
    (i.e. exactly two digits after, OR a dash for "no cents" CH style)."""
    if not s:
        return ""
    cleaned = s.strip()
    # Drop trailing CH "no cents" markers
    cleaned = re.sub(r"[.,]\s*[–—\-]\s*$", "", cleaned)
    # Detect decimal: last separator with 1-2 digits after
    m = re.match(r"^(\d{1,3}(?:[.,'\s  ]\d{3})*)([.,]\d{1,2})?$", cleaned)
    if m:
        whole = re.sub(r"[.,'\s  ]", "", m.group(1))
        return whole
    return re.sub(r"[.,'\s  –—\-]", "", cleaned)


def _str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x).strip() for x in v if x)
    return str(v).strip()


def _html_to_text(html_text: Any) -> str:
    if not html_text:
        return ""
    if not isinstance(html_text, str):
        html_text = str(html_text)
    soup = BeautifulSoup(html_text, "lxml")
    return _normalize(soup.get_text(" ", strip=True))


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _uniq(seq: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for s in seq:
        s = (s or "").strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def _is_likely_job_page(page_url: str, soup: BeautifulSoup) -> bool:
    path = (urlparse(page_url).path or "").lower()
    h1 = soup.find("h1")
    h1_text = h1.get_text(" ", strip=True).lower() if h1 else ""
    generic = {"careers", "jobs", "open positions", "openings", "job openings"}
    if h1_text in generic:
        return False
    if any(tok in path for tok in ["/job", "/jobs", "/career", "/careers", "/positions", "/opening", "/vacanc", "/stellen", "/joboffer", "/detail/"]):
        return True
    text = soup.get_text(" ", strip=True).lower()
    signals = ["apply", "responsibilities", "requirements", "qualifications", "bewerben", "aufgaben", "anforderung"]
    return sum(1 for s in signals if s in text) >= 2
