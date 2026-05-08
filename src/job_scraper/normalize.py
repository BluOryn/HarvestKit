import hashlib
import re
from typing import Iterable, List
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import JobListing

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}


def normalize_text(text: str) -> str:
    text = text or ""
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    normalized = parsed._replace(fragment="", query=urlencode(query, doseq=True))
    return urlunparse(normalized).rstrip("/")


def job_fingerprint(job: JobListing) -> str:
    key_parts = [
        canonicalize_url(job.apply_url) or canonicalize_url(job.job_url),
        normalize_text(job.title),
        normalize_text(job.company),
        normalize_text(job.location),
    ]
    raw = "|".join(key_parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def text_blob(job: JobListing) -> str:
    return " ".join(
        [
            job.title or "",
            job.company or "",
            job.location or "",
            job.description or "",
        ]
    ).lower()


def match_keywords(job: JobListing, keywords: Iterable[str]) -> List[str]:
    matches: List[str] = []
    if not keywords:
        return matches
    blob = text_blob(job)
    for keyword in keywords:
        keyword = (keyword or "").strip()
        if not keyword:
            continue
        kw_lower = keyword.lower()
        # Word-boundary match for short tokens to avoid "ai" matching "maintain".
        if len(kw_lower) <= 3:
            if re.search(rf"\b{re.escape(kw_lower)}\b", blob):
                matches.append(keyword)
        elif kw_lower in blob:
            matches.append(keyword)
    return matches


def location_blob(job: JobListing) -> str:
    return " ".join([job.location or "", job.remote or "", job.title or ""]).lower()


def match_location(job: JobListing, includes: Iterable[str], excludes: Iterable[str], allow_remote: bool) -> bool:
    """Return True if job location passes filter.

    - includes: any keyword (city/country/code) must appear in location/title.
    - excludes: any match in EXCLUDES → reject (unless overridden by include match).
    - allow_remote: remote jobs pass if remote field set.
    """
    inc = [k.strip().lower() for k in includes if k and k.strip()]
    exc = [k.strip().lower() for k in excludes if k and k.strip()]
    blob = location_blob(job)
    if allow_remote and (job.remote or "").lower() == "remote" and not blob.strip():
        return True
    inc_hit = any(re.search(rf"\b{re.escape(k)}\b", blob) for k in inc) if inc else True
    exc_hit = any(re.search(rf"\b{re.escape(k)}\b", blob) for k in exc) if exc else False
    if inc and not inc_hit:
        return False
    if exc_hit and not inc_hit:
        return False
    return True
