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

from .robots import RobotsCache

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


DEFAULT_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


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
        self._exhausted_warned = False
        # Per-proxy cookie jar — each proxy = its own browser session.
        self._jars: dict[str, requests.cookies.RequestsCookieJar] = {}

    def __bool__(self) -> bool:
        return bool(self.entries)

    def acquire(self) -> dict[str, Any] | None:
        """Return a live proxy, or None when every proxy is cooling down.

        None makes the caller fall back to a direct connection, which leaks the
        real egress IP — so warn loudly (once per exhaustion event) rather than
        failing over silently.
        """
        if not self.entries:
            return None
        with self._lock:
            now = time.time()
            n = len(self.entries)
            for _ in range(n):
                if self.rotation == "random":
                    idx = random.randrange(n)
                else:
                    idx = self._cursor % n
                    self._cursor = (self._cursor + 1) % n
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
        if entry is None:
            return
        with self._lock:
            entry["failures"] += 1
            if entry["failures"] >= self.max_failures:
                entry["dead_until"] = time.time() + self.cooldown_seconds
                entry["failures"] = 0
                logging.info("proxy %s cooled down for %ds", entry["url"], self.cooldown_seconds)

    def jar_for(self, entry: dict[str, Any] | None) -> requests.cookies.RequestsCookieJar:
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

    def get(self, url: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT final_url, body, fetched_at FROM http_cache WHERE url=?",
                (url,),
            ).fetchone()
        if not row:
            return None
        final_url, body, fetched_at = row
        if time.time() - (fetched_at or 0) > self.ttl:
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
    ) -> None:
        self.user_agent = user_agent
        self.delay_seconds = max(0.0, delay_seconds)
        self.obey_robots = obey_robots
        self.timeout_seconds = timeout_seconds
        self.rotate_user_agents = rotate_user_agents
        self.ua_pool = DEFAULT_UA_POOL
        self._last_request_at = 0.0
        self._global_lock = threading.Lock()
        self._robots = RobotsCache()
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
        self._cache: _ResponseCache | None = (
            _ResponseCache(cache_path, cache_ttl_seconds) if cache_enabled else None
        )
        self._throttle = HostThrottle(per_host_concurrency, per_host_min_delay)
        self._proxies = _ProxyPool(
            proxies or [],
            rotation=proxy_rotation,
            max_failures=proxy_max_failures,
            cooldown_seconds=proxy_cooldown_seconds,
        )
        if self._proxies:
            logging.info("HTTP: %d proxies loaded (%s rotation)", len(self._proxies.entries), proxy_rotation)

    def close(self) -> None:
        """Release the connection pool and the SQLite cache handle.

        Long runs that create several clients (tools/, tests) otherwise leak an
        open sqlite connection and a pool of sockets per client.
        """
        with suppress(Exception):
            self._session.close()
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

    def _pick_ua(self) -> str:
        if self.rotate_user_agents and self.ua_pool:
            return random.choice(self.ua_pool)
        return self.user_agent

    @staticmethod
    def _hostname(url: str) -> str:
        try:
            return urlparse(url).netloc or "_"
        except ValueError:
            return "_"

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
        if not url:
            return None
        return {"http": url, "https": url}

    @staticmethod
    def _stealth_headers(url: str, ua: str) -> dict[str, str]:
        """Browser-like default headers, including modern Sec-Fetch-* + Sec-CH-UA hints."""
        host = urlparse(url).netloc
        is_chromium = "Chrome" in ua and "Edg" not in ua and "Safari" in ua
        is_firefox = "Firefox" in ua
        h: dict[str, str] = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,image/avif,image/webp,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9,de;q=0.8,fr;q=0.7,it;q=0.6",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
        }
        if is_chromium:
            h["Sec-CH-UA"] = '"Chromium";v="120", "Not A(Brand";v="99", "Google Chrome";v="120"'
            h["Sec-CH-UA-Mobile"] = "?0"
            h["Sec-CH-UA-Platform"] = (
                '"Windows"' if "Windows" in ua else ('"macOS"' if "Macintosh" in ua else '"Linux"')
            )
        if is_firefox:
            h["TE"] = "trailers"
        # Plausible referer for detail pages
        if "/detail/" in url or "/job/" in url or "/jobs/" in url:
            h["Referer"] = f"https://{host}/"
        return h

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        allow_404: bool = False,
        use_cache: bool = True,
    ) -> tuple[str, str] | None:
        if self.obey_robots and not self._robots.is_allowed(url, self.user_agent):
            logging.debug("robots disallow %s", url)
            return None
        if use_cache and self._cache is not None:
            cached = self._cache.get(url)
            if cached is not None:
                return cached
        self._global_pace()
        host = self._hostname(url)
        proxy_entry = self._proxies.acquire() if self._proxies else None
        proxy_url = (proxy_entry or {}).get("url", "")
        throttle_key = f"{host}|{proxy_url}"
        self._throttle.acquire(throttle_key)
        # Backing off is this caller's penalty, not a reason to keep holding a
        # slot every other worker is queued on. Recorded here, slept after the
        # release below.
        backoff_seconds = 0.0
        try:
            ua = self._pick_ua()
            merged = self._stealth_headers(url, ua)
            if headers:
                merged.update(headers)
            jar = self._proxies.jar_for(proxy_entry)
            try:
                response = self._session.get(
                    url,
                    headers=merged,
                    timeout=self.timeout_seconds,
                    allow_redirects=True,
                    proxies=self._proxies_dict(proxy_entry),
                    cookies=jar,
                )
                # Persist cookies into the per-proxy jar
                jar.update(response.cookies)
            except requests.RequestException as exc:
                logging.debug("HTTP error %s: %s", url, exc)
                self._proxies.report_failure(proxy_entry)
                return None
            if response.status_code == 429:
                backoff_seconds = _retry_after_seconds(response.headers.get("Retry-After"))
                logging.info("429 from %s — backing off %.1fs", url, backoff_seconds)
                self._proxies.report_failure(proxy_entry)
                return None
            if response.status_code == 403:
                # WAF / proxy block
                self._proxies.report_failure(proxy_entry)
                return None
            if response.status_code >= 400 and not allow_404:
                logging.debug("HTTP %s %s", response.status_code, url)
                return None
            # requests falls back to ISO-8859-1 whenever a text/* response omits
            # charset, which mangles UTF-8 Norwegian/German pages. Only pay for
            # charset sniffing (a full-body scan) in that specific case.
            if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
                response.encoding = response.apparent_encoding or "utf-8"
            final_url = response.url
            text = response.text
            if _looks_like_block(text):
                self._proxies.report_failure(proxy_entry)
                logging.debug("WAF/block at %s — signaling retry", url)
                return None
            self._proxies.report_success(proxy_entry)
            if self._cache is not None and use_cache:
                self._cache.put(url, final_url, text)
            return final_url, text
        finally:
            self._throttle.release(throttle_key)
            if backoff_seconds > 0:
                time.sleep(backoff_seconds)

    def head(self, url: str) -> int | None:
        if self.obey_robots and not self._robots.is_allowed(url, self.user_agent):
            logging.debug("robots disallow (HEAD) %s", url)
            return None
        host = self._hostname(url)
        self._throttle.acquire(f"{host}|")
        try:
            r = self._session.head(
                url,
                headers={"User-Agent": self._pick_ua()},
                timeout=self.timeout_seconds,
                allow_redirects=True,
            )
            return r.status_code
        except requests.RequestException:
            return None
        finally:
            self._throttle.release(f"{host}|")

    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> Any | None:
        merged = {"Accept": "application/json"}
        if headers:
            merged.update(headers)
        result = self.get(url, headers=merged, use_cache=use_cache)
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
        if self.obey_robots and not self._robots.is_allowed(url, self.user_agent):
            return None
        self._global_pace()
        host = self._hostname(url)
        proxy_entry = self._proxies.acquire() if self._proxies else None
        proxy_url = (proxy_entry or {}).get("url", "")
        throttle_key = f"{host}|{proxy_url}"
        self._throttle.acquire(throttle_key)
        try:
            merged = {
                "User-Agent": self._pick_ua(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
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
                self._proxies.report_failure(proxy_entry)
                time.sleep(min(_retry_after_seconds(resp.headers.get("Retry-After")), 30.0))
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
