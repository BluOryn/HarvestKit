import { flatten, safeJSON } from "../lib/utils";

const URL_HINTS = /\/(jobs?|careers?|stellen|stelle|positions?|opening|vacanc|stellenangebote|joboffer|view)/i;
const TEXT_SIGNALS = [
  /\bapply\s+now\b/i, /\bjetzt\s+bewerben\b/i, /\bresponsibilities\b/i,
  /\bqualifications?\b/i, /\brequirements?\b/i, /\bwhat\s+you'?ll\s+do\b/i,
  /\bwhat\s+we\s+offer\b/i, /\bdein\s+profil\b/i, /\bdeine\s+aufgaben\b/i,
  /\bwir\s+bieten\b/i,
];
const ATS_HOSTS: [RegExp, string][] = [
  [/boards\.greenhouse\.io|greenhouse\.io\/embed/i, "greenhouse"],
  [/jobs\.lever\.co|lever\.co\/postings/i, "lever"],
  [/\.jobs\.personio\./i, "personio"],
  [/jobs\.ashbyhq\.com|ashbyhq\.com\/job/i, "ashby"],
  [/\.recruitee\.com/i, "recruitee"],
  [/apply\.workable\.com|\.workable\.com/i, "workable"],
  [/\.wd\d+\.myworkdayjobs\.com/i, "workday"],
  [/jobs\.smartrecruiters\.com|smartrecruiters\.com/i, "smartrecruiters"],
  [/jobvite\.com/i, "jobvite"],
  [/icims\.com/i, "icims"],
  [/successfactors|sapsf/i, "successfactors"],
  [/taleo\.net/i, "taleo"],
  [/bamboohr\.com\/jobs/i, "bamboohr"],
  [/teamtailor\.com/i, "teamtailor"],
];

export type Detection = {
  isJob: boolean;
  confidence: number;
  reasons: string[];
  ats: string;
  jsonld: any[];
};

export function detectJobPage(): Detection {
  const reasons: string[] = [];
  let confidence = 0;

  const lds = collectJsonLd();
  if (lds.some(isJobPosting)) { confidence += 1.0; reasons.push("jsonld:JobPosting"); }

  if (document.querySelector('[itemtype*="schema.org/JobPosting" i]')) {
    confidence = Math.max(confidence, 0.9);
    reasons.push("microdata:JobPosting");
  }

  if (URL_HINTS.test(location.pathname)) { confidence += 0.3; reasons.push("url:job-path"); }

  const body = (document.body && document.body.innerText) || "";
  let textHits = 0;
  for (const rx of TEXT_SIGNALS) if (rx.test(body)) textHits++;
  if (textHits) { confidence += Math.min(0.4, 0.08 * textHits); reasons.push(`text:${textHits}`); }

  const html = document.documentElement.outerHTML.slice(0, 200000);
  let ats = "";
  for (const [rx, name] of ATS_HOSTS) {
    if (rx.test(html)) { ats = name; confidence += 0.3; reasons.push(`ats:${name}`); break; }
  }

  return { isJob: confidence >= 0.6, confidence: Math.min(1, confidence), reasons, ats, jsonld: lds };
}

export function collectJsonLd(): any[] {
  const out: any[] = [];
  for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
    const data = safeJSON(node.textContent || "");
    if (!data) continue;
    for (const item of flatten(data)) if (item && typeof item === "object") out.push(item);
  }
  return out;
}

export function isJobPosting(item: any): boolean {
  if (!item) return false;
  const t = item["@type"];
  if (Array.isArray(t)) return t.some((x: any) => String(x).toLowerCase() === "jobposting");
  return String(t || "").toLowerCase() === "jobposting";
}
