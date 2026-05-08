"""Auto-discovery for ATS endpoints and sitemaps.

Given a careers/company URL, probe known ATS hosts and structured feeds and
return a list of (adapter_name, canonical_url) hints.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .http import HttpClient


ATS_HOST_PATTERNS = [
    (re.compile(r"boards\.greenhouse\.io"), "greenhouse"),
    (re.compile(r"boards-api\.greenhouse\.io"), "greenhouse"),
    (re.compile(r"jobs\.lever\.co"), "lever"),
    (re.compile(r"\.smartrecruiters\.com"), "smartrecruiters"),
    (re.compile(r"smartrecruiters\.com"), "smartrecruiters"),
    (re.compile(r"\.jobs\.personio\.(de|com)"), "personio"),
    (re.compile(r"jobs\.ashbyhq\.com"), "ashby"),
    (re.compile(r"\.recruitee\.com"), "recruitee"),
    (re.compile(r"\.workable\.com"), "workable"),
    (re.compile(r"apply\.workable\.com"), "workable"),
    (re.compile(r"\.wd\d+\.myworkdayjobs\.com"), "workday"),
    (re.compile(r"jobs\.jobvite\.com"), "jobvite"),
    (re.compile(r"jobs\.eu\.lever\.co"), "lever"),
]

ATS_PATH_PATTERNS = [
    (re.compile(r"/jobs/", re.I), "generic"),
    (re.compile(r"/career", re.I), "generic"),
]


def detect_from_url(url: str) -> Optional[str]:
    host = urlparse(url).netloc.lower()
    for rx, name in ATS_HOST_PATTERNS:
        if rx.search(host):
            return name
    return None


def discover_from_homepage(url: str, http: HttpClient) -> List[Tuple[str, str]]:
    """Fetch homepage HTML, find linked ATS URLs."""
    result = http.get(url, allow_404=True)
    if result is None:
        return []
    final_url, html = result
    found: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    soup = BeautifulSoup(html, "lxml")
    # Look at links and iframes for ATS hosts
    for tag in soup.find_all(["a", "iframe", "script"], href=True):
        href = tag.get("href", "")
        if not href:
            continue
        absolute = urljoin(final_url, href)
        adapter = detect_from_url(absolute)
        if adapter and absolute not in seen:
            seen.add(absolute)
            found.append((adapter, absolute))
    for tag in soup.find_all("iframe", src=True):
        absolute = urljoin(final_url, tag.get("src", ""))
        adapter = detect_from_url(absolute)
        if adapter and absolute not in seen:
            seen.add(absolute)
            found.append((adapter, absolute))
    for tag in soup.find_all("script", src=True):
        absolute = urljoin(final_url, tag.get("src", ""))
        adapter = detect_from_url(absolute)
        if adapter and absolute not in seen:
            seen.add(absolute)
            found.append((adapter, absolute))
    # Inline script content (Greenhouse embed grafted via JS)
    text_blob = " ".join(s.string or "" for s in soup.find_all("script") if s.string)
    for rx, name in ATS_HOST_PATTERNS:
        for m in rx.finditer(text_blob):
            ctx = text_blob[max(0, m.start() - 60): m.end() + 100]
            url_m = re.search(r"https?://[^\s\"'<>]+", ctx)
            if url_m and url_m.group(0) not in seen:
                seen.add(url_m.group(0))
                found.append((name, url_m.group(0)))
    return found


def fetch_sitemap_urls(base_url: str, http: HttpClient, max_urls: int = 5000) -> List[str]:
    """Walk sitemap.xml (and sitemap index) and return URLs."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        f"{root}/sitemap.xml",
        f"{root}/sitemap_index.xml",
        f"{root}/sitemaps.xml",
        f"{root}/jobs-sitemap.xml",
        f"{root}/careers/sitemap.xml",
    ]
    # Robots.txt → sitemap header
    robots = http.get(f"{root}/robots.txt", allow_404=True)
    if robots:
        for line in robots[1].splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                sm = line.split(":", 1)[1].strip()
                if sm not in candidates:
                    candidates.append(sm)
    out: List[str] = []
    seen: Set[str] = set()
    queue = list(candidates)
    while queue and len(out) < max_urls:
        sm_url = queue.pop(0)
        if sm_url in seen:
            continue
        seen.add(sm_url)
        result = http.get(sm_url, allow_404=True, headers={"Accept": "application/xml,text/xml"})
        if result is None:
            continue
        _, body = result
        try:
            root_xml = ET.fromstring(body)
        except ET.ParseError:
            continue
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        # Sitemap index
        for sm in root_xml.findall(".//sm:sitemap/sm:loc", ns):
            if sm.text and sm.text not in seen:
                queue.append(sm.text.strip())
        # URL set
        for loc in root_xml.findall(".//sm:url/sm:loc", ns):
            if loc.text:
                out.append(loc.text.strip())
                if len(out) >= max_urls:
                    break
    return out


def filter_job_urls(urls: Iterable[str]) -> List[str]:
    keepers = []
    pat = re.compile(r"/(jobs?|careers?|stellen|stelle|positions?|opening|vacanc|stellenangebote)/", re.I)
    for u in urls:
        if pat.search(u):
            keepers.append(u)
    return keepers


def probe_personio(slug: str, http: HttpClient) -> Optional[str]:
    for tld in ("de", "com"):
        url = f"https://{slug}.jobs.personio.{tld}/xml"
        result = http.get(url, allow_404=True, headers={"Accept": "application/xml"})
        if result and result[1].strip().startswith("<?xml"):
            if "<position" in result[1] or "<workflow" in result[1]:
                return f"https://{slug}.jobs.personio.{tld}"
    return None


def probe_workable(slug: str, http: HttpClient) -> Optional[str]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    payload = http.get_json(url)
    if payload and isinstance(payload, dict) and payload.get("jobs"):
        return f"https://apply.workable.com/{slug}"
    return None


def probe_recruitee(slug: str, http: HttpClient) -> Optional[str]:
    """Recruitee public host slug; returns URL with valid offers feed."""
    candidates = [slug, slug.replace("-", ""), slug.replace("_", "-")]
    for c in candidates:
        for path in ("/api/offers/", "/api/offers"):
            url = f"https://{c}.recruitee.com{path}"
            payload = http.get_json(url)
            if payload and isinstance(payload, dict) and payload.get("offers") is not None:
                return f"https://{c}.recruitee.com"
    return None


def probe_smartrecruiters(slug: str, http: HttpClient) -> Optional[str]:
    """Try slug, slug+Group, slug+SE/AG/GmbH and case variants."""
    suffixes = ["", "Group", "SE", "AG", "GmbH", "Inc"]
    bases = {slug, slug.capitalize(), slug.upper(), slug.lower()}
    seen: Set[str] = set()
    for base in bases:
        for suf in suffixes:
            cand = f"{base}{suf}"
            if cand in seen:
                continue
            seen.add(cand)
            url = f"https://api.smartrecruiters.com/v1/companies/{cand}/postings?limit=1"
            payload = http.get_json(url)
            if payload and payload.get("totalFound", 0) > 0:
                return f"https://api.smartrecruiters.com/v1/companies/{cand}"
    return None


def probe_ashby(slug: str, http: HttpClient) -> Optional[str]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    payload = http.get_json(url)
    if payload and payload.get("jobs"):
        return f"https://jobs.ashbyhq.com/{slug}"
    return None


def probe_greenhouse(slug: str, http: HttpClient) -> Optional[str]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    payload = http.get_json(url)
    if payload and payload.get("jobs"):
        return f"https://boards.greenhouse.io/{slug}"
    return None


def probe_lever(slug: str, http: HttpClient) -> Optional[str]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    payload = http.get_json(url)
    if isinstance(payload, list) and payload:
        return f"https://jobs.lever.co/{slug}"
    return None


PROBES = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "personio": probe_personio,
    "workable": probe_workable,
    "recruitee": probe_recruitee,
    "smartrecruiters": probe_smartrecruiters,
    "ashby": probe_ashby,
}


def auto_discover(name_or_url: str, http: HttpClient) -> List[Tuple[str, str]]:
    """Take a company name OR URL and return [(adapter, canonical_url), ...].

    1. If URL: detect adapter; if generic, fetch homepage and find ATS links.
    2. If bare name: try each ATS probe with that name as slug.
    """
    discovered: List[Tuple[str, str]] = []
    seen: Set[str] = set()

    if name_or_url.startswith("http://") or name_or_url.startswith("https://"):
        adapter = detect_from_url(name_or_url)
        if adapter and adapter != "generic":
            discovered.append((adapter, name_or_url))
            seen.add(name_or_url)
        else:
            for ad, link in discover_from_homepage(name_or_url, http):
                if link not in seen:
                    seen.add(link)
                    discovered.append((ad, link))
        return discovered

    slug = re.sub(r"[^a-z0-9-]", "", name_or_url.lower().replace(" ", "-"))
    for ad, fn in PROBES.items():
        try:
            url = fn(slug, http)
        except Exception as exc:  # pragma: no cover
            logging.debug("probe %s failed: %s", ad, exc)
            continue
        if url and url not in seen:
            seen.add(url)
            discovered.append((ad, url))
    return discovered
