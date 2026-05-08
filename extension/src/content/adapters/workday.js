import { clean } from "../../lib/utils.js";

export function isMatch() {
  return /\.myworkdayjobs\.com/i.test(location.host);
}

export function extract(root = document) {
  const job = { source_ats: "workday" };
  job.title = clean(root.querySelector("[data-automation-id='jobPostingHeader'], h2[data-automation-id='jobTitle']")?.textContent);
  job.company = location.host.split(".")[0];
  job.location = clean(root.querySelector("[data-automation-id='locations'], [data-automation-id='locationsBlock']")?.innerText);
  job.posted_date = clean(root.querySelector("[data-automation-id='postedOn']")?.textContent);
  job.description = clean(root.querySelector("[data-automation-id='jobPostingDescription']")?.innerText);
  job.requisition_id = clean(root.querySelector("[data-automation-id='requisitionId']")?.textContent);
  const apply = root.querySelector("a[data-uxi-element-id='Apply']");
  if (apply) job.apply_url = apply.href;
  return job;
}
