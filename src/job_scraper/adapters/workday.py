import json
import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class WorkdayAdapter(BaseAdapter):
    """Workday CXS public site.
    URL form: https://{tenant}.{wd-host}.com/{lang}/{site}
    Example: https://sap.wd3.myworkdayjobs.com/en-US/SAPCareers
    """

    PAGE_SIZE = 20

    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        parts = self._extract_parts(target.url)
        if not parts:
            return []
        tenant, host, site, lang = parts
        cxs_base = f"https://{tenant}.{host}.com/wday/cxs/{tenant}/{site}/jobs"
        listings: List[JobListing] = []
        offset = 0
        seen_total: Optional[int] = None
        while True:
            body = {"limit": self.PAGE_SIZE, "offset": offset, "searchText": "", "appliedFacets": {}}
            data = self._post_json(http, cxs_base, body)
            if data is None:
                break
            postings = data.get("jobPostings", []) or []
            if not postings:
                break
            for item in postings:
                external_path = item.get("externalPath") or ""
                full_path = f"https://{tenant}.{host}.com{external_path}" if external_path else ""
                detail_api = f"https://{tenant}.{host}.com/wday/cxs/{tenant}/{site}{external_path}" if external_path else ""
                description = ""
                detail = http.get_json(detail_api) if detail_api else None
                if detail:
                    posting = (detail.get("jobPostingInfo") or {})
                    description_html = posting.get("jobDescription") or ""
                    if description_html:
                        description = BeautifulSoup(description_html, "lxml").get_text(" ", strip=True)
                listings.append(
                    JobListing(
                        title=item.get("title") or "",
                        company=tenant,
                        location=item.get("locationsText") or "",
                        employment_type="",
                        posted_date=item.get("postedOn") or "",
                        description=description,
                        apply_url=canonicalize_url(full_path),
                        job_url=canonicalize_url(full_path),
                    )
                )
            seen_total = data.get("total", seen_total)
            offset += self.PAGE_SIZE
            if seen_total is not None and offset >= seen_total:
                break
            if offset > 5000:
                break
        logging.info("workday %s/%s: %d jobs", tenant, site, len(listings))
        return listings

    def _post_json(self, http: HttpClient, url: str, body: dict) -> Optional[dict]:
        return http.post_json(url, body)

    def _extract_parts(self, url: str) -> Optional[Tuple[str, str, str, str]]:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        m = re.match(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
        if not m:
            return None
        tenant = m.group(1)
        wd = m.group(2)
        full_host = f"{wd}.myworkdayjobs"  # used as f"{tenant}.{full_host}.com"
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 2:
            return None
        lang = path_parts[0]
        site = path_parts[1]
        return tenant, full_host, site, lang
