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
  parts.push(fromOpenGraph(document));
  parts.push(fromHeuristics(document));

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

/** List-page card extraction: returns clickable detail URLs to deep-scrape. */
export function extractGeneralCards(): { url: string; name: string; address: string; snippet: string }[] {
  const out: { url: string; name: string; address: string; snippet: string }[] = [];
  const seen = new Set<string>();

  // Strategy 1: JSON-LD ItemList
  for (const item of collectJsonLd()) {
    if (String(item["@type"] || "").toLowerCase() === "itemlist") {
      for (const el of (item.itemListElement || [])) {
        const x = el?.item || el;
        if (x && isBusiness(x)) {
          const url = x.url || "";
          if (!url || seen.has(url)) continue;
          seen.add(url);
          out.push({ url, name: clean(x.name || ""), address: clean(typeof x.address === "string" ? x.address : (x.address?.streetAddress || "")), snippet: clean(x.description || "").slice(0, 240) });
        }
      }
    }
  }
  if (out.length >= 2) return out;

  // Strategy 2: heuristic — repeating siblings with title + address/phone hints.
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
    out.push({ url, name, address: clean(addr?.textContent || ""), snippet: clean(c.textContent || "").slice(0, 240) });
  }
  return out;
}
