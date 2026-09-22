"""HTTP client — robust, polite, deep-scrape friendly, proxy-aware.

Features:
  - Per-host token-bucket throttle (separate from `delay_seconds` global pacing).
  - Proxy rotation pool with health tracking + cooldown after consecutive failures.
  - Realistic User-Agent rotation pool (off by default, opt-in via config).
  - SQLite response cache with TTL (saves repeat fetches in deep-scrape).
  - Per-proxy cookie jar (each proxy looks like a distinct browser session).
  - urllib3 Retry + per-call 429 / Retry-After honoring.
  - WAF / 403 / captcha detection — short-circuits to retry layer.
  - Robots.txt enforcement (opt-out per request).
  - Thread-safe — used by ThreadPoolExecutor in deep_scrape.py.

Parallelism note: with N proxies you can safely set
`deep_per_host_concurrency = N` because each proxy has its own egress IP
and the per-host throttle keys on (host, proxy) — each (host, proxy) pair
gets its own bucket.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
import zlib
from contextlib import suppress
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .identity import IDENTITY_POOL, country_for_host, headers_for, identity_for
from .robots import RobotsCache
from .transport import FetchResponse as FetchResponse  # re-exported for callers
from .transport import Outcome, build_ladder, registrable_domain

_BLOCK_KEYWORDS = (
    "request could not be satisfied",
    "request blocked",
    "cloudfront",
    "access denied",
    "403 forbidden",
    "404 not found",
    "captcha",
    "are you a robot",
    "are you a human",
    "checking your browser",
    "verify you are human",
    "ddos protection",
    "cf-error",
    "request_id:",
    "enable javascript and cookies to continue",
)

# A real posting almost always carries one of these. Their presence vetoes the
# block heuristic so a legitimately short page that happens to say "access
# denied" in its cookie notice is not thrown away.
_CONTENT_MARKERS = ("<jobposting", "schema.org/jobposting", "application/ld+json", "og:title")


def _looks_like_block(text: str) -> bool:
    """Heuristic: a response that smells like a WAF / bot wall rather than content.

    Only short bodies are considered: a 200 KB page that mentions "captcha"
    somewhere in its footer is a real page, not a block.
    """
    if not text or not text.strip():
        return True
    lo = text.lower()
    if any(marker in lo for marker in _CONTENT_MARKERS):
        return False
    if len(lo) < 5000 and any(kw in lo for kw in _BLOCK_KEYWORDS):
        return True
    return len(lo) < 1500 and "<title>" in lo and "error" in lo


_DEFAULT_RETRY_AFTER_SECONDS = 8.0
_MAX_RETRY_AFTER_SECONDS = 30.0


def _retry_after_seconds(header: str | None) -> float:
    """Parse a Retry-After header. RFC 9110 allows both delta-seconds and an
    HTTP-date; `float()` alone raises on the date form and on garbage.
    """
    if not header:
        return _DEFAULT_RETRY_AFTER_SECONDS
    header = header.strip()
    try:
        return max(0.0, min(float(header), _MAX_RETRY_AFTER_SECONDS))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(header)
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_RETRY_AFTER_SECONDS
    if target is None:
        return _DEFAULT_RETRY_AFTER_SECONDS
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    delta = (target - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, min(delta, _MAX_RETRY_AFTER_SECONDS))


#: Sourced from `identity.IDENTITY_POOL` so the User-Agent, the client hints
#: and the TLS fingerprint always describe the same browser. The hand-written
#: list this replaced was pinned to Chrome 120 and Firefox 121 — both released
#: in late 2023, and by now old enough that the version alone draws attention.
DEFAULT_UA_POOL = [entry.user_agent for entry in IDENTITY_POOL]


#: URL shapes that enumerate rather than describe. A board's `/jobs` feed, a
#: search with a `page=` or `offset=`, an ATS `postings` endpoint: the string is
#: stable and the contents change every day, so caching them for the page TTL
#: (24 h, or 7 d where configured) meant a daily run re-read a stale snapshot
#: and `--only-new` legitimately found nothing new. Detail pages are unaffected
#: and keep the long TTL, which is where the cache earns its keep.
_INDEX_PATH_MARKERS: tuple[str, ...] = (
    "/jobs",
    "/job-search",
    "/jobsearch",
    "/search",
    "/suche",
    "/recherche",
    "/postings",
    "/positions",
    "/vacancies",
    "/openings",
    "/stellenangebote",
    "/stellen",
    "/offres",
    "/offerte",
    "/vacatures",
    "/ledige-stillinger",
    "/karriere",
    "/careers",
    "/sitemap",
    "/search.json",
    "/feed",
    "/rss",
)
_INDEX_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "page",
        "p",
        "offset",
        "from",
        "start",
        "skip",
        "cursor",
        "keyword",
        "keywords",
        "q",
        "query",
        "search",
        "pageno",
        "seite",
    }
)


#: Collection endpoints where the *last* segment is the board slug rather than a
#: document, so the "ends with a marker" rule below cannot see them. Listed
#: explicitly rather than guessed at: a rule loose enough to catch
#: `/v0/postings/<slug>` also catches `/karriere/stelle/<id>`, which is a
#: detail page and belongs in the long cache.
_INDEX_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/v\d+/postings/[^/]+/?$", re.I),  # Lever
    re.compile(r"/v\d+/boards/[^/]+(?:/jobs)?/?$", re.I),  # Greenhouse
    re.compile(r"/sr-jobs/search", re.I),  # SmartRecruiters
    re.compile(r"/api/v\d+/jobs", re.I),  # Workable
    re.compile(r"/spa/jobboerse/", re.I),  # Arbeitsagentur
)


def _looks_like_index(url: str) -> bool:
    """True for a listing/search URL, whose cached copy goes stale in hours."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    path = (parsed.path or "").rstrip("/").lower()
    # Only the *last* segments count. A detail page living under /karriere/ is
    # still a detail page, and giving it the short TTL would re-fetch thousands
    # of stable pages for nothing.
    trimmed = re.sub(r"/(?:page/)?\d+$", "", path)
    if any(trimmed.endswith(marker) for marker in _INDEX_PATH_MARKERS):
        return True
    if any(pattern.search(path) for pattern in _INDEX_URL_PATTERNS):
        return True
    if parsed.query:
        keys = {piece.split("=", 1)[0].lower() for piece in parsed.query.split("&") if piece}
        return bool(keys & _INDEX_QUERY_KEYS)
    return False


#: An egress entry of this shape means "connect directly, but from this local
#: address". It is not a proxy at all, and it is the cheapest genuine rotation
#: available: a host with a routed IPv6 /64 — which Oracle's always-free tier,
#: Hetzner, OVH and most VPS providers hand out at no charge — owns 18 quintillion
#: source addresses. Each one is a distinct client as far as any rate limiter is
#: concerned, and no third party carries the traffic, so none of the trust
#: problems of a borrowed proxy apply.
#:
#:     bind://2a01:4f8:c17:1234::7
#:     bind://[2a01:4f8:c17:1234::7]
BIND_SCHEME = "bind://"


def _bind_address(entry_url: str) -> str:
    """The local address in a `bind://` entry, or "" if it is a real proxy."""
    if not entry_url.lower().startswith(BIND_SCHEME):
        return ""
    return entry_url[len(BIND_SCHEME) :].strip().strip("[]").split("/")[0]


class _SourceBoundAdapter(HTTPAdapter):
    """An adapter that opens every connection from one local address."""

    def __init__(self, source_address: str, **kwargs: Any) -> None:
        self._source = (source_address, 0)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["source_address"] = self._source
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **kwargs: Any) -> Any:
        kwargs["source_address"] = self._source
        return super().proxy_manager_for(proxy, **kwargs)


def _configured_session(retry: Retry, source_address: str = "") -> requests.Session:
    """A Session with our retry policy and pool sizing, and nothing else.

    Deliberately not a copy of an existing session: the point is that it starts
    with an empty cookie jar.
    """
    session = requests.Session()
    if source_address:
        adapter: HTTPAdapter = _SourceBoundAdapter(
            source_address, max_retries=retry, pool_connections=10, pool_maxsize=20
        )
    else:
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class _ProxyPool:
    """Rotates over a list of proxy URLs with health tracking.

    Each proxy is an entry in `proxies`: "http://user:pass@host:port" /
    "http://host:port" / "socks5://host:port". Empty list = direct connection.

    A proxy is marked dead after `max_failures` consecutive failures and
    cooled down for `cooldown_seconds` before being retried.
    """

    def __init__(
        self,
        proxies: list[str],
        rotation: str = "round_robin",
        max_failures: int = 3,
        cooldown_seconds: int = 300,
        *,
        require_proxy: bool = False,
    ) -> None:
        self.entries: list[dict[str, Any]] = [
            {"url": p.strip(), "failures": 0, "dead_until": 0.0, "uses": 0}
            for p in (proxies or [])
            if p and p.strip()
        ]
        self.rotation = rotation
        self.max_failures = max(1, max_failures)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._lock = threading.Lock()
        self._cursor = 0
        # Per-proxy cookie jar — each proxy = its own browser session.
        self._jars: dict[str, requests.cookies.RequestsCookieJar] = {}
        self._exhausted_warned = False
        #: Fail closed. When every proxy is cooling down, refuse the fetch
        #: rather than connecting directly. Falling back to a direct connection
        #: is not a graceful degradation on a machine that must never be seen —
        #: it is the exact leak the pool exists to prevent, and it happened
        #: silently on the worst possible occasion, when the pool had just been
        #: burned by the host now being requested.
        self.require_proxy = bool(require_proxy)
        #: Distinct registrable domains that refused this proxy recently. A
        #: single host walling us says nothing about the proxy; the same proxy
        #: being walled by several unrelated domains says its IP is burned.
        self._blocked_domains: dict[str, dict[str, float]] = {}

    def __bool__(self) -> bool:
        return bool(self.entries)

    @property
    def exhausted(self) -> bool:
        """True when proxies are configured but none is currently usable."""
        if not self.entries:
            return False
        now = time.time()
        with self._lock:
            return all(entry["dead_until"] > now for entry in self.entries)

    def acquire(self) -> dict[str, Any] | None:
        """Return a live proxy, or None when every proxy is cooling down.

        None makes the caller fall back to a direct connection, which leaks the
        real egress IP — so warn loudly (once per exhaustion event) rather than
        failing over silently. With `require_proxy` set, the caller refuses the
        fetch instead; `exhausted` is how it tells "no proxies configured"
        (direct is intended) from "every proxy is dead" (direct is a leak).
        """
        if not self.entries:
            return None
        with self._lock:
            now = time.time()
            n = len(self.entries)
            # Sample without replacement. Drawing a fresh random index each time
            # could miss the one live proxy in a pool of dead ones (35% of the
            # time with 10 entries) and silently fall back to a direct
            # connection — the exact IP leak the warning below is about.
            if self.rotation == "random":
                order = random.sample(range(n), n)
            else:
                order = [(self._cursor + offset) % n for offset in range(n)]
            for idx in order:
                if self.rotation != "random":
                    self._cursor = (idx + 1) % n
                entry = self.entries[idx]
                if entry["dead_until"] <= now:
                    entry["uses"] += 1
                    self._exhausted_warned = False
                    return entry
            if not self._exhausted_warned:
                self._exhausted_warned = True
                soonest = min(e["dead_until"] for e in self.entries) - now
                logging.warning(
                    "all %d proxies are cooling down (next available in %.0fs) — "
                    "falling back to a DIRECT connection, which exposes your real IP",
                    n,
                    max(0.0, soonest),
                )
            return None  # all dead

    def report_success(self, entry: dict[str, Any] | None) -> None:
        if entry is None:
            return
        with self._lock:
            entry["failures"] = 0

    def report_failure(self, entry: dict[str, Any] | None) -> None:
        """Charge a *transport* failure to this proxy.

        Connect refused, TLS failure, proxy auth rejected, timeout: all of those
        are the proxy's fault and count directly.
        """
        if entry is None:
            return
        with self._lock:
            entry["failures"] += 1
            if entry["failures"] >= self.max_failures:
                entry["dead_until"] = time.time() + self.cooldown_seconds
                entry["failures"] = 0
                logging.info("proxy %s cooled down for %ds", entry["url"], self.cooldown_seconds)

    def report_block(self, entry: dict[str, Any] | None, domain: str) -> None:
        """Charge a *host-side* refusal — 403, 429, a challenge page.

        These used to be counted like a transport failure, so three walled
        pages on one stubborn site cooled down every proxy in the pool and the
        whole run fell back to a direct connection. A host refusing a request
        is overwhelmingly about the host, not the exit IP; it only implicates
        the proxy when several unrelated domains refuse the same one.
        """
        if entry is None:
            return
        domain = (domain or "").lower()
        if not domain:
            return
        now = time.time()
        with self._lock:
            seen = self._blocked_domains.setdefault(entry["url"], {})
            seen[domain] = now
            # Only recent evidence counts: a domain that walled us an hour ago
            # says nothing about this proxy now.
            window = now - self.cooldown_seconds
            for stale in [key for key, when in seen.items() if when < window]:
                seen.pop(stale, None)
            if len(seen) >= max(3, self.max_failures):
                entry["dead_until"] = now + self.cooldown_seconds
                entry["failures"] = 0
                seen.clear()
                logging.info(
                    "proxy %s was refused by %d distinct domains — cooled down for %ds",
                    entry["url"],
                    max(3, self.max_failures),
                    self.cooldown_seconds,
                )

    def jar_for(self, entry: dict[str, Any] | None) -> requests.cookies.RequestsCookieJar:
        """This proxy's cookie jar.

        The jar has to be handed to a session that has *no* cookies of its own,
        or `requests` merges the two and replays one exit IP's session cookies
        through every other — which is a stronger correlation signal than
        sharing the IP would have been. `HttpClient._session_for` supplies such
        a session, one per proxy.
        """
        key = entry["url"] if entry else "_direct_"
        with self._lock:
            jar = self._jars.get(key)
            if jar is None:
                jar = requests.cookies.RequestsCookieJar()
                self._jars[key] = jar
            return jar

    def stats(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self.entries]


class HostThrottle:
    """Per-key inflight cap + min-delay throttle. Thread-safe.

    Keys are opaque: HttpClient uses "host|proxy" so each egress IP gets its own
    bucket; deep_scrape uses the bare host. Shared by both so the two layers
    cannot drift apart.
    """

    def __init__(self, max_inflight: int, min_delay: float) -> None:
        self.max_inflight = max(1, max_inflight)
        self.min_delay = max(0.0, min_delay)
        self._cv = threading.Condition()
        self._inflight: dict[str, int] = {}
        self._last: dict[str, float] = {}

    def acquire(self, key: str) -> None:
        with self._cv:
            while True:
                wait = self.min_delay - (time.time() - self._last.get(key, 0.0))
                if self._inflight.get(key, 0) < self.max_inflight and wait <= 0:
                    self._inflight[key] = self._inflight.get(key, 0) + 1
                    self._last[key] = time.time()
                    return
                # A negative `wait` means the delay has already elapsed and we
                # are only waiting on an inflight slot — block until notified.
                self._cv.wait(timeout=wait if wait > 0 else None)

    def release(self, key: str) -> None:
        with self._cv:
            self._inflight[key] = max(0, self._inflight.get(key, 0) - 1)
            self._cv.notify_all()


# Back-compat alias — external tooling imported the private name.
_HostThrottle = HostThrottle


class _ResponseCache:
    """SQLite cache for GET responses. Keyed by canonical URL."""

    def __init__(self, path: str, ttl_seconds: int) -> None:
        self.path = path
        self.ttl = ttl_seconds
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS http_cache ("
            "url TEXT PRIMARY KEY, final_url TEXT, body BLOB, fetched_at INTEGER)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_fetched ON http_cache(fetched_at)")

    def get(self, url: str, max_age: float | None = None) -> tuple[str, str] | None:
        """The cached body, or None when there is none fresh enough.

        `max_age` lets a caller demand something fresher than the global TTL.
        A search or index URL is stable in shape and changes every day, so the
        24 h (configurably 7 d) default meant a daily run re-read a week-old
        snapshot and `--only-new` produced nothing at all.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT final_url, body, fetched_at FROM http_cache WHERE url=?",
                (url,),
            ).fetchone()
        if not row:
            return None
        final_url, body, fetched_at = row
        age_limit = self.ttl if max_age is None else min(self.ttl, max(0.0, max_age))
        if time.time() - (fetched_at or 0) > age_limit:
            return None
        try:
            text = zlib.decompress(body).decode("utf-8", errors="replace")
        except Exception:
            return None
        if _looks_like_block(text):
            self.delete(url)
            return None
        return final_url, text

    def put(self, url: str, final_url: str, body: str) -> None:
        try:
            blob = zlib.compress(body.encode("utf-8", errors="replace"), 6)
        except Exception:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO http_cache(url, final_url, body, fetched_at) VALUES (?, ?, ?, ?)",
                (url, final_url, blob, int(time.time())),
            )

    def delete(self, url: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM http_cache WHERE url=?", (url,))

    def purge_expired(self) -> int:
        cutoff = int(time.time()) - self.ttl
        with self._lock:
            cur = self._conn.execute("DELETE FROM http_cache WHERE fetched_at < ?", (cutoff,))
            return cur.rowcount or 0

    def close(self) -> None:
        with self._lock, suppress(sqlite3.Error):
            self._conn.close()


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        delay_seconds: float,
        obey_robots: bool,
        timeout_seconds: float = 8.0,
        max_retries: int = 1,
        cache_enabled: bool = True,
        cache_ttl_seconds: int = 86400,
        cache_path: str = ".cache/http_cache.sqlite",
        rotate_user_agents: bool = True,
        per_host_concurrency: int = 4,
        per_host_min_delay: float = 0.0,
        proxies: list[str] | None = None,
        proxy_rotation: str = "round_robin",
        proxy_max_failures: int = 3,
        proxy_cooldown_seconds: int = 300,
        robots_exempt_hosts: tuple[str, ...] = (),
        use_impersonation: bool = True,
        use_browser: bool = False,
        browser_headless: bool = True,
        browser_concurrency: int = 2,
        escalate_on_block: bool = True,
        transport_memory_path: str = ".cache/transport_memory.sqlite",
        robots_unreadable_is_allowed: bool = True,
        require_proxy: bool = False,
        index_cache_ttl_seconds: int = 3600,
    ) -> None:
        self.user_agent = user_agent
        self.delay_seconds = max(0.0, delay_seconds)
        self.obey_robots = obey_robots
        # Hosts whose robots.txt is not consulted because access was authorised
        # out of band -- an API that issued us credentials. Deliberately an
        # explicit per-host list rather than a global switch: a run that needs
        # one credentialled API must not stop honouring robots everywhere else.
        self.robots_exempt_hosts = frozenset(host.lower() for host in robots_exempt_hosts)
        self.timeout_seconds = timeout_seconds
        self.rotate_user_agents = rotate_user_agents
        self.ua_pool = DEFAULT_UA_POOL
        self._last_request_at = 0.0
        self._global_lock = threading.Lock()
        self._robots = RobotsCache(unreadable_is_allowed=robots_unreadable_is_allowed)
        self._session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=1.0,
            # 429 is deliberately absent. urllib3 would retry it and then raise
            # MaxRetryError, which arrives here as a generic RequestException --
            # so a rate-limited host became indistinguishable from one with no
            # results, and the 429 branch below never ran. A block that looks
            # like an empty result set is the kind of thing you act on wrongly
            # for an hour. Let it through and handle it where it can be logged.
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD", "POST"]),
            # urllib3 honours Retry-After by sleeping inside the adapter, and it
            # does not cap that sleep. A rate-limited host answering
            # "Retry-After: 3600" therefore parks the calling thread for an hour
            # while it still holds its per-host slot, and every other worker
            # queues behind it: the run stops dead with no CPU, no log line and
            # no error. Observed against a search API after a burst.
            #
            # The 429 handler below does the same job with a 30s ceiling, so the
            # header is still respected — just never unboundedly.
            respect_retry_after_header=False,
            backoff_max=_MAX_RETRY_AFTER_SECONDS,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=40)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._retry_policy = retry
        self._cache: _ResponseCache | None = (
            _ResponseCache(cache_path, cache_ttl_seconds) if cache_enabled else None
        )
        self._throttle = HostThrottle(per_host_concurrency, per_host_min_delay)
        self._proxies = _ProxyPool(
            proxies or [],
            rotation=proxy_rotation,
            max_failures=proxy_max_failures,
            cooldown_seconds=proxy_cooldown_seconds,
            require_proxy=require_proxy,
        )
        #: Freshness ceiling for URLs a caller marks as an index or search page.
        #: Those are stable URLs with changing contents, and serving them from a
        #: day-old cache is how `--only-new` came back empty on a daily run.
        self.index_cache_ttl_seconds = max(0, index_cache_ttl_seconds)
        #: One requests.Session per proxy. A single shared Session pools every
        #: cookie it is ever handed in `session.cookies` and merges that jar
        #: into every subsequent request, so the "per-proxy cookie jar" isolated
        #: nothing: one exit IP's session cookie was replayed through all the
        #: others, correlating them far more tightly than the shared address
        #: alone would have. Sessions also give each proxy its own connection
        #: pool, which is what stops TLS sessions being resumed across exits.
        self._proxy_sessions: dict[str, requests.Session] = {}
        self._proxy_session_lock = threading.Lock()
        self._session_factory = lambda: _configured_session(retry)
        if self._proxies:
            logging.info("HTTP: %d proxies loaded (%s rotation)", len(self._proxies.entries), proxy_rotation)

        # The escalation ladder. Rung 0 is this session, so everything already
        # configured above — the retry policy, the connection pool — still
        # applies to the common case; the further rungs only come into play for
        # hosts that refuse it.
        self._ladder = build_ladder(
            self._session,
            memory_path=transport_memory_path,
            use_impersonation=use_impersonation,
            use_browser=use_browser,
            headless=browser_headless,
            browser_concurrency=browser_concurrency,
            escalate=escalate_on_block,
            remember=cache_enabled,
        )
        # robots.txt now travels the same road as everything else: same proxy,
        # same browser identity, same TLS fingerprint. Fetching it with bare
        # urllib meant the first request to every host went out from the real
        # egress IP looking like a bot, which is both an IP leak and the single
        # most likely request in the run to be refused.
        self._robots.set_fetcher(self._fetch_robots)

    def _session_for(self, proxy_url: str):
        """The Session this egress uses. One per proxy, never shared.

        Each is created with an empty cookie jar and kept that way: cookies for
        a fetch are passed per-request from `_ProxyPool.jar_for`, and a Session
        that has accumulated its own would merge them in.
        """
        if not proxy_url:
            return self._session
        with self._proxy_session_lock:
            session = self._proxy_sessions.get(proxy_url)
            if session is None:
                session = _configured_session(self._retry_policy, _bind_address(proxy_url))
                self._proxy_sessions[proxy_url] = session
            return session

    def _proxy_refused(self, url: str) -> FetchResponse:
        """The response for a fetch we declined to make without a proxy."""
        logging.warning(
            "refusing %s: every proxy is cooling down and require_proxy is set. "
            "Connecting directly here would expose the real IP.",
            url,
        )
        return FetchResponse(
            url=url,
            outcome=Outcome.ERROR,
            error="proxy pool exhausted and require_proxy is set",
        )

    def close(self) -> None:
        """Release the connection pool and the SQLite cache handle.

        Long runs that create several clients (tools/, tests) otherwise leak an
        open sqlite connection and a pool of sockets per client.
        """
        ladder = getattr(self, "_ladder", None)
        if ladder is not None:
            # Closes the browser rung too. A leaked Chromium outlives the run
            # and holds its profile directory open.
            with suppress(Exception):
                ladder.close()
        with suppress(Exception):
            self._session.close()
        with self._proxy_session_lock:
            for session in self._proxy_sessions.values():
                with suppress(Exception):
                    session.close()
            self._proxy_sessions.clear()
        if self._cache is not None:
            self._cache.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def has_proxies(self) -> bool:
        return bool(self._proxies)

    def proxy_stats(self) -> list[dict[str, Any]]:
        return self._proxies.stats()

    def transport_stats(self) -> list[dict[str, Any]]:
        """Which domains needed which rung. Empty when memory is disabled."""
        memory = getattr(self._ladder, "memory", None)
        return memory.stats() if memory is not None else []

    def _pick_ua(self, host: str = "") -> str:
        """The User-Agent this host sees.

        Derived from the hostname rather than drawn at random per request. A
        browser does not change what it is between two page loads, and the
        previous behaviour — a fresh random UA every time — meant one host saw
        Chrome, then Safari, then Firefox arrive over a single connection.
        """
        if self.rotate_user_agents:
            return identity_for(host).user_agent
        return self.user_agent

    @staticmethod
    def _hostname(url: str) -> str:
        try:
            return urlparse(url).netloc or "_"
        except ValueError:
            return "_"

    def robots_allows(self, url: str) -> bool:
        """Would robots.txt let us fetch this? Public so a caller can tell a
        blocked host apart from a broken one; those need different fixes and
        reporting the wrong one sends the operator chasing a phantom."""
        return self._robots_allow(url)

    def _robots_allow(self, url: str) -> bool:
        if not self.obey_robots:
            return True
        if self._hostname(url).lower() in self.robots_exempt_hosts:
            return True
        return self._robots.is_allowed(url, self.user_agent)

    def _global_pace(self) -> None:
        with self._global_lock:
            elapsed = time.time() - self._last_request_at
            wait = self.delay_seconds - elapsed
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.25))
            self._last_request_at = time.time()

    @staticmethod
    def _proxies_dict(entry: dict[str, Any] | None) -> dict[str, str] | None:
        if not entry:
            return None
        url = entry.get("url") or ""
        # A `bind://` entry names a local source address, which the session's
        # adapter applies. It is not an upstream proxy and must never be handed
        # to `requests` as one.
        if not url or _bind_address(url):
            return None
        return {"http": url, "https": url}

    @staticmethod
    def _stealth_headers(url: str, ua: str = "") -> dict[str, str]:
        """Browser-like headers for a URL. Delegates to `identity`.

        The version this replaced built the header set by hand and pinned
        `Sec-CH-UA` to Chromium 120 regardless of which User-Agent it had drawn
        — so a Safari UA arrived announcing itself as Chrome, which no real
        browser does. It also sent `Sec-Fetch-Site: same-origin` on first
        contact with a host, where a browser sends `none`. Both were stronger
        bot signals than sending nothing at all.
        """
        host = urlparse(url).netloc
        identity = identity_for(host)
        # A detail page is plausibly reached from the site's own index, and a
        # Referer makes `Sec-Fetch-Site: same-origin` truthful rather than the
        # contradiction it used to be.
        referer = f"https://{host}/" if any(s in url for s in ("/detail/", "/job/", "/jobs/")) else None
        return headers_for(url, identity, country=country_for_host(host), referer=referer)

    def fetch(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        use_cache: bool = True,
        country: str | None = None,
        max_rung: int | None = None,
        max_age: float | None = None,
        is_index: bool = False,
    ) -> FetchResponse:
        """Fetch a URL and report what actually happened.

        This is the API to prefer. `get()` below flattens the result to
        "text or nothing", which cannot express the difference between a page
        that had no content and a bot wall that refused to show us one — and
        that difference is the whole diagnosis when a run comes back empty.
        """
        if not self._robots_allow(url):
            logging.debug("robots disallow %s", url)
            return FetchResponse(
                url=url, outcome=Outcome.ROBOTS_DENIED, error="robots.txt disallows this URL"
            )
        if max_age is None and (is_index or _looks_like_index(url)):
            max_age = float(self.index_cache_ttl_seconds)
        if use_cache and self._cache is not None:
            cached = self._cache.get(url, max_age=max_age)
            if cached is not None:
                final_url, text = cached
                return FetchResponse(
                    url=url,
                    final_url=final_url,
                    status=200,
                    text=text,
                    outcome=Outcome.OK,
                    transport="cache",
                )
        self._global_pace()
        host = self._hostname(url)
        proxy_entry = self._proxies.acquire() if self._proxies else None
        proxy_url = (proxy_entry or {}).get("url", "")
        if proxy_entry is None and self._proxies.require_proxy and self._proxies.exhausted:
            return self._proxy_refused(url)
        throttle_key = f"{host}|{proxy_url}"
        self._throttle.acquire(throttle_key)
        # Backing off is this caller's penalty, not a reason to keep holding a
        # slot every other worker is queued on. Recorded here, slept after the
        # release below.
        backoff_seconds = 0.0
        try:
            # The identity is salted with the proxy, so moving to a new egress
            # IP also means presenting a different browser. Keeping the old one
            # would hand the site a correlation between the two addresses.
            identity = identity_for(host, salt=proxy_url)
            jar = self._proxies.jar_for(proxy_entry)
            # A `bind://` entry selects a local source address, not an upstream
            # proxy — the session carries it, and passing it on as a proxy URL
            # would simply fail to connect.
            upstream = "" if _bind_address(proxy_url) else proxy_url
            result = self._ladder.fetch(
                url,
                headers=headers,
                proxy=upstream or None,
                session=self._session_for(proxy_url),
                timeout=self.timeout_seconds,
                identity=identity,
                country=country or country_for_host(host),
                cookies=jar,
                max_rung=max_rung,
            )
            # Attribution matters here. A 403 or a 429 is the *host* refusing
            # us; charging that to the proxy meant one stubborn site burned the
            # entire pool and the run fell back to a direct connection. Only a
            # transport-level failure — connect refused, TLS, timeout — is the
            # proxy's own fault.
            domain = registrable_domain(url)
            if result.outcome is Outcome.RATE_LIMITED:
                backoff_seconds = result.retry_after or _DEFAULT_RETRY_AFTER_SECONDS
                logging.info("429 from %s — backing off %.1fs", url, backoff_seconds)
                self._proxies.report_block(proxy_entry, domain)
            elif result.outcome is Outcome.BLOCKED:
                self._proxies.report_block(proxy_entry, domain)
            elif result.outcome is Outcome.ERROR:
                self._proxies.report_failure(proxy_entry)
            else:
                self._proxies.report_success(proxy_entry)
            # Only a genuinely good response is cached. Pinning an error page
            # for the whole TTL is how a transient 503 became a day of empty
            # results.
            if result.ok and self._cache is not None and use_cache:
                self._cache.put(url, result.final_url or url, result.text)
            return result
        finally:
            self._throttle.release(throttle_key)
            if backoff_seconds > 0:
                time.sleep(backoff_seconds)

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        allow_404: bool = False,
        use_cache: bool = True,
        max_age: float | None = None,
        is_index: bool = False,
    ) -> tuple[str, str] | None:
        """(final_url, text), or None. Kept for the callers that only want the
        body; `fetch()` carries the reason when there isn't one."""
        result = self.fetch(url, headers, use_cache=use_cache, max_age=max_age, is_index=is_index)
        if result.ok:
            return result.final_url or url, result.text
        # `allow_404` lets an error body reach the caller — board probes read it
        # to tell "no such board" from "wrong host". A block is still withheld:
        # handing back a challenge page as though it were content is how the
        # parser ends up mining a CAPTCHA for job listings.
        if allow_404 and result.text and result.outcome in (Outcome.NOT_FOUND, Outcome.SERVER_ERROR):
            return result.final_url or url, result.text
        return None

    def _fetch_robots(self, robots_url: str) -> tuple[int, str] | None:
        """Fetch robots.txt the same way we fetch everything else.

        Deliberately bypasses `fetch()`: that method consults robots first, and
        this is the call it would consult. Capped at the impersonation rung —
        launching a browser to read a text file is never worth it.
        """
        host = self._hostname(robots_url)
        proxy_entry = self._proxies.acquire() if self._proxies else None
        proxy_url = (proxy_entry or {}).get("url", "")
        result = self._ladder.fetch(
            robots_url,
            proxy=proxy_url or None,
            timeout=min(self.timeout_seconds, 10.0),
            identity=identity_for(host, salt=proxy_url),
            country=country_for_host(host),
            max_rung=1,
        )
        if not result.status:
            return None
        return result.status, result.text

    def head(self, url: str) -> int | None:
        if not self._robots_allow(url):
            logging.debug("robots disallow (HEAD) %s", url)
            return None
        host = self._hostname(url)
        # A HEAD that skipped the proxy pool would send the operator's real
        # address to a host every other request was careful to reach through a
        # proxy — which defeats the point of configuring one at all.
        proxy_entry = self._proxies.acquire() if self._proxies else None
        proxy_url = (proxy_entry or {}).get("url", "")
        throttle_key = f"{host}|{proxy_url}"
        self._throttle.acquire(throttle_key)
        try:
            identity = identity_for(host, salt=proxy_url)
            r = self._session_for(proxy_url).head(
                url,
                headers=headers_for(url, identity, country=country_for_host(host)),
                timeout=self.timeout_seconds,
                allow_redirects=True,
                proxies=self._proxies_dict(proxy_entry),
                verify=True,
            )
            if r.status_code < 400:
                self._proxies.report_success(proxy_entry)
            elif r.status_code in (401, 403, 429):
                self._proxies.report_block(proxy_entry, registrable_domain(url))
            return r.status_code
        except requests.RequestException:
            self._proxies.report_failure(proxy_entry)
            return None
        finally:
            self._throttle.release(throttle_key)

    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        max_age: float | None = None,
        is_index: bool = False,
    ) -> Any | None:
        merged = {"Accept": "application/json"}
        if headers:
            merged.update(headers)
        result = self.get(url, headers=merged, use_cache=use_cache, max_age=max_age, is_index=is_index)
        if result is None:
            return None
        _, body = result
        try:
            return json.loads(body)
        except (ValueError, TypeError):
            logging.debug("non-JSON body from %s (%d bytes)", url, len(body or ""))
            return None

    def post_json(
        self,
        url: str,
        payload: Any,
        headers: dict[str, str] | None = None,
    ) -> Any | None:
        """POST JSON. Not cached."""
        if not self._robots_allow(url):
            return None
        self._global_pace()
        host = self._hostname(url)
        proxy_entry = self._proxies.acquire() if self._proxies else None
        proxy_url = (proxy_entry or {}).get("url", "")
        throttle_key = f"{host}|{proxy_url}"
        self._throttle.acquire(throttle_key)
        # Same rule as `get`: a back-off is this caller's penalty, not a reason
        # to hold a slot every other worker is queued on. Slept after release.
        backoff_seconds = 0.0
        try:
            identity = identity_for(host, salt=proxy_url)
            merged = headers_for(url, identity, country=country_for_host(host))
            merged.update(
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    # An XHR is not a top-level navigation, and claiming to be
                    # one is a contradiction a WAF can test for.
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Site": "same-origin",
                }
            )
            merged.pop("Sec-Fetch-User", None)
            merged.pop("Upgrade-Insecure-Requests", None)
            if headers:
                merged.update(headers)
            try:
                resp = self._session.post(
                    url,
                    json=payload,
                    headers=merged,
                    timeout=self.timeout_seconds,
                    proxies=self._proxies_dict(proxy_entry),
                )
            except requests.RequestException as exc:
                logging.debug("POST error %s: %s", url, exc)
                self._proxies.report_failure(proxy_entry)
                return None
            if resp.status_code == 429:
                backoff_seconds = _retry_after_seconds(resp.headers.get("Retry-After"))
                logging.info("429 from %s — backing off %.1fs", url, backoff_seconds)
                self._proxies.report_failure(proxy_entry)
                return None
            if resp.status_code >= 400:
                if resp.status_code == 403:
                    self._proxies.report_failure(proxy_entry)
                return None
            self._proxies.report_success(proxy_entry)
            try:
                return resp.json()
            except Exception:
                return None
        finally:
            self._throttle.release(throttle_key)
            if backoff_seconds > 0:
                time.sleep(backoff_seconds)
