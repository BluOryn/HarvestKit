"""General-mode config: typed, validated, and sharing the job-mode path resolver.

General mode previously read raw dicts with `.get()` chains, so a typo in
`selectors:` or `pagination:` silently produced an empty scrape. This mirrors the
job-mode loader: unknown keys are rejected and every field has a declared type.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from job_scraper.config import load_raw, resolve_config_path

__all__ = [
    "GeneralAppConfig",
    "GeneralRunConfig",
    "GeneralTargetConfig",
    "PaginationConfig",
    "load_general_config",
    "resolve_config_path",
]


@dataclass
class GeneralRunConfig:
    user_agent: str = "HarvestKitBot/1.0 (+https://github.com/BluOryn/HarvestKit)"
    delay_seconds: float = 1.0
    max_pages: int = 5
    obey_robots: bool = True
    confirm_permission: bool = False
    use_playwright: bool = False
    deep_scrape: bool = True
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86400
    cache_path: str = ".cache/general_http_cache.sqlite"
    rotate_user_agents: bool = True


@dataclass
class PaginationConfig:
    """How to reach page N+1.

    `param`+`step` rewrites a query parameter (Yelp's `?start=10`); `selector`
    points at a next-page anchor. With neither set the crawler falls back to
    `<link rel=next>` / `a[rel=next]`.
    """

    param: str = ""
    step: int = 1
    selector: str = ""
    max_pages: int | None = None


@dataclass
class GeneralTargetConfig:
    name: str = ""
    url: str = ""
    use_playwright: bool | None = None
    pagination: PaginationConfig = field(default_factory=PaginationConfig)
    selectors: dict[str, str] = field(default_factory=dict)


@dataclass
class GeneralExportConfig:
    enabled: bool = True
    path: str = "output/general.csv"


@dataclass
class GeneralAppConfig:
    run: GeneralRunConfig = field(default_factory=GeneralRunConfig)
    targets: list[GeneralTargetConfig] = field(default_factory=list)
    csv: GeneralExportConfig = field(default_factory=GeneralExportConfig)


# Selector keys the card extractor understands. Anything else is a typo.
KNOWN_SELECTOR_KEYS = frozenset(
    {
        "card",
        "title",
        "name",
        "url",
        "address",
        "city",
        "region",
        "phone",
        "rating",
        "review_count",
        "price_range",
        "category",
        "image",
    }
)


def _build(cls, raw: dict[str, Any], where: str):
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(
            f"Unknown key(s) in `{where}`: {', '.join(unknown)}. " f"Valid keys: {', '.join(sorted(known))}"
        )
    return cls(**raw)


def _mapping(raw: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"`{where}.{key}` must be a mapping, got {type(value).__name__}")
    return value


def load_general_config(path: str) -> GeneralAppConfig:
    raw = load_raw(path)

    run = _build(GeneralRunConfig, _mapping(raw, "run", "config"), "run")

    targets: list[GeneralTargetConfig] = []
    for index, target_raw in enumerate(raw.get("targets") or []):
        where = f"targets[{index}]"
        if not isinstance(target_raw, dict):
            raise ValueError(f"{where} must be a mapping, got {type(target_raw).__name__}")
        target_raw = dict(target_raw)
        pagination = _build(
            PaginationConfig, _mapping(target_raw, "pagination", where), f"{where}.pagination"
        )
        selectors = _mapping(target_raw, "selectors", where)
        unknown_selectors = sorted(set(selectors) - KNOWN_SELECTOR_KEYS)
        if unknown_selectors:
            raise ValueError(
                f"Unknown selector key(s) in `{where}.selectors`: {', '.join(unknown_selectors)}. "
                f"Valid keys: {', '.join(sorted(KNOWN_SELECTOR_KEYS))}"
            )
        target_raw.pop("pagination", None)
        target_raw.pop("selectors", None)
        target = _build(GeneralTargetConfig, target_raw, where)
        target.pagination = pagination
        target.selectors = {k: str(v) for k, v in selectors.items()}
        if not target.url:
            raise ValueError(f"{where} ({target.name!r}) has no url")
        if not target.name:
            target.name = target.url
        targets.append(target)

    exports = _mapping(raw, "exports", "config")
    csv_cfg = _build(GeneralExportConfig, _mapping(exports, "csv", "exports"), "exports.csv")

    return GeneralAppConfig(run=run, targets=targets, csv=csv_cfg)


def is_general_config(path: str) -> bool:
    try:
        return str(load_raw(path).get("mode") or "").lower() == "general"
    except (OSError, ValueError):
        return False
