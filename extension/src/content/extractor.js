import { collectJsonLd, detectJobPage, isJobPosting } from "./detector.js";
import { fromJsonLd } from "./strategies/jsonld.js";
import { fromMicrodata } from "./strategies/microdata.js";
import { fromOpenGraph } from "./strategies/opengraph.js";
import { fromHeuristics } from "./strategies/heuristics.js";
import { fromRecruiter } from "./strategies/recruiter.js";
import { pickAdapter } from "./adapters/index.js";
import { emptyJob, mergeJobs } from "../lib/schema.js";

export function extract() {
  const det = detectJobPage();
  if (!det.isJob) return { job: null, detection: det };

  const parts = [];

  // Strategy 1: JSON-LD JobPosting (richest)
  for (const ld of det.jsonld || []) {
    if (isJobPosting(ld)) parts.push(fromJsonLd(ld));
  }

  // Strategy 2: Microdata
  parts.push(fromMicrodata(document));

  // Strategy 3: ATS-specific adapter
  const adapter = pickAdapter();
  if (adapter) parts.push(adapter.extract(document));

  // Strategy 4: OpenGraph + meta
  parts.push(fromOpenGraph(document));

  // Strategy 5: Heuristics (salary regex, sections, tech stack)
  parts.push(fromHeuristics(document));

  // Strategy 6: Recruiter / contact extraction
  parts.push(fromRecruiter(document));

  const job = mergeJobs([emptyJob(), ...parts.filter(Boolean)]);
  job.source_domain = location.hostname;
  job.job_url = job.job_url || location.href;

  // Description fallback: capture the full visible text from the main content area
  if (!job.description || job.description.length < 50) {
    const descEl = document.querySelector(
      "main, article, [class*='description'], [class*='job-detail'], [class*='job-content'], " +
      "[class*='posting'], [class*='vacancy'], [id*='job'], [role='main']"
    );
    if (descEl) {
      const fullText = (descEl.innerText || descEl.textContent || "").trim();
      if (fullText.length > 50) {
        job.description = fullText.replace(/\s+/g, " ").slice(0, 10000);
      }
    }
  }

  // Apply URL fallback: scan visible anchors for "apply"/"bewerben"
  if (!job.apply_url || job.apply_url === job.job_url) {
    for (const a of document.querySelectorAll("a[href]")) {
      const text = (a.textContent || "").trim().toLowerCase();
      const href = a.getAttribute("href") || "";
      if (!href) continue;
      if (/^(apply|apply now|jetzt bewerben|bewerben|application|submit application)/i.test(text) ||
          /\/apply|\/application|\/bewerb/i.test(href)) {
        try { job.apply_url = new URL(href, location.href).href; break; } catch {}
      }
    }
  }
  job.apply_url = job.apply_url || job.job_url;
  job.confidence = String(det.confidence.toFixed(2));
  job.scraped_at = new Date().toISOString();
  if (!job.source_ats && det.ats) job.source_ats = det.ats;
  return { job, detection: det };
}
