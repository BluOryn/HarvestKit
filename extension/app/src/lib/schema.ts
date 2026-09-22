import { canonicalizeUrl } from "./canonicalUrl";
import { sha1Hex } from "./sha1";

// Mirror of JOB_FIELDS in src/job_scraper/models.py — order matters (CSV columns).
export const JOB_FIELDS = [
  "title","company","company_logo","company_size","company_industry","company_website",
  "department","team","location","city","region","country","postal_code",
  "remote_type","employment_type","seniority",
  "salary_min","salary_max","salary_currency","salary_period","equity",
  "posted_date","valid_through","start_date","language",
  "description","responsibilities","requirements","qualifications","benefits",
  "tech_stack","skills","education_required","experience_years",
  "work_authorization","visa_sponsorship","relocation","travel_required",
  "recruiter_name","recruiter_title","recruiter_email","recruiter_phone","recruiter_linkedin",
  "hiring_manager","hiring_manager_email","application_email","application_phone",
  "apply_url","job_url","external_id","requisition_id",
  "source_ats","source_domain","raw_jsonld",
  "confidence","scraped_at",
] as const;

export type JobField = typeof JOB_FIELDS[number];
export type Job = Record<JobField, string> & {
  id?: string;          // fingerprint
  tags?: string[];
  notes?: string;
  starred?: boolean;
  saved_at?: number;
};

export function emptyJob(): Job {
  const out: any = {};
  for (const f of JOB_FIELDS) out[f] = "";
  out.tags = [];
  out.notes = "";
  out.starred = false;
  return out;
}

/**
 * Stable dedupe key. MUST stay byte-identical to `JobListing.fingerprint()` in
 * src/job_scraper/models.py — both write into the same `id` column.
 *
 * Contract: canonicalised apply/job URL + lowercased title/company/location,
 * joined with " | ", whitespace-collapsed, SHA-1 hex.
 *
 * This previously returned the raw joined string (not a hash) and joined with
 * "|" rather than " | ", so extension ids never matched CLI ids for the same
 * posting.
 */
export function fingerprint(job: Partial<Job>): string {
  const parts = [
    canonicalizeUrl(job.apply_url || job.job_url || "").toLowerCase(),
    (job.title || "").toLowerCase(),
    (job.company || "").toLowerCase(),
    (job.location || "").toLowerCase(),
  ];
  return sha1Hex(parts.join(" | ").replace(/\s+/g, " ").trim());
}

/**
 * Fields where "longer" means nothing, so the first value found wins.
 *
 * Mirrors `_FIRST_WINS_FIELDS` in src/job_scraper/models.py. Longest-string-wins
 * is a decent heuristic for prose — a full description beats a card snippet —
 * and actively harmful everywhere else: it lets a sentence overwrite an ISO
 * date, a tracking-laden URL beat a clean one, and a formatted salary range
 * beat the parsed number. The Python side fixed this; this side did not, and
 * the two write into the same table.
 */
export const FIRST_WINS_FIELDS: ReadonlySet<string> = new Set([
  "posted_date",
  "valid_through",
  "start_date",
  "scraped_at",
  "salary_min",
  "salary_max",
  "salary_currency",
  "salary_period",
  "external_id",
  "requisition_id",
  "source_ats",
  "source_domain",
  "confidence",
  "apply_url",
  "job_url",
  "company_website",
  "company_logo",
  "recruiter_email",
  "recruiter_phone",
  "recruiter_linkedin",
  "application_email",
  "application_phone",
  "hiring_manager_email",
]);

export function mergeJobs(parts: Partial<Job>[]): Job {
  const out = emptyJob();
  for (const part of parts) {
    if (!part) continue;
    for (const k of JOB_FIELDS) {
      const v = (part as any)[k];
      if (v == null) continue;
      const str = Array.isArray(v) ? v.filter(Boolean).join(" | ") : String(v).trim();
      if (!str) continue;
      const cur = out[k];
      if (!cur) {
        (out as any)[k] = str;
        continue;
      }
      // Identifiers, dates, URLs and enums keep what they already have.
      if (FIRST_WINS_FIELDS.has(k)) continue;
      if (str.length > cur.length) (out as any)[k] = str;
    }
  }
  return out;
}
