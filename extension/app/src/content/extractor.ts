import { detectJobPage, isJobPosting } from "./detector";
import { fromJsonLd } from "./strategies/jsonld";
import { fromMicrodata } from "./strategies/microdata";
import { fromOpenGraph } from "./strategies/opengraph";
import { fromHeuristics } from "./strategies/heuristics";
import { fromRecruiter } from "./strategies/recruiter";
import { emptyJob, mergeJobs, fingerprint, type Job } from "../lib/schema";

// Captured at script load — before any client-side navigation, redirects, banners.
const ENTRY_HREF = (() => {
  try { return location.href; } catch { return ""; }
})();

// URLs we never trust as the canonical job URL — generic landing/account pages
// the SPA may redirect to mid-extraction (jobs.ch /account/applications, /job-recommendations,
// LinkedIn /feed, Indeed /jobs?, etc.).
const NON_JOB_PATH_RX = /\/(account|profile|recommendations?|saved|applications?|login|signin|signup|register|home|feed|search\?|results\??|index)(\/|$|\?)/i;

function isJunkUrl(u: string): boolean {
  if (!u) return true;
  try {
    const p = new URL(u);
    if (NON_JOB_PATH_RX.test(p.pathname)) return true;
    if (p.pathname === "/" || p.pathname === "") return true;
    return false;
  } catch { return true; }
}

function bestJobUrl(jsonLdUrl: string, jobUrlField: string): string {
  const cands: string[] = [];
  // 1. JSON-LD url field (highest trust if it's a real detail URL)
  if (jsonLdUrl) cands.push(jsonLdUrl);
  if (jobUrlField) cands.push(jobUrlField);
  // 2. <link rel="canonical">
  const canon = document.querySelector("link[rel='canonical']") as HTMLLinkElement | null;
  if (canon?.href) cands.push(canon.href);
  // 3. og:url
  const og = document.querySelector("meta[property='og:url']") as HTMLMetaElement | null;
  if (og?.content) cands.push(og.content);
  // 4. URL captured at script load (before SPA redirects)
  if (ENTRY_HREF) cands.push(ENTRY_HREF);
  // 5. current location.href as last resort
  try { cands.push(location.href); } catch {}

  for (const c of cands) {
    if (c && !isJunkUrl(c)) return c;
  }
  // Everything is junk — return the entry href anyway so we have something
  return ENTRY_HREF || (typeof location !== "undefined" ? location.href : "");
}

// Apply URL: prefer external ATS redirect (refline.ch, smartrecruiters.com, greenhouse,
// lever, workday, ashby) over an in-site form path.
const APPLY_HOST_RX = /(refline\.ch|smartrecruiters\.com|greenhouse\.io|lever\.co|workable\.com|workday|myworkdayjobs\.com|ashbyhq\.com|teamtailor\.com|recruitee\.com|personio\.|bamboohr\.com|jobvite\.com)/i;
const APPLY_TEXT_RX = /^(apply|apply now|jetzt bewerben|bewerben|application|submit application|postuler|candidater)/i;
const APPLY_PATH_RX = /\/(apply|application|bewerb|postuler|candidat|submission)/i;

function bestApplyUrl(jsonLdApply: string, jobUrl: string): string {
  // 1. trust JSON-LD's apply_url if it points to a known ATS
  if (jsonLdApply && APPLY_HOST_RX.test(jsonLdApply)) return jsonLdApply;
  // 2. scan page for "Apply" anchor pointing at external ATS
  const anchors = Array.from(document.querySelectorAll("a[href]")) as HTMLAnchorElement[];
  let inSite = "";
  for (const a of anchors) {
    const text = (a.textContent || "").trim();
    const href = a.getAttribute("href") || "";
    if (!href) continue;
    let abs = href;
    try { abs = new URL(href, location.href).href; } catch { continue; }
    const matchText = APPLY_TEXT_RX.test(text);
    const matchPath = APPLY_PATH_RX.test(abs);
    const matchHost = APPLY_HOST_RX.test(abs);
    if (matchHost) return abs;
    if ((matchText || matchPath) && !inSite && !isJunkUrl(abs)) inSite = abs;
  }
  if (inSite) return inSite;
  // 3. fall back to the JSON-LD apply_url even if it's just the canonical
  if (jsonLdApply && !isJunkUrl(jsonLdApply)) return jsonLdApply;
  return jobUrl;
}

/** Norwegian-specific contact mining. finn.no, NAV, jobbnorge all use Norwegian
 *  labels like "Kontaktperson: <Name> Stillingstittel: <Role>". */
function mineNorwegianContacts(text: string): {
  recruiter_name?: string;
  recruiter_title?: string;
  recruiter_phone?: string;
  recruiter_email?: string;
} {
  const out: any = {};
  const stmatch = text.match(/Kontaktperson\s*:?\s*([^:]{3,60}?)\s+Stillingstittel/i);
  if (stmatch) {
    const cand = stmatch[1].trim();
    if (cand.split(/\s+/).length >= 2 && !/^(Send|Mobil|Telefon|Epost)\b/i.test(cand)) {
      out.recruiter_name = cand;
      const after = text.slice(stmatch.index! + stmatch[0].length);
      const tmatch = after.match(/Stillingstittel\s*:?\s*([^:]{3,80}?)(?:\s+Mobil|\s+Telefon|\s+E[-\s]?post|\s+Send|$)/i);
      if (tmatch) out.recruiter_title = tmatch[1].trim();
    }
  }
  // NO phone — strict (8 digits with spaces / +47)
  const phoneMatch = text.match(/(?:\+47[\s]?)?\d{2}\s\d{2}\s\d{2}\s\d{2}/);
  if (phoneMatch) out.recruiter_phone = phoneMatch[0].replace(/\s/g, "");
  return out;
}

export function extract(): { job: Job | null; detection: ReturnType<typeof detectJobPage> } {
  const det = detectJobPage();
  if (!det.isJob) return { job: null, detection: det };

  const parts: any[] = [];
  for (const ld of det.jsonld || []) if (isJobPosting(ld)) parts.push(fromJsonLd(ld));
  parts.push(fromMicrodata(document));
  parts.push(fromOpenGraph(document));
  parts.push(fromHeuristics(document));
  parts.push(fromRecruiter(document));

  const job = mergeJobs([emptyJob(), ...parts.filter(Boolean)]);
  job.source_domain = location.hostname;

  // Norwegian contact mining (always run — works for finn.no / NAV / jobbnorge)
  const bodyText = (document.body?.innerText || document.body?.textContent || "").slice(0, 30000);
  const norwegian = mineNorwegianContacts(bodyText);
  if (norwegian.recruiter_name) job.recruiter_name = norwegian.recruiter_name;
  if (norwegian.recruiter_title) job.recruiter_title = norwegian.recruiter_title;
  if (norwegian.recruiter_phone && !job.recruiter_phone) job.recruiter_phone = norwegian.recruiter_phone;

  // Canonical URL: never trust a junk redirect target.
  const ldUrl = (parts.find((p) => p && p.job_url)?.job_url) || "";
  job.job_url = bestJobUrl(ldUrl, job.job_url);

  // Description fallback: capture full visible text from main content area.
  if (!job.description || job.description.length < 50) {
    const descEl = document.querySelector(
      "main, article, [class*='description'], [class*='job-detail'], [class*='job-content'], " +
      "[class*='posting'], [class*='vacancy'], [id*='job'], [role='main']"
    );
    if (descEl) {
      const ft = (descEl.textContent || "").trim();
      if (ft.length > 50) job.description = ft.replace(/\s+/g, " ").slice(0, 10000);
    }
  }

  // Apply URL — prefer external ATS over current page.
  const ldApply = (parts.find((p) => p && p.apply_url)?.apply_url) || "";
  job.apply_url = bestApplyUrl(ldApply, job.job_url);

  job.confidence = String(det.confidence.toFixed(2));
  job.scraped_at = new Date().toISOString();
  if (!job.source_ats && det.ats) job.source_ats = det.ats;
  job.id = fingerprint(job);
  return { job, detection: det };
}
