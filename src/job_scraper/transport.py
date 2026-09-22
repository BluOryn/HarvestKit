"""Fetch transports, and the ladder that escalates between them.

The engine used to have exactly one way to fetch a page: `requests`. When a WAF
refused that, `get()` returned `None` and the URL was gone for the rest of the
run. Measured against sixteen European company domains, that cost three of them
outright — `getyourguide.com` and `zalando.de` answered 403, and `personio.de`
answered 429, to `requests`; all three answer 200 to a client presenting a real
Chrome TLS fingerprint. No header change reaches those sites, because what they
score is the ClientHello, which `requests` cannot alter.

So fetching becomes a ladder, cheapest rung first:

    1. `requests`     — fast, no extra process, fine for APIs and most SSR HTML.
    2. `curl_cffi`    — real Chrome/Firefox/Safari TLS + HTTP2 fingerprint.
    3. a stealth browser — executes JS, for sites that ship no server-side HTML
                           or that demand a JS challenge be solved.

Two things make this cheap rather than three times the work:

**Per-domain memory.** The rung that worked for a domain is written to SQLite
and tried first next time. A run that learned `zalando.de` needs impersonation
does not re-pay the 403 on every subsequent URL, and a domain that never needed
escalation never pays for it at all.

**Honest classification.** Escalation is only worth anything if we can tell a
block from a genuine 404 and, harder, from a page that returns HTTP 200 and
contains nothing but a JS challenge. `classify()` below does that, and it is
the part most worth reading: a soft block scored as success is how a run comes
to report thousands of companies that "name nobody".
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from . import browser as browser_mod
from .identity import accept_language_for, headers_for
from .net_guard import is_safe_url

log = logging.getLogger(__name__)


class Outcome(str, Enum):
    """Why a fetch ended the way it did. The distinction that matters most is
    BLOCKED vs EMPTY: the first is our problem and worth escalating, the second
    is the site's answer and escalating it only wastes requests."""

    OK = "ok"
    BLOCKED = "blocked"  # WAF, bot wall, challenge page — try a better transport
    RATE_LIMITED = "rate_limited"  # 429 — slow down, do not escalate
    NOT_FOUND = "not_found"  # 404/410 — the page is genuinely absent
    SERVER_ERROR = "server_error"  # 5xx — theirs, retry later
    ERROR = "error"  # DNS, TLS, timeout, connection reset
    ROBOTS_DENIED = "robots_denied"


#: Body markers for a challenge or interstitial served *with* a success status.
#: These are deliberately specific product strings rather than generic words:
#: "access denied" appears in the cookie banner of plenty of real pages, and the
#: previous heuristic threw those away.
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "just a moment...",
    "attention required! | cloudflare",
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "cf-browser-verification",
    "cf_chl_opt",
    "/cdn-cgi/challenge-platform",
    "_incapsula_resource",
    "incapsula incident id",
    "powered by datadome",
    "captcha-delivery.com",
    "geo.captcha-delivery",
    "px-captcha",
    "perimeterx",
    "/_px/",
    "please verify you are a human",
    "are you a robot",
    "request unsuccessful. incapsula",
    "this request seems a bit unusual",
    "your request has been blocked",
    "bot detection",
    "akamai reference",
    "reference #18.",  # Akamai "Access Denied" reference block
    "radware",
    "sucuri website firewall",
    "queue-it.net",
)

#: Headers a challenge sets even when the body is opaque.
_CHALLENGE_HEADERS: tuple[tuple[str, str], ...] = (
    ("cf-mitigated", "challenge"),
    ("x-datadome", ""),
    ("x-datadome-cid", ""),
    ("x-iinfo", ""),  # Imperva/Incapsula
    ("x-px-block", ""),
)

#: Presence of any of these says a real document came back, which vetoes the
#: heuristics below. A challenge page carries none of them.
_CONTENT_MARKERS: tuple[str, ...] = (
    "application/ld+json",
    'property="og:',
    "<article",
    "<main",
    "schema.org",
    "</nav>",
    "<footer",
)

_TITLE_RX = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

#: Redirect hops followed before giving up. `requests` defaults to 30, and
#: each hop used to get a fresh urllib3 retry budget, so a chain of slow
#: redirects could hold a worker for tens of minutes.
_MAX_REDIRECTS = 8

#: Transport errors that a better transport cannot fix, so escalating past them
#: only doubles the cost of a domain that was never going to answer.
_UNFIXABLE_ERRORS: tuple[str, ...] = (
    "nameresolutionerror",
    "name or service not known",
    "getaddrinfo failed",
    "could not resolve host",
    "nodename nor servname",
    "temporary failure in name resolution",
    "no address associated with hostname",
)


@dataclass
class FetchResponse:
    """The result of one fetch attempt, whatever transport produced it."""

    url: str
    final_url: str = ""
    status: int = 0
    text: str = ""
    outcome: Outcome = Outcome.ERROR
    transport: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str = ""
    retry_after: float = 0.0

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    @property
    def escalatable(self) -> bool:
        """Worth retrying on a stronger transport. A 404 is not — the page is
        absent no matter how convincing the browser asking for it."""
        if self.outcome is Outcome.BLOCKED:
            return True
        if self.outcome is Outcome.ERROR:
            # A hostname that does not resolve will not resolve for a better
            # client either, and a dead domain retried up the whole ladder is
            # pure cost — at lead-run scale, thousands of them.
            lowered = (self.error or "").lower()
            return not any(marker in lowered for marker in _UNFIXABLE_ERRORS)
        return False


def classify(status: int, text: str, headers: Optional[dict[str, str]] = None) -> Outcome:
    """Decide what a response actually was.

    Ordered so the cheapest and most certain signals run first, and so that a
    positive content signal can veto the fuzzy body heuristics.
    """
    lowered_headers = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    for name, needle in _CHALLENGE_HEADERS:
        value = lowered_headers.get(name)
        if value is not None and (not needle or needle in value):
            return Outcome.BLOCKED

    if status == 429:
        return Outcome.RATE_LIMITED
    if status in (401, 402, 403, 406, 451):
        return Outcome.BLOCKED
    if status in (404, 410):
        return Outcome.NOT_FOUND
    if status >= 500:
        # 503 is the status Cloudflare's legacy interstitial used, so a body
        # check still applies before calling it the origin's own fault.
        if status == 503 and _body_is_challenge(text):
            return Outcome.BLOCKED
        return Outcome.SERVER_ERROR
    if status and status >= 400:
        return Outcome.BLOCKED

    # 2xx/3xx from here. A success status is not yet a success.
    if not text or not text.strip():
        # An empty 200 is a bot wall often enough, and useless either way.
        return Outcome.BLOCKED
    if _body_is_challenge(text):
        return Outcome.BLOCKED
    return Outcome.OK


#: A rendered DOM this long, carrying real structure and no challenge marker,
#: is the page — whatever the navigation response said on the way in.
_RENDERED_CONTENT_FLOOR = 20_000


def classify_rendered(status: int, text: str, headers: Optional[dict[str, str]] = None) -> Outcome:
    """Classify what a *browser* ended up with, not what the first byte said.

    The navigation status and the final document are frequently different
    things once JavaScript has run: a managed challenge answers 403 or 405,
    then solves itself and swaps in the real page. Judging on the status alone
    threw away exactly the content the browser was launched to obtain —
    measured on siemens.com, which answered 405 to the navigation and then
    rendered a megabyte of real page.

    A rate limit is still a rate limit, and a genuine 404 is still absent; only
    the "blocked" verdict can be overturned, and only by substantial, structured
    content.
    """
    verdict = classify(status, text, headers)
    if verdict is not Outcome.BLOCKED:
        return verdict
    if len(text or "") < _RENDERED_CONTENT_FLOOR:
        return verdict
    if _body_is_challenge(text):
        return verdict
    lowered = (text or "")[:200_000].lower()
    if not any(marker in lowered for marker in _CONTENT_MARKERS):
        return verdict
    log.debug("transport: navigation said %s but the rendered DOM is a real page", status)
    return Outcome.OK


def _body_is_challenge(text: str) -> bool:
    """True when the body is an interstitial rather than a document."""
    if not text:
        return False
    head = text[:200_000].lower()

    for marker in _CHALLENGE_MARKERS:
        if marker in head:
            return True

    title_match = _TITLE_RX.search(head)
    title = (title_match.group(1).strip() if title_match else "")[:120]
    if title in ("just a moment", "just a moment...", "access denied", "attention required!", "blocked"):
        return True

    # A short document with no structural markers and a hostile title. The size
    # gate matters: a real page that merely *mentions* access control is long
    # and carries markers, an interstitial is neither.
    if len(head) < 6000 and not any(marker in head for marker in _CONTENT_MARKERS):
        if title and any(word in title for word in ("denied", "forbidden", "blocked", "robot", "captcha")):
            return True
        if "<html" in head and ("error 1" in head or "cloudflare" in head):
            return True
    return False


class TransportMemory:
    """Which rung last worked for a domain, persisted across runs.

    Without this the ladder re-discovers `zalando.de needs impersonation` on
    every single URL, paying a 403 each time. With it, the first URL on a domain
    pays that once and the rest of the run starts where it left off.
    """

    def __init__(self, path: str = ".cache/transport_memory.sqlite") -> None:
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS transport_memory ("
            "domain TEXT PRIMARY KEY, rung INTEGER NOT NULL, "
            "successes INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, updated_at INTEGER)"
        )

    def preferred_rung(self, domain: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT rung FROM transport_memory WHERE domain=?", (domain,)).fetchone()
        return int(row[0]) if row else 0

    def record(self, domain: str, rung: int, *, success: bool) -> None:
        now = int(time.time())
        with self._lock, suppress(sqlite3.Error):
            if success:
                self._conn.execute(
                    "INSERT INTO transport_memory(domain, rung, successes, failures, updated_at) "
                    "VALUES (?, ?, 1, 0, ?) ON CONFLICT(domain) DO UPDATE SET "
                    "rung=excluded.rung, successes=successes+1, updated_at=excluded.updated_at",
                    (domain, rung, now),
                )
            else:
                self._conn.execute(
                    "INSERT INTO transport_memory(domain, rung, successes, failures, updated_at) "
                    "VALUES (?, ?, 0, 1, ?) ON CONFLICT(domain) DO UPDATE SET "
                    "failures=failures+1, updated_at=excluded.updated_at",
                    (domain, rung, now),
                )

    def stats(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT domain, rung, successes, failures FROM transport_memory ORDER BY rung DESC, domain"
            ).fetchall()
        return [{"domain": r[0], "rung": r[1], "successes": r[2], "failures": r[3]} for r in rows]

    def close(self) -> None:
        with self._lock, suppress(sqlite3.Error):
            self._conn.close()


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def registrable_domain(url_or_host: str) -> str:
    """Best-effort eTLD+1, used as the memory key so that `careers.acme.de` and
    `www.acme.de` share what was learned about the WAF in front of both."""
    host = host_of(url_or_host) or (url_or_host or "").lower().strip("/")
    if not host:
        return ""
    try:
        import tldextract

        extracted = tldextract.extract(host)
        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}"
    except Exception:  # tldextract absent or its cache unwritable — fall through
        pass
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------


#: Headers whose value belongs to the impersonation profile, not to the caller.
#: Letting a caller set these on the impersonating transport recreates exactly
#: the header/fingerprint contradiction this module exists to remove.
_IDENTITY_HEADERS = frozenset(
    {"user-agent", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "accept-encoding"}
)


def _retry_after(header: Optional[str]) -> float:
    """Seconds to wait, capped at 30. Uncapped honouring of this header is how
    one rate-limited host parks a worker for an hour holding its throttle slot
    while every other worker queues behind it."""
    if not header:
        return 0.0
    value = header.strip()
    try:
        return max(0.0, min(float(value), 30.0))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if target is None:
        return 0.0
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0.0, min((target - datetime.now(timezone.utc)).total_seconds(), 30.0))


class Transport:
    """One way of fetching a URL. Rungs are ordered cheapest-first."""

    name = "base"
    rung = 0

    @property
    def available(self) -> bool:
        return True

    def fetch(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: float = 20.0,
        identity: Any = None,
        country: Optional[str] = None,
        cookies: Any = None,
        session: Any = None,
    ) -> FetchResponse:
        raise NotImplementedError

    def close(self) -> None:
        return None


class RequestsTransport(Transport):
    """Plain `requests`. Cheapest, and still right for the great majority of
    hosts — APIs, ATS feeds, and any server-rendered site without a WAF."""

    name = "requests"
    rung = 0

    def __init__(self, session: Any) -> None:
        #: Fallback session, used when the caller names none.
        self._session = session

    def fetch(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: float = 20.0,
        identity: Any = None,
        country: Optional[str] = None,
        cookies: Any = None,
        session: Any = None,
    ) -> FetchResponse:
        import requests as _requests

        merged = headers_for(url, identity, country=country) if identity is not None else {}
        if headers:
            merged.update(headers)
        proxies = {"http": proxy, "https": proxy} if proxy else None
        # The caller picks the session so each proxy gets its own cookie jar and
        # connection pool. One shared Session merged every jar it was handed
        # into `session.cookies` and replayed them through every exit.
        http_session = session if session is not None else self._session
        # Redirects are followed by hand so every hop can be checked.
        #
        # Every URL this engine fetches is attacker-influenced — a <loc> in
        # somebody's sitemap, an apply_url out of a job ad. `is_safe_url` gates
        # the URL we were given, but with `allow_redirects=True` the *target*
        # was never re-checked, so a page under an attacker's control could
        # answer `302 Location: http://169.254.169.254/latest/meta-data/` and
        # the run would fetch cloud credentials and hand the body back to the
        # extractor. Guarding only the first URL guards nothing.
        current = url
        response = None
        try:
            for _ in range(_MAX_REDIRECTS):
                response = http_session.get(
                    current,
                    headers=merged,
                    timeout=timeout,
                    allow_redirects=False,
                    proxies=proxies,
                    cookies=cookies,
                    # Never turned off. A free or otherwise untrusted proxy that
                    # could also strip certificate validation would be able to
                    # read and rewrite every page this tool fetches.
                    verify=True,
                )
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                location = response.headers.get("Location") or ""
                if not location:
                    break
                current = urljoin(current, location)
                if not is_safe_url(current):
                    log.warning(
                        "transport: %s redirected to %s, which resolves into internal address "
                        "space — refusing to follow",
                        url,
                        current,
                    )
                    return FetchResponse(
                        url=url,
                        final_url=current,
                        outcome=Outcome.ERROR,
                        transport=self.name,
                        error="redirect into internal address space",
                    )
            else:
                return FetchResponse(
                    url=url,
                    final_url=current,
                    outcome=Outcome.ERROR,
                    transport=self.name,
                    error=f"more than {_MAX_REDIRECTS} redirects",
                )
        except _requests.RequestException as exc:
            return FetchResponse(url=url, outcome=Outcome.ERROR, transport=self.name, error=str(exc)[:200])
        if response is None:
            return FetchResponse(url=url, outcome=Outcome.ERROR, transport=self.name, error="no response")

        if cookies is not None:
            with suppress(Exception):
                cookies.update(response.cookies)

        # `requests` falls back to ISO-8859-1 for any text/* response that omits
        # a charset, which mangles the umlauts and slashed o's that European
        # names are full of. Only pay for a body scan in that specific case.
        if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
            response.encoding = response.apparent_encoding or "utf-8"
        text = response.text
        response_headers = dict(response.headers)
        return FetchResponse(
            url=url,
            final_url=response.url,
            status=response.status_code,
            text=text,
            outcome=classify(response.status_code, text, response_headers),
            transport=self.name,
            headers=response_headers,
            retry_after=_retry_after(response_headers.get("Retry-After")),
        )


class ImpersonateTransport(Transport):
    """`curl_cffi`, presenting a real browser's TLS and HTTP/2 fingerprint.

    This is the rung that recovers sites which score the ClientHello rather than
    the headers — Akamai, Cloudflare and Imperva all do. Measured on the sixteen
    EU company domains used to design this ladder, it turned three hard failures
    into 200s that no amount of header tuning on rung 0 could have reached.
    """

    name = "impersonate"
    rung = 1

    def __init__(self) -> None:
        self._local = threading.local()
        self._available: Optional[bool] = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                import curl_cffi  # noqa: F401

                self._available = True
            except Exception:
                self._available = False
                log.warning(
                    "transport: curl_cffi is not installed, so the impersonation rung is off and "
                    "every TLS-fingerprinting site will stay blocked. Install it with "
                    "`pip install curl_cffi`."
                )
        return bool(self._available)

    def _session(self) -> Any:
        # A curl_cffi session wraps a libcurl handle, which is not safe to share
        # across threads. One per thread, reused so the connection and the TLS
        # session ticket survive between requests to the same host.
        session = getattr(self._local, "session", None)
        if session is None:
            from curl_cffi import requests as cf

            session = cf.Session()
            self._local.session = session
        return session

    def fetch(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: float = 20.0,
        identity: Any = None,
        country: Optional[str] = None,
        cookies: Any = None,
        session: Any = None,
    ) -> FetchResponse:
        if not self.available:
            return FetchResponse(
                url=url, outcome=Outcome.ERROR, transport=self.name, error="curl_cffi not installed"
            )
        merged = (
            headers_for(url, identity, country=country, for_impersonation=True)
            if identity is not None
            else {}
        )
        if headers:
            merged.update({k: v for k, v in headers.items() if k.lower() not in _IDENTITY_HEADERS})
        profile = getattr(identity, "impersonate", None) or "chrome"
        # Hops are guarded here for the same reason as on the requests rung: a
        # scraped URL that redirects into internal address space must not be
        # followed. See RequestsTransport.fetch.
        current = url
        response = None
        try:
            for _ in range(_MAX_REDIRECTS):
                response = self._session().get(
                    current,
                    headers=merged,
                    timeout=timeout,
                    allow_redirects=False,
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                    impersonate=profile,
                    verify=True,
                )
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                location = response.headers.get("Location") or ""
                if not location:
                    break
                current = urljoin(current, location)
                if not is_safe_url(current):
                    log.warning(
                        "transport: %s redirected to %s, which resolves into internal address "
                        "space — refusing to follow",
                        url,
                        current,
                    )
                    return FetchResponse(
                        url=url,
                        final_url=current,
                        outcome=Outcome.ERROR,
                        transport=self.name,
                        error="redirect into internal address space",
                    )
            else:
                return FetchResponse(
                    url=url,
                    final_url=current,
                    outcome=Outcome.ERROR,
                    transport=self.name,
                    error=f"more than {_MAX_REDIRECTS} redirects",
                )
        except Exception as exc:
            return FetchResponse(url=url, outcome=Outcome.ERROR, transport=self.name, error=str(exc)[:200])
        if response is None:
            return FetchResponse(url=url, outcome=Outcome.ERROR, transport=self.name, error="no response")

        try:
            text = response.text
        except Exception:
            text = (response.content or b"").decode("utf-8", errors="replace")
        response_headers = {str(k): str(v) for k, v in dict(response.headers).items()}
        return FetchResponse(
            url=url,
            final_url=str(response.url),
            status=response.status_code,
            text=text,
            outcome=classify(response.status_code, text, response_headers),
            transport=self.name,
            headers=response_headers,
            retry_after=_retry_after(response_headers.get("Retry-After")),
        )

    def close(self) -> None:
        session = getattr(self._local, "session", None)
        if session is not None:
            with suppress(Exception):
                session.close()


class BrowserTransport(Transport):
    """A real browser, for pages that ship no server-side HTML or that insist a
    JS challenge be solved before serving one.

    The driver lives on a dedicated thread (see `browser.BrowserWorker`), which
    is what makes this usable from the deep-scrape pool at all: Playwright's
    sync API is pinned to the greenlet that created it, so a second thread
    calling into it raises `greenlet.error` no matter what lock is held.

    Prefers `patchright` over `playwright` when installed. Patchright is a
    drop-in fork that removes the tells plain Playwright leaves behind — chiefly
    the `Runtime.enable` CDP call, which is what Cloudflare and DataDome look
    for first. The API is identical, so this class works with either.

    Expensive: a browser context costs roughly 80-150 MB and a second or two per
    page. It is the last rung for exactly that reason, and the per-domain memory
    in `TransportMemory` keeps it to the domains that genuinely need it.
    """

    name = "browser"
    rung = 2

    def __init__(self, *, headless: bool = True, max_concurrent: int = 2) -> None:
        self.headless = headless
        self._slots = threading.Semaphore(max(1, max_concurrent))
        self._worker = browser_mod.BrowserWorker(headless=headless)
        self._available: Optional[bool] = None

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = bool(browser_mod.driver_module())
            if not self._available:
                log.info(
                    "transport: neither patchright nor playwright is installed, so the browser "
                    "rung is off. `pip install patchright && patchright install chromium` "
                    "enables it."
                )
        return bool(self._available)

    def fetch(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: float = 20.0,
        identity: Any = None,
        country: Optional[str] = None,
        cookies: Any = None,
        session: Any = None,
    ) -> FetchResponse:
        if not self.available:
            return FetchResponse(
                url=url, outcome=Outcome.ERROR, transport=self.name, error="no browser driver installed"
            )

        accept_language = accept_language_for(host_of(url), country)

        def _run(browser: Any) -> FetchResponse:
            context = None
            try:
                context = browser_mod.new_context(
                    browser,
                    user_agent=getattr(identity, "user_agent", None),
                    locale=accept_language.split(",")[0],
                    accept_language=accept_language,
                    proxy=proxy,
                )
                page = context.new_page()
                response = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                # A managed challenge resolves itself a beat after load. Waiting
                # for the network to settle is what turns an interstitial into
                # the real page; failing that wait is not fatal, since the body
                # is classified either way.
                with suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=min(8000, int(timeout * 1000)))
                # A European page behind an unclicked consent dialog renders the
                # banner and nothing else. Dismissing it is what turns ten
                # kilobytes of wall into the page we came for.
                with suppress(Exception):
                    if browser_mod.dismiss_consent(page):
                        page.wait_for_load_state("networkidle", timeout=min(6000, int(timeout * 1000)))
                text = page.content()
                final_url = page.url
                status = response.status if response is not None else 0
                if response is None:
                    # No navigation happened. Returning the blank about:blank
                    # DOM here is how an empty page came to be cached as a
                    # success for a day.
                    return FetchResponse(
                        url=url,
                        outcome=Outcome.ERROR,
                        transport=self.name,
                        error="navigation produced no response",
                    )
                response_headers = {}
                with suppress(Exception):
                    response_headers = {str(k): str(v) for k, v in response.headers.items()}
                return FetchResponse(
                    url=url,
                    final_url=final_url,
                    status=status,
                    text=text,
                    outcome=classify_rendered(status, text, response_headers),
                    transport=self.name,
                    headers=response_headers,
                )
            finally:
                if context is not None:
                    with suppress(Exception):
                        context.close()

        with self._slots:
            try:
                return self._worker.submit(_run, timeout=max(30.0, timeout * 3))
            except Exception as exc:
                return FetchResponse(
                    url=url, outcome=Outcome.ERROR, transport=self.name, error=str(exc)[:200]
                )

    def close(self) -> None:
        self._worker.close()


class TransportLadder:
    """Try transports in order, remember what worked, start there next time.

    `fetch()` returns the first response that is not escalatable — an OK, a real
    404, a 429 — or the last attempt if every rung was blocked. The caller gets
    a `FetchResponse` either way and can tell the difference, which is the whole
    point: the code this replaces returned `None` for a bot wall and `None` for
    an empty page, so a run could not report which of the two it had hit.
    """

    def __init__(
        self,
        transports: list[Transport],
        memory: Optional[TransportMemory] = None,
        *,
        escalate: bool = True,
    ) -> None:
        self.transports = sorted([t for t in transports if t is not None], key=lambda t: t.rung)
        self.memory = memory
        self.escalate = escalate

    def _usable(self) -> list[Transport]:
        return [t for t in self.transports if t.available]

    def fetch(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: float = 20.0,
        identity: Any = None,
        country: Optional[str] = None,
        cookies: Any = None,
        session: Any = None,
        max_rung: Optional[int] = None,
    ) -> FetchResponse:
        usable = self._usable()
        if not usable:
            return FetchResponse(url=url, outcome=Outcome.ERROR, error="no transport available")

        domain = registrable_domain(url)
        start_rung = self.memory.preferred_rung(domain) if self.memory else 0
        ceiling = max_rung if max_rung is not None else max(t.rung for t in usable)

        candidates = [t for t in usable if start_rung <= t.rung <= ceiling]
        if not candidates:
            # Memory points above the ceiling this caller allows; fall back to
            # whatever is permitted rather than refusing to fetch at all.
            candidates = [t for t in usable if t.rung <= ceiling] or usable[:1]

        last = FetchResponse(url=url, outcome=Outcome.ERROR, error="no attempt made")
        for transport in candidates:
            last = transport.fetch(
                url,
                headers=headers,
                proxy=proxy,
                timeout=timeout,
                identity=identity,
                country=country,
                cookies=cookies,
                session=session,
            )
            if self.memory:
                self.memory.record(domain, transport.rung, success=last.ok)
            if last.ok:
                if transport.rung > start_rung:
                    log.info(
                        "transport: %s needed the %s rung (%s answered %s)",
                        domain,
                        transport.name,
                        candidates[0].name,
                        last.status or "an error",
                    )
                return last
            if not self.escalate or not last.escalatable:
                return last
        return last

    def close(self) -> None:
        for transport in self.transports:
            with suppress(Exception):
                transport.close()
        if self.memory is not None:
            with suppress(Exception):
                self.memory.close()


def build_ladder(
    session: Any,
    *,
    memory_path: str = ".cache/transport_memory.sqlite",
    use_impersonation: bool = True,
    use_browser: bool = False,
    headless: bool = True,
    browser_concurrency: int = 2,
    escalate: bool = True,
    remember: bool = True,
) -> TransportLadder:
    """Assemble the ladder a run should use."""
    transports: list[Transport] = [RequestsTransport(session)]
    if use_impersonation:
        transports.append(ImpersonateTransport())
    if use_browser:
        transports.append(BrowserTransport(headless=headless, max_concurrent=browser_concurrency))
    memory = TransportMemory(memory_path) if remember else None
    return TransportLadder(transports, memory, escalate=escalate)
