// Canonical job record. Every field optional. Strings empty when missing.
export const JOB_FIELDS = [
  "title",
  "company",
  "company_logo",
  "company_size",
  "company_industry",
  "company_website",
  "department",
  "team",
  "location",
  "city",
  "region",
  "country",
  "postal_code",
  "remote_type",          // remote | hybrid | onsite | ""
  "employment_type",      // full-time | part-time | contract | internship
  "seniority",            // junior | mid | senior | staff | principal | director
  "salary_min",
  "salary_max",
  "salary_currency",
  "salary_period",        // year | month | hour | day
  "equity",
  "posted_date",
  "valid_through",
  "start_date",
  "language",
  "description",
  "responsibilities",
  "requirements",
  "qualifications",
  "benefits",
  "tech_stack",
  "skills",
  "education_required",
  "experience_years",
  "work_authorization",
  "visa_sponsorship",
  "relocation",
  "travel_required",
  "recruiter_name",
  "recruiter_title",
  "recruiter_email",
  "recruiter_phone",
  "recruiter_linkedin",
  "hiring_manager",
  "hiring_manager_email",
  "application_email",
  "application_phone",
  "apply_url",
  "job_url",
  "external_id",          // ATS posting id / requisition number
  "requisition_id",
  "source_ats",           // greenhouse | lever | workday | personio | ashby | ...
  "source_domain",
  "raw_jsonld",           // string blob, debugging
  "confidence",           // 0-1 detection confidence
  "scraped_at",
];

export function emptyJob() {
  return Object.fromEntries(JOB_FIELDS.map((f) => [f, ""]));
}

// Merge multiple partial extractions. Later values overwrite earlier ONLY when
// the new value is longer/non-empty. Arrays joined with " | ".
export function mergeJobs(parts) {
  const out = emptyJob();
  for (const part of parts) {
    if (!part) continue;
    for (const k of JOB_FIELDS) {
      const v = part[k];
      if (v == null) continue;
      const str = Array.isArray(v) ? v.filter(Boolean).join(" | ") : String(v).trim();
      if (!str) continue;
      const cur = out[k];
      if (!cur || str.length > cur.length) {
        out[k] = str;
      }
    }
  }
  return out;
}
