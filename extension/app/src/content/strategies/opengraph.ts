import { clean } from "../../lib/utils";
import type { Job } from "../../lib/schema";

export function fromOpenGraph(root: Document = document): Partial<Job> {
  const meta = (sel: string) => {
    const el = root.querySelector(sel);
    if (!el) return "";
    return clean(el.getAttribute("content") || "");
  };
  const link = (sel: string) => {
    const el = root.querySelector(sel);
    if (!el) return "";
    return clean(el.getAttribute("href") || "");
  };
  const titleTag = root.querySelector("title");
  const job: Partial<Job> = {};
  job.title = meta('meta[property="og:title"]') || (titleTag ? clean(titleTag.textContent) : "");
  job.description = meta('meta[property="og:description"]') || meta('meta[name="description"]');
  job.company = meta('meta[property="og:site_name"]');
  job.company_logo = meta('meta[property="og:image"]');
  job.job_url = link('link[rel="canonical"]') || location.href;
  job.language = clean((root.documentElement && root.documentElement.lang) || "");
  return job;
}
