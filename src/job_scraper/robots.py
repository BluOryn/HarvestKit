"""robots.txt gate.

Three things were wrong with the version this replaces, and each of them cost
real coverage:

1. **It fetched robots.txt with bare `urllib.request`.** That bypassed the
   session, the proxy pool and every header the rest of the engine sends. So
   the very first request to each host — the one a WAF profiles hardest — went
   out from the operator's real IP with no Accept-Language, no Sec-Fetch
   headers and a bot User-Agent, no matter how carefully the run was proxied.
   For anyone who cannot expose their own address, that alone was fatal.

2. **A naked urllib request is the most blockable thing we could send**, so
   robots.txt was *more* likely to be refused than the page it was gating.

3. **A refusal was read as policy, and the RFC says the opposite.** The old
   code treated a 401/403 on robots.txt as "the whole site is off-limits",
   citing RFC 9309. The RFC says the reverse, in both directions:

   - §2.3.1.3 "Unavailable" — status codes in the 400-499 range mean the file
     is unavailable, and "the crawler MAY access any resources on the server".
   - §2.3.1.4 "Unreachable" — 5xx and network errors mean the file is
     *undefined*, and there the crawler "MUST assume complete disallow".

   The old code had 4xx disallowing everything and 5xx allowing everything.
   Both branches were inverted. Measured across twelve European company
   domains, three — hellofresh.de, getyourguide.com and zalando.de — were
   written off entirely on the strength of a 403 that was a bot wall, not a
   policy, and that the RFC never said to honour anyway.

So: the fetch goes through the caller's own transport (proxy, identity and TLS
fingerprint included); 4xx follows the RFC and permits crawling; and a body we
cannot parse as robots.txt expresses no policy rather than a fabricated one. A
site that genuinely publishes `Disallow: /` is still honoured when
`obey_robots` is on — sap.com and n26.com both do, and that is a real answer we
should respect rather than a wall we failed to climb.

The one deliberate divergence is §2.3.1.4: a transient 5xx or a connection
reset is left to `unreadable_is_allowed` (default: allow) rather than
blackholing the host, because at lead-run scale one flaky minute would
otherwise poison a company for the rest of the process. Set it to False for
literal RFC behaviour.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from enum import Enum
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)

# urllib has no default socket timeout. Without this, a host that accepts the
# connection and then stalls hangs the whole run — every deep-scrape worker
# blocks on the same robots fetch.
ROBOTS_TIMEOUT_SECONDS = 6.0
ROBOTS_MAX_BYTES = 512 * 1024

#: A robots.txt body this large, or one that is plainly HTML, is not robots.txt.
#: Challenge pages are served with `Content-Type: text/html` at the /robots.txt
#: path all the time, and `RobotFileParser` will happily parse one into "no
#: rules at all", which reads as blanket permission. Detecting it matters in
#: both directions: we must not infer permission from a block page either.
_HTML_MARKERS = ("<html", "<!doctype html", "<head", "<script")


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    UNKNOWN = "unknown"  # could not read robots.txt — no policy was expressed


#: (status, text) for a URL, or None when the fetch failed outright.
Fetcher = Callable[[str], Optional[tuple[int, str]]]


def _looks_like_html(text: str) -> bool:
    head = text[:2000].lstrip().lower()
    return any(marker in head for marker in _HTML_MARKERS)


class RobotsCache:
    """Per-origin robots.txt cache. Thread-safe (deep_scrape runs N workers).

    `fetcher` is how robots.txt gets retrieved. Pass the engine's own fetch
    function and robots.txt travels the same proxy and presents the same browser
    identity as everything else, which is both politer and far more likely to
    actually return the file. Left unset, it falls back to `urllib` so the class
    still works standalone in tests and tools.
    """

    def __init__(
        self,
        timeout: float = ROBOTS_TIMEOUT_SECONDS,
        *,
        fetcher: Optional[Fetcher] = None,
        unreadable_is_allowed: bool = True,
    ) -> None:
        self.timeout = timeout
        self.fetcher = fetcher
        # RFC 9309 says to treat an unavailable robots.txt as a full disallow.
        # That rule assumes the status is the site speaking. When a WAF answers
        # instead, it is not, and obeying it hands a bot wall the power to
        # delist a site that permits crawling. Default to allowing, and say so
        # in the log so the decision is visible rather than silent.
        self.unreadable_is_allowed = unreadable_is_allowed
        self._cache: dict[str, tuple[Verdict, Optional[RobotFileParser]]] = {}
        self._lock = threading.Lock()

    def set_fetcher(self, fetcher: Fetcher) -> None:
        """Wire in the engine's transport after construction.

        `HttpClient` builds its `RobotsCache` before its own session exists, so
        it hands the fetcher over here rather than at construction time.
        """
        self.fetcher = fetcher

    def is_allowed(self, url: str, user_agent: str) -> bool:
        verdict, parser = self.verdict(url, user_agent)
        if verdict is Verdict.DENY:
            return False
        if verdict is Verdict.UNKNOWN:
            return self.unreadable_is_allowed
        return True if parser is None else parser.can_fetch(user_agent, url)

    def verdict(self, url: str, user_agent: str) -> tuple[Verdict, Optional[RobotFileParser]]:
        """The policy for this URL, and the parser it came from.

        Public because a caller that wants to report *why* a host produced
        nothing needs to tell "they said no" from "we never got to ask".
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return Verdict.ALLOW, None
        base = f"{parsed.scheme}://{parsed.netloc}"

        with self._lock:
            if base in self._cache:
                return self._cache[base]

        result = self._fetch(base, user_agent)

        with self._lock:
            # A concurrent worker may have won the race; either result is valid.
            self._cache.setdefault(base, result)
            return self._cache[base]

    def _fetch(self, base: str, user_agent: str) -> tuple[Verdict, Optional[RobotFileParser]]:
        robots_url = f"{base}/robots.txt"
        fetched = self._retrieve(robots_url, user_agent)
        if fetched is None:
            log.debug("robots: %s unreachable — no policy expressed", robots_url)
            return Verdict.UNKNOWN, None

        status, text = fetched

        if 400 <= status <= 499:
            # RFC 9309 §2.3.1.3 "Unavailable": the whole 4xx range means the
            # file is unavailable and "the crawler MAY access any resources on
            # the server". That covers 404 (no robots.txt at all), 401 and 403
            # alike — and 403 is exactly what a bot wall answers, so the old
            # reading handed every WAF the power to delist a site that in fact
            # permits crawling. No body sniffing is needed here: the status
            # alone settles it, whether the wall replies in HTML, in plain text
            # (rest.arbeitsagentur.de answers 403 with a single space), or with
            # nothing at all.
            if status not in (404, 410):
                log.debug(
                    "robots: %s answered %d — unavailable, so no rules apply (RFC 9309 §2.3.1.3)",
                    robots_url,
                    status,
                )
            return Verdict.ALLOW, None

        if status >= 500:
            # §2.3.1.4 "Unreachable": the RFC mandates complete disallow here.
            # We report it as UNKNOWN and let `unreadable_is_allowed` decide —
            # see the module docstring for why a transient 5xx should not
            # blackhole a company for the life of the process.
            log.debug("robots: %s answered %d — unreachable, no policy read", robots_url, status)
            return Verdict.UNKNOWN, None

        if _looks_like_html(text):
            # A 200 whose body is a web page. Parsing it yields zero rules,
            # which would masquerade as blanket permission.
            log.debug("robots: %s returned HTML, not robots.txt — no policy read", robots_url)
            return Verdict.UNKNOWN, None

        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.parse(text.splitlines())
        except Exception as exc:  # malformed robots.txt should never kill a run
            log.debug("robots: parse failed for %s: %s", robots_url, exc)
            return Verdict.UNKNOWN, None
        return Verdict.ALLOW, parser

    def _retrieve(self, robots_url: str, user_agent: str) -> Optional[tuple[int, str]]:
        """(status, body), via the engine's transport when one was supplied."""
        if self.fetcher is not None:
            try:
                return self.fetcher(robots_url)
            except Exception as exc:
                log.debug("robots: fetcher failed for %s: %s", robots_url, exc)
                return None
        return self._retrieve_with_urllib(robots_url, user_agent)

    def _retrieve_with_urllib(self, robots_url: str, user_agent: str) -> Optional[tuple[int, str]]:
        """Standalone fallback. Used by tests and tools that have no client.

        Note this path sends the real egress IP and a bare bot User-Agent, which
        is why the engine always supplies a fetcher instead.
        """
        request = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(ROBOTS_MAX_BYTES)
                return int(getattr(response, "status", 200) or 200), raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = ""
            with suppress(Exception):
                body = exc.read(ROBOTS_MAX_BYTES).decode("utf-8", errors="replace")
            return int(exc.code), body
        except (TimeoutError, urllib.error.URLError, ValueError, OSError) as exc:
            log.debug("robots fetch failed for %s: %s", robots_url, exc)
            return None
