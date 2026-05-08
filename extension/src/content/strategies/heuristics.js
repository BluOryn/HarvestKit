import { clean, uniq } from "../../lib/utils.js";

const TECH_DICT = [
  "python","java","kotlin","scala","golang","go","rust","c++","c#","typescript","javascript",
  "react","vue","angular","svelte","next.js","nuxt","node.js","node","express","fastapi","django","flask","spring","spring boot",
  "aws","gcp","azure","kubernetes","k8s","docker","terraform","ansible","helm","istio","linkerd",
  "postgres","postgresql","mysql","mongodb","redis","elasticsearch","clickhouse","snowflake","bigquery","redshift","databricks",
  "kafka","rabbitmq","spark","airflow","dbt","flink","hadoop","beam",
  "tensorflow","pytorch","jax","scikit-learn","huggingface","transformers","langchain","llamaindex",
  "graphql","rest","grpc","openapi","swagger",
  "ci/cd","jenkins","github actions","gitlab ci","circleci","argocd","spinnaker",
  "linux","unix","bash","shell","powershell",
  "ios","swift","objective-c","android","jetpack compose","flutter","react native",
  "salesforce","sap","servicenow","oracle","sqlserver","datadog","new relic","grafana","prometheus","sentry",
  "tableau","power bi","looker","metabase","mixpanel",
  "openai","anthropic","gemini","llm","rag","embeddings","vector db","pinecone","weaviate","chroma","qdrant",
];

const SENIORITY = [
  ["principal", /\bprincipal\b/i],
  ["staff", /\bstaff\b/i],
  ["senior", /\bsenior\b|\bsr\.?\b/i],
  ["lead", /\blead\b|\btech\s+lead\b/i],
  ["mid", /\bmid[-\s]?level\b|\bii\b\s/i],
  ["junior", /\bjunior\b|\bjr\.?\b|\bgraduate\b|\bentry[-\s]?level\b/i],
  ["intern", /\bintern\b|\bpraktikum\b|\bwerkstudent\b/i],
  ["director", /\bdirector\b/i],
  ["head", /\bhead\s+of\b|\bvp\b|\bcto\b/i],
];

const SALARY_RX = /(?:€|EUR|USD|\$|£|GBP|CHF|SEK|DKK|NOK)\s?(\d{2,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?)\s?(?:k\b|tausend|thousand)?\s?(?:-|–|to|bis)?\s?(?:€|EUR|USD|\$|£|GBP|CHF|SEK|DKK|NOK)?\s?(\d{2,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?)?\s?(per|\/)?\s?(year|yr|jahr|month|mo|monat|hour|hr|stunde)?/gi;
const EMAIL_RX = /[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/gi;
const PHONE_RX = /(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{2,4}/g;

const REMOTE_RX = /\b(remote|home\s?office|work\s+from\s+home|fully\s+remote|telecommut|distributed)\b/i;
const HYBRID_RX = /\b(hybrid|flex(?:ible)?\s+location)\b/i;
const ONSITE_RX = /\b(on[-\s]?site|in[-\s]?office|vor\s+ort)\b/i;

const VISA_RX = /\b(visa\s+sponsorship|sponsor\s+(?:your)?\s?visa|h-?1b|blue\s+card|relocation\s+(?:support|assistance|package))\b/i;
const RELOC_RX = /relocation\s+(?:support|assistance|package|paid|covered|provided)/i;
const EQUITY_RX = /\b(equity|stock\s+options?|rsu|esop|share\s+options?)\b/i;

const SKILLS_HEADERS = /(skills?|tech(?:nologies)?|stack|technische\s+anforderungen|qualifikationen|requirements)\s*[:：]?/i;

function getText(el) {
  if (!el) return "";
  // innerText is layout-aware; fall back to textContent (jsdom + offline tests).
  return (el.innerText && el.innerText.length > 0 ? el.innerText : el.textContent) || "";
}

export function fromHeuristics(root = document) {
  const text = (getText(root.body) || "").slice(0, 200000);
  const job = {};

  // Tech stack
  const lower = text.toLowerCase();
  const techs = TECH_DICT.filter((t) => {
    const escaped = t.replace(/[.+*?^$()|[\]\\]/g, "\\$&");
    return new RegExp(`(^|[^a-z0-9])${escaped}([^a-z0-9]|$)`, "i").test(lower);
  });
  job.tech_stack = uniq(techs).join(", ");

  // Seniority
  for (const [name, rx] of SENIORITY) {
    if (rx.test(text)) {
      job.seniority = name;
      break;
    }
  }

  // Remote
  if (REMOTE_RX.test(text)) job.remote_type = "remote";
  else if (HYBRID_RX.test(text)) job.remote_type = "hybrid";
  else if (ONSITE_RX.test(text)) job.remote_type = "onsite";

  // Salary
  const salMatch = SALARY_RX.exec(text);
  if (salMatch) {
    const [whole, lo, hi, , period] = salMatch;
    job.salary_min = (lo || "").replace(/[\s.,]/g, "");
    job.salary_max = (hi || "").replace(/[\s.,]/g, "");
    if (/€|EUR/i.test(whole)) job.salary_currency = "EUR";
    else if (/\$|USD/i.test(whole)) job.salary_currency = "USD";
    else if (/£|GBP/i.test(whole)) job.salary_currency = "GBP";
    if (period) job.salary_period = period.toLowerCase().includes("month") || period.toLowerCase().includes("monat") ? "month" : "year";
  }

  // Visa / relocation / equity
  if (VISA_RX.test(text)) job.visa_sponsorship = "yes";
  if (RELOC_RX.test(text)) job.relocation = "yes";
  if (EQUITY_RX.test(text)) job.equity = "yes";

  // Emails / phones (filter generic noreply etc)
  const emails = uniq(text.match(EMAIL_RX) || []).filter((e) => !/noreply|no-reply|example\.com/i.test(e));
  if (emails.length) {
    const recruiterEmail = emails.find((e) => /jobs?|career|talent|recruit|hr|hiring|people/i.test(e));
    if (recruiterEmail) job.recruiter_email = recruiterEmail;
    job.application_email = recruiterEmail || emails[0];
  }
  const phones = uniq((text.match(PHONE_RX) || []).filter((p) => p.replace(/\D/g, "").length >= 7));
  if (phones.length) job.application_phone = phones[0];

  // Try section blocks (Responsibilities, Requirements, Benefits)
  job.responsibilities = sectionText(root, /responsibilit|aufgaben|what\s+you'?ll\s+do|deine\s+aufgaben|tasks/i);
  job.requirements = sectionText(root, /requirement|qualific|profil|what\s+you'?ll\s+need|dein\s+profil|skills?\s*we|must[-\s]?have/i);
  job.benefits = sectionText(root, /benefits?|perks|wir\s+bieten|what\s+we\s+offer|deine\s+vorteile/i);
  job.qualifications = job.requirements;

  return job;
}

function sectionText(root, headerRx) {
  const headings = root.querySelectorAll("h1,h2,h3,h4,strong,b");
  const STOP = /^(H[1-4]|ASIDE|SECTION|FOOTER|NAV|FORM)$/;
  for (const h of headings) {
    if (!headerRx.test(h.textContent || "")) continue;
    const buf = [];
    let n = h.nextElementSibling;
    while (n && !STOP.test(n.tagName)) {
      const items = n.querySelectorAll ? n.querySelectorAll("li, p") : [];
      if (items && items.length) {
        for (const it of items) buf.push(it.textContent || "");
      } else {
        buf.push((n.innerText && n.innerText.length > 0 ? n.innerText : n.textContent) || "");
      }
      n = n.nextElementSibling;
      if (buf.join("\n").length > 4000) break;
    }
    return clean(buf.join(" • "));
  }
  return "";
}
