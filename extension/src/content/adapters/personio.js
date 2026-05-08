import { clean } from "../../lib/utils.js";

export function isMatch() {
  return /\.jobs\.personio\.(de|com)/i.test(location.host);
}

export function extract(root = document) {
  const job = { source_ats: "personio" };
  job.title = clean(root.querySelector("h1, [data-test='job-title']")?.textContent);
  job.company = location.host.split(".")[0];
  job.location = clean(root.querySelector(".jobInfo .location, [data-test='job-location']")?.textContent);
  job.department = clean(root.querySelector("[data-test='job-department']")?.textContent);
  job.employment_type = clean(root.querySelector("[data-test='job-employment-type']")?.textContent);
  job.description = clean(root.querySelector("[data-test='job-description'], main, .content")?.innerText);
  const apply = root.querySelector("a[href*='apply'], button[data-test='apply-button']");
  if (apply) job.apply_url = apply.href || location.href;
  return job;
}
