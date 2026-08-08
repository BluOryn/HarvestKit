import re
from collections.abc import Iterable

from .models import TRACKING_PARAMS, JobListing, canonicalize_url

__all__ = [
    "TRACKING_PARAMS",
    "canonicalize_url",
    "job_fingerprint",
    "location_blob",
    "match_keywords",
    "match_location",
    "normalize_text",
    "text_blob",
]


def normalize_text(text: str) -> str:
    text = text or ""
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def job_fingerprint(job: JobListing) -> str:
    """Dedupe key for a listing.

    Delegates to `JobListing.fingerprint()` so the key used to drop duplicates is
    the same string that lands in the CSV `id` column. Two implementations here
    previously disagreed (one canonicalised the URL, the other didn't), which let
    the same posting dedupe under one id and export under another.
    """
    return job.fingerprint()


def text_blob(job: JobListing) -> str:
    """Haystack for keyword filtering.

    Includes tech_stack/skills because that is what the shipped configs document
    ("filter on the merged title+description+tech_stack") — without them a
    `python`/`kubernetes` include list silently dropped postings whose stack was
    only detectable from the parsed tech list.
    """
    return " ".join(
        [
            job.title or "",
            job.company or "",
            job.location or "",
            job.department or "",
            job.description or "",
            job.tech_stack or "",
            job.skills or "",
        ]
    ).lower()


def match_keywords(job: JobListing, keywords: Iterable[str]) -> list[str]:
    matches: list[str] = []
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
    return " ".join(
        [job.location or "", job.city or "", job.region or "", job.country or "", job.title or ""]
    ).lower()


def match_location(
    job: JobListing, includes: Iterable[str], excludes: Iterable[str], allow_remote: bool
) -> bool:
    """Return True if a job passes the location filter.

    - includes: at least one keyword (city/region/country/code) must appear in the
      location fields or the title.
    - excludes: a match rejects the job unless an include also matched.
    - allow_remote: a job flagged remote passes the include check even when its
      physical location says nothing about the requested city.

    The remote escape hatch previously tested `not blob.strip()` while `blob`
    itself contained `job.remote` — so the condition could never be true and the
    `allow_remote` flag did nothing. It now keys off `remote_type` alone.
    """
    inc = [k.strip().lower() for k in includes if k and k.strip()]
    exc = [k.strip().lower() for k in excludes if k and k.strip()]
    blob = location_blob(job)
    is_remote = "remote" in (job.remote_type or "").lower()

    inc_hit = any(re.search(rf"\b{re.escape(k)}\b", blob) for k in inc)
    if allow_remote and is_remote:
        inc_hit = True
    exc_hit = any(re.search(rf"\b{re.escape(k)}\b", blob) for k in exc)

    if inc and not inc_hit:
        return False
    # An exclude rejects, unless an explicitly configured include also matched.
    # `inc_hit` used to default to True when no includes were set, which made
    # `locations.exclude` a no-op for every config that only listed excludes.
    return not (exc_hit and not (inc and inc_hit))
