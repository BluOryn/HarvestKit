import { clean } from "../../lib/utils";
import type { Job } from "../../lib/schema";

export function fromMicrodata(root: Document = document): Partial<Job> | null {
  const node = root.querySelector('[itemtype*="schema.org/JobPosting" i]');
  if (!node) return null;
  const get = (prop: string) => {
    const el = node.querySelector(`[itemprop="${prop}"]`) as HTMLElement | null;
    if (!el) return "";
    if (el.hasAttribute("content")) return clean(el.getAttribute("content"));
    if (el.tagName === "META") return clean(el.getAttribute("content"));
    if (el.tagName === "TIME") return clean(el.getAttribute("datetime") || el.textContent);
    if (el.tagName === "A") return clean(el.getAttribute("href") || el.textContent);
    return clean(el.textContent);
  };
  const job: Partial<Job> = {};
  job.title = get("title");
  job.description = get("description");
  job.posted_date = get("datePosted");
  job.valid_through = get("validThrough");
  job.employment_type = get("employmentType");
  job.location = get("jobLocation");
  job.salary_currency = get("salaryCurrency");
  return job;
}
