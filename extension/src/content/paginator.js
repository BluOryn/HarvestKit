/**
 * paginator.js — Auto-pagination engine for job listing pages.
 *
 * Handles three pagination patterns:
 *  Pattern A: "Next" button / numbered page links (traditional pagination)
 *  Pattern B: "Load More" / "Show More" button (click-to-load)
 *  Pattern C: Infinite scroll (scroll-to-load)
 *
 * Usage:
 *   const paginator = new Paginator({ maxPages: 10, delayMs: 1500 });
 *   const allCards = await paginator.collectAll(extractFn);
 */

// ---------------------------------------------------------------------------
// Pagination type detection
// ---------------------------------------------------------------------------
const NEXT_BUTTON_RX = /^(next|→|›|»|weiter|nächste|suivant|siguiente|próximo|avanti|次|अगला)$/i;
const NEXT_LINK_TEXT_RX = /\b(next|nächste|page\s*\d|weiter|more\s+results)\b/i;
const LOAD_MORE_RX = /\b(load\s+more|show\s+more|see\s+more|mehr\s+(laden|anzeigen)|view\s+more|afficher\s+plus|mostrar\s+más|もっと見る|और दिखाएं)\b/i;

const PAGINATION_SELECTORS = [
  "nav[aria-label*='page' i]",
  "nav[aria-label*='pagination' i]",
  "nav[role='navigation']",
  "[class*='pagination' i]",
  "[class*='pager' i]",
  "[class*='page-nav' i]",
  "[data-testid*='pagination' i]",
  ".paginator",
  ".pages",
];

export function detectPagination(root = document) {
  // --- Pattern A: Next/numbered page links ---
  const nextBtn = findNextButton(root);
  if (nextBtn) {
    return { type: "next-button", element: nextBtn, hasMore: true };
  }

  // --- Pattern B: Load More button ---
  const loadMore = findLoadMoreButton(root);
  if (loadMore) {
    return { type: "load-more", element: loadMore, hasMore: true };
  }

  // --- Pattern C: Infinite scroll detection ---
  if (detectInfiniteScroll(root)) {
    return { type: "infinite-scroll", element: null, hasMore: true };
  }

  return { type: "none", element: null, hasMore: false };
}

function findNextButton(root) {
  // 1. Look inside pagination containers first
  for (const sel of PAGINATION_SELECTORS) {
    const nav = root.querySelector(sel);
    if (!nav) continue;
    // Find "Next" link/button in the pagination
    for (const el of nav.querySelectorAll("a, button")) {
      const text = (el.textContent || "").trim();
      const ariaLabel = (el.getAttribute("aria-label") || "").trim();
      if (NEXT_BUTTON_RX.test(text) || NEXT_BUTTON_RX.test(ariaLabel)) {
        if (!el.disabled && !el.classList.contains("disabled") && el.getAttribute("aria-disabled") !== "true") {
          return el;
        }
      }
    }
    // Find the "active" page number and get the next sibling
    const active = nav.querySelector(".active, [aria-current='page'], [class*='current']");
    if (active) {
      const next = active.nextElementSibling;
      if (next && (next.tagName === "A" || next.tagName === "BUTTON" || next.querySelector("a"))) {
        return next.tagName === "A" || next.tagName === "BUTTON" ? next : next.querySelector("a");
      }
    }
  }

  // 2. Broad scan for "Next" links/buttons anywhere
  for (const el of root.querySelectorAll("a, button")) {
    const text = (el.textContent || "").trim();
    const ariaLabel = (el.getAttribute("aria-label") || "").trim();
    const rel = (el.getAttribute("rel") || "").toLowerCase();
    if (rel === "next") return el;
    if (NEXT_BUTTON_RX.test(text) && text.length < 20) {
      if (!el.disabled && el.offsetParent !== null) return el;
    }
    if (NEXT_LINK_TEXT_RX.test(ariaLabel)) {
      if (!el.disabled && el.offsetParent !== null) return el;
    }
  }

  // 3. Look for page number links where the next number is clickable
  const pageLinks = Array.from(root.querySelectorAll("a, button")).filter((el) => {
    const text = (el.textContent || "").trim();
    return /^\d+$/.test(text) && parseInt(text) > 0;
  });
  if (pageLinks.length >= 3) {
    // Find current page (usually styled differently or not a link)
    const currentPage = pageLinks.find((el) =>
      el.classList.contains("active") || el.classList.contains("current") ||
      el.getAttribute("aria-current") === "page" ||
      el.tagName === "SPAN" || el.tagName === "STRONG"
    );
    if (currentPage) {
      const currentNum = parseInt(currentPage.textContent.trim());
      const nextPage = pageLinks.find((el) => parseInt(el.textContent.trim()) === currentNum + 1);
      if (nextPage) return nextPage;
    }
  }

  return null;
}

function findLoadMoreButton(root) {
  for (const el of root.querySelectorAll("button, a, [role='button']")) {
    const text = (el.textContent || "").trim();
    if (LOAD_MORE_RX.test(text) && text.length < 50) {
      if (!el.disabled && el.offsetParent !== null) return el;
    }
  }
  return null;
}

function detectInfiniteScroll(root) {
  // Check for common infinite scroll indicators
  const indicators = [
    "[class*='infinite']",
    "[class*='lazy-load']",
    "[data-infinite]",
    "[data-page]",
    ".loading-spinner",
    "[class*='sentinel']",
    "[class*='waypoint']",
  ];
  for (const sel of indicators) {
    if (root.querySelector(sel)) return true;
  }

  // Check if the page is very tall (likely infinite scroll)
  const docHeight = document.documentElement.scrollHeight;
  const viewHeight = window.innerHeight;
  if (docHeight > viewHeight * 3) return true;

  return false;
}

// ---------------------------------------------------------------------------
// Paginator class — orchestrates multi-page collection
// ---------------------------------------------------------------------------
export class Paginator {
  constructor(opts = {}) {
    this.maxPages = opts.maxPages || 10;
    this.delayMs = opts.delayMs || 1500;
    this.scrollDelayMs = opts.scrollDelayMs || 2000;
    this.onProgress = opts.onProgress || (() => {});
    this._aborted = false;
  }

  abort() {
    this._aborted = true;
  }

  /**
   * Collect cards from all pages. extractFn() should return { cards: [...] }.
   * Returns accumulated cards array.
   */
  async collectAll(extractFn) {
    const allCards = [];
    const seenUrls = new Set();
    let page = 0;

    while (page < this.maxPages && !this._aborted) {
      page++;
      this.onProgress({ page, status: "extracting" });

      // Extract cards from current page state
      const result = extractFn();
      const newCards = (result.cards || []).filter((c) => {
        if (seenUrls.has(c.url)) return false;
        seenUrls.add(c.url);
        return true;
      });
      allCards.push(...newCards);

      this.onProgress({ page, status: "extracted", newCards: newCards.length, total: allCards.length });

      if (page >= this.maxPages) break;

      // Detect and execute pagination
      const pagination = detectPagination(document);
      if (!pagination.hasMore || pagination.type === "none") {
        this.onProgress({ page, status: "no-more-pages", total: allCards.length });
        break;
      }

      const advanced = await this._advance(pagination);
      if (!advanced) {
        this.onProgress({ page, status: "pagination-failed", total: allCards.length });
        break;
      }

      // Wait for new content to load
      await this._waitForNewContent(pagination.type);
    }

    this.onProgress({ page, status: "done", total: allCards.length });
    return allCards;
  }

  async _advance(pagination) {
    switch (pagination.type) {
      case "next-button":
        return this._clickAndWait(pagination.element);
      case "load-more":
        return this._clickAndWait(pagination.element);
      case "infinite-scroll":
        return this._scrollAndWait();
      default:
        return false;
    }
  }

  async _clickAndWait(element) {
    if (!element) return false;
    try {
      // Scroll element into view
      element.scrollIntoView({ behavior: "smooth", block: "center" });
      await sleep(300);

      // Record current card count to detect change
      const beforeCount = document.querySelectorAll(
        "article, [class*='result'], [class*='job'], [class*='card'], [class*='listing'], [data-advert]"
      ).length;

      // Click
      element.click();

      // Wait for DOM to change
      const changed = await this._waitForDomChange(beforeCount, 8000);
      await sleep(this.delayMs);
      return changed;
    } catch (e) {
      console.warn("[Paginator] click failed:", e);
      return false;
    }
  }

  async _scrollAndWait() {
    const beforeHeight = document.documentElement.scrollHeight;
    const beforeCount = document.querySelectorAll("a[href]").length;

    // Scroll to bottom
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
    await sleep(this.scrollDelayMs);

    const afterHeight = document.documentElement.scrollHeight;
    const afterCount = document.querySelectorAll("a[href]").length;

    // Check if new content appeared
    return afterHeight > beforeHeight || afterCount > beforeCount;
  }

  async _waitForDomChange(beforeCount, timeoutMs = 8000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await sleep(300);
      const currentCount = document.querySelectorAll(
        "article, [class*='result'], [class*='job'], [class*='card'], [class*='listing'], [data-advert]"
      ).length;
      if (currentCount !== beforeCount) return true;
      // Also check if URL changed (for server-side pagination)
      if (document.readyState === "complete") {
        await sleep(500);
        return true;
      }
    }
    return false;
  }

  async _waitForNewContent(type) {
    if (type === "next-button") {
      // For page navigation, wait for full page load
      await sleep(this.delayMs);
      await waitForIdle(5000);
    } else {
      // For load-more / infinite scroll, just wait briefly
      await sleep(this.delayMs);
    }
  }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitForIdle(timeoutMs = 5000) {
  return new Promise((resolve) => {
    if (typeof requestIdleCallback === "function") {
      const id = requestIdleCallback(() => resolve(), { timeout: timeoutMs });
      setTimeout(() => { cancelIdleCallback(id); resolve(); }, timeoutMs);
    } else {
      setTimeout(resolve, 1000);
    }
  });
}
