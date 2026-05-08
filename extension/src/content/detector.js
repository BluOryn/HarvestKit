import { flatten, safeJSON } from "../lib/utils.js";

const URL_HINTS = /\/(jobs?|careers?|stellen|stelle|positions?|opening|vacanc|stellenangebote|joboffer)/i;
const AGGREGATOR_HOSTS = /jobrapido\.com|indeed\.com|glassdoor\.com|monster\.com|simplyhired\.com|ziprecruiter\.com|naukri\.com|reed\.co\.uk|seek\.com|stepstone\./i;
const TEXT_SIGNALS = [
  /\bapply\s+now\b/i,
  /\bjetzt\s+bewerben\b/i,
  /\bresponsibilities\b/i,
  /\bqualifications?\b/i,
  /\brequirements?\b/i,
  /\bwhat\s+you'?ll\s+do\b/i,
  /\bwhat\s+we\s+offer\b/i,
  /\bdein\s+profil\b/i,
  /\bdeine\s+aufgaben\b/i,
  /\bwir\s+bieten\b/i,
];

export function detectJobPage() {
  const reasons = [];
  let confidence = 0;

  // 0. Known job-aggregator domain
  if (AGGREGATOR_HOSTS.test(location.hostname)) {
    confidence += 0.7;
    reasons.push("aggregator-domain");
  }

  // 1. JSON-LD JobPosting
  const lds = collectJsonLd();
  const hasJobPosting = lds.some((it) => isJobPosting(it));
  if (hasJobPosting) {
    confidence += 1.0;
    reasons.push("jsonld:JobPosting");
  }

  // 2. Microdata
  const md = document.querySelector('[itemtype*="schema.org/JobPosting" i]');
  if (md) {
    confidence = Math.max(confidence, 0.9);
    reasons.push("microdata:JobPosting");
  }

  // 3. URL hint (path-based or query-param based for aggregators)
  if (URL_HINTS.test(location.pathname)) {
    confidence += 0.4;
    reasons.push("url:job-path");
  } else if (/[?&](w|q|keywords?|search)=/i.test(location.search)) {
    confidence += 0.2;
    reasons.push("url:search-query");
  }

  // 3b. data-advert elements (Jobrapido-specific structured data)
  const advertEls = document.querySelectorAll("[data-advert]");
  if (advertEls.length > 0) {
    confidence += 0.5;
    reasons.push(`data-advert:${advertEls.length}`);
  }

  // 4. Text signals
  const body = (document.body && document.body.innerText) || "";
  let textHits = 0;
  for (const rx of TEXT_SIGNALS) if (rx.test(body)) textHits++;
  if (textHits) {
    confidence += Math.min(0.4, 0.08 * textHits);
    reasons.push(`text-signals:${textHits}`);
  }

  // 5. Known ATS embed/iframe
  const html = document.documentElement.outerHTML.slice(0, 200000);
  const atsMatches = [
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
    [/workday\.com/i, "workday"],
  ];
  let ats = "";
  for (const [rx, name] of atsMatches) {
    if (rx.test(html)) {
      ats = name;
      confidence += 0.3;
      reasons.push(`ats:${name}`);
      break;
    }
  }

  return {
    isJob: confidence >= 0.7,
    confidence: Math.min(1, confidence),
    reasons,
    ats,
    jsonld: lds,
  };
}

export function collectJsonLd() {
  const out = [];
  for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
    const data = safeJSON(node.textContent);
    if (!data) continue;
    for (const item of flatten(data)) {
      if (item && typeof item === "object") out.push(item);
    }
  }
  return out;
}

export function isJobPosting(item) {
  if (!item) return false;
  const t = item["@type"];
  if (Array.isArray(t)) return t.some((x) => String(x).toLowerCase() === "jobposting");
  return String(t || "").toLowerCase() === "jobposting";
}
