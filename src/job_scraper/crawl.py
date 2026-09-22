"""Browser fetcher (Playwright) + BFS link crawler.

PlaywrightFetcher is used for JS-heavy sites where plain HTTP doesn't return
the rendered DOM (jobs.ch's React SPA, LinkedIn's job board, Workday detail
pages, etc.). It:
  - Runs Chromium headless with a realistic UA + viewport.
  - Reuses a single persistent context across .get() calls (cookies + storage).
  - Auto-dismisses cookie banners and overlays before snapshotting HTML.
  - Auto-clicks "Show more / Read more" expanders to reveal hidden description.
  - Waits for Schema.org JobPosting JSON-LD to be present, with timeout fallback.

Fall back to plain HttpClient when Playwright isn't installed.
"""

import logging
from collections import deque
from collections.abc import Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import browser
from .http import HttpClient
from .identity import accept_language_for, country_for_host, identity_for
from .transport import Outcome, classify_rendered

# The consent-wall selectors live in `browser` now, so the crawler and the
# transport ladder cannot drift into dismissing different dialogs.
COOKIE_BUTTON_SELECTORS = browser.COOKIE_BUTTON_SELECTORS

EXPAND_SELECTORS_TEXT = [
    "Show more",
    "Read more",
    "See more",
    "View full",
    "Mehr anzeigen",
    "Mehr lesen",
    "Voir plus",
    "Vis mer",
]

# Binary/asset URLs that are never job pages. Followed blindly, a crawl burns its
# whole page budget downloading PDFs and images.
SKIP_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".avif",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".rar",
    ".7z",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".mp3",
    ".mp4",
    ".webm",
    ".avi",
    ".mov",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".rss",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
)


# Evaluated in the page: true once something recognisably job-shaped exists.
_JOB_CONTENT_PREDICATE = """
() => {
  const lds = document.querySelectorAll('script[type="application/ld+json"]');
  for (const el of lds) {
    try {
      const d = JSON.parse(el.textContent || 'null');
      const flat = (Array.isArray(d) ? d : [d]).flatMap(x => x && x['@graph'] ? x['@graph'] : [x]);
      if (flat.some(x => x && (x['@type'] === 'JobPosting' || (Array.isArray(x['@type']) && x['@type'].includes('JobPosting'))))) return true;
    } catch {}
  }
  if (document.querySelector('[itemtype*="schema.org/JobPosting" i]')) return true;
  if (document.querySelectorAll('a[href*="/job/"], a[href*="/joboffer/"], a[href*="/vacancies/"], a[href*="/stellenangebote/"], [data-jk], [data-advert], article[data-at="job-item"]').length >= 3) return true;
  return false;
}
"""


@dataclass
class Page:
    url: str
    html: str


class PlaywrightFetcher:
    """A browser fetcher that egresses where it is told to and admits failure.

    Three things this class used to get wrong are worth naming, because each one
    was silent:

    * It connected **directly**, ignoring the proxy pool entirely, so the one
      fetch path used against the most hostile sites was the only one sending
      the operator's real address.
    * `goto()` was wrapped in `suppress(Exception)` and the method then returned
      `page.url, page.content()` regardless — so a failed navigation produced an
      empty `about:blank` DOM that the HTTP cache stored as a success for 24 h.
    * Plain headless Chromium is trivially fingerprinted: `navigator.webdriver`
      is set and `userAgentData` still says `HeadlessChrome` however the UA
      string is spoofed.

    It is also safe to call from a worker thread now — the driver lives on its
    own thread (`browser.BrowserWorker`) rather than being locked around, which
    is the only thing that fixes Playwright's greenlet pinning.
    """

    def __init__(
        self,
        timeout_ms: int = 30000,
        headless: bool = True,
        *,
        proxy: str | None = None,
        http: HttpClient | None = None,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.headless = headless
        #: A fixed proxy for every fetch. When `http` is given instead, a proxy
        #: is drawn from its pool per fetch, which is what rotates.
        self.proxy = proxy
        self._http = http
        self._worker = browser.BrowserWorker(headless=headless)

    def __enter__(self) -> "PlaywrightFetcher":
        if not self._worker.start():
            raise RuntimeError(
                "Playwright is not installed or could not launch. Run: "
                "pip install playwright && playwright install chromium"
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._worker.close()

    def _proxy_for(self) -> tuple[str | None, object | None]:
        """(proxy url, pool entry) for this fetch."""
        if self.proxy:
            return self.proxy, None
        pool = getattr(self._http, "_proxies", None) if self._http is not None else None
        if pool is None:
            return None, None
        entry = pool.acquire()
        return ((entry or {}).get("url") or None), entry

    def get(self, url: str) -> tuple[str, str] | None:
        """(final_url, html), or None when nothing was actually fetched."""
        proxy, entry = self._proxy_for()
        host = urlparse(url).hostname or ""
        identity = identity_for(host, salt=proxy or "")
        accept_language = accept_language_for(host, country_for_host(host))
        timeout_ms = self.timeout_ms

        def _run(browser_obj):
            context = None
            try:
                context = browser.new_context(
                    browser_obj,
                    user_agent=identity.user_agent,
                    locale=accept_language.split(",")[0],
                    accept_language=accept_language,
                    proxy=proxy,
                )
                page = context.new_page()
                # 'networkidle' is brittle on SPAs; domcontentloaded plus the
                # content wait below is both faster and more reliable.
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if response is None:
                    return None
                status = response.status
                PlaywrightFetcher._dismiss_overlays(page)
                PlaywrightFetcher._wait_for_job_content(page)
                PlaywrightFetcher._expand_content(page)
                return page.url, page.content(), status
            finally:
                if context is not None:
                    with suppress(Exception):
                        context.close()

        try:
            result = self._worker.submit(_run, timeout=max(30.0, timeout_ms / 1000 * 3))
        except Exception as exc:
            logging.debug("playwright fetch failed for %s: %s", url, exc)
            self._report(entry, ok=False)
            return None
        if result is None:
            logging.debug("playwright: no navigation response for %s", url)
            self._report(entry, ok=False)
            return None
        final_url, html, status = result
        outcome = classify_rendered(status, html)
        if outcome is not Outcome.OK:
            # Handing a challenge page back as content is how a CAPTCHA ended
            # up being mined for job listings.
            logging.debug("playwright: %s -> %s (%d)", url, outcome.value, status)
            self._report(entry, ok=False)
            return None
        self._report(entry, ok=True)
        return final_url, html

    def _report(self, entry, *, ok: bool) -> None:
        pool = getattr(self._http, "_proxies", None) if self._http is not None else None
        if pool is None or entry is None:
            return
        with suppress(Exception):
            if ok:
                pool.report_success(entry)
            else:
                pool.report_failure(entry)

    @staticmethod
    def _dismiss_overlays(page) -> None:
        with suppress(Exception):
            browser.dismiss_consent(page)

    @staticmethod
    def _wait_for_job_content(page) -> None:
        # Race: JSON-LD JobPosting -> microdata -> known card selectors -> 6s timeout.
        with suppress(Exception):
            page.wait_for_function(
                _JOB_CONTENT_PREDICATE,
                timeout=6000,
            )

    @staticmethod
    def _expand_content(page) -> None:
        for label in EXPAND_SELECTORS_TEXT:
            with suppress(Exception):
                page.get_by_role("button", name=label).click(timeout=800)
                page.wait_for_timeout(150)


class Crawler:
    def __init__(
        self,
        http: HttpClient,
        max_pages: int,
        max_depth: int,
        allow_domains: Iterable[str],
        use_playwright: bool,
    ) -> None:
        self.http = http
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.allow_domains = {self._normalize_domain(d) for d in allow_domains if d}
        self.use_playwright = use_playwright

    def crawl(self, start_url: str) -> Iterator[Page]:
        visited: set[str] = set()
        queued: set[str] = {start_url}
        queue = deque([(start_url, 0)])
        pages = 0

        fetcher = None
        if self.use_playwright:
            try:
                # Sharing the client's proxy pool: a browser connecting
                # directly would defeat every proxy the run is configured with.
                fetcher = PlaywrightFetcher(http=self.http)
                fetcher.__enter__()
            except Exception as exc:
                logging.warning("Playwright unavailable (%s) — crawling over plain HTTP", exc)
                fetcher = None

        try:
            while queue and pages < self.max_pages:
                url, depth = queue.popleft()
                if url in visited or depth > self.max_depth:
                    continue
                visited.add(url)

                result = fetcher.get(url) if fetcher else self.http.get(url)
                if result is None:
                    continue
                final_url, html = result
                # A redirect can land several queue entries on the same page.
                if final_url != url:
                    if final_url in visited:
                        continue
                    visited.add(final_url)
                pages += 1
                yield Page(final_url, html)

                if depth >= self.max_depth:
                    continue
                for link in self._extract_links(final_url, html):
                    # Track membership separately: `link not in visited` let the
                    # same URL be appended once per referring page, so a site
                    # with a global nav queued thousands of duplicates.
                    if link in visited or link in queued:
                        continue
                    queued.add(link)
                    queue.append((link, depth + 1))
        finally:
            if fetcher is not None:
                with suppress(Exception):
                    fetcher.__exit__(None, None, None)

    def _extract_links(self, base_url: str, html: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        seen = set()
        links: list[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag.get("href", "")
            href = (href if isinstance(href, str) else " ".join(href)).strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
                continue
            try:
                absolute = urljoin(base_url, href)
                parsed = urlparse(absolute)
            except ValueError:
                continue
            absolute = parsed._replace(fragment="").geturl()
            if not self._is_allowed_url(absolute):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            links.append(absolute)
        return links

    def _is_allowed_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.path.lower().endswith(SKIP_EXTENSIONS):
            return False
        if not self.allow_domains:
            return True
        netloc = self._normalize_domain(parsed.netloc)
        return any(netloc == domain or netloc.endswith(f".{domain}") for domain in self.allow_domains)

    @staticmethod
    def _normalize_domain(value: str) -> str:
        domain = value.lower().strip()
        if domain.startswith("http://") or domain.startswith("https://"):
            parsed = urlparse(domain)
            domain = parsed.netloc
        if ":" in domain:
            domain = domain.split(":", 1)[0]
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
