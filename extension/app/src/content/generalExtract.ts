/**
 * generalExtract.ts — extracts a non-job business / place / product record from
 * the current page (mirrors src/general_scraper/extract.py).
 *
 * Exposes:
 *   - extractGeneral() → { record, found }
 *   - extractGeneralCards() → list-page card extraction (Yelp-style result lists).
 *
 * Strategy stack: JSON-LD LocalBusiness/Restaurant/Place → microdata → OpenGraph →
 * heuristics. Same merge-longer-string-wins logic as the job extractor.
 */
import { emptyRecord, mergeRecords, fingerprintRecord, type GeneralRecord, GENERAL_FIELDS } from "../lib/generalSchema";
import { clean, flatten, safeJSON } from "../lib/utils";
import { pickGeneralSite, universalCardExtract, type GeneralCard } from "./generalSites";

const BUSINESS_TYPES = new Set([
  "localbusiness","restaurant","store","shop","hotel","lodging","place",
  "medicalbusiness","medicalclinic","physician","dentist","hospital",
  "automotivebusiness","foodestablishment","barorpub","cafeorcoffeeshop",
  "professionalservice","homeandconstructionbusiness","financialservice",
  "legalservice","healthandbeautybusiness","sportsclub","travelagency",
  "library","museum","park","stadiumorarena","civicstructure",
]);

function collectJsonLd(): any[] {
  const out: any[] = [];
  for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
    const data = safeJSON(node.textContent || "");
    if (!data) continue;
    for (const item of flatten(data)) if (item && typeof item === "object") out.push(item);
  }
  return out;
}

function isBusiness(item: any): boolean {
  const t = item?.["@type"];
  const types: string[] = Array.isArray(t) ? t.map((x) => String(x).toLowerCase()) : t ? [String(t).toLowerCase()] : [];
  return types.some((typ) => BUSINESS_TYPES.has(typ) || typ.includes("business"));
}

function fromJsonLd(item: any): Partial<GeneralRecord> {
  const r: Partial<GeneralRecord> = {};
  r.name = clean(item.name || item.legalName);
  r.description = clean(item.description);
  const img = item.image;
  r.image = clean(Array.isArray(img) ? img[0] : img);
  r.website = clean(item.url || item.sameAs);
  r.price_range = clean(item.priceRange);
  r.menu_url = clean(typeof item.hasMenu === "string" ? item.hasMenu : item.menu);

  const t = item["@type"];
  if (Array.isArray(t)) r.category = t.filter(Boolean).join(", ");
  else if (t) r.category = clean(t);

  const sub = item.servesCuisine || item.category;
  if (Array.isArray(sub)) r.subcategories = sub.filter(Boolean).join(", ");
  else if (sub) r.subcategories = clean(sub);

  let addr = item.address;
  if (Array.isArray(addr)) addr = addr[0];
  if (addr && typeof addr === "object") {
    r.street_address = clean(addr.streetAddress);
    r.city = clean(addr.addressLocality);
    r.region = clean(addr.addressRegion);
    const country = typeof addr.addressCountry === "object" ? (addr.addressCountry?.name || addr.addressCountry?.identifier) : addr.addressCountry;
    r.country = clean(country);
    r.postal_code = clean(addr.postalCode);
    if (!r.address) {
      const parts = [r.street_address, r.city, r.region, r.postal_code, r.country];
      r.address = parts.filter(Boolean).join(", ");
    }
  } else if (typeof addr === "string") {
    r.address = clean(addr);
  }

  if (item.geo && typeof item.geo === "object") {
    r.latitude = clean(item.geo.latitude);
    r.longitude = clean(item.geo.longitude);
  }

  r.phone = clean(item.telephone || item.phone);
  r.email = clean(item.email);

  if (item.aggregateRating && typeof item.aggregateRating === "object") {
    r.rating = clean(item.aggregateRating.ratingValue);
    r.review_count = clean(item.aggregateRating.reviewCount || item.aggregateRating.ratingCount);
  }

  const oh = item.openingHoursSpecification || item.openingHours;
  if (oh) {
    if (Array.isArray(oh)) {
      r.hours = oh.map((spec: any) => {
        if (typeof spec === "string") return spec;
        if (typeof spec === "object") {
          const days = Array.isArray(spec.dayOfWeek) ? spec.dayOfWeek.map((d: string) => d.replace("https://schema.org/", "")).join("/") : (spec.dayOfWeek || "");
          return `${days}: ${spec.opens || ""}-${spec.closes || ""}`;
        }
        return "";
      }).filter(Boolean).join("; ");
    } else if (typeof oh === "string") {
      r.hours = oh;
    }
  }

  if (Array.isArray(item.sameAs)) r.social_links = item.sameAs.filter(Boolean).join(", ");
  else if (typeof item.sameAs === "string") r.social_links = item.sameAs;

  try { r.raw_jsonld = JSON.stringify(item).slice(0, 8000); } catch { r.raw_jsonld = ""; }
  return r;
}

const PHONE_RX = /(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{2,4}/;
const EMAIL_RX = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/;

function fromHeuristics(root: Document = document): Partial<GeneralRecord> {
  const r: Partial<GeneralRecord> = {};
  const text = (root.body?.innerText || root.body?.textContent || "").slice(0, 50000);
  const phone = text.match(PHONE_RX);
  if (phone && phone[0].replace(/\D/g, "").length >= 7) r.phone = phone[0];
  const email = text.match(EMAIL_RX);
  if (email && !/noreply|no-reply|example\.com/i.test(email[0])) r.email = email[0];
  return r;
}

function fromOpenGraph(root: Document = document): Partial<GeneralRecord> {
  const og = (n: string) => (root.querySelector(`meta[property='${n}'], meta[name='${n}']`) as HTMLMetaElement | null)?.content?.trim() || "";
  const r: Partial<GeneralRecord> = {};
  r.name = og("og:title");
  r.description = og("og:description") || og("description");
  r.image = og("og:image");
  r.website = og("og:url");
  return r;
}

export function extractGeneral(): { record: GeneralRecord | null; found: boolean } {
  const lds = collectJsonLd().filter(isBusiness);
  const parts: Partial<GeneralRecord>[] = [];
  for (const ld of lds) parts.push(fromJsonLd(ld));
  parts.push(fromMicrodata(document));
  parts.push(fromOpenGraph(document));
  parts.push(fromHeuristics(document));
  parts.push(fromSiteSpecific(document));

  const rec = mergeRecords([emptyRecord(), ...parts.filter(Boolean)]);
  rec.source_url = location.href;
  rec.source_domain = location.hostname;
  rec.scraped_at = new Date().toISOString();

  if (!rec.name) {
    const h1 = document.querySelector("h1");
    if (h1) rec.name = clean(h1.textContent || "");
  }
  if (!rec.name) return { record: null, found: false };

  rec.id = fingerprintRecord(rec);
  return { record: rec, found: true };
}

/** Microdata extraction: schema.org/{LocalBusiness,Restaurant,Place,Hotel,Store}. */
function fromMicrodata(root: Document = document): Partial<GeneralRecord> {
  const r: Partial<GeneralRecord> = {};
  const scope = root.querySelector("[itemtype*='schema.org/LocalBusiness' i], [itemtype*='schema.org/Restaurant' i], [itemtype*='schema.org/Place' i], [itemtype*='schema.org/Store' i], [itemtype*='schema.org/Hotel' i], [itemtype*='schema.org/Organization' i]");
  if (!scope) return r;
  const get = (prop: string) => {
    const el = scope.querySelector(`[itemprop='${prop}']`) as HTMLElement | null;
    if (!el) return "";
    if (el.hasAttribute("content")) return el.getAttribute("content") || "";
    if (el.tagName === "META") return el.getAttribute("content") || "";
    if (el.tagName === "A" || el.tagName === "LINK") return el.getAttribute("href") || el.textContent || "";
    return el.textContent || "";
  };
  r.name = clean(get("name"));
  r.description = clean(get("description"));
  r.phone = clean(get("telephone") || get("phone"));
  r.email = clean(get("email"));
  r.website = clean(get("url") || get("sameAs"));
  r.image = clean(get("image"));
  r.price_range = clean(get("priceRange"));
  r.street_address = clean(get("streetAddress"));
  r.city = clean(get("addressLocality"));
  r.region = clean(get("addressRegion"));
  r.country = clean(get("addressCountry"));
  r.postal_code = clean(get("postalCode"));
  r.rating = clean(get("ratingValue"));
  r.review_count = clean(get("reviewCount") || get("ratingCount"));
  if (!r.address && (r.street_address || r.city)) {
    r.address = [r.street_address, r.city, r.region, r.postal_code, r.country].filter(Boolean).join(", ");
  }
  return r;
}

/** Site-specific detail-page selectors. Yelp/Google Maps don't always include
 *  full Schema.org markup, so pull from their unique class names. */
function fromSiteSpecific(root: Document = document): Partial<GeneralRecord> {
  const host = location.hostname;
  const r: Partial<GeneralRecord> = {};
  const get = (q: string) => clean((root.querySelector(q) as HTMLElement | null)?.textContent || "");

  if (/yelp\./i.test(host)) {
    r.name = r.name || get("h1");
    // Yelp categories (linked elements after the rating)
    const cats = Array.from(root.querySelectorAll("a[href*='/c/']")).map((a) => clean(a.textContent || "")).filter(Boolean);
    if (cats.length) r.category = cats.slice(0, 5).join(", ");
    // Address: <address> or "Get Directions" sibling
    const addr = root.querySelector("address, p[class*='addressContainer']");
    if (addr) r.address = clean(addr.textContent || "");
    // Phone: data-testid or aria-label
    const phoneEl = root.querySelector("p[class*='phone'], a[href^='tel:']");
    if (phoneEl) r.phone = clean(phoneEl.textContent || (phoneEl as HTMLAnchorElement).href?.replace(/^tel:/, "") || "");
    // Website
    const webA = root.querySelector("a[href^='http']:not([href*='yelp.com'])[role='link']") as HTMLAnchorElement | null;
    if (webA) r.website = webA.href;
    // Rating from aria-label
    const ratingEl = root.querySelector("[role='img'][aria-label*='star']") as HTMLElement | null;
    if (ratingEl) {
      const m = (ratingEl.getAttribute("aria-label") || "").match(/[\d.]+/);
      if (m) r.rating = m[0];
    }
    // Review count
    const rcText = clean(root.body.textContent || "");
    const rcMatch = rcText.match(/(\d{1,3}(?:,\d{3})*)\s*reviews?\b/i);
    if (rcMatch) r.review_count = rcMatch[1].replace(/,/g, "");
    // Hours
    const hoursTable = root.querySelector("table[class*='hours']");
    if (hoursTable) r.hours = clean(hoursTable.textContent || "").slice(0, 500);
  } else if (/google\.[a-z.]+/i.test(host) && /\/maps\//.test(location.pathname)) {
    r.name = get("h1");
    r.address = get("button[data-item-id='address']");
    r.phone = get("button[data-tooltip='Copy phone number']") || get("button[aria-label*='Phone']");
    const webBtn = root.querySelector("a[data-item-id='authority']") as HTMLAnchorElement | null;
    if (webBtn) r.website = webBtn.href;
  }
  return r;
}

/**
 * List-page card extraction. Order of strategies:
 *   1. Site-specific adapter (yelp, google-maps, tripadvisor, yellowpages)
 *   2. JSON-LD ItemList (LocalBusiness items)
 *   3. Universal detail-URL pattern matcher (any /biz/, /place/, /listing/, ...)
 *   4. Address+phone heuristic (last resort)
 */
export function extractGeneralCards(): GeneralCard[] {
  // 1. Site adapter
  const adapter = pickGeneralSite();
  if (adapter) {
    const cards = adapter.extract(document);
    if (cards.length >= 2) return cards;
  }

  const out: GeneralCard[] = [];
  const seen = new Set<string>();

  // 2. JSON-LD ItemList
  for (const item of collectJsonLd()) {
    if (String(item["@type"] || "").toLowerCase() === "itemlist") {
      for (const el of (item.itemListElement || [])) {
        const x = el?.item || el;
        if (x && isBusiness(x)) {
          const url = x.url || "";
          if (!url || seen.has(url)) continue;
          seen.add(url);
          out.push({
            url,
            name: clean(x.name || ""),
            address: clean(typeof x.address === "string" ? x.address : (x.address?.streetAddress || "")),
            snippet: clean(x.description || "").slice(0, 240),
          });
        }
      }
    }
  }
  if (out.length >= 2) return out;

  // 3. Universal pattern: any anchor whose URL looks like a business/place detail page.
  const universal = universalCardExtract(document);
  if (universal.length >= 2) return universal;

  // 4. Last resort: heuristic — repeating siblings with name + (address OR phone).
  const candidates = document.querySelectorAll("li, article, div[class*='result'], div[class*='card'], div[class*='listing'], div[class*='business'], div[class*='Business']");
  for (const c of Array.from(candidates) as Element[]) {
    const h = c.querySelector("h1, h2, h3, h4, [class*='title'], [class*='name'], [class*='Name']");
    if (!h) continue;
    const name = clean(h.textContent || "");
    if (!name || name.length < 2) continue;
    const addr = c.querySelector("address, [class*='address'], [class*='Address'], [class*='location'], [class*='secondaryAttributes']");
    const phone = c.querySelector("[class*='phone'], [class*='Phone'], [class*='tel']");
    if (!addr && !phone) continue;
    const a = c.querySelector("a[href]") as HTMLAnchorElement | null;
    const url = a ? new URL(a.getAttribute("href") || "", location.href).href : "";
    if (!url || seen.has(url)) continue;
    seen.add(url);
    out.push({
      url,
      name,
      address: clean(addr?.textContent || ""),
      snippet: clean(c.textContent || "").slice(0, 240),
    });
  }
  return out;
}
