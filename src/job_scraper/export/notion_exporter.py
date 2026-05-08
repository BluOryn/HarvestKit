from typing import Dict, List

import requests

from ..config import NotionExportConfig
from ..models import JobListing

NOTION_VERSION = "2022-06-28"


def export_notion(jobs: List[JobListing], cfg: NotionExportConfig) -> None:
    if not cfg.token or not cfg.database_id:
        return

    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    for job in jobs:
        payload = {
            "parent": {"database_id": cfg.database_id},
            "properties": _build_properties(job, cfg.property_map),
        }
        requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload, timeout=20)


def _build_properties(job: JobListing, property_map: Dict[str, str]) -> Dict[str, Dict]:
    properties: Dict[str, Dict] = {}
    for field, prop_name in property_map.items():
        value = getattr(job, field, "")
        if field == "title":
            properties[prop_name] = {"title": [{"text": {"content": value}}]}
        elif field.endswith("_url"):
            properties[prop_name] = {"url": value}
        else:
            properties[prop_name] = {"rich_text": [{"text": {"content": value}}]}
    return properties
