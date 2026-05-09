from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


@dataclass
class RunConfig:
    user_agent: str = "JobScraperBot/1.0 (+contact@example.com)"
    delay_seconds: float = 1.0
    max_pages: int = 200
    max_depth: int = 3
    obey_robots: bool = True
    use_playwright: bool = False
    allow_domains: List[str] = field(default_factory=list)
    confirm_permission: bool = False
    # Deep-scrape: visit each posting's detail page after listing/feed parse
    # to enrich fields (description, salary, recruiter, tech stack, etc.).
    deep_scrape: bool = True
    deep_concurrency: int = 6
    deep_per_host_concurrency: int = 1
    deep_per_host_delay_seconds: float = 1.5
    deep_max_retries: int = 2
    # HTTP cache + UA rotation (Phase 3)
    cache_enabled: bool = True
    cache_ttl_seconds: int = 86400
    cache_path: str = ".cache/http_cache.sqlite"
    rotate_user_agents: bool = True
    # Proxy rotation: list of "http://user:pass@host:port" or "socks5://host:port".
    # Empty list = direct connection.
    proxies: List[str] = field(default_factory=list)
    proxy_rotation: str = "round_robin"  # round_robin | random
    proxy_max_failures: int = 3          # mark proxy dead after N consecutive fails
    proxy_cooldown_seconds: int = 300    # before retrying a dead proxy


@dataclass
class KeywordConfig:
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    sectors: List[str] = field(default_factory=list)


@dataclass
class LocationConfig:
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    require_match: bool = False
    allow_remote: bool = True


@dataclass
class TargetConfig:
    name: str
    url: str
    adapter: str = "auto"
    use_playwright: Optional[bool] = None


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
    property_map: Dict[str, str] = field(default_factory=dict)


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
    targets: List[TargetConfig] = field(default_factory=list)
    exports: ExportsConfig = field(default_factory=ExportsConfig)


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    run_raw = raw.get("run", {})
    keywords_raw = raw.get("keywords", {})
    exports_raw = raw.get("exports", {})

    run = RunConfig(
        user_agent=run_raw.get("user_agent", RunConfig.user_agent),
        delay_seconds=run_raw.get("delay_seconds", RunConfig.delay_seconds),
        max_pages=run_raw.get("max_pages", RunConfig.max_pages),
        max_depth=run_raw.get("max_depth", RunConfig.max_depth),
        obey_robots=run_raw.get("obey_robots", RunConfig.obey_robots),
        use_playwright=run_raw.get("use_playwright", RunConfig.use_playwright),
        allow_domains=run_raw.get("allow_domains", []),
        confirm_permission=run_raw.get("confirm_permission", RunConfig.confirm_permission),
        deep_scrape=run_raw.get("deep_scrape", RunConfig.deep_scrape),
        deep_concurrency=run_raw.get("deep_concurrency", RunConfig.deep_concurrency),
        deep_per_host_concurrency=run_raw.get("deep_per_host_concurrency", RunConfig.deep_per_host_concurrency),
        deep_per_host_delay_seconds=run_raw.get("deep_per_host_delay_seconds", RunConfig.deep_per_host_delay_seconds),
        deep_max_retries=run_raw.get("deep_max_retries", RunConfig.deep_max_retries),
        cache_enabled=run_raw.get("cache_enabled", RunConfig.cache_enabled),
        cache_ttl_seconds=run_raw.get("cache_ttl_seconds", RunConfig.cache_ttl_seconds),
        cache_path=run_raw.get("cache_path", RunConfig.cache_path),
        rotate_user_agents=run_raw.get("rotate_user_agents", RunConfig.rotate_user_agents),
        proxies=run_raw.get("proxies", []) or [],
        proxy_rotation=run_raw.get("proxy_rotation", RunConfig.proxy_rotation),
        proxy_max_failures=run_raw.get("proxy_max_failures", RunConfig.proxy_max_failures),
        proxy_cooldown_seconds=run_raw.get("proxy_cooldown_seconds", RunConfig.proxy_cooldown_seconds),
    )

    keywords = KeywordConfig(
        include=keywords_raw.get("include", []),
        exclude=keywords_raw.get("exclude", []),
        sectors=keywords_raw.get("sectors", []),
    )

    locations_raw = raw.get("locations", {})
    locations = LocationConfig(
        include=locations_raw.get("include", []),
        exclude=locations_raw.get("exclude", []),
        require_match=locations_raw.get("require_match", False),
        allow_remote=locations_raw.get("allow_remote", True),
    )

    targets = [TargetConfig(**target) for target in raw.get("targets", [])]

    csv_cfg = CsvExportConfig(**exports_raw.get("csv", {}))
    gsheets_cfg = GSheetsExportConfig(**exports_raw.get("gsheets", {}))
    notion_cfg = NotionExportConfig(**exports_raw.get("notion", {}))
    slack_cfg = SlackExportConfig(**exports_raw.get("slack", {}))

    exports = ExportsConfig(
        csv=csv_cfg,
        gsheets=gsheets_cfg,
        notion=notion_cfg,
        slack=slack_cfg,
    )

    return AppConfig(run=run, keywords=keywords, locations=locations, targets=targets, exports=exports)
