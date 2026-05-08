import { clean } from "../../lib/utils.js";

export function isMatch() {
  return /boards\.greenhouse\.io|greenhouse\.io\/embed/i.test(location.host + location.pathname);
}

export function extract(root = document) {
  const job = { source_ats: "greenhouse" };
  job.title = clean(root.querySelector(".app-title, h1.app-title, .posting-headline h2, h1")?.textContent);
  job.company = clean(root.querySelector(".company-name, #company_name, .header .company")?.textContent);
  job.location = clean(root.querySelector(".location, .posting-categories .location")?.textContent);
  job.department = clean(root.querySelector(".department, [data-mapped='department']")?.textContent);
  job.description = clean(root.querySelector("#content, .content, .body, .opening")?.innerText || root.body?.innerText);
  const apply = root.querySelector("a#apply_button, a.template-btn-submit, a[href*='application']");
  if (apply) job.apply_url = apply.href;
  return job;
}
