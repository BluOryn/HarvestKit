import { clean } from "../../lib/utils.js";

export function isMatch() {
  return /jobs\.lever\.co|lever\.co\/postings/i.test(location.host + location.pathname);
}

export function extract(root = document) {
  const job = { source_ats: "lever" };
  job.title = clean(root.querySelector(".posting-headline h2, h2[data-qa='posting-name']")?.textContent);
  job.company = clean(root.querySelector(".main-header-logo img")?.alt) || location.hostname.split(".")[0];
  job.location = clean(root.querySelector(".sort-by-time .posting-categories .location, .posting-category.location")?.textContent);
  job.team = clean(root.querySelector(".sort-by-team .posting-categories .department, .posting-category.department")?.textContent);
  job.employment_type = clean(root.querySelector(".posting-category.commitment")?.textContent);
  job.remote_type = clean(root.querySelector(".posting-category.workplaceTypes")?.textContent);
  job.description = clean(root.querySelector(".section-wrapper.page-full-width, .content-wrapper")?.innerText || "");
  const apply = root.querySelector("a.postings-btn[data-qa='btn-apply'], a[href*='application']");
  if (apply) job.apply_url = apply.href;
  return job;
}
