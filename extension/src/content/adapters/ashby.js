import { clean } from "../../lib/utils.js";

export function isMatch() {
  return /jobs\.ashbyhq\.com|ashbyhq\.com\/job-board/i.test(location.host + location.pathname);
}

export function extract(root = document) {
  const job = { source_ats: "ashby" };
  job.title = clean(root.querySelector("h1, h2[class*='Heading']")?.textContent);
  job.company = clean(root.querySelector("[class*='OrganizationName'], img[alt]")?.textContent || root.querySelector("img[alt]")?.alt);
  job.location = clean(root.querySelector("[class*='Location']")?.textContent);
  job.employment_type = clean(root.querySelector("[class*='EmploymentType']")?.textContent);
  job.description = clean(root.querySelector("[class*='JobDescription'], main, article")?.innerText || "");
  const apply = root.querySelector("a[class*='ApplyButton'], a[href*='application']");
  if (apply) job.apply_url = apply.href;
  return job;
}
