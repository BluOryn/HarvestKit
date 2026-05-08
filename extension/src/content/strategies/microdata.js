import { clean } from "../../lib/utils.js";

export function fromMicrodata(root = document) {
  const node = root.querySelector('[itemtype*="schema.org/JobPosting" i]');
  if (!node) return null;
  const get = (prop) => {
    const el = node.querySelector(`[itemprop="${prop}"]`);
    if (!el) return "";
    if (el.hasAttribute("content")) return clean(el.getAttribute("content"));
    if (el.tagName === "META") return clean(el.getAttribute("content"));
    if (el.tagName === "TIME") return clean(el.getAttribute("datetime") || el.textContent);
    if (el.tagName === "A") return clean(el.getAttribute("href") || el.textContent);
    return clean(el.textContent);
  };
  const job = {};
  job.title = get("title");
  job.description = get("description");
  job.posted_date = get("datePosted");
  job.valid_through = get("validThrough");
  job.employment_type = get("employmentType");
  job.location = get("jobLocation");
  job.salary_currency = get("salaryCurrency");
  const baseSalary = get("baseSalary");
  if (baseSalary) job.salary_max = baseSalary;
  const orgEl = node.querySelector('[itemprop="hiringOrganization"]');
  if (orgEl) {
    const nameEl = orgEl.querySelector('[itemprop="name"]');
    if (nameEl) job.company = clean(nameEl.textContent);
    const url = orgEl.querySelector('[itemprop="sameAs"], [itemprop="url"]');
    if (url) job.company_website = clean(url.getAttribute("href") || url.textContent);
  }
  return job;
}
