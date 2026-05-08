/**
 * paginator.ts — Auto-pagination engine.
 * Handles: Next button, Load More, infinite scroll.
 */
import type { ListCard } from "../lib/messages";
import { pickSiteAdapter, type SiteAdapter } from "./sites";

// ---------------------------------------------------------------------------
// Pagination detection
// ---------------------------------------------------------------------------
const NEXT_RX = /^(next|→|›|»|weiter|nächste|suivant|siguiente|próximo|avanti|次|अगला)$/i;
const NEXT_TEXT_RX = /\b(next|nächste|page\s*\d|weiter|more\s+results)\b/i;
const LOAD_MORE_RX = /\b(load\s+more|show\s+more|see\s+more|mehr\s+(laden|anzeigen)|view\s+more|और दिखाएं)\b/i;

const PAGINATION_SEL = [
  "nav[aria-label*='page' i]",
  "nav[aria-label*='pagination' i]",
  "[class*='pagination' i]",
  "[class*='pager' i]",
  "[class*='page-nav' i]",
  "[data-testid*='pagination' i]",
];

export type PaginationType = "next-button" | "load-more" | "infinite-scroll" | "none";

export function detectPagination(root: Document = document): { type: PaginationType; element: HTMLElement | null; hasMore: boolean } {
  // Site adapter may have nextPage()
  const adapter = pickSiteAdapter();
  if (adapter?.nextPage) {
    const nextUrl = adapter.nextPage(root);
    if (nextUrl) {
      const el = root.querySelector(`a[href="${nextUrl}"], a[href$="${new URL(nextUrl).pathname}"]`) as HTMLElement | null;
      return { type: "next-button", element: el || findNextButton(root), hasMore: true };
    }
  }

  const next = findNextButton(root);
  if (next) return { type: "next-button", element: next, hasMore: true };

  const loadMore = findLoadMore(root);
  if (loadMore) return { type: "load-more", element: loadMore, hasMore: true };

  if (detectInfiniteScroll(root)) return { type: "infinite-scroll", element: null, hasMore: true };

  return { type: "none", element: null, hasMore: false };
}

function findNextButton(root: Document): HTMLElement | null {
  for (const sel of PAGINATION_SEL) {
    const nav = root.querySelector(sel);
    if (!nav) continue;
    for (const el of nav.querySelectorAll("a, button")) {
      const text = (el.textContent || "").trim();
      const aria = el.getAttribute("aria-label") || "";
      const rel = el.getAttribute("rel") || "";
      if (rel === "next" || NEXT_RX.test(text) || NEXT_RX.test(aria)) {
        if (!(el as HTMLButtonElement).disabled && (el as HTMLElement).offsetParent !== null) return el as HTMLElement;
      }
    }
    const active = nav.querySelector(".active, [aria-current='page'], [class*='current']");
    if (active?.nextElementSibling) {
      const nxt = active.nextElementSibling;
      const link = (nxt.tagName === "A" || nxt.tagName === "BUTTON") ? nxt : nxt.querySelector("a, button");
      if (link) return link as HTMLElement;
    }
  }

  for (const el of root.querySelectorAll("a, button")) {
    const text = (el.textContent || "").trim();
    const rel = el.getAttribute("rel") || "";
    if (rel === "next") return el as HTMLElement;
    if (NEXT_RX.test(text) && text.length < 20 && (el as HTMLElement).offsetParent !== null) return el as HTMLElement;
  }
  return null;
}

function findLoadMore(root: Document): HTMLElement | null {
  for (const el of root.querySelectorAll("button, a, [role='button']")) {
    const text = (el.textContent || "").trim();
    if (LOAD_MORE_RX.test(text) && text.length < 50 && (el as HTMLElement).offsetParent !== null) return el as HTMLElement;
  }
  return null;
}

function detectInfiniteScroll(_root: Document): boolean {
  if (document.querySelector("[class*='infinite'], [data-infinite], [class*='sentinel'], [class*='waypoint']")) return true;
  return document.documentElement.scrollHeight > window.innerHeight * 3;
}

// ---------------------------------------------------------------------------
// Paginator class
// ---------------------------------------------------------------------------
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export class Paginator {
  maxPages: number;
  delayMs: number;
  scrollDelayMs: number;
  onProgress: (p: { page: number; status: string; newCards?: number; total?: number }) => void;
  private aborted = false;

  constructor(opts: { maxPages?: number; delayMs?: number; scrollDelayMs?: number; onProgress?: Paginator["onProgress"] } = {}) {
    this.maxPages = opts.maxPages || 10;
    this.delayMs = opts.delayMs || 1500;
    this.scrollDelayMs = opts.scrollDelayMs || 2000;
    this.onProgress = opts.onProgress || (() => {});
  }

  abort() { this.aborted = true; }

  async collectAll(extractFn: () => ListCard[]): Promise<ListCard[]> {
    const all: ListCard[] = [];
    const seen = new Set<string>();
    let page = 0;

    while (page < this.maxPages && !this.aborted) {
      page++;
      this.onProgress({ page, status: "extracting" });

      const cards = extractFn().filter((c) => { if (seen.has(c.url)) return false; seen.add(c.url); return true; });
      all.push(...cards);
      this.onProgress({ page, status: "extracted", newCards: cards.length, total: all.length });

      if (page >= this.maxPages) break;

      const pag = detectPagination(document);
      if (!pag.hasMore || pag.type === "none") {
        this.onProgress({ page, status: "no-more-pages", total: all.length });
        break;
      }

      const advanced = await this.advance(pag);
      if (!advanced) {
        this.onProgress({ page, status: "pagination-failed", total: all.length });
        break;
      }

      await sleep(this.delayMs);
    }

    this.onProgress({ page, status: "done", total: all.length });
    return all;
  }

  private async advance(pag: ReturnType<typeof detectPagination>): Promise<boolean> {
    if (pag.type === "infinite-scroll") return this.scrollAndWait();
    if (pag.element) return this.clickAndWait(pag.element);
    return false;
  }

  private async clickAndWait(el: HTMLElement): Promise<boolean> {
    try {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      await sleep(300);
      const before = document.querySelectorAll("a[href]").length;
      el.click();
      // wait for DOM change
      const start = Date.now();
      while (Date.now() - start < 8000) {
        await sleep(300);
        if (document.querySelectorAll("a[href]").length !== before) return true;
      }
      return true; // timeout but still try
    } catch { return false; }
  }

  private async scrollAndWait(): Promise<boolean> {
    const before = document.documentElement.scrollHeight;
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
    await sleep(this.scrollDelayMs);
    return document.documentElement.scrollHeight > before || document.querySelectorAll("a[href]").length > 0;
  }
}
