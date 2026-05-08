import { clean, htmlToText } from "../../lib/utils";
import type { Job } from "../../lib/schema";

const SECTION_RX = {
  responsibilities: /\b(responsibilit|what\s+you'?ll\s+do|your\s+role|key\s+(tasks|duties)|deine\s+aufgabe|ihre\s+aufgabe|aufgaben|aufgabengebiet|hauptaufgaben|das\s+erwartet\s+dich|was\s+(dich|sie)\s+erwartet|was\s+du\s+bewegst|damit\s+unterst(ü|u)tzt\s+du\s+uns|dein\s+aufgabenfeld|mission|t(ä|a)tigkeitsbereich|vos\s+missions|vos\s+responsabilit|tes\s+missions|le\s+tue\s+responsabilit)\b/i,
  requirements: /\b(requirement|qualification|what\s+you'?ll\s+need|must[-\s]?have|dein\s+profil|ihr\s+profil|das\s+bringst\s+du\s+mit|das\s+(zeichnet\s+dich\s+aus|sind\s+sie)|so\s+machst\s+du\s+uns\s+happy|damit\s+(begeisterst\s+du|kannst\s+du\s+uns)|das\s+wünschen\s+wir\s+uns|anforderung|profil|qualifikation|was\s+sie\s+(daf(ü|u)r\s+)?mitbringen|votre\s+profil|tu\s+es|nous\s+recherchons|il\s+tuo\s+profilo)\b/i,
  benefits: /\b(benefits?|perks|what\s+we\s+offer|wir\s+bieten|deine\s+vorteile|freue\s+dich\s+auf|damit\s+begeistern\s+wir\s+dich|das\s+bieten\s+wir|haben\s+wir\s+dein\s+interesse|unser\s+angebot|deine\s+benefits|nous\s+offrons|nos\s+avantages|ti\s+offriamo|i\s+nostri\s+vantaggi)\b/i,
  contact: /\b(contact|kontakt|deine\s+kontakte|haben\s+wir\s+(dein|ihr)\s+interesse|bist\s+du\s+(bereit|interessiert)|fragen\s+beantwortet|bewerbung|bewerben|jetzt\s+bewerben|interesse\s+geweckt|contactez|postuler)\b/i,
};

const RECRUITER_NAME_RX = /\b(Frau|Herr|Mr\.?|Mrs\.?|Ms\.?)\s+([A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+){1,3})\b/;
const PHONE_INTL_RX = /\+\d{1,3}\s?\d{1,4}\s?\d{2,4}\s?\d{2,4}\s?\d{2,4}/;
const EMAIL_RX_LOCAL = /[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i;
const APPLY_HOST_RX_LOCAL = /(refline\.ch|smartrecruiters\.com|greenhouse\.io|lever\.co|workable\.com|workday|myworkdayjobs\.com|ashbyhq\.com|teamtailor\.com|recruitee\.com|personio\.|bamboohr\.com|jobvite\.com)/i;
const APPLY_TEXT_RX_LOCAL = /^(apply|apply now|jetzt bewerben|bewerben|application|submit application|postuler|candidater)\b/i;

function parseDescriptionHtml(html: string): {
  responsibilities?: string;
  requirements?: string;
  benefits?: string;
  recruiter_name?: string;
  recruiter_phone?: string;
  recruiter_email?: string;
  apply_url?: string;
} {
  if (!html || typeof html !== "string") return {};
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  const out: any = {};
  const headings = Array.from(tmp.querySelectorAll("h1,h2,h3,h4,h5,h6,strong,b"));

  for (const h of headings) {
    const text = (h.textContent || "").trim();
    if (!text || text.length > 120) continue;
    let kind: keyof typeof SECTION_RX | null = null;
    for (const k of ["responsibilities","requirements","benefits","contact"] as const) {
      if (SECTION_RX[k].test(text)) { kind = k; break; }
    }
    if (!kind) continue;

    // climb to container with next siblings
    let container: Element = h;
    for (let i = 0; i < 3; i++) {
      const parent = container.parentElement;
      if (!parent) break;
      if (parent.nextElementSibling) { container = parent; break; }
      container = parent;
    }
    const buf: string[] = [];
    let n: Element | null = container.nextElementSibling;
    const STOP = /^(H[1-4]|ASIDE|SECTION|FOOTER|NAV|FORM)$/;
    while (n && !STOP.test(n.tagName)) {
      const innerHeadings = n.querySelectorAll("h1,h2,h3,h4,h5,h6,strong,b");
      let isNext = false;
      for (const ih of Array.from(innerHeadings)) {
        const t = (ih.textContent || "").trim();
        for (const rx of Object.values(SECTION_RX)) { if (t && rx.test(t)) { isNext = true; break; } }
        if (isNext) break;
      }
      if (isNext) break;
      const items = n.querySelectorAll("li, p");
      if (items.length) {
        for (const it of Array.from(items)) buf.push(it.textContent || "");
      } else {
        buf.push(n.textContent || "");
      }
      n = n.nextElementSibling;
      if (buf.join(" ").length > 4000) break;
    }
    if (buf.length === 0 && h.parentElement) {
      const items = h.parentElement.querySelectorAll("li, p");
      for (const it of Array.from(items)) buf.push(it.textContent || "");
    }
    const body = clean(buf.join(" • "));
    if (!body || body.length < 20) continue;

    if (kind === "contact") {
      const nm = body.match(RECRUITER_NAME_RX);
      if (nm) out.recruiter_name = `${nm[1]} ${nm[2]}`;
      const ph = body.match(PHONE_INTL_RX);
      if (ph) out.recruiter_phone = ph[0];
      const em = body.match(EMAIL_RX_LOCAL);
      if (em && !/noreply|no-reply|example\.com/i.test(em[0])) out.recruiter_email = em[0];
    } else {
      if (!out[kind] || body.length > (out[kind] as string).length) out[kind] = body;
    }
  }

  // Apply URL: scan all anchors in description HTML for ATS host or "Apply" text
  for (const a of Array.from(tmp.querySelectorAll("a[href]")) as HTMLAnchorElement[]) {
    const href = a.getAttribute("href") || "";
    const txt = (a.textContent || "").trim();
    if (!href) continue;
    if (APPLY_HOST_RX_LOCAL.test(href)) { out.apply_url = href; break; }
    if (APPLY_TEXT_RX_LOCAL.test(txt) && !out.apply_url) out.apply_url = href;
  }

  return out;
}

export function fromJsonLd(item: any): Partial<Job> | null {
  if (!item) return null;
  const job: Partial<Job> = {};
  const rawDesc = item.description || "";
  job.title = clean(item.title || item.name);
  job.description = htmlToText(rawDesc);
  // Mine the description HTML for sections + recruiter + apply URL.
  if (rawDesc && typeof rawDesc === "string") {
    const sects = parseDescriptionHtml(rawDesc);
    if (sects.responsibilities) job.responsibilities = sects.responsibilities;
    if (sects.requirements) { job.requirements = sects.requirements; job.qualifications = sects.requirements; }
    if (sects.benefits) job.benefits = sects.benefits;
    if (sects.recruiter_name) job.recruiter_name = sects.recruiter_name;
    if (sects.recruiter_phone) job.recruiter_phone = sects.recruiter_phone;
    if (sects.recruiter_email) job.recruiter_email = sects.recruiter_email;
    if (sects.apply_url) job.apply_url = sects.apply_url;
  }

  const org = item.hiringOrganization;
  if (org && typeof org === "object") {
    job.company = clean(org.name);
    job.company_logo = clean(org.logo && (org.logo.url || org.logo));
    job.company_website = clean(org.sameAs || org.url);
  } else if (typeof org === "string") {
    job.company = clean(org);
  }

  const locArr = Array.isArray(item.jobLocation) ? item.jobLocation : [item.jobLocation].filter(Boolean);
  const locStrs: string[] = [];
  for (const l of locArr) {
    if (!l) continue;
    if (typeof l === "string") { locStrs.push(clean(l)); continue; }
    const a = l.address || l;
    if (a && typeof a === "object") {
      const parts = [a.addressLocality, a.addressRegion, a.addressCountry, a.postalCode];
      locStrs.push(parts.filter(Boolean).map(clean).join(", "));
      job.city = job.city || clean(a.addressLocality);
      job.region = job.region || clean(a.addressRegion);
      job.country = job.country || clean(typeof a.addressCountry === "object" ? a.addressCountry.name : a.addressCountry);
      job.postal_code = job.postal_code || clean(a.postalCode);
    }
  }
  job.location = locStrs.filter(Boolean).join(" | ");

  const locType = item.jobLocationType;
  if (locType && /TELECOMMUTE|REMOTE/i.test(JSON.stringify(locType))) job.remote_type = "remote";
  else if (item.applicantLocationRequirements) job.remote_type = "remote";

  job.employment_type = clean(Array.isArray(item.employmentType) ? item.employmentType.join(", ") : item.employmentType);
  job.posted_date = clean(item.datePosted);
  job.valid_through = clean(item.validThrough);
  job.start_date = clean(item.jobStartDate);
  job.language = clean(Array.isArray(item.inLanguage) ? item.inLanguage.join(", ") : item.inLanguage);

  const sal = item.baseSalary;
  if (sal && typeof sal === "object") {
    job.salary_currency = clean(sal.currency);
    const v = sal.value;
    if (v && typeof v === "object") {
      job.salary_min = clean(v.minValue);
      job.salary_max = clean(v.maxValue || v.value);
      job.salary_period = clean(v.unitText);
    }
  }

  const edu = item.educationRequirements;
  if (edu) job.education_required = clean(typeof edu === "object" ? edu.credentialCategory || edu.name : edu);
  const exp = item.experienceRequirements;
  if (exp && typeof exp === "object") {
    job.experience_years = clean(exp.monthsOfExperience ? String(exp.monthsOfExperience / 12) : exp.name);
  } else if (exp) {
    job.experience_years = clean(exp);
  }

  if (item.identifier) {
    if (typeof item.identifier === "object") job.external_id = clean(item.identifier.value || item.identifier.name);
    else job.external_id = clean(item.identifier);
  }

  // Schema.org JobPosting: url = canonical detail URL, hiringOrganization.url = company,
  // potentialAction.target = apply URL. Never default to location.href here — extractor.ts
  // resolves canonical/og/entry URL with junk-path filtering.
  let applyHref = "";
  const pa = item.potentialAction;
  if (pa) {
    const arr = Array.isArray(pa) ? pa : [pa];
    for (const action of arr) {
      if (!action) continue;
      if (typeof action === "string") { applyHref = applyHref || action; continue; }
      if (typeof action === "object") {
        if (typeof action.target === "string") applyHref = applyHref || action.target;
        else if (action.target && typeof action.target === "object" && action.target.urlTemplate) applyHref = applyHref || action.target.urlTemplate;
      }
    }
  }
  if (!applyHref) {
    if (typeof item.directApply === "string") applyHref = item.directApply;
    else if (item.applicationContact && typeof item.applicationContact === "object") applyHref = item.applicationContact.url || "";
  }
  job.apply_url = clean(applyHref || item.url);
  job.job_url = clean(item.url);
  job.department = clean(item.occupationalCategory || item.industry);
  job.raw_jsonld = JSON.stringify(item).slice(0, 8000);
  return job;
}
