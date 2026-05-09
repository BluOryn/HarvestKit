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

import logging
import os
import random
import sqlite3
import threading
import time
import zlib
from typing import Any, Dict, List, Optional, Tuple

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
    "checking your browser",
    "ddos protection",
    "cf-error",
    "request_id:",
)


def _looks_like_block(text: str) -> bool:
    """Heuristic: small response that smells like a WAF/bot wall."""
    if not text:
        return True
    lo = text.lower()
    if len(lo) < 5000 and any(kw in lo for kw in _BLOCK_KEYWORDS):
        return True
    if len(lo) < 1500 and "<jobposting" not in lo and "schema.org" not in lo:
        if "<title>" in lo and "error" in lo:
            return True
    return False


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
        proxies: List[str],
        rotation: str = "round_robin",
        max_failures: int = 3,
        cooldown_seconds: int = 300,
    ) -> None:
        self.entries: List[Dict[str, Any]] = [
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
        self._jars: Dict[str, requests.cookies.RequestsCookieJar] = {}

    def __bool__(self) -> bool:
        return bool(self.entries)

    def acquire(self) -> Optional[Dict[str, Any]]:
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
                    return entry
            return None  # all dead

    def report_success(self, entry: Optional[Dict[str, Any]]) -> None:
        if entry is None:
            return
        with self._lock:
            entry["failures"] = 0

    def report_failure(self, entry: Optional[Dict[str, Any]]) -> None:
        if entry is None:
            return
        with self._lock:
            entry["failures"] += 1
            if entry["failures"] >= self.max_failures:
                entry["dead_until"] = time.time() + self.cooldown_seconds
                entry["failures"] = 0
                logging.info("proxy %s cooled down for %ds", entry["url"], self.cooldown_seconds)

    def jar_for(self, entry: Optional[Dict[str, Any]]) -> requests.cookies.RequestsCookieJar:
        key = entry["url"] if entry else "_direct_"
        with self._lock:
            jar = self._jars.get(key)
            if jar is None:
                jar = requests.cookies.RequestsCookieJar()
                self._jars[key] = jar
            return jar

    def stats(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self.entries]


class _HostThrottle:
    """Per-(host, proxy) inflight cap + min-delay throttle. Thread-safe."""

    def __init__(self, max_inflight: int, min_delay: float) -> None:
        self.max_inflight = max(1, max_inflight)
        self.min_delay = max(0.0, min_delay)
        self._cv = threading.Condition()
        self._inflight: Dict[str, int] = {}
        self._last: Dict[str, float] = {}

    def acquire(self, key: str) -> None:
        with self._cv:
            while True:
                wait = self.min_delay - (time.time() - self._last.get(key, 0.0))
                if self._inflight.get(key, 0) < self.max_inflight and wait <= 0:
                    self._inflight[key] = self._inflight.get(key, 0) + 1
                    self._last[key] = time.time()
                    return
                self._cv.wait(timeout=max(0.05, wait))

    def release(self, key: str) -> None:
        with self._cv:
            self._inflight[key] = max(0, self._inflight.get(key, 0) - 1)
            self._cv.notify_all()


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

    def get(self, url: str) -> Optional[Tuple[str, str]]:
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
        proxies: Optional[List[str]] = None,
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
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD", "POST"]),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=40)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._cache: Optional[_ResponseCache] = (
            _ResponseCache(cache_path, cache_ttl_seconds) if cache_enabled else None
        )
        self._throttle = _HostThrottle(per_host_concurrency, per_host_min_delay)
        self._proxies = _ProxyPool(
            proxies or [],
            rotation=proxy_rotation,
            max_failures=proxy_max_failures,
            cooldown_seconds=proxy_cooldown_seconds,
        )
        if self._proxies:
            logging.info("HTTP: %d proxies loaded (%s rotation)", len(self._proxies.entries), proxy_rotation)

    @property
    def has_proxies(self) -> bool:
        return bool(self._proxies)

    def proxy_stats(self) -> List[Dict[str, Any]]:
        return self._proxies.stats()

    def _pick_ua(self) -> str:
        if self.rotate_user_agents and self.ua_pool:
            return random.choice(self.ua_pool)
        return self.user_agent

    def _hostname(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc or "_"
        except Exception:
            return "_"

    def _global_pace(self) -> None:
        with self._global_lock:
            elapsed = time.time() - self._last_request_at
            wait = self.delay_seconds - elapsed
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.25))
            self._last_request_at = time.time()

    @staticmethod
    def _proxies_dict(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
        if not entry:
            return None
        url = entry.get("url") or ""
        if not url:
            return None
        return {"http": url, "https": url}

    @staticmethod
    def _stealth_headers(url: str, ua: str) -> Dict[str, str]:
        """Browser-like default headers, including modern Sec-Fetch-* + Sec-CH-UA hints."""
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        is_chromium = "Chrome" in ua and "Edg" not in ua and "Safari" in ua
        is_firefox = "Firefox" in ua
        h: Dict[str, str] = {
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
            h["Sec-CH-UA-Platform"] = '"Windows"' if "Windows" in ua else ('"macOS"' if "Macintosh" in ua else '"Linux"')
        if is_firefox:
            h["TE"] = "trailers"
        # Plausible referer for detail pages
        if "/detail/" in url or "/job/" in url or "/jobs/" in url:
            h["Referer"] = f"https://{host}/"
        return h

    def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        allow_404: bool = False,
        use_cache: bool = True,
    ) -> Optional[Tuple[str, str]]:
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
                retry_after = response.headers.get("Retry-After")
                try:
                    wait_s = float(retry_after) if retry_after else 8.0
                except ValueError:
                    wait_s = 8.0
                logging.info("429 from %s — backing off %ss", url, wait_s)
                self._proxies.report_failure(proxy_entry)
                time.sleep(min(wait_s, 30.0))
                return None
            if response.status_code == 403:
                # WAF / proxy block
                self._proxies.report_failure(proxy_entry)
                return None
            if response.status_code >= 400 and not allow_404:
                logging.debug("HTTP %s %s", response.status_code, url)
                return None
            response.encoding = response.apparent_encoding or response.encoding or "utf-8"
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

    def head(self, url: str) -> Optional[int]:
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

    def get_json(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True,
    ) -> Optional[Any]:
        merged = {"Accept": "application/json"}
        if headers:
            merged.update(headers)
        result = self.get(url, headers=merged, use_cache=use_cache)
        if result is None:
            return None
        _, body = result
        try:
            import json
            return json.loads(body)
        except Exception:
            return None

    def post_json(
        self,
        url: str,
        payload: Any,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Any]:
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
                time.sleep(min(float(resp.headers.get("Retry-After", "8") or 8), 30.0))
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
