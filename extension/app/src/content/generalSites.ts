/**
 * Site adapters for general-mode (businesses, places, listings).
 *
 * Each adapter returns list cards extracted from a Yelp/Google Maps/etc.
 * search-results page. They are tried before the generic heuristic in
 * generalExtract.ts so well-known sites get reliable selectors.
 *
 * Pattern is identical to the job-mode site adapters in content/sites/index.ts.
 */
import { clean } from "../lib/utils";

export type GeneralCard = {
  url: string;
  name: string;
  address?: string;
  category?: string;
  rating?: string;
  review_count?: string;
  phone?: string;
  snippet: string;
};

export type GeneralSiteAdapter = {
  name: string;
  hostMatch: RegExp;
  extract(root: Document): GeneralCard[];
  nextPage?(root: Document): string | null;
  /** URL pattern that identifies a detail/business page (used by detector). */
  detailUrlMatch?: RegExp;
};

const sels = (root: Document | Element, q: string) => Array.from(root.querySelectorAll(q));
const sel = (root: Document | Element, q: string) => root.querySelector(q);

function abs(href: string | null): string {
  if (!href) return "";
  try { return new URL(href, window.location.href).href.split("#")[0]; } catch { return ""; }
}

function uniqByUrl(cards: GeneralCard[]): GeneralCard[] {
  const seen = new Set<string>();
  const out: GeneralCard[] = [];
  for (const c of cards) {
    if (!c.url || seen.has(c.url)) continue;
    seen.add(c.url);
    out.push(c);
  }
  return out;
}

export const GENERAL_SITES: GeneralSiteAdapter[] = [
  {
    name: "yelp",
    hostMatch: /(^|\.)yelp\.[a-z.]+$/i,
    detailUrlMatch: /\/biz\/[^?]+/i,
    extract(root) {
      const out: GeneralCard[] = [];
      // Yelp result cards: <div data-testid="serp-ia-card"> contains everything.
      // Falls back to any anchor pointing at /biz/<slug>.
      const cards = sels(root, "div[data-testid='serp-ia-card'], li[class*='businessCard'], div[class*='businessName']");
      const seen = new Set<string>();
      for (const card of cards) {
        const a = sel(card, "a[href*='/biz/']") as HTMLAnchorElement | null;
        if (!a) continue;
        const url = abs(a.getAttribute("href"));
        if (!url || seen.has(url)) continue;
        seen.add(url);
        const name = clean(a.textContent || sel(card, "h3,h4,[class*='businessName']")?.textContent || "");
        if (!name || name.length < 2) continue;
        // Yelp shows neighborhood + categories below the name; extract them.
        const cat = clean(sel(card, "[class*='priceCategory'], [class*='categoryStr']")?.textContent || "");
        const addr = clean(sel(card, "[class*='secondaryAttributes'], [class*='neighborhood']")?.textContent || "");
        const ratingEl = sel(card, "[role='img'][aria-label*='star'], [class*='ratingValue']");
        const rating = ratingEl ? clean((ratingEl.getAttribute("aria-label") || ratingEl.textContent || "").match(/[\d.]+/)?.[0] || "") : "";
        const rcMatch = (clean(card.textContent || "")).match(/\((\d{1,3}(?:,\d{3})*)\s*reviews?\)/i);
        const rc = rcMatch ? rcMatch[1].replace(/,/g, "") : "";
        const phoneEl = sel(card, "[class*='phone']");
        const phone = clean(phoneEl?.textContent || "");
        out.push({
          url,
          name,
          address: addr,
          category: cat,
          rating,
          review_count: rc,
          phone,
          snippet: clean(card.textContent || "").slice(0, 280),
        });
      }
      // Last resort: every anchor pointing at /biz/. Pair with closest heading.
      if (out.length < 2) {
        for (const a of sels(root, "a[href*='/biz/']") as HTMLAnchorElement[]) {
          const url = abs(a.getAttribute("href"));
          if (!url || seen.has(url)) continue;
          // Skip nav links (typically empty text or "More" / "Reviews")
          const linkText = clean(a.textContent || "");
          if (!linkText || linkText.length < 3) continue;
          if (/^(reviews?|menu|photos|map|view|more)$/i.test(linkText)) continue;
          seen.add(url);
          const card = a.closest("li, article, div[class*='result'], div[class*='card']") || a;
          const name = clean(sel(card as Element, "h3,h4,[class*='businessName']")?.textContent || linkText);
          out.push({ url, name, snippet: clean((card as Element).textContent || "").slice(0, 280) });
        }
      }
      return uniqByUrl(out);
    },
    nextPage(root) {
      const a = sel(root, "a[class*='next-link'], a[aria-label*='next' i]") as HTMLAnchorElement | null;
      return a && a.href ? a.href : null;
    },
  },
  {
    name: "google-maps",
    hostMatch: /(^|\.)google\.[a-z.]+$/i,
    detailUrlMatch: /\/maps\/place\/[^?]+/i,
    extract(root) {
      const out: GeneralCard[] = [];
      // Google Maps result feed
      const cards = sels(root, "div[role='article'], div[role='feed'] > div, div[jsaction*='mouseover:pane']");
      const seen = new Set<string>();
      for (const card of cards) {
        const a = sel(card, "a[href*='/maps/place/']") as HTMLAnchorElement | null;
        if (!a) continue;
        const url = abs(a.getAttribute("href"));
        if (!url || seen.has(url)) continue;
        seen.add(url);
        const name = clean(a.getAttribute("aria-label") || sel(card, "[class*='fontHeadlineSmall']")?.textContent || "");
        if (!name) continue;
        const cat = clean(sel(card, "[class*='fontBodyMedium'] > span:nth-child(2)")?.textContent || "");
        out.push({
          url,
          name,
          category: cat,
          snippet: clean(card.textContent || "").slice(0, 280),
        });
      }
      return uniqByUrl(out);
    },
  },
  {
    name: "tripadvisor",
    hostMatch: /(^|\.)tripadvisor\.[a-z.]+$/i,
    detailUrlMatch: /\/(Hotel_Review|Restaurant_Review|Attraction_Review|ShowUserReviews)/i,
    extract(root) {
      const out: GeneralCard[] = [];
      const seen = new Set<string>();
      for (const a of sels(root, "a[href*='Hotel_Review'], a[href*='Restaurant_Review'], a[href*='Attraction_Review']") as HTMLAnchorElement[]) {
        const url = abs(a.getAttribute("href"));
        if (!url || seen.has(url)) continue;
        const name = clean(a.textContent || "");
        if (!name || name.length < 3) continue;
        seen.add(url);
        const card = a.closest("article, div[class*='listing'], li") || a;
        out.push({ url, name, snippet: clean((card as Element).textContent || "").slice(0, 280) });
      }
      return uniqByUrl(out);
    },
  },
  {
    name: "yellowpages",
    hostMatch: /(^|\.)yellowpages\.[a-z.]+$/i,
    detailUrlMatch: /\/mip\//i,
    extract(root) {
      const out: GeneralCard[] = [];
      for (const card of sels(root, "div.result, div.search-results > div")) {
        const a = sel(card, "a.business-name") as HTMLAnchorElement | null;
        if (!a) continue;
        const url = abs(a.getAttribute("href"));
        if (!url) continue;
        const name = clean(a.textContent || "");
        if (!name) continue;
        out.push({
          url,
          name,
          phone: clean(sel(card, ".phones")?.textContent || ""),
          address: clean(sel(card, ".street-address")?.textContent || ""),
          category: clean(sel(card, ".categories")?.textContent || ""),
          rating: clean(sel(card, ".result-rating")?.getAttribute("class")?.match(/(\d+(?:\.\d+)?)/)?.[1] || ""),
          snippet: clean(card.textContent || "").slice(0, 280),
        });
      }
      return uniqByUrl(out);
    },
  },
];

export function pickGeneralSite(): GeneralSiteAdapter | null {
  const host = window.location.hostname;
  for (const s of GENERAL_SITES) if (s.hostMatch.test(host)) return s;
  return null;
}

/**
 * Universal card finder for any site without an adapter.
 * Strategy: find every anchor whose URL matches a "detail page" shape, dedupe
 * by URL, and pair each with the closest heading text.
 *
 * "Detail page" shape:
 *  - URL has a slug-like segment (one of /place/, /biz/, /listing/, /detail/,
 *    /business/, /location/, /shop/, /store/, /property/) OR
 *  - URL ends in a slug after the path keyword we're looking for.
 *
 * Drops links that look like nav (text == 'Reviews', 'Photos', 'More', etc.)
 * and links shorter than 3 chars.
 */
const DETAIL_URL_RX = /\/(biz|place|listing|detail|business|location|shop|store|property|venue|rental|hotel|restaurant|companies?|profile|firm|practice|provider|merchant)\//i;
const NAV_TEXT_RX = /^(reviews?|menu|photos|map|view|more|details|info|share|book|save|contact|directions?|website)$/i;

export function universalCardExtract(root: Document = document): GeneralCard[] {
  const out: GeneralCard[] = [];
  const seen = new Set<string>();
  const anchors = Array.from(root.querySelectorAll("a[href]")) as HTMLAnchorElement[];
  for (const a of anchors) {
    const href = a.getAttribute("href") || "";
    if (!href || href.startsWith("javascript:") || href.startsWith("mailto:")) continue;
    if (!DETAIL_URL_RX.test(href)) continue;
    const url = abs(href);
    if (!url || seen.has(url)) continue;
    // Same-domain only — skip cross-site links.
    let sameDomain = false;
    try { sameDomain = new URL(url).hostname === window.location.hostname; } catch {}
    if (!sameDomain) continue;
    const linkText = clean(a.textContent || "");
    if (!linkText || linkText.length < 3) continue;
    if (NAV_TEXT_RX.test(linkText)) continue;
    seen.add(url);
    const card = a.closest("li, article, div[class*='result'], div[class*='card'], div[class*='listing'], section") || a;
    const heading = sel(card as Element, "h1,h2,h3,h4,[class*='title'],[class*='name'],[class*='Name']");
    const name = clean(heading?.textContent || linkText);
    out.push({ url, name, snippet: clean((card as Element).textContent || "").slice(0, 280) });
  }
  return uniqByUrl(out);
}
