"""One browser, owned by one thread, usable from all of them.

Playwright's synchronous API is not thread-safe, and not in the ordinary way a
lock fixes. Each `sync_playwright()` handle is driven by a greenlet pinned to
the thread that started it; calling into it from a second thread raises
`greenlet.error: cannot switch to a different thread`, whatever locking the
caller does around the call. The deep-scrape pool is a ThreadPoolExecutor, so
every browser call it made raised — and the exception was caught at DEBUG, so
`use_playwright: true` appeared to work while doing nothing at all.

The fix is not a lock but an owner: one dedicated thread creates the driver and
runs every subsequent call, and other threads post work to it and wait. That is
`BrowserWorker` below. It costs one thread and gives back a browser that any
worker can use.

On top of that this module owns the two things that decide whether the browser
is worth launching:

* **Stealth.** Plain headless Chromium announces itself — `navigator.webdriver`
  is true, `navigator.userAgentData` says "HeadlessChrome", the plugin array is
  empty, WebGL reports SwiftShader. Patchright is used when installed because
  it removes the deepest tell (the `Runtime.enable` CDP call) which no init
  script can reach; the init script here covers the rest for plain Playwright.
* **Egress.** A browser that ignores the proxy pool sends the operator's own
  address to exactly the hosts the pool exists to keep it away from. Proxy is a
  required argument of the context, not an afterthought.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Optional

log = logging.getLogger(__name__)

#: Runs in every page before any site script. Kept to the checks that are both
#: cheap and actually performed by commercial bot detection; a long script is
#: itself a tell, because real Chrome has no such patches.
STEALTH_INIT_SCRIPT = """
(() => {
  const patch = (obj, prop, value) => {
    try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); } catch (e) {}
  };
  // The single most-read flag. Playwright sets it; Chrome does not have it.
  patch(Navigator.prototype, 'webdriver', undefined);
  try { delete Object.getPrototypeOf(navigator).webdriver; } catch (e) {}

  // userAgentData is structured, so a spoofed UA string with an unspoofed
  // brand list is a direct contradiction — and "HeadlessChrome" appears here
  // even when the UA string has been overridden.
  const uaMatch = (navigator.userAgent || '').match(/Chrome\\/(\\d+)/);
  if (uaMatch && navigator.userAgentData) {
    const major = uaMatch[1];
    const brands = [
      { brand: 'Chromium', version: major },
      { brand: 'Google Chrome', version: major },
      { brand: 'Not=A?Brand', version: '24' },
    ];
    patch(Object.getPrototypeOf(navigator.userAgentData), 'brands', brands);
  }

  // A zero-length plugin array is not something a desktop browser produces.
  if (!navigator.plugins || navigator.plugins.length === 0) {
    patch(Navigator.prototype, 'plugins', [1, 2, 3, 4, 5]);
    patch(Navigator.prototype, 'mimeTypes', [1, 2, 3]);
  }

  // Headless reports SwiftShader; a real machine reports its GPU.
  try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (parameter) {
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel Iris OpenGL Engine';
      return getParameter.apply(this, arguments);
    };
  } catch (e) {}

  // window.chrome is absent in headless and present in every real Chrome.
  if (!window.chrome) { window.chrome = { runtime: {}, app: { isInstalled: false } }; }

  // Headless denies every permission synchronously, which real Chrome does not.
  try {
    const query = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (parameters) =>
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : query(parameters);
  } catch (e) {}
})();
"""

#: Launch flags that remove automation tells visible before any script runs.
LAUNCH_ARGS: tuple[str, ...] = (
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
)


#: Consent-wall buttons, in the languages this engine actually meets. A European
#: page behind an unclicked consent dialog renders a few kilobytes of banner and
#: nothing else — siemens.com served 10 KB that way and 1 MB once dismissed — so
#: clicking is not a nicety, it is the difference between content and no content.
COOKIE_BUTTON_SELECTORS: tuple[str, ...] = (
    "#onetrust-accept-btn-handler",
    "#didomi-notice-agree-button",
    "button[id*='accept-all' i]",
    "button[aria-label*='accept' i]",
    "button[aria-label*='agree' i]",
    "button[aria-label*='akzeptieren' i]",
    "button:has-text('Accept all')",
    "button:has-text('Accept All Cookies')",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "button:has-text('Allow all')",
    "button:has-text('Akzeptieren')",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Zustimmen')",
    "button:has-text('Einverstanden')",
    "button:has-text('Tout accepter')",
    "button:has-text('Accepter')",
    "button:has-text('Accetta tutti')",
    "button:has-text('Aceptar todo')",
    "button:has-text('Alles accepteren')",
    "button:has-text('Godta alle')",
    "button:has-text('Acceptera alla')",
    "[class*='cookie'] button[class*='accept']",
    "[class*='consent'] button[class*='accept']",
    "[id*='cookie'] button[id*='accept']",
    ".cc-dismiss",
    ".cc-allow",
)


def dismiss_consent(page, *, timeout_ms: int = 1500) -> bool:
    """Click the first consent button that is actually there. True if one was."""
    for selector in COOKIE_BUTTON_SELECTORS:
        try:
            button = page.query_selector(selector)
            if button is None or not button.is_visible():
                continue
            button.click(timeout=timeout_ms)
            page.wait_for_timeout(400)
            return True
        except Exception:
            continue
    return False


_SENTINEL = object()


def driver_module() -> Any:
    """`patchright.sync_api` if installed, else `playwright.sync_api`, else None."""
    for module_name in ("patchright.sync_api", "playwright.sync_api"):
        try:
            return __import__(module_name, fromlist=["sync_playwright"])
        except Exception:
            continue
    return None


def driver_name() -> str:
    module = driver_module()
    return module.__name__.split(".")[0] if module is not None else ""


class BrowserWorker:
    """A thread that owns a Playwright driver and runs callables on it.

    Every public method is safe to call from any thread. `submit` blocks until
    the owner thread has run the callable and hands back its result or re-raises
    its exception, so callers see ordinary synchronous behaviour.
    """

    def __init__(self, *, headless: bool = True) -> None:
        self.headless = headless
        self._requests: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._start_error = ""
        self._driver = ""
        self._playwright: Any = None
        self._browser: Any = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        """Launch the browser. Returns False (and logs why) if it cannot be."""
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                self._ready.wait(timeout=90)
                return self._browser is not None
            module = driver_module()
            if module is None:
                self._start_error = (
                    "neither patchright nor playwright is installed — "
                    "`pip install patchright && patchright install chromium`"
                )
                log.info("browser: %s", self._start_error)
                return False
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run, args=(module,), name="playwright-owner", daemon=True
            )
            self._thread.start()
        self._ready.wait(timeout=90)
        if self._browser is None and self._start_error:
            log.warning("browser: launch failed — %s", self._start_error)
        return self._browser is not None

    def _run(self, module: Any) -> None:
        try:
            self._driver = module.__name__.split(".")[0]
            self._playwright = module.sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless, args=list(LAUNCH_ARGS))
            log.info("browser: %s chromium ready (headless=%s)", self._driver, self.headless)
        except Exception as exc:
            self._start_error = str(exc)[:300]
            self._browser = None
        finally:
            self._ready.set()

        if self._browser is None:
            # Drain anything already queued so callers are not left blocking.
            self._drain()
            return

        while True:
            item = self._requests.get()
            if item is _SENTINEL:
                break
            function, reply = item
            try:
                reply.put((True, function(self._browser)))
            except BaseException as exc:  # noqa: BLE001 — relayed to the caller
                reply.put((False, exc))
        with suppress(Exception):
            self._browser.close()
        with suppress(Exception):
            self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                item = self._requests.get_nowait()
            except queue.Empty:
                return
            if item is _SENTINEL:
                continue
            _, reply = item
            reply.put((False, RuntimeError("browser worker is not running")))

    def close(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._requests.put(_SENTINEL)
            thread.join(timeout=20)
        self._thread = None

    # -- use ---------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._browser is not None

    @property
    def driver(self) -> str:
        return self._driver

    def submit(self, function: Callable[[Any], Any], *, timeout: float = 180.0) -> Any:
        """Run `function(browser)` on the owner thread and return its result."""
        if not self.start():
            raise RuntimeError(self._start_error or "browser unavailable")
        reply: queue.Queue = queue.Queue(maxsize=1)
        self._requests.put((function, reply))
        try:
            succeeded, payload = reply.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"browser call exceeded {timeout:.0f}s") from exc
        if succeeded:
            return payload
        raise payload


def new_context(
    browser: Any,
    *,
    user_agent: Optional[str] = None,
    locale: str = "en-US",
    accept_language: str = "en-US,en;q=0.9",
    proxy: Optional[str] = None,
    timezone_id: Optional[str] = None,
    viewport: Optional[dict[str, int]] = None,
    block_assets: bool = True,
) -> Any:
    """A context that looks like a browser and egresses where it is told to.

    `proxy` is threaded through deliberately: a browser context created without
    it connects directly, so the one transport most likely to be used against a
    hostile site would be the one leaking the operator's address.
    """
    context = browser.new_context(
        user_agent=user_agent,
        locale=locale,
        timezone_id=timezone_id,
        extra_http_headers={"Accept-Language": accept_language},
        proxy={"server": proxy} if proxy else None,
        viewport=viewport or {"width": 1440, "height": 900},
        ignore_https_errors=False,
        java_script_enabled=True,
    )
    with suppress(Exception):
        context.add_init_script(STEALTH_INIT_SCRIPT)
    if block_assets:
        # Images and fonts are never the content we came for, and a jobs page
        # can carry hundreds of them.
        with suppress(Exception):
            context.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,otf,mp4,webm,avi,mov}",
                lambda route: route.abort(),
            )
    return context


#: Process-wide worker, so a run launches one browser rather than one per
#: component that wants it. Callers that need their own (different headless
#: mode, say) can construct `BrowserWorker` directly.
_shared_worker: Optional[BrowserWorker] = None
_shared_lock = threading.Lock()


def shared_worker(*, headless: bool = True) -> BrowserWorker:
    global _shared_worker
    with _shared_lock:
        if _shared_worker is None:
            _shared_worker = BrowserWorker(headless=headless)
        return _shared_worker


def close_shared_worker() -> None:
    global _shared_worker
    with _shared_lock:
        if _shared_worker is not None:
            _shared_worker.close()
            _shared_worker = None
