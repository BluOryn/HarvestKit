import logging
from typing import List, Set
from urllib.parse import urlparse

from ..config import RunConfig, TargetConfig
from ..crawl import Crawler
from ..discovery import auto_discover, fetch_sitemap_urls, filter_job_urls
from ..extract import extract_apply_url_from_html, extract_job_postings
from ..http import HttpClient
from ..models import JobListing
from ..normalize import canonicalize_url
from .base import BaseAdapter


class GenericAdapter(BaseAdapter):
    def fetch_jobs(
        self,
        target: TargetConfig,
        run_config: RunConfig,
        http: HttpClient,
    ) -> List[JobListing]:
        listings: List[JobListing] = []
        seen_urls: Set[str] = set()

        # Step 1: try ATS auto-discovery from homepage
        for adapter_name, ats_url in auto_discover(target.url, http):
            from . import ADAPTERS  # local import to avoid circular
            adapter = ADAPTERS.get(adapter_name)
            if adapter is None or adapter is self:
                continue
            sub_target = TargetConfig(name=f"{target.name}-{adapter_name}", url=ats_url, adapter=adapter_name)
            try:
                jobs = adapter.fetch_jobs(sub_target, run_config, http)
            except Exception as exc:
                logging.debug("generic delegated %s fail: %s", adapter_name, exc)
                continue
            for j in jobs:
                key = canonicalize_url(j.job_url) or canonicalize_url(j.apply_url)
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                listings.append(j)

        if listings:
            return listings

        # Step 2: sitemap walk
        sitemap_urls = fetch_sitemap_urls(target.url, http)
        job_urls = filter_job_urls(sitemap_urls)
        if job_urls:
            logging.info("generic %s: sitemap → %d job URLs", target.name, len(job_urls))
            host = urlparse(target.url).netloc
            for url in job_urls[: run_config.max_pages]:
                page_result = http.get(url, allow_404=True)
                if page_result is None:
                    continue
                final_url, html = page_result
                postings = extract_job_postings(html, final_url)
                for job in postings:
                    if not job.apply_url:
                        apply_url = extract_apply_url_from_html(html, final_url)
                        job.apply_url = apply_url or final_url
                    if not job.job_url:
                        job.job_url = final_url
                    job.apply_url = canonicalize_url(job.apply_url)
                    job.job_url = canonicalize_url(job.job_url)
                    if job.job_url in seen_urls:
                        continue
                    seen_urls.add(job.job_url)
                    listings.append(job)
            if listings:
                return listings

        # Step 3: BFS crawl, escalate to Playwright if first pass yields nothing
        allow_domains = run_config.allow_domains or [urlparse(target.url).netloc]
        target_pw = target.use_playwright
        for use_pw in (False if target_pw is None else target_pw, True):
            if listings:
                break
            crawler = Crawler(
                http=http,
                max_pages=run_config.max_pages,
                max_depth=run_config.max_depth,
                allow_domains=allow_domains,
                use_playwright=bool(use_pw),
            )
            for page in crawler.crawl(target.url):
                jobs = extract_job_postings(page.html, page.url)
                for job in jobs:
                    if not job.apply_url:
                        apply_url = extract_apply_url_from_html(page.html, page.url)
                        job.apply_url = apply_url or page.url
                    if not job.job_url:
                        job.job_url = page.url
                    job.apply_url = canonicalize_url(job.apply_url)
                    job.job_url = canonicalize_url(job.job_url)
                    if job.job_url in seen_urls:
                        continue
                    seen_urls.add(job.job_url)
                    listings.append(job)
            if not run_config.use_playwright:
                break  # don't escalate if the user explicitly disabled it
        return listings
