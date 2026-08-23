"""Bundesagentur für Arbeit jobsuche public API.

Public client_id `jobboerse-jobsuche` is used by the official frontend.
We respect a small page size and add a delay.
"""

import logging
from urllib.parse import parse_qs, urlencode, urlparse

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing, _stringify
from ..normalize import canonicalize_url
from .base import BaseAdapter

CLIENT_ID = "jobboerse-jobsuche"

# The service has moved between path versions more than once, and a stale path
# answers 403 "No match found for request" rather than 404 — which reads as an
# auth problem and sends you looking in the wrong place. Probe the known paths
# once per process and remember the one that answers.
BASE_CANDIDATES: list[str] = [
    "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/app/jobs",
    "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs",
    "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v5/app/jobs",
    "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v5/jobs",
]
BASE = BASE_CANDIDATES[0]

_HEADERS = {"X-API-Key": CLIENT_ID, "Accept": "application/json"}

# Resolved once per process: None = not yet probed, "" = every candidate failed.
_resolved_base: str | None = None


def resolve_base(http: HttpClient) -> str:
    """Return the first candidate path that answers with a JSON object."""
    global _resolved_base
    if _resolved_base is not None:
        return _resolved_base
    probe = urlencode({"was": "Softwareentwickler", "page": 1, "size": 1})
    for candidate in BASE_CANDIDATES:
        payload = http.get_json(f"{candidate}?{probe}", headers=_HEADERS)
        if isinstance(payload, dict) and "stellenangebote" in payload:
            logging.info("arbeitsagentur: using %s", candidate)
            _resolved_base = candidate
            return candidate
    # "Moved" and "we are not allowed to ask" look identical from here -- both
    # are an empty result -- and they need opposite fixes. rest.arbeitsagentur.de
    # answers robots.txt with 403, which RFC 9309 defines as the whole host being
    # off-limits, so the honest report is that we were refused rather than that
    # the endpoint vanished.
    if not http.robots_allows(f"{BASE_CANDIDATES[0]}?{probe}"):
        logging.warning(
            "arbeitsagentur: robots.txt on this host disallows the API, so no listings "
            "will be returned. This is a permission boundary, not an outage: the "
            "Bundesagentur publishes the API to registered users. Nothing here will "
            "make it work without that permission."
        )
    else:
        logging.warning(
            "arbeitsagentur: none of the %d known API paths answered — the service has "
            "probably moved again. Tried: %s",
            len(BASE_CANDIDATES),
            ", ".join(BASE_CANDIDATES),
        )
    _resolved_base = ""
    return ""


def _reset_base_cache() -> None:
    """Test seam — the resolved path is process-global."""
    global _resolved_base
    _resolved_base = None


class ArbeitsagenturAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> list[JobListing]:
        params_from_url = self._params(target.url)
        was = params_from_url.get("was", ["Software Engineer"])[0]
        wo = params_from_url.get("wo", ["Deutschland"])[0]
        size = _int_param(params_from_url, "size", 100, lo=1, hi=100)
        umkreis = _int_param(params_from_url, "umkreis", 100, lo=0, hi=200)
        # `max_pages` in the target URL is a HarvestKit knob, not an API param;
        # the config-level run.max_pages caps it so --max-pages actually bites.
        max_pages = _int_param(params_from_url, "max_pages", 20, lo=1, hi=1000)
        if run_config.max_pages:
            max_pages = min(max_pages, run_config.max_pages)

        base = resolve_base(http)
        if not base:
            return []

        listings: list[JobListing] = []
        seen_ids = set()
        for page in range(1, max_pages + 1):
            qs = urlencode({"was": was, "wo": wo, "page": page, "size": size, "umkreis": umkreis})
            url = f"{base}?{qs}"
            payload = http.get_json(url, headers=_HEADERS)
            if not isinstance(payload, dict):
                break
            angebote = payload.get("stellenangebote") or []
            if not angebote:
                break
            for item in angebote:
                if not isinstance(item, dict):
                    continue
                hash_id = item.get("hashId") or item.get("refnr") or ""
                if not hash_id or hash_id in seen_ids:
                    continue
                seen_ids.add(hash_id)
                title = _stringify(item.get("titel") or item.get("beruf"))
                company = _stringify(item.get("arbeitgeber"))
                arbeitsort = item.get("arbeitsort") or {}
                location = ", ".join(
                    p
                    for p in (
                        arbeitsort.get("ort"),
                        arbeitsort.get("region"),
                        arbeitsort.get("land"),
                    )
                    if p
                )
                detail_url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{hash_id}" if hash_id else ""
                listings.append(
                    JobListing(
                        title=title,
                        company=company,
                        location=location,
                        city=_stringify(arbeitsort.get("ort")),
                        region=_stringify(arbeitsort.get("region")),
                        country="Germany",
                        postal_code=_stringify(arbeitsort.get("plz")),
                        # `arbeitszeitmodelle` is a LIST in the API — assigning it
                        # raw produced "['Vollzeit']" in the CSV.
                        employment_type=_stringify(item.get("arbeitszeitmodelle")),
                        posted_date=_stringify(item.get("aktuelleVeroeffentlichungsdatum")),
                        start_date=_stringify(item.get("eintrittsdatum")),
                        description=_stringify(item.get("stellenbeschreibung")),
                        external_id=_stringify(hash_id),
                        requisition_id=_stringify(item.get("refnr")),
                        source_ats="arbeitsagentur",
                        source_domain="www.arbeitsagentur.de",
                        apply_url=canonicalize_url(detail_url),
                        job_url=canonicalize_url(detail_url),
                    )
                )
            total = payload.get("maxErgebnisse") or payload.get("anzahl") or 0
            if total and page * size >= total:
                break
        if listings:
            logging.info("arbeitsagentur %s/%s: %d jobs", was, wo, len(listings))
        return listings

    def _params(self, url: str) -> dict:
        parsed = urlparse(url)
        return parse_qs(parsed.query)


def _int_param(params: dict, key: str, default: int, *, lo: int, hi: int) -> int:
    """Read an int query param, clamped. A non-numeric value used to raise
    ValueError and kill the whole target."""
    raw = (params.get(key) or [""])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        if raw:
            logging.warning("arbeitsagentur: ignoring non-numeric %s=%r", key, raw)
        return default
    return max(lo, min(hi, value))
