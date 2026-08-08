/**
 * Strip site/company chrome from a `<title>` or `og:title`.
 *
 * Mirrors GENERIC_TITLE_SUFFIX / TITLE_SITE_PREFIX in src/job_scraper/universal.py.
 *
 * Without this, `mergeJobs` (longest-string-wins) let
 * "Senior Backend Engineer — Acme GmbH" from the <title> tag beat the
 * authoritative JSON-LD "Senior Backend Engineer".
 */

const SEPARATORS = /\s+[|–—‒-]\s+/;

/** Job boards and ATSs that append their own name to the page title. */
const AGGREGATOR_RX =
  /^(FINN\.no|NAV|arbeidsplassen|LinkedIn|Indeed|Glassdoor|Monster|jobs\.ch|Stepstone|Workday|Jobbnorge|Greenhouse|Lever|Workable|The\s*Hub|Jobbsafari|Karrierestart|Ashby|SmartRecruiters|Personio|Recruitee|Teamtailor|BambooHR|Jobvite|Arbeitsagentur)\b/i;

const JOB_NOISE_RX = /^(job|jobs|career|careers|stillinger|stellenangebote|vacancies|hiring)$/i;

/**
 * @param raw       title text as found in the DOM
 * @param company   known employer name, if any — its own segment is dropped
 */
export function cleanTitle(raw: string, company = ""): string {
  const text = (raw || "").replace(/\s+/g, " ").trim();
  if (!text) return "";

  const parts = text
    .split(SEPARATORS)
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length < 2) return text;

  const co = company.trim().toLowerCase();
  const keep = parts.filter((part) => {
    const lower = part.toLowerCase();
    if (AGGREGATOR_RX.test(part)) return false;
    if (JOB_NOISE_RX.test(part)) return false;
    if (co && (lower === co || lower.startsWith(`${co} `) || lower.endsWith(` ${co}`))) return false;
    return true;
  });

  if (keep.length === 0) return parts[0];
  if (keep.length === parts.length) {
    // Nothing recognisably removable — assume the LAST segment is the site or
    // employer, which is by far the most common layout ("Role | Company").
    return parts.slice(0, -1).join(" - ");
  }
  return keep.join(" - ");
}
