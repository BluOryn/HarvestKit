"""schema.org Person objects — the cleanest source when a site publishes them."""

from __future__ import annotations

import json
import logging

from bs4 import BeautifulSoup

from ..hit import PersonHit

log = logging.getLogger(__name__)

_PERSON_KEYS = ("employee", "employees", "founder", "founders", "member", "members", "author")


def _walk(node: object, out: list[dict]) -> None:
    if isinstance(node, dict):
        if str(node.get("@type", "")).lower() == "person":
            out.append(node)
        for key, value in node.items():
            if key in _PERSON_KEYS or isinstance(value, (dict, list)):
                _walk(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk(item, out)


def extract(html: str, url: str) -> list[PersonHit]:
    soup = BeautifulSoup(html, "html.parser")
    people: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            _walk(json.loads(raw), people)
        except (json.JSONDecodeError, TypeError) as exc:
            log.debug("jsonld_person: unparseable block on %s: %s", url, exc)

    hits: list[PersonHit] = []
    seen: set[str] = set()
    for person in people:
        name = str(person.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        email = str(person.get("email") or "").replace("mailto:", "").strip()
        same_as = person.get("sameAs") or []
        if isinstance(same_as, str):
            same_as = [same_as]
        linkedin = next((str(s) for s in same_as if "linkedin.com/in/" in str(s)), "")
        hits.append(
            PersonHit(
                name=name,
                role=str(person.get("jobTitle") or "").strip(),
                email=email,
                phone=str(person.get("telephone") or "").strip(),
                linkedin=linkedin,
                source_url=url,
                strategy="jsonld_person",
            )
        )
    return hits
