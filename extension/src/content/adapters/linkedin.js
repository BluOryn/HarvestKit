import { clean } from "../../lib/utils.js";

export function isMatch() {
  return /linkedin\.com\/jobs\/(view|search)/i.test(location.host + location.pathname);
}

export function extract(root = document) {
  const job = { source_ats: "linkedin" };
  job.title = clean(root.querySelector(".top-card-layout__title, h1")?.textContent);
  job.company = clean(root.querySelector(".topcard__org-name-link, .top-card-layout__second-subline a")?.textContent);
  job.location = clean(root.querySelector(".topcard__flavor--bullet, .top-card-layout__second-subline span")?.textContent);
  job.posted_date = clean(root.querySelector(".posted-time-ago__text, time")?.textContent);
  job.description = clean(root.querySelector(".description__text, .show-more-less-html")?.innerText);
  job.employment_type = clean(root.querySelector(".description__job-criteria-text--criteria")?.textContent);
  const apply = root.querySelector("a.top-card-layout__cta, button[data-test='apply-button']");
  if (apply && apply.href) job.apply_url = apply.href;
  return job;
}
