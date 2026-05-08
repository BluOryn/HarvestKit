import { clean } from "../../lib/utils";
import type { Job } from "../../lib/schema";

const RECRUITER_TITLE_RX = /(recruiter|talent\s+(?:acquisition|partner|manager)|hr\s+manager|people\s+(?:partner|operations)|hiring\s+manager|sourcer)/i;
const PERSON_NAME_RX = /\b([A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+){1,3})\b/;
const LINKEDIN_RX = /https?:\/\/(?:[a-z]+\.)?linkedin\.com\/in\/[A-Za-z0-9-_%]+/i;

export function fromRecruiter(root: Document = document): Partial<Job> {
  const job: Partial<Job> = {};
  const candidates = root.querySelectorAll("section, aside, footer, div, article");
  for (const el of Array.from(candidates)) {
    const raw = ((el as any).innerText && (el as any).innerText.length > 0 ? (el as any).innerText : el.textContent) || "";
    const text = raw.slice(0, 1000);
    if (!RECRUITER_TITLE_RX.test(text)) continue;

    const titleMatch = text.match(RECRUITER_TITLE_RX);
    if (titleMatch) job.recruiter_title = clean(titleMatch[0]);

    const stripped = text
      .replace(/^(your|our|meet)\s+(talent|recruiting|hiring)\s+(partner|team|contact)/i, "")
      .replace(RECRUITER_TITLE_RX, "");
    const nameMatch = stripped.match(PERSON_NAME_RX);
    if (nameMatch) job.recruiter_name = clean(nameMatch[1]);

    const liEl = el.querySelector('a[href*="linkedin.com/in/"]') as HTMLAnchorElement | null;
    if (liEl) { const m = liEl.href.match(LINKEDIN_RX); if (m) job.recruiter_linkedin = m[0]; }

    const mailEl = el.querySelector('a[href^="mailto:"]') as HTMLAnchorElement | null;
    if (mailEl) job.recruiter_email = clean((mailEl.getAttribute("href") || "").replace(/^mailto:/, "").split("?")[0]);

    const telEl = el.querySelector('a[href^="tel:"]') as HTMLAnchorElement | null;
    if (telEl) job.recruiter_phone = clean((telEl.getAttribute("href") || "").replace(/^tel:/, ""));

    if (job.recruiter_email || job.recruiter_name || job.recruiter_linkedin) break;
  }

  if (!job.recruiter_linkedin) {
    const liAny = root.querySelector('a[href*="linkedin.com/in/"]') as HTMLAnchorElement | null;
    if (liAny) { const m = liAny.href.match(LINKEDIN_RX); if (m) job.recruiter_linkedin = m[0]; }
  }
  if (!job.recruiter_email) {
    for (const a of Array.from(root.querySelectorAll('a[href^="mailto:"]'))) {
      const e = ((a.getAttribute("href") || "")).replace(/^mailto:/, "").split("?")[0];
      if (/jobs?|career|talent|recruit|hr|hiring|people/i.test(e)) { job.recruiter_email = clean(e); break; }
    }
  }
  return job;
}
