import logging

import requests

from ..config import SlackExportConfig
from ..models import JobListing

# Slack truncates incoming-webhook payloads past ~40 KB; stay well clear.
MAX_PAYLOAD_CHARS = 30000


def export_slack(jobs: list[JobListing], cfg: SlackExportConfig) -> None:
    if not cfg.webhook_url:
        logging.warning("slack: export enabled but webhook_url missing — skipping")
        return

    preview = jobs[: max(0, cfg.max_items)]
    lines = [f"HarvestKit run complete — {len(jobs)} job(s)."]
    for job in preview:
        parts = [p for p in (job.title, job.company, job.location) if p]
        label = " | ".join(parts) or "(untitled)"
        url = job.job_url or job.apply_url
        lines.append(f"• <{url}|{_escape(label)}>" if url else f"• {_escape(label)}")
    if len(jobs) > len(preview):
        lines.append(f"…and {len(jobs) - len(preview)} more.")

    text = "\n".join(lines)[:MAX_PAYLOAD_CHARS]
    try:
        response = requests.post(cfg.webhook_url, json={"text": text}, timeout=20)
    except requests.RequestException as exc:
        logging.warning("slack: post failed (%s)", exc)
        return
    if response.status_code >= 300:
        logging.warning("slack: HTTP %s — %s", response.status_code, (response.text or "")[:200])
    else:
        logging.info("slack: notified (%d jobs)", len(jobs))


def _escape(text: str) -> str:
    """Slack mrkdwn requires these three entities escaped inside link labels."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
