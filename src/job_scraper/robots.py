"""robots.txt gate.

Deliberately fail-open: a robots.txt we cannot fetch (network error, timeout,
5xx) must not silently drop every URL on the host. It is cached either way, so a
dead robots endpoint costs one request per host instead of one per URL.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

# urllib has no default socket timeout. Without this, a host that accepts the
# connection and then stalls hangs the whole run — every deep-scrape worker
# blocks on the same robots fetch.
ROBOTS_TIMEOUT_SECONDS = 6.0
ROBOTS_MAX_BYTES = 512 * 1024


class RobotsCache:
    """Per-origin robots.txt cache. Thread-safe (deep_scrape runs N workers)."""

    def __init__(self, timeout: float = ROBOTS_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout
        self._cache: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def is_allowed(self, url: str, user_agent: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return True
        base = f"{parsed.scheme}://{parsed.netloc}"

        with self._lock:
            if base in self._cache:
                parser = self._cache[base]
                return True if parser is None else parser.can_fetch(user_agent, url)

        parser = self._fetch(base, user_agent)

        with self._lock:
            # A concurrent worker may have won the race; either result is valid.
            self._cache.setdefault(base, parser)
            parser = self._cache[base]

        return True if parser is None else parser.can_fetch(user_agent, url)

    def _fetch(self, base: str, user_agent: str) -> RobotFileParser | None:
        """Return a parser, or None meaning "unknown — allow everything".

        RobotFileParser.read() calls urlopen() with no timeout and no UA, so we
        do the fetch ourselves and hand the text to parse().
        """
        robots_url = f"{base}/robots.txt"
        request = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(ROBOTS_MAX_BYTES)
        except urllib.error.HTTPError as exc:
            parser = RobotFileParser()
            parser.set_url(robots_url)
            if exc.code in (401, 403):
                # RFC 9309: auth-required robots.txt means the whole site is off-limits.
                parser.disallow_all = True
                return parser
            # 404/410/5xx → no usable rules. Allow.
            return None
        except (TimeoutError, urllib.error.URLError, ValueError, OSError) as exc:
            logging.debug("robots fetch failed for %s: %s", robots_url, exc)
            return None

        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.parse(raw.decode("utf-8", errors="replace").splitlines())
        except Exception as exc:  # malformed robots.txt should never kill a run
            logging.debug("robots parse failed for %s: %s", robots_url, exc)
            return None
        return parser
