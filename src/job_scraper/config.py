"""YAML config loading, validation and path resolution.

Config files live in `configs/` (see configs/README.md). `resolve_config_path`
accepts a bare name (`norway-big`), a name with extension (`norway-big.yaml`), a
path relative to `configs/`, or an absolute/relative filesystem path — so the
same invocation works from the repo root, from inside Docker (`/app/configs`),
and from an installed console script.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

# Directories searched for a config named without a path, in priority order.
# Relative entries are resolved against both the CWD and the repo root, so
# `--config norway-big` works no matter where the process was started.
CONFIG_SEARCH_DIRS = (
    "",
    "configs",
    "configs/regions",
    "configs/sites",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class RunConfig:
    user_agent: str = "HarvestKitBot/1.0 (+https://github.com/BluOryn/HarvestKit)"
    delay_seconds: float = 1.0
    max_pages: int = 200
    max_depth: int = 3
    # Off by default at the operator's instruction. robots.txt is advisory, not
    # an access control, but ignoring it does raise ToS exposure and weakens the
    # legitimate-interest argument for collecting personal data in the EU — so
    # it stays a switch rather than something hard-coded, and
    # docs/OPERATOR-TERMS.md records what turning it on and off means.
    obey_robots: bool = False
    # When robots IS obeyed: a robots.txt we could not read expresses no policy.
    # RFC 9309 says to treat an unavailable one as a blanket disallow, which is
    # right when the site is refusing us and wrong when a WAF is. Measured over
    # twelve EU company domains, three were being written off entirely on the
    # strength of a bot wall's 403. See src/job_scraper/robots.py.
    robots_unreadable_is_allowed: bool = True
    use_playwright: bool = False
    # ---- Transport ladder (see src/job_scraper/transport.py) ----
    # Rung 1: a real browser TLS/HTTP2 fingerprint via curl_cffi. Cheap, and the
    # single highest-yield anti-blocking measure available — `requests` presents
    # a ClientHello no browser has ever sent, which is what Akamai and Cloudflare
    # score first.
    use_impersonation: bool = True
    # Rung 2: a real browser, for JS-only pages and interactive challenges.
    # Costs ~100 MB and a second or two per page, so it stays off until asked.
    use_stealth_browser: bool = False
    stealth_browser_headless: bool = True
    stealth_browser_concurrency: int = 2
    # Whether a blocked response may be retried on a stronger rung at all.
    escalate_on_block: bool = True
    transport_memory_path: str = ".cache/transport_memory.sqlite"
    allow_domains: list[str] = field(default_factory=list)
    confirm_permission: bool = False
    # Deep-scrape: visit each posting's detail page after listing/feed parse
    # to enrich fields (description, salary, recruiter, tech stack, etc.).
    deep_scrape: bool = True
    deep_concurrency: int = 6
    deep_per_host_concurrency: int = 1
    deep_per_host_delay_seconds: float = 1.5
    deep_max_retries: int = 2
    # HTTP cache + UA rotation
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86400
    cache_path: str = ".cache/http_cache.sqlite"
    rotate_user_agents: bool = True
    # Proxy rotation: list of "http://user:pass@host:port" or "socks5://host:port".
    # Empty list = direct connection. `proxies_file` points at a newline-delimited
    # file (the format tools/proxy_sources.py writes) and is merged in.
    proxies: list[str] = field(default_factory=list)
    proxies_file: str = ""
    proxy_rotation: str = "round_robin"  # round_robin | random
    proxy_max_failures: int = 3  # mark proxy dead after N consecutive fails
    proxy_cooldown_seconds: int = 300  # before retrying a dead proxy
    # Fail closed. With proxies configured and every one of them cooling down,
    # refuse the fetch instead of connecting directly. On a machine that must
    # never be seen from, a silent direct fallback is not degradation — it is
    # the leak the pool existed to prevent, arriving at the worst moment.
    require_proxy: bool = False
    # Index and search URLs are stable strings whose contents change daily.
    # Serving them from the 24 h page cache made `--only-new` return nothing on
    # a daily run, so they get their own, much shorter, freshness ceiling.
    index_cache_ttl_seconds: int = 3600
    # LLM-fallback adapter — when the heuristic extractor fills < llm_min_fields
    # fields, call Anthropic Haiku to infer a selector map. Cached per host.
    llm_fallback_enabled: bool = False
    llm_api_key: str = ""  # set via env ANTHROPIC_API_KEY if blank
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_min_fields: int = 5  # trigger LLM if fewer non-empty fields filled
    llm_max_html_chars: int = 60000  # truncate HTML before sending
    llm_cache_path: str = ".cache/llm_selectors.sqlite"
    llm_monthly_budget_usd: float = 5.0  # hard cap; tracked in the cache DB


@dataclass
class KeywordConfig:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)


@dataclass
class LocationConfig:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    require_match: bool = False
    allow_remote: bool = True


@dataclass
class TargetConfig:
    name: str
    url: str
    adapter: str = "auto"
    use_playwright: bool | None = None


@dataclass
class CsvExportConfig:
    enabled: bool = True
    path: str = "output/jobs.csv"


@dataclass
class GSheetsExportConfig:
    enabled: bool = False
    service_account_json: str = ""
    spreadsheet_id: str = ""
    worksheet: str = "jobs"


@dataclass
class NotionExportConfig:
    enabled: bool = False
    token: str = ""
    database_id: str = ""
    property_map: dict[str, str] = field(default_factory=dict)


@dataclass
class SlackExportConfig:
    enabled: bool = False
    webhook_url: str = ""
    max_items: int = 5


@dataclass
class ExportsConfig:
    csv: CsvExportConfig = field(default_factory=CsvExportConfig)
    gsheets: GSheetsExportConfig = field(default_factory=GSheetsExportConfig)
    notion: NotionExportConfig = field(default_factory=NotionExportConfig)
    slack: SlackExportConfig = field(default_factory=SlackExportConfig)


@dataclass
class AppConfig:
    run: RunConfig = field(default_factory=RunConfig)
    keywords: KeywordConfig = field(default_factory=KeywordConfig)
    locations: LocationConfig = field(default_factory=LocationConfig)
    targets: list[TargetConfig] = field(default_factory=list)
    exports: ExportsConfig = field(default_factory=ExportsConfig)


def candidate_config_paths(name: str) -> list[Path]:
    """Every path `resolve_config_path` would try, in order. Exposed for errors."""
    raw = Path(os.path.expanduser(name))
    if raw.is_absolute():
        return [raw] if raw.suffix else [raw.with_suffix(".yaml"), raw.with_suffix(".yml")]

    stems = [name] if Path(name).suffix in (".yaml", ".yml") else [f"{name}.yaml", f"{name}.yml", name]
    roots = [Path.cwd(), _REPO_ROOT]
    out: list[Path] = []
    for root in roots:
        for directory in CONFIG_SEARCH_DIRS:
            for stem in stems:
                candidate = (root / directory / stem) if directory else (root / stem)
                if candidate not in out:
                    out.append(candidate)
    return out


def resolve_config_path(name: str) -> str:
    """Turn a user-supplied config reference into a real file path."""
    if not name:
        raise FileNotFoundError("No config path given.")
    tried = candidate_config_paths(name)
    for candidate in tried:
        if candidate.is_file():
            return str(candidate)
    shown = "\n  ".join(str(p) for p in tried[:8])
    raise FileNotFoundError(f"Config {name!r} not found. Looked in:\n  {shown}")


def _load_proxies(run_raw: dict[str, Any], config_path: str) -> list[str]:
    """Inline `proxies:` plus anything in `proxies_file`, deduped, order-preserved.

    The proxies file is resolved relative to the config file first, then the CWD,
    so `proxies_file: proxies.txt` works for a config living in `configs/`.
    """
    collected: list[str] = [str(p).strip() for p in (run_raw.get("proxies") or []) if str(p).strip()]

    path_value = str(run_raw.get("proxies_file") or "").strip()
    if path_value:
        base = Path(config_path).resolve().parent
        for candidate in (base / path_value, Path.cwd() / path_value, _REPO_ROOT / path_value):
            if candidate.is_file():
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    collected.append(line if "://" in line else f"http://{line}")
                break

    seen = set()
    out: list[str] = []
    for proxy in collected:
        if proxy not in seen:
            seen.add(proxy)
            out.append(proxy)
    return out


def _as_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        # A bare string is almost always a YAML mistake (`include: python`), and
        # silently iterating it character-by-character produces nonsense filters.
        return [value]
    if isinstance(value, Iterable):
        return [str(v) for v in value if v is not None and str(v).strip()]
    raise ValueError(f"{where} must be a list, got {type(value).__name__}")


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"`{key}` must be a mapping, got {type(value).__name__}")
    return value


def _build(cls, raw: dict[str, Any], where: str):
    """Instantiate a dataclass from a YAML mapping, rejecting unknown keys.

    A typo like `deep_concurency:` used to be silently ignored, so the run kept
    the default and the user had no signal that their setting did nothing.
    """
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(
            f"Unknown key(s) in `{where}`: {', '.join(unknown)}. Valid keys: {', '.join(sorted(known))}"
        )
    try:
        return cls(**raw)
    except TypeError as exc:
        # A dataclass field with no default is a required YAML key; surface that
        # as a config error rather than a bare TypeError traceback.
        required = sorted(
            f.name
            for f in fields(cls)
            if f.default is MISSING and f.default_factory is MISSING  # type: ignore[misc]
        )
        missing = [name for name in required if name not in raw]
        if missing:
            raise ValueError(f"`{where}` is missing required key(s): {', '.join(missing)}") from exc
        raise ValueError(f"`{where}` is invalid: {exc}") from exc


def load_raw(path: str) -> dict[str, Any]:
    """Read a YAML config into a plain dict. Shared with the general scraper."""
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("top level of the config must be a mapping")
    return raw


def load_config(path: str) -> AppConfig:
    raw = load_raw(path)
    if str(raw.get("mode") or "").lower() == "general":
        raise ValueError(
            "this is a general-mode config (mode: general) — run it with "
            "`python run.py --general --config <name>`"
        )

    run_raw = dict(_section(raw, "run"))
    run_raw.pop("proxies", None)
    run_raw.pop("proxies_file", None)
    run = _build(RunConfig, run_raw, "run")
    run.proxies = _load_proxies(_section(raw, "run"), path)
    run.allow_domains = _as_list(run.allow_domains, "run.allow_domains")

    keywords_raw = _section(raw, "keywords")
    keywords = KeywordConfig(
        include=_as_list(keywords_raw.get("include"), "keywords.include"),
        exclude=_as_list(keywords_raw.get("exclude"), "keywords.exclude"),
        sectors=_as_list(keywords_raw.get("sectors"), "keywords.sectors"),
    )

    locations_raw = _section(raw, "locations")
    locations = LocationConfig(
        include=_as_list(locations_raw.get("include"), "locations.include"),
        exclude=_as_list(locations_raw.get("exclude"), "locations.exclude"),
        require_match=bool(locations_raw.get("require_match", False)),
        allow_remote=bool(locations_raw.get("allow_remote", True)),
    )

    targets: list[TargetConfig] = []
    for index, target_raw in enumerate(raw.get("targets") or []):
        if not isinstance(target_raw, dict):
            raise ValueError(f"targets[{index}] must be a mapping, got {type(target_raw).__name__}")
        target = _build(TargetConfig, target_raw, f"targets[{index}]")
        if not target.url:
            raise ValueError(f"targets[{index}] ({target.name!r}) has no url")
        targets.append(target)

    exports_raw = _section(raw, "exports")
    exports = ExportsConfig(
        csv=_build(CsvExportConfig, _section(exports_raw, "csv"), "exports.csv"),
        gsheets=_build(GSheetsExportConfig, _section(exports_raw, "gsheets"), "exports.gsheets"),
        notion=_build(NotionExportConfig, _section(exports_raw, "notion"), "exports.notion"),
        slack=_build(SlackExportConfig, _section(exports_raw, "slack"), "exports.slack"),
    )

    return AppConfig(run=run, keywords=keywords, locations=locations, targets=targets, exports=exports)
