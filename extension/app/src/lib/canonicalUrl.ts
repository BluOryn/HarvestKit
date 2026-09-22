/**
 * URL canonicalisation shared by both fingerprint functions.
 *
 * Must stay in lock-step with `canonicalize_url` / `TRACKING_PARAMS` in
 * src/job_scraper/models.py — the CLI and the extension write into the same
 * `id` column, so a divergence here silently splits duplicates. The golden
 * vectors in tests/canonical-vectors.json are asserted by both sides for
 * exactly that reason; add a case there before changing anything here.
 *
 * The rule this file used to break: **do not hand the output to the WHATWG
 * serialiser.** `new URL(x).toString()` punycodes IDN hosts, percent-encodes
 * non-ASCII path bytes, resolves dot-segments and re-serialises the query with
 * form-encoding rules. Python's urlparse/urlunparse does none of that, so
 * `…/stellen/bürokauffrau-münchen` became two different ids — and German,
 * French and Nordic boards put accented words in path slugs constantly.
 *
 * So the canonical form is defined as the operations both languages can
 * perform identically on the raw string:
 *
 *   1. trim
 *   2. lowercase the scheme and the host (nothing else)
 *   3. drop the port when it is the scheme's default
 *   4. drop the fragment
 *   5. drop tracking parameters, leaving the rest in their original order and
 *      original encoding
 *   6. strip trailing slashes
 *
 * The path, the query values and any percent-encoding are passed through
 * byte-for-byte.
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

const DEFAULT_PORTS: Record<string, string> = { http: "80", https: "443" };

/** `scheme://authority` split off the front, or null when there is no scheme. */
function splitScheme(raw: string): { scheme: string; rest: string } | null {
  const match = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/(.*)$/s.exec(raw);
  return match ? { scheme: match[1], rest: match[2] } : null;
}

/** Lowercase the host, drop a default port, leave userinfo alone. */
function normaliseAuthority(authority: string, scheme: string): string {
  const at = authority.lastIndexOf("@");
  const userinfo = at >= 0 ? authority.slice(0, at + 1) : "";
  let hostport = at >= 0 ? authority.slice(at + 1) : authority;

  // An IPv6 literal keeps its brackets and may contain colons of its own.
  let host = hostport;
  let port = "";
  if (hostport.startsWith("[")) {
    const close = hostport.indexOf("]");
    if (close >= 0) {
      host = hostport.slice(0, close + 1);
      const tail = hostport.slice(close + 1);
      if (tail.startsWith(":")) port = tail.slice(1);
    }
  } else {
    const colon = hostport.lastIndexOf(":");
    if (colon >= 0) {
      host = hostport.slice(0, colon);
      port = hostport.slice(colon + 1);
    }
  }
  host = host.toLowerCase();
  if (port && port === DEFAULT_PORTS[scheme]) port = "";
  hostport = port ? `${host}:${port}` : host;
  return userinfo + hostport;
}

/**
 * Drop tracking parameters, preserving order and the original encoding of
 * everything kept. Deliberately a string operation: re-encoding through
 * URLSearchParams turns `+` into `%2B` and `%20` into `+`, which is another
 * way the two implementations drifted apart.
 */
function stripTracking(query: string): string {
  if (!query) return "";
  return query
    .split("&")
    .filter((piece) => {
      if (!piece) return false;
      const key = piece.split("=", 1)[0];
      return !TRACKING_PARAMS.has(key.toLowerCase());
    })
    .join("&");
}

export function canonicalizeUrl(raw: string): string {
  if (!raw) return "";
  let working = raw.trim();
  if (!working) return "";

  // The fragment goes first, so a `#` cannot be mistaken for part of the query.
  const hash = working.indexOf("#");
  if (hash >= 0) working = working.slice(0, hash);

  const questionMark = working.indexOf("?");
  let query = "";
  if (questionMark >= 0) {
    query = working.slice(questionMark + 1);
    working = working.slice(0, questionMark);
  }
  query = stripTracking(query);

  const split = splitScheme(working);
  if (split) {
    const scheme = split.scheme.toLowerCase();
    const slash = split.rest.search(/[/?]/);
    const authority = slash >= 0 ? split.rest.slice(0, slash) : split.rest;
    const path = slash >= 0 ? split.rest.slice(slash) : "";
    working = `${scheme}://${normaliseAuthority(authority, scheme)}${path}`;
  }
  // No scheme: a relative URL. It is left as it is apart from the fragment,
  // query and trailing-slash handling above — same as the Python side, which
  // gets an empty netloc from urlparse and reassembles the rest verbatim.

  const out = query ? `${working}?${query}` : working;
  return out.replace(/\/+$/, "");
}
