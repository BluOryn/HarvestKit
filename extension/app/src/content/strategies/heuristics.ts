import { clean, getText, uniq } from "../../lib/utils";
import type { Job } from "../../lib/schema";

const TECH_DICT = [
  "python","java","kotlin","scala","golang","go","rust","c++","c#","typescript","javascript",
  "react","vue","angular","svelte","next.js","nuxt","node.js","node","express","fastapi","django","flask","spring","spring boot",
  "aws","gcp","azure","kubernetes","k8s","docker","terraform","ansible","helm","istio",
  "postgres","postgresql","mysql","mongodb","redis","elasticsearch","clickhouse","snowflake","bigquery","redshift","databricks",
  "kafka","rabbitmq","spark","airflow","dbt","flink","hadoop",
  "tensorflow","pytorch","jax","scikit-learn","huggingface","transformers","langchain","llamaindex",
  "graphql","rest","grpc","openapi","swagger",
  "ci/cd","jenkins","github actions","gitlab ci","circleci","argocd",
  "linux","bash",
  "ios","swift","objective-c","android","jetpack compose","flutter","react native",
  "salesforce","sap","servicenow","oracle","datadog","new relic","grafana","prometheus","sentry",
  "tableau","power bi","looker","metabase","mixpanel",
  "openai","anthropic","gemini","llm","rag","embeddings","vector db","pinecone","weaviate","chroma","qdrant",
];

const SENIORITY: [string, RegExp][] = [
  ["principal", /\bprincipal\b/i],
  ["staff", /\bstaff\b/i],
  ["senior", /\bsenior\b|\bsr\.?\b/i],
  ["lead", /\blead\b|\btech\s+lead\b/i],
  ["mid", /\bmid[-\s]?level\b/i],
  ["junior", /\bjunior\b|\bjr\.?\b|\bgraduate\b|\bentry[-\s]?level\b/i],
  ["intern", /\bintern\b|\bpraktikum\b|\bwerkstudent\b/i],
  ["director", /\bdirector\b/i],
  ["head", /\bhead\s+of\b|\bvp\b|\bcto\b/i],
];

const SALARY_RX = /(?:€|EUR|USD|\$|£|GBP|CHF|SEK|DKK|NOK)\s?(\d{2,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?)\s?(?:k\b|tausend|thousand)?\s?(?:-|–|to|bis)?\s?(?:€|EUR|USD|\$|£|GBP|CHF|SEK|DKK|NOK)?\s?(\d{2,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?)?\s?(per|\/)?\s?(year|yr|jahr|month|mo|monat|hour|hr|stunde)?/i;
const EMAIL_RX = /[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/gi;
const PHONE_RX = /(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{2,4}/g;

const REMOTE_RX = /\b(remote|home\s?office|work\s+from\s+home|fully\s+remote|telecommut|distributed)\b/i;
const HYBRID_RX = /\b(hybrid|flex(?:ible)?\s+location)\b/i;
const ONSITE_RX = /\b(on[-\s]?site|in[-\s]?office|vor\s+ort)\b/i;

const VISA_RX = /\b(visa\s+sponsorship|sponsor\s+(?:your)?\s?visa|h-?1b|blue\s+card)\b/i;
const RELOC_RX = /relocation\s+(?:support|assistance|package|paid|covered|provided)/i;
const EQUITY_RX = /\b(equity|stock\s+options?|rsu|esop|share\s+options?)\b/i;
const EDUCATION_RX = /\b(Bachelor(?:'?s)?|Master(?:'?s)?|M\.?Sc\.?|M\.?Eng\.?|M\.?A\.?|B\.?Sc\.?|B\.?Eng\.?|B\.?A\.?|Ph\.?D\.?|Doctorate|Studium|Diplom|Promotion|Hochschulabschluss|FH(?:-Abschluss)?|ETH-Abschluss|Lehre|Berufsausbildung|Berufsmatur|Fachhochschule|Apprenticeship|EFZ|Licence|Master\s+pro|DEA|DESS|diploma|degree)\b/i;
const EXPERIENCE_RX = /(\d+)\s?\+?\s?(?:-\s?\d+\s?)?(?:years?|jahre?n?|jahresberufserfahrung|ans|anni)\b/i;
const HIRING_MANAGER_RX = /\b(?:hiring\s+manager|reports?\s+to|berichtet\s+an|vorgesetzt|line\s+manager|direct\s+supervisor|supervisor)\s*[:\-]?\s*([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+){1,3})/i;

export function fromHeuristics(root: Document = document): Partial<Job> {
  const text = (getText(root.body) || "").slice(0, 200000);
  const job: Partial<Job> = {};

  const lower = text.toLowerCase();
  const techs = TECH_DICT.filter((t) => {
    const escaped = t.replace(/[.+*?^$()|[\]\\]/g, "\\$&");
    return new RegExp(`(^|[^a-z0-9])${escaped}([^a-z0-9]|$)`, "i").test(lower);
  });
  job.tech_stack = uniq(techs).join(", ");

  for (const [name, rx] of SENIORITY) if (rx.test(text)) { job.seniority = name; break; }

  if (REMOTE_RX.test(text)) job.remote_type = "remote";
  else if (HYBRID_RX.test(text)) job.remote_type = "hybrid";
  else if (ONSITE_RX.test(text)) job.remote_type = "onsite";

  const sal = SALARY_RX.exec(text);
  if (sal) {
    const [whole, lo, hi, , period] = sal;
    job.salary_min = (lo || "").replace(/[\s.,]/g, "");
    job.salary_max = (hi || "").replace(/[\s.,]/g, "");
    if (/€|EUR/i.test(whole)) job.salary_currency = "EUR";
    else if (/\$|USD/i.test(whole)) job.salary_currency = "USD";
    else if (/£|GBP/i.test(whole)) job.salary_currency = "GBP";
    if (period) job.salary_period = /month|monat/i.test(period) ? "month" : "year";
  }

  if (VISA_RX.test(text)) job.visa_sponsorship = "yes";
  if (RELOC_RX.test(text)) job.relocation = "yes";
  if (EQUITY_RX.test(text)) job.equity = "yes";

  const eduM = text.match(EDUCATION_RX);
  if (eduM) job.education_required = clean(eduM[0]);
  const expM = text.match(EXPERIENCE_RX);
  if (expM && expM[1]) job.experience_years = expM[1];
  const hmM = text.match(HIRING_MANAGER_RX);
  if (hmM && hmM[1]) job.hiring_manager = clean(hmM[1]);

  const emails = uniq((text.match(EMAIL_RX) || []).filter((e: string) => !/noreply|no-reply|example\.com/i.test(e)));
  if (emails.length) {
    const recruiter = emails.find((e: string) => /jobs?|career|talent|recruit|hr|hiring|people/i.test(e));
    if (recruiter) job.recruiter_email = recruiter;
    job.application_email = recruiter || emails[0];
  }
  const phones = uniq((text.match(PHONE_RX) || []).filter((p: string) => p.replace(/\D/g, "").length >= 7));
  if (phones.length) job.application_phone = phones[0];

  // Broad EN/DE/FR/IT section patterns (parity with src/job_scraper/extract.py).
  job.responsibilities = sectionText(root, /\b(responsibilit|what\s+you'?ll\s+do|your\s+role|key\s+(tasks|duties)|deine\s+aufgabe|ihre\s+aufgabe|aufgaben|aufgabengebiet|hauptaufgaben|das\s+erwartet\s+dich|was\s+(dich|sie)\s+erwartet|was\s+du\s+bewegst|damit\s+unterst(ü|u)tzt\s+du\s+uns|dein\s+aufgabenfeld|mission|t(ä|a)tigkeitsbereich|vos\s+missions|vos\s+responsabilit|tes\s+missions|le\s+tue\s+responsabilit)\b/i);
  job.requirements = sectionText(root, /\b(requirement|qualification|what\s+you'?ll\s+need|must[-\s]?have|dein\s+profil|ihr\s+profil|das\s+bringst\s+du\s+mit|das\s+(zeichnet\s+dich\s+aus|sind\s+sie)|so\s+machst\s+du\s+uns\s+happy|damit\s+(begeisterst\s+du|kannst\s+du\s+uns)|das\s+wünschen\s+wir\s+uns|anforderung|profil|qualifikation|was\s+sie\s+(daf(ü|u)r\s+)?mitbringen|votre\s+profil|tu\s+es|nous\s+recherchons|il\s+tuo\s+profilo)\b/i);
  job.benefits = sectionText(root, /\b(benefits?|perks|what\s+we\s+offer|wir\s+bieten|deine\s+vorteile|freue\s+dich\s+auf|damit\s+begeistern\s+wir\s+dich|das\s+bieten\s+wir|haben\s+wir\s+dein\s+interesse|unser\s+angebot|deine\s+benefits|nous\s+offrons|nos\s+avantages|ti\s+offriamo|i\s+nostri\s+vantaggi)\b/i);
  job.qualifications = job.requirements;

  // Skills: dedupe of tech_stack + leading bullet phrases from requirements section
  const skillParts: string[] = [];
  if (job.tech_stack) skillParts.push(...job.tech_stack.split(",").map((s: string) => s.trim()).filter(Boolean));
  if (job.requirements) {
    for (const bullet of job.requirements.split(" • ")) {
      const b = bullet.trim();
      if (b.length > 4 && b.length < 80 && !/^[a-z]/.test(b)) skillParts.push(b);
    }
  }
  if (skillParts.length) {
    const seen = new Set<string>();
    const skills: string[] = [];
    for (const s of skillParts) {
      const k = s.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      skills.push(s);
    }
    job.skills = skills.slice(0, 20).join(", ");
  }

  return job;
}

const STOP_TAG = /^(H[1-4]|ASIDE|SECTION|FOOTER|NAV|FORM)$/;
const SECTION_HEADER_RX = /\b(responsibilit|requirement|qualific|benefits?|aufgaben|profil|wir\s+bieten|freue\s+dich\s+auf|damit\s+begeistern|so\s+machst|deine\s+vorteile|votre\s+profil|nous\s+offrons|haben\s+wir\s+dein\s+interesse)\b/i;

function sectionText(root: Document, headerRx: RegExp): string {
  // Walk h1-h6/strong/b headings. Container of heading is its parent block — its
  // next siblings hold the section body until we hit another section heading.
  const headings = Array.from(root.querySelectorAll("h1,h2,h3,h4,h5,h6,strong,b"));
  for (const h of headings) {
    const text = (h.textContent || "").trim();
    if (!text || text.length > 120) continue;
    if (!headerRx.test(text)) continue;

    // Climb to a container that has next siblings (handles nested div/strong wrappers).
    let container: Element = h;
    for (let i = 0; i < 3; i++) {
      const parent = container.parentElement;
      if (!parent) break;
      let sib: Element | null = parent.nextElementSibling;
      if (sib) { container = parent; break; }
      container = parent;
    }

    const buf: string[] = [];
    let n: Element | null = container.nextElementSibling;
    while (n && !STOP_TAG.test(n.tagName)) {
      // Stop on a sibling that itself is/contains another section heading.
      const innerHeadings = n.querySelectorAll("h1,h2,h3,h4,h5,h6,strong,b");
      let isNextSection = false;
      for (const ih of Array.from(innerHeadings)) {
        const t = (ih.textContent || "").trim();
        if (t && SECTION_HEADER_RX.test(t)) { isNextSection = true; break; }
      }
      if (isNextSection) break;

      const items = (n.querySelectorAll && n.querySelectorAll("li, p")) || [];
      if (items.length) {
        for (const it of Array.from(items)) buf.push((it as HTMLElement).textContent || "");
      } else {
        buf.push(((n as any).innerText && (n as any).innerText.length > 0 ? (n as any).innerText : n.textContent) || "");
      }
      n = n.nextElementSibling;
      if (buf.join("\n").length > 4000) break;
    }

    // Fallback: collect heading's parent block's li/p when we found nothing.
    if (buf.length === 0 && h.parentElement) {
      const items = h.parentElement.querySelectorAll("li, p");
      for (const it of Array.from(items)) {
        const t = (it as HTMLElement).textContent || "";
        if (t.trim()) buf.push(t);
      }
    }

    if (buf.length) return clean(buf.join(" • "));
  }
  return "";
}
