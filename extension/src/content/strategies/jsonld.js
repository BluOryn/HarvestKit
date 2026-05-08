import { clean, htmlToText } from "../../lib/utils.js";

export function fromJsonLd(item) {
  if (!item) return null;
  const job = {};
  job.title = clean(item.title || item.name);
  job.description = htmlToText(item.description || "");

  const org = item.hiringOrganization;
  if (org && typeof org === "object") {
    job.company = clean(org.name);
    job.company_logo = clean(org.logo && (org.logo.url || org.logo));
    job.company_website = clean(org.sameAs || org.url);
  } else if (typeof org === "string") {
    job.company = clean(org);
  }

  // Location(s)
  const locArr = Array.isArray(item.jobLocation) ? item.jobLocation : [item.jobLocation].filter(Boolean);
  const locStrs = [];
  for (const l of locArr) {
    if (!l) continue;
    if (typeof l === "string") {
      locStrs.push(clean(l));
      continue;
    }
    const a = l.address || l;
    if (a && typeof a === "object") {
      const parts = [a.addressLocality, a.addressRegion, a.addressCountry, a.postalCode];
      locStrs.push(parts.filter(Boolean).map(clean).join(", "));
      job.city = job.city || clean(a.addressLocality);
      job.region = job.region || clean(a.addressRegion);
      job.country = job.country || clean(typeof a.addressCountry === "object" ? a.addressCountry.name : a.addressCountry);
      job.postal_code = job.postal_code || clean(a.postalCode);
    }
  }
  job.location = locStrs.filter(Boolean).join(" | ");

  // Remote
  const locType = item.jobLocationType;
  if (locType && /TELECOMMUTE|REMOTE/i.test(JSON.stringify(locType))) {
    job.remote_type = "remote";
  } else if (item.applicantLocationRequirements) {
    job.remote_type = "remote";
  }

  job.employment_type = clean(Array.isArray(item.employmentType) ? item.employmentType.join(", ") : item.employmentType);
  job.posted_date = clean(item.datePosted);
  job.valid_through = clean(item.validThrough);
  job.start_date = clean(item.jobStartDate);
  job.language = clean(Array.isArray(item.inLanguage) ? item.inLanguage.join(", ") : item.inLanguage);

  // Salary
  const sal = item.baseSalary;
  if (sal && typeof sal === "object") {
    job.salary_currency = clean(sal.currency);
    const v = sal.value;
    if (v && typeof v === "object") {
      job.salary_min = clean(v.minValue);
      job.salary_max = clean(v.maxValue || v.value);
      job.salary_period = clean(v.unitText);
    }
  }
  if (item.estimatedSalary && typeof item.estimatedSalary === "object") {
    const v = item.estimatedSalary.value || item.estimatedSalary;
    if (typeof v === "object") {
      job.salary_min = job.salary_min || clean(v.minValue);
      job.salary_max = job.salary_max || clean(v.maxValue);
    }
  }

  // Education / experience
  const edu = item.educationRequirements;
  if (edu) job.education_required = clean(typeof edu === "object" ? edu.credentialCategory || edu.name : edu);
  const exp = item.experienceRequirements;
  if (exp && typeof exp === "object") {
    job.experience_years = clean(exp.monthsOfExperience ? exp.monthsOfExperience / 12 : exp.name);
  } else if (exp) {
    job.experience_years = clean(exp);
  }

  // Identifier
  if (item.identifier) {
    if (typeof item.identifier === "object") {
      job.external_id = clean(item.identifier.value || item.identifier.name);
    } else {
      job.external_id = clean(item.identifier);
    }
  }

  // Apply
  job.apply_url = clean(item.url || item.applicationContact || item.directApply);
  job.job_url = clean(item.url) || location.href;

  // Department / occupation
  job.department = clean(item.occupationalCategory || item.industry);

  job.raw_jsonld = JSON.stringify(item).slice(0, 8000);
  return job;
}
