"""Auto-discovery for ATS endpoints and sitemaps.

Given a careers/company URL, probe known ATS hosts and structured feeds and
return a list of (adapter_name, canonical_url) hints.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import safe_xml
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


def detect_from_url(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    for rx, name in ATS_HOST_PATTERNS:
        if rx.search(host):
            return name
    return None


def discover_from_homepage(url: str, http: HttpClient) -> list[tuple[str, str]]:
    """Fetch homepage HTML, find linked ATS URLs."""
    result = http.get(url, allow_404=True)
    if result is None:
        return []
    final_url, html = result
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "lxml")

    def consider(raw: str) -> None:
        if not raw:
            return
        try:
            absolute = urljoin(final_url, raw.strip())
        except ValueError:
            return
        adapter = detect_from_url(absolute)
        if adapter and absolute not in seen:
            seen.add(absolute)
            found.append((adapter, absolute))

    # Anchors carry href; iframes and scripts carry src. The original code asked
    # for href=True on all three, so embedded ATS iframes were never seen here.
    for tag in soup.find_all("a", href=True):
        consider(tag.get("href", ""))
    for tag in soup.find_all(["iframe", "script", "link"], src=True):
        consider(tag.get("src", ""))
    for tag in soup.find_all("link", href=True):
        consider(tag.get("href", ""))

    # Inline script content (Greenhouse embed grafted via JS)
    text_blob = " ".join(s.string or "" for s in soup.find_all("script") if s.string)
    for rx, name in ATS_HOST_PATTERNS:
        for m in rx.finditer(text_blob):
            ctx = text_blob[max(0, m.start() - 60) : m.end() + 100]
            url_m = re.search(r"https?://[^\s\"'<>]+", ctx)
            if url_m and url_m.group(0) not in seen:
                seen.add(url_m.group(0))
                found.append((name, url_m.group(0)))
    return found


def fetch_sitemap_urls(
    base_url: str, http: HttpClient, max_urls: int = 5000, max_sitemaps: int = 100
) -> list[str]:
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
    # Robots.txt → Sitemap: directives
    robots = http.get(f"{root}/robots.txt", allow_404=True)
    if robots:
        for line in robots[1].splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                sm = line.split(":", 1)[1].strip()
                if sm and sm not in candidates:
                    candidates.append(sm)

    out: list[str] = []
    seen_urls: set[str] = set()
    seen_sitemaps: set[str] = set()
    queue = deque(candidates)
    # A sitemap index that points back at itself (or a cycle between two) would
    # otherwise spin forever; cap the number of sitemap documents fetched.
    while queue and len(out) < max_urls and len(seen_sitemaps) < max_sitemaps:
        sm_url = queue.popleft()
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)
        result = http.get(sm_url, allow_404=True, headers={"Accept": "application/xml,text/xml"})
        if result is None:
            continue
        _, body = result
        root_xml = safe_xml.fromstring(body, source=sm_url)
        if root_xml is None:
            continue
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        # Sitemap index
        for sm in root_xml.findall(".//sm:sitemap/sm:loc", ns):
            child = (sm.text or "").strip()
            if child and child not in seen_sitemaps:
                queue.append(child)
        # URL set
        for loc in root_xml.findall(".//sm:url/sm:loc", ns):
            url = (loc.text or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            out.append(url)
            if len(out) >= max_urls:
                break
    return out


def filter_job_urls(urls: Iterable[str]) -> list[str]:
    keepers = []
    pat = re.compile(r"/(jobs?|careers?|stellen|stelle|positions?|opening|vacanc|stellenangebote)/", re.I)
    for u in urls:
        if pat.search(u):
            keepers.append(u)
    return keepers


def probe_personio(slug: str, http: HttpClient) -> str | None:
    for tld in ("de", "com"):
        url = f"https://{slug}.jobs.personio.{tld}/xml"
        result = http.get(url, allow_404=True, headers={"Accept": "application/xml"})
        if not result:
            continue
        body = result[1].strip()
        if body.startswith("<?xml") and ("<position" in body or "<workflow" in body):
            return f"https://{slug}.jobs.personio.{tld}"
    return None


def probe_workable(slug: str, http: HttpClient) -> str | None:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    payload = http.get_json(url)
    if isinstance(payload, dict) and payload.get("jobs"):
        return f"https://apply.workable.com/{slug}"
    return None


def probe_recruitee(slug: str, http: HttpClient) -> str | None:
    """Recruitee public host slug; returns URL with valid offers feed."""
    candidates = [slug, slug.replace("-", ""), slug.replace("_", "-")]
    for c in dict.fromkeys(candidates):
        for path in ("/api/offers/", "/api/offers"):
            url = f"https://{c}.recruitee.com{path}"
            payload = http.get_json(url)
            if isinstance(payload, dict) and payload.get("offers") is not None:
                return f"https://{c}.recruitee.com"
    return None


def probe_smartrecruiters(slug: str, http: HttpClient) -> str | None:
    """Try slug, slug+Group, slug+SE/AG/GmbH and case variants."""
    suffixes = ["", "Group", "SE", "AG", "GmbH", "Inc"]
    bases = {slug, slug.capitalize(), slug.upper(), slug.lower()}
    seen: set[str] = set()
    for base in bases:
        for suf in suffixes:
            cand = f"{base}{suf}"
            if cand in seen:
                continue
            seen.add(cand)
            url = f"https://api.smartrecruiters.com/v1/companies/{cand}/postings?limit=1"
            payload = http.get_json(url)
            if isinstance(payload, dict) and (payload.get("totalFound") or 0) > 0:
                return f"https://api.smartrecruiters.com/v1/companies/{cand}"
    return None


def probe_ashby(slug: str, http: HttpClient) -> str | None:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    payload = http.get_json(url)
    if isinstance(payload, dict) and payload.get("jobs"):
        return f"https://jobs.ashbyhq.com/{slug}"
    return None


def probe_greenhouse(slug: str, http: HttpClient) -> str | None:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    payload = http.get_json(url)
    if isinstance(payload, dict) and payload.get("jobs"):
        return f"https://boards.greenhouse.io/{slug}"
    return None


def probe_lever(slug: str, http: HttpClient) -> str | None:
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


def auto_discover(name_or_url: str, http: HttpClient) -> list[tuple[str, str]]:
    """Take a company name OR URL and return [(adapter, canonical_url), ...].

    1. If URL: detect adapter; if generic, fetch homepage and find ATS links.
    2. If bare name: try each ATS probe with that name as slug.
    """
    discovered: list[tuple[str, str]] = []
    seen: set[str] = set()

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
