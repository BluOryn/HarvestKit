// Mirror of src/general_scraper/models.py — keep field list in sync.
import { canonicalizeUrl } from "./canonicalUrl";
import { sha1Hex } from "./sha1";

export const GENERAL_FIELDS = [
  "name",
  "category",
  "subcategories",
  "description",
  "address",
  "street_address",
  "city",
  "region",
  "country",
  "postal_code",
  "latitude",
  "longitude",
  "phone",
  "email",
  "website",
  "social_links",
  "rating",
  "review_count",
  "price_range",
  "hours",
  "image",
  "tags",
  "amenities",
  "menu_url",
  "reservation_url",
  "is_claimed",
  "external_id",
  "source_url",
  "source_listing_url",
  "source_domain",
  "raw_jsonld",
  "scraped_at",
] as const;

export type GeneralField = typeof GENERAL_FIELDS[number];
export type GeneralRecord = Record<GeneralField, string> & {
  id?: string;
  saved_at?: number;
};

export function emptyRecord(): GeneralRecord {
  const out: any = {};
  for (const f of GENERAL_FIELDS) out[f] = "";
  return out;
}

/** Must stay byte-identical to `GeneralRecord.fingerprint()` in
 *  src/general_scraper/models.py. See the note on `fingerprint()` in schema.ts. */
export function fingerprintRecord(r: Partial<GeneralRecord>): string {
  const parts = [
    canonicalizeUrl(r.source_url || "").toLowerCase(),
    (r.name || "").toLowerCase(),
    (r.address || "").toLowerCase(),
    (r.phone || "").toLowerCase(),
  ];
  return sha1Hex(parts.join(" | ").replace(/\s+/g, " ").trim());
}

export function mergeRecords(parts: Partial<GeneralRecord>[]): GeneralRecord {
  const out = emptyRecord();
  for (const part of parts) {
    if (!part) continue;
    for (const k of GENERAL_FIELDS) {
      const v = (part as any)[k];
      if (v == null) continue;
      const str = Array.isArray(v) ? v.filter(Boolean).join(" | ") : String(v).trim();
      if (!str) continue;
      const cur = out[k];
      if (!cur || str.length > cur.length) (out as any)[k] = str;
    }
  }
  return out;
}
