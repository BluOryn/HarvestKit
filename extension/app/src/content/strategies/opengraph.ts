import type { Job } from "../../lib/schema";
import { clean } from "../../lib/utils";
import { cleanTitle } from "../titleClean";

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
  const siteName = meta('meta[property="og:site_name"]');
  // Strip the "| Company" / "— Job Board" suffix. mergeJobs keeps the longest
  // string, so an uncleaned <title> would outrank the JSON-LD title.
  const rawTitle = meta('meta[property="og:title"]') || (titleTag ? clean(titleTag.textContent) : "");
  job.title = cleanTitle(rawTitle, siteName);
  job.description = meta('meta[property="og:description"]') || meta('meta[name="description"]');
  job.company = siteName;
  job.company_logo = meta('meta[property="og:image"]');
  job.job_url = link('link[rel="canonical"]') || location.href;
  job.language = clean((root.documentElement && root.documentElement.lang) || "");
  return job;
}
