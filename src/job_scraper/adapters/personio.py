import logging
import re
from typing import List, Optional
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from ..config import RunConfig, TargetConfig
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class PersonioAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        bases = self._resolve_bases(target.url, http)
        if not bases:
            return []
        for base in bases:
            jobs = self._fetch_feed(base, http)
            if jobs:
                return jobs
        return []

    def _resolve_bases(self, url: str, http: HttpClient) -> List[str]:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if not host:
            return []
        scheme = parsed.scheme or "https"
        m = re.match(r"^([a-z0-9-]+)\.jobs\.personio\.(de|com)$", host)
        if m:
            slug = m.group(1)
            return [f"{scheme}://{slug}.jobs.personio.de", f"{scheme}://{slug}.jobs.personio.com"]
        # User passed a bare slug or arbitrary URL — try slug as last path segment
        slug = host.split(".")[0]
        return [f"https://{slug}.jobs.personio.de", f"https://{slug}.jobs.personio.com"]

    def _fetch_feed(self, base: str, http: HttpClient) -> List[JobListing]:
        feed_url = f"{base.rstrip('/')}/xml"
        result = http.get(feed_url, headers={"Accept": "application/xml,text/xml,*/*"}, allow_404=True)
        if result is None:
            return []
        _, body = result
        if not body or not body.strip().startswith("<?xml"):
            return []
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            logging.debug("personio xml parse fail %s: %s", feed_url, exc)
            return []
        slug = urlparse(base).netloc.split(".")[0]
        listings: List[JobListing] = []
        for position in root.iter("position"):
            title = (position.findtext("name") or "").strip()
            office = (position.findtext("office") or "").strip()
            department = (position.findtext("department") or "").strip()
            employment_type = (position.findtext("employmentType") or "").strip()
            schedule = (position.findtext("schedule") or "").strip()
            seniority = (position.findtext("seniority") or "").strip()
            recruiting_category = (position.findtext("recruitingCategory") or "").strip()
            occupation = (position.findtext("occupation") or "").strip()
            occupation_category = (position.findtext("occupationCategory") or "").strip()
            created_at = (position.findtext("createdAt") or "").strip()
            job_descriptions_node = position.find("jobDescriptions")
            description_parts: List[str] = []
            if job_descriptions_node is not None:
                for jd in job_descriptions_node.findall("jobDescription"):
                    name = (jd.findtext("name") or "").strip()
                    value = (jd.findtext("value") or "").strip()
                    if value:
                        text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
                        description_parts.append(f"{name}: {text}" if name else text)
            job_id = (position.findtext("id") or "").strip()
            position_url = ""
            url_node = position.find("url")
            if url_node is not None and url_node.text:
                position_url = url_node.text.strip()
            if not position_url and job_id:
                position_url = f"{base.rstrip('/')}/job/{job_id}"
            description = " ".join(description_parts)
            location = office
            tags = " ".join([department, recruiting_category, occupation, occupation_category, seniority]).strip()
            full_desc = (description + " " + tags).strip()
            listings.append(
                JobListing(
                    title=title,
                    company=slug,
                    location=location,
                    remote="remote" if re.search(r"\bremote\b", schedule + " " + office, re.I) else "",
                    employment_type=employment_type or schedule,
                    posted_date=created_at,
                    description=full_desc,
                    apply_url=canonicalize_url(position_url),
                    job_url=canonicalize_url(position_url),
                )
            )
        if listings:
            logging.info("personio %s: %d jobs", slug, len(listings))
        return listings
