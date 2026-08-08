/**
 * URL canonicalisation shared by both fingerprint functions.
 *
 * Must stay in lock-step with `canonicalize_url` / `TRACKING_PARAMS` in
 * src/job_scraper/models.py — the CLI and the extension write into the same
 * `id` column, so a divergence here silently splits duplicates.
 */

export const TRACKING_PARAMS = new Set([
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_term",
  "utm_content",
  "utm_id",
  "gclid",
  "gbraid",
  "wbraid",
  "fbclid",
  "msclkid",
  "mc_cid",
  "mc_eid",
  "igshid",
  "ref_src",
]);

export function canonicalizeUrl(raw: string): string {
  if (!raw) return "";
  const trimmed = raw.trim();
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return trimmed;
  }
  url.hash = "";
  // Delete during iteration over a snapshot — mutating URLSearchParams while
  // iterating it skips entries.
  for (const key of [...url.searchParams.keys()]) {
    if (TRACKING_PARAMS.has(key.toLowerCase())) url.searchParams.delete(key);
  }
  // Python's urlunparse drops the "?" when the query is empty; match that, then
  // strip a single trailing slash exactly like `.rstrip("/")` on the whole URL.
  let out = url.toString();
  if (out.endsWith("?")) out = out.slice(0, -1);
  return out.replace(/\/+$/, "");
}
