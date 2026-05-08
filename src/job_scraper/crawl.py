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
from collections import deque
from dataclasses import dataclass
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .http import HttpClient


COOKIE_BUTTON_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "button[aria-label*='accept' i]",
    "button[aria-label*='agree' i]",
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "button:has-text('Allow all')",
    "button:has-text('Akzeptieren')",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Tout accepter')",
    "[class*='cookie'] button[class*='accept']",
    "[class*='consent'] button[class*='accept']",
    "[class*='gdpr'] button:not([class*='reject'])",
    ".cc-dismiss",
    ".cc-allow",
]

EXPAND_SELECTORS_TEXT = [
    "Show more", "Read more", "See more", "View full", "Mehr anzeigen", "Mehr lesen", "Voir plus", "Vis mer",
]


@dataclass
class Page:
    url: str
    html: str


class PlaywrightFetcher:
    """Single persistent browser; thread-safe-ish (caller serializes)."""

    def __init__(self, timeout_ms: int = 30000, headless: bool = True) -> None:
        self.timeout_ms = timeout_ms
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "PlaywrightFetcher":
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9,de;q=0.8,fr;q=0.7"},
        )
        # Block heavy resources to speed up — jobs.ch has hundreds of images/fonts.
        self._context.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,otf,mp4,webm}", lambda r: r.abort())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass

    def get(self, url: str) -> Optional[Tuple[str, str]]:
        if self._context is None:
            return None
        page = self._context.new_page()
        try:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            except Exception:
                # 'networkidle' is brittle on SPAs; domcontentloaded + manual wait is safer.
                pass
            self._dismiss_overlays(page)
            self._wait_for_job_content(page)
            self._expand_content(page)
            html = page.content()
            return page.url, html
        except Exception:
            return None
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _dismiss_overlays(self, page) -> None:
        for sel in COOKIE_BUTTON_SELECTORS:
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click(timeout=1500)
                    page.wait_for_timeout(200)
                    break
            except Exception:
                continue

    def _wait_for_job_content(self, page) -> None:
        # Race: JSON-LD JobPosting → microdata → known card selectors → 6s timeout.
        try:
            page.wait_for_function(
                """
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
                """,
                timeout=6000,
            )
        except Exception:
            pass

    def _expand_content(self, page) -> None:
        for label in EXPAND_SELECTORS_TEXT:
            try:
                page.get_by_role("button", name=label).click(timeout=800)
                page.wait_for_timeout(150)
            except Exception:
                pass


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

    def crawl(self, start_url: str) -> Iterable[Page]:
        visited: Set[str] = set()
        queue = deque([(start_url, 0)])
        pages = 0

        fetcher = None
        if self.use_playwright:
            try:
                fetcher = PlaywrightFetcher()
                fetcher.__enter__()
            except Exception:
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
                pages += 1
                yield Page(final_url, html)

                for link in self._extract_links(final_url, html):
                    if link not in visited:
                        queue.append((link, depth + 1))
        finally:
            if fetcher is not None:
                fetcher.__exit__(None, None, None)

    def _extract_links(self, base_url: str, html: str) -> List[str]:
        soup = BeautifulSoup(html, "lxml")
        seen = set()
        links: List[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag.get("href", "").strip()
            if not href:
                continue
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            absolute = parsed._replace(fragment="").geturl()
            if not self._is_allowed_url(absolute):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            links.append(absolute)
        return links

    def _is_allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if any(parsed.path.lower().endswith(ext) for ext in [".png", ".jpg", ".pdf", ".zip", ".svg"]):
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
