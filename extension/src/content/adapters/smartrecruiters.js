import { clean } from "../../lib/utils.js";

export function isMatch() {
  return /jobs\.smartrecruiters\.com|smartrecruiters\.com/i.test(location.host + location.pathname);
}

export function extract(root = document) {
  const job = { source_ats: "smartrecruiters" };
  job.title = clean(root.querySelector("h1.job-title, h1[data-test='job-title']")?.textContent);
  job.company = clean(root.querySelector(".company-name, [data-test='company-name']")?.textContent);
  job.location = clean(root.querySelector(".job-location, [data-test='location']")?.textContent);
  job.department = clean(root.querySelector("[data-test='department']")?.textContent);
  job.description = clean(root.querySelector(".job-sections, main, article")?.innerText);
  const apply = root.querySelector("a.application-link, a[href*='apply']");
  if (apply) job.apply_url = apply.href;
  return job;
}
