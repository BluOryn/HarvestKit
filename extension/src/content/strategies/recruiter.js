import { clean } from "../../lib/utils.js";

const RECRUITER_TITLE_RX = /(recruiter|talent\s+(?:acquisition|partner|manager)|hr\s+manager|people\s+(?:partner|operations)|hiring\s+manager|sourcer)/i;
const PERSON_NAME_RX = /\b([A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+){1,3})\b/;
const LINKEDIN_RX = /https?:\/\/(?:[a-z]+\.)?linkedin\.com\/in\/[A-Za-z0-9-_%]+/i;

export function fromRecruiter(root = document) {
  const job = {};

  // 1. Look for elements containing recruiter-like titles
  const candidates = root.querySelectorAll("section, aside, footer, div, article");
  for (const el of candidates) {
    const raw = (el.innerText && el.innerText.length > 0 ? el.innerText : el.textContent) || "";
    const text = raw.slice(0, 1000);
    if (!RECRUITER_TITLE_RX.test(text)) continue;

    const titleMatch = text.match(RECRUITER_TITLE_RX);
    if (titleMatch) job.recruiter_title = clean(titleMatch[0]);

    // Strip leading section headings before matching person name
    const stripped = text
      .replace(/^(your|our|meet)\s+(talent|recruiting|hiring)\s+(partner|team|contact)/i, "")
      .replace(RECRUITER_TITLE_RX, "");
    const nameMatch = stripped.match(PERSON_NAME_RX);
    if (nameMatch) job.recruiter_name = clean(nameMatch[1]);

    const liEl = el.querySelector(`a[href*="linkedin.com/in/"]`);
    if (liEl) {
      const m = liEl.href.match(LINKEDIN_RX);
      if (m) job.recruiter_linkedin = m[0];
    }

    const mailEl = el.querySelector('a[href^="mailto:"]');
    if (mailEl) {
      job.recruiter_email = clean(mailEl.getAttribute("href").replace(/^mailto:/, "").split("?")[0]);
    }

    const telEl = el.querySelector('a[href^="tel:"]');
    if (telEl) {
      job.recruiter_phone = clean(telEl.getAttribute("href").replace(/^tel:/, ""));
    }

    const imgEl = el.querySelector("img[alt]");
    if (imgEl && /recruit|talent|people|hr/i.test(imgEl.alt)) {
      // recruiter photo present, name may be alt text
      if (!job.recruiter_name) job.recruiter_name = clean(imgEl.alt);
    }
    if (job.recruiter_email || job.recruiter_name || job.recruiter_linkedin) break;
  }

  // 2. Page-wide LinkedIn /in/ link as fallback
  if (!job.recruiter_linkedin) {
    const liAny = root.querySelector('a[href*="linkedin.com/in/"]');
    if (liAny) {
      const m = liAny.href.match(LINKEDIN_RX);
      if (m) job.recruiter_linkedin = m[0];
    }
  }

  // 3. Scan all mailtos for hiring-ish addresses
  if (!job.recruiter_email) {
    for (const a of root.querySelectorAll('a[href^="mailto:"]')) {
      const e = (a.getAttribute("href") || "").replace(/^mailto:/, "").split("?")[0];
      if (/jobs?|career|talent|recruit|hr|hiring|people/i.test(e)) {
        job.recruiter_email = clean(e);
        break;
      }
    }
  }
  return job;
}
