from typing import List

import requests

from ..config import SlackExportConfig
from ..models import JobListing


def export_slack(jobs: List[JobListing], cfg: SlackExportConfig) -> None:
    if not cfg.webhook_url:
        return
    count = len(jobs)
    preview = jobs[: cfg.max_items]
    lines = [f"Scrape complete. Jobs: {count}"]
    for job in preview:
        lines.append(f"- {job.title} | {job.company} | {job.job_url}")
    payload = {"text": "\n".join(lines)}
    requests.post(cfg.webhook_url, json=payload, timeout=20)
