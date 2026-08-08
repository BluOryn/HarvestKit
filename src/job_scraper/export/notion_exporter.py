import logging
import time

import requests

from ..config import NotionExportConfig
from ..models import JobListing

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1/pages"
# Notion's documented limit is ~3 requests/second per integration.
MIN_INTERVAL_SECONDS = 0.35
# Notion rejects rich_text/title values longer than 2000 characters outright.
MAX_TEXT_LEN = 2000


def export_notion(jobs: list[JobListing], cfg: NotionExportConfig) -> None:
    if not cfg.token or not cfg.database_id:
        logging.warning("notion: export enabled but token/database_id missing — skipping")
        return
    if not cfg.property_map:
        logging.warning("notion: no property_map configured — skipping")
        return

    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    sent = failed = 0
    with requests.Session() as session:
        for job in jobs:
            payload = {
                "parent": {"database_id": cfg.database_id},
                "properties": _build_properties(job, cfg.property_map),
            }
            if _post_with_retry(session, headers, payload):
                sent += 1
            else:
                failed += 1
            time.sleep(MIN_INTERVAL_SECONDS)

    logging.info("notion: %d pages created, %d failed", sent, failed)


def _post_with_retry(session: requests.Session, headers: dict[str, str], payload: dict) -> bool:
    """POST one page, honouring 429 Retry-After. Returns True on success.

    Failures used to be invisible: the response was never inspected, so a bad
    token or a schema mismatch produced a silent no-op export.
    """
    for attempt in range(3):
        try:
            response = session.post(NOTION_API, headers=headers, json=payload, timeout=20)
        except requests.RequestException as exc:
            logging.warning("notion: request failed (%s)", exc)
            return False

        if response.status_code < 300:
            return True

        if response.status_code == 429 and attempt < 2:
            try:
                wait = float(response.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1.0
            time.sleep(min(wait, 30.0))
            continue

        logging.warning("notion: HTTP %s — %s", response.status_code, (response.text or "")[:300])
        return False
    return False


def _build_properties(job: JobListing, property_map: dict[str, str]) -> dict[str, dict]:
    properties: dict[str, dict] = {}
    for field, prop_name in property_map.items():
        value = getattr(job, field, "")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value if v)
        value = str(value or "")
        if field == "title":
            properties[prop_name] = {"title": [{"text": {"content": value[:MAX_TEXT_LEN]}}]}
        elif field.endswith("_url") or field in ("company_website", "recruiter_linkedin"):
            # Notion rejects an empty string for a url property; null is accepted.
            properties[prop_name] = {"url": value or None}
        elif field.endswith("_email"):
            properties[prop_name] = {"email": value or None}
        elif not value:
            properties[prop_name] = {"rich_text": []}
        else:
            properties[prop_name] = {"rich_text": [{"text": {"content": value[:MAX_TEXT_LEN]}}]}
    return properties
