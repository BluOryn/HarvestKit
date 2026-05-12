import type { ListCard } from "../../lib/messages";
import { clean } from "../../lib/utils";

export type SiteAdapter = {
  name: string;
  hostMatch: RegExp;
  extract(root: Document): ListCard[];
  nextPage?(root: Document): string | null;
};

const sel = (root: Document | Element, q: string) => root.querySelector(q);
const sels = (root: Document | Element, q: string) => Array.from(root.querySelectorAll(q));

function abs(href: string | null): string {
  if (!href) return "";
  try { return new URL(href, window.location.href).href; } catch { return ""; }
}

export const SITES: SiteAdapter[] = [
  {
    name: "jobrapido",
    hostMatch: /(^|\.)jobrapido\.com$/i,
    extract(root) {
      const out: ListCard[] = [];
      const stripHtml = (s: string) => (s || "").replace(/<[^>]*>/g, "").trim();

      // Fast-path: data-advert JSON attributes (most reliable)
      for (const el of sels(root, "[data-advert]")) {
        let adv: any;
        try { adv = JSON.parse(el.getAttribute("data-advert") || ""); } catch { continue; }
        if (!adv) continue;
        const title = clean(stripHtml(adv.title) || sel(el, ".result-item__title, h3")?.textContent || "");
        const company = clean(stripHtml(adv.company) || sel(el, ".result-item__company-label")?.textContent || "");
        const loc = clean(stripHtml(adv.location) || sel(el, ".result-item__location-label")?.textContent || "");
        let url = "";
        const link = sel(el, "a.result-item__link, a[href*='open.app.jobrapido'], a[href]") as HTMLAnchorElement | null;
        if (link) {
          const href = link.getAttribute("href") || "";
          if (href && !href.includes("[[") && !href.includes("{{")) url = abs(href);
        }
        if (!url && adv.advertId) url = `${window.location.origin}/jobpreview/${adv.advertId}`;
        if (!url || !title) continue;
        out.push({ title, company, location: loc, url, snippet: clean(el.textContent || "").slice(0, 240) });
      }

      // Fallback: CSS selectors
      if (out.length < 2) {
        for (const card of sels(root, "article, li[class*='result'], div[class*='result-list-item'], div[class*='job-result']")) {
          const a = sel(card, "a[href]") as HTMLAnchorElement | null;
          if (!a) continue;
          const href = a.getAttribute("href") || "";
          if (!/\/(joboffer|job|posting|view)/i.test(href) && !/jobrapido\.com/i.test(href)) continue;
          const title = clean(sel(card, "h2, h3, [class*='title']")?.textContent || a.textContent);
          if (!title) continue;
          const company = clean(sel(card, "[class*='company']")?.textContent || "");
          const loc = clean(sel(card, "[class*='location']")?.textContent || "");
          const url = abs(href);
          if (!url) continue;
          out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
        }
      }
      return uniqByUrl(out);
    },
    nextPage(root) {
      const a = sel(root, "a[rel='next'], a[class*='next']") as HTMLAnchorElement | null;
      return a && a.href ? a.href : null;
    },
  },
  {
    name: "indeed",
    hostMatch: /(^|\.)indeed\.[a-z.]+$/i,
    extract(root) {
      const out: ListCard[] = [];
      for (const card of sels(root, "div.job_seen_beacon, [data-jk]")) {
        const a = sel(card, "a.jcs-JobTitle, a[id^='job_'], a[data-jk]") as HTMLAnchorElement | null;
        if (!a) continue;
        const title = clean(a.textContent);
        if (!title) continue;
        const company = clean(sel(card, "[data-testid='company-name']")?.textContent || sel(card, ".companyName")?.textContent || "");
        const loc = clean(sel(card, "[data-testid='text-location']")?.textContent || sel(card, ".companyLocation")?.textContent || "");
        const url = abs(a.getAttribute("href"));
        if (!url) continue;
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
    nextPage(root) {
      const a = sel(root, "a[data-testid='pagination-page-next']") as HTMLAnchorElement | null;
      return a && a.href ? a.href : null;
    },
  },
  {
    name: "stepstone",
    hostMatch: /(^|\.)stepstone\.(de|com|at|nl|fr|be)$/i,
    extract(root) {
      const out: ListCard[] = [];
      for (const card of sels(root, "article[data-at='job-item'], article[data-testid='job-item']")) {
        const a = sel(card, "a[data-at='job-item-title'], a[data-testid='job-item-title']") as HTMLAnchorElement | null;
        if (!a) continue;
        const title = clean(a.textContent);
        const company = clean(sel(card, "[data-at='job-item-company-name']")?.textContent || "");
        const loc = clean(sel(card, "[data-at='job-item-location']")?.textContent || "");
        const url = abs(a.getAttribute("href"));
        if (!url || !title) continue;
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
    nextPage(root) {
      const a = sel(root, "a[data-at='pagination-next']") as HTMLAnchorElement | null;
      return a && a.href ? a.href : null;
    },
  },
  {
    name: "linkedin-jobs",
    hostMatch: /(^|\.)linkedin\.com$/i,
    extract(root) {
      const out: ListCard[] = [];
      for (const card of sels(root, "li.jobs-search-results__list-item, div.job-card-container, li.scaffold-layout__list-item")) {
        const a = sel(card, "a.job-card-list__title, a[data-control-name='job_card_click'], a.base-card__full-link") as HTMLAnchorElement | null;
        if (!a) continue;
        const title = clean(a.textContent);
        const company = clean(sel(card, ".job-card-container__company-name, .base-search-card__subtitle")?.textContent || "");
        const loc = clean(sel(card, ".job-card-container__metadata-item, .job-search-card__location")?.textContent || "");
        const url = abs(a.getAttribute("href"));
        if (!url || !title) continue;
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
  },
  {
    name: "glassdoor",
    hostMatch: /(^|\.)glassdoor\.[a-z.]+$/i,
    extract(root) {
      const out: ListCard[] = [];
      for (const card of sels(root, "li[data-test='jobListing'], li.react-job-listing")) {
        const a = sel(card, "a[data-test='job-link'], a.jobLink") as HTMLAnchorElement | null;
        if (!a) continue;
        const title = clean(a.textContent);
        const company = clean(sel(card, "[data-test='employer-name']")?.textContent || "");
        const loc = clean(sel(card, "[data-test='emp-location']")?.textContent || "");
        const url = abs(a.getAttribute("href"));
        if (!url || !title) continue;
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
  },
  {
    name: "monster",
    hostMatch: /(^|\.)monster\.[a-z.]+$/i,
    extract(root) {
      const out: ListCard[] = [];
      for (const card of sels(root, "section[data-testid='svx_jobCard'], article.results-card")) {
        const a = sel(card, "a[data-testid='svx_jobTitle'], a") as HTMLAnchorElement | null;
        if (!a) continue;
        const title = clean(a.textContent);
        const company = clean(sel(card, "[data-testid='company']")?.textContent || "");
        const loc = clean(sel(card, "[data-testid='jobDetailLocation']")?.textContent || "");
        const url = abs(a.getAttribute("href"));
        if (!url || !title) continue;
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
  },
  {
    name: "xing",
    hostMatch: /(^|\.)xing\.com$/i,
    extract(root) {
      const out: ListCard[] = [];
      for (const card of sels(root, "article[data-qa='job-search-result']")) {
        const a = sel(card, "a[data-qa='job-search-result-link']") as HTMLAnchorElement | null;
        if (!a) continue;
        const title = clean(sel(card, "[data-qa='job-search-result-title']")?.textContent || a.textContent);
        const company = clean(sel(card, "[data-qa='job-search-result-company']")?.textContent || "");
        const loc = clean(sel(card, "[data-qa='job-search-result-location']")?.textContent || "");
        const url = abs(a.getAttribute("href"));
        if (!url || !title) continue;
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
  },
  {
    name: "finn.no",
    hostMatch: /(^|\.)finn\.no$/i,
    extract(root) {
      const out: ListCard[] = [];
      const seen = new Set<string>();
      // finn.no uses simple /job/ad/<id> for detail URLs
      const AD_RX = /\/job\/ad\/(\d+)/;
      for (const a of Array.from(root.querySelectorAll("a[href*='/job/ad/']")) as HTMLAnchorElement[]) {
        const href = a.getAttribute("href") || "";
        const m = href.match(AD_RX);
        if (!m) continue;
        const id = m[1];
        if (seen.has(id)) continue;
        seen.add(id);
        const url = `https://www.finn.no/job/ad/${id}`;
        const card = (a.closest("article, li, div[class*='card'], div[class*='ad'], div[class*='listing']") || a) as Element;
        const title = clean(card.querySelector("h2,h3,[class*='title'],[class*='Title']")?.textContent || a.textContent || "");
        if (!title || title.length < 4) continue;
        const company = clean(card.querySelector("[class*='company'], [class*='employer']")?.textContent || "");
        const loc = clean(card.querySelector("[class*='location'], [class*='place'], [class*='workplace']")?.textContent || "");
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
    nextPage(root) {
      const link = root.querySelector("link[rel='next']") as HTMLLinkElement | null;
      if (link?.href) return link.href;
      const a = root.querySelector("a[rel='next'], a[aria-label*='neste' i], a[aria-label*='next' i]") as HTMLAnchorElement | null;
      return a?.href || null;
    },
  },
  {
    name: "nav.no",
    hostMatch: /(^|\.)nav\.no$/i,
    extract(root) {
      const out: ListCard[] = [];
      const seen = new Set<string>();
      const UUID_RX = /\/stillinger\/stilling\/([a-f0-9\-]{20,})/i;
      for (const a of Array.from(root.querySelectorAll("a[href*='/stillinger/stilling/']")) as HTMLAnchorElement[]) {
        const href = a.getAttribute("href") || "";
        const m = href.match(UUID_RX);
        if (!m) continue;
        const uuid = m[1];
        if (seen.has(uuid)) continue;
        seen.add(uuid);
        const url = `https://arbeidsplassen.nav.no/stillinger/stilling/${uuid}`;
        const card = (a.closest("article, li, div[class*='card'], div[class*='result'], div[class*='aksel']") || a) as Element;
        const title = clean(card.querySelector("h2,h3,[class*='title'],[class*='Title']")?.textContent || a.textContent || "");
        if (!title || title.length < 4) continue;
        const company = clean(card.querySelector("[class*='employer'], [class*='company']")?.textContent || "");
        const loc = clean(card.querySelector("[class*='location'], [class*='place']")?.textContent || "");
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
    nextPage(root) {
      const a = root.querySelector("a[rel='next'], a[aria-label*='neste' i], a[aria-label*='next' i]") as HTMLAnchorElement | null;
      return a?.href || null;
    },
  },
  {
    name: "jobs.ch",
    hostMatch: /(^|\.)jobs\.ch$/i,
    extract(root) {
      const out: ListCard[] = [];
      // jobs.ch renders cards as <a href="/{lang}/vacancies|stellenangebote|offres-emplois/detail/{uuid}/...">
      const DETAIL_RX = /\/(vacancies|stellenangebote|offres-emplois)\/detail\//i;
      for (const a of Array.from(root.querySelectorAll("a[href]")) as HTMLAnchorElement[]) {
        const href = a.getAttribute("href") || "";
        if (!DETAIL_RX.test(href)) continue;
        const url = abs(href).split("#")[0].split("?")[0];
        if (!url) continue;
        const card = (a.closest("article, li, div[class*='vacancy'], div[class*='card'], div[class*='result']") || a) as Element;
        // Title: first h2/h3 inside card, fallback to anchor text
        const title = clean(
          (card.querySelector("h2, h3, [class*='title'], [class*='Title']")?.textContent) ||
          a.textContent || ""
        );
        if (!title || title.length < 4) continue;
        const company = clean(
          card.querySelector("[class*='company'], [class*='Company'], [class*='employer']")?.textContent || ""
        );
        const loc = clean(
          card.querySelector("[class*='location'], [class*='Location'], [class*='place'], [class*='Place']")?.textContent || ""
        );
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
    nextPage(root) {
      // jobs.ch puts a <link rel="next" href="..."> in <head>
      const linkNext = root.querySelector("link[rel='next']") as HTMLLinkElement | null;
      if (linkNext?.href) return linkNext.href;
      const a = root.querySelector("a[rel='next'], a[aria-label*='next' i], a[aria-label*='nächste' i], a[aria-label*='suivante' i]") as HTMLAnchorElement | null;
      return a && a.href ? a.href : null;
    },
  },
  {
    name: "naukri",
    hostMatch: /(^|\.)naukri\.com$/i,
    extract(root) {
      const out: ListCard[] = [];
      // Naukri renders job cards as article.jobTuple or divs with .cust-job-tuple / srp-jobtuple-wrapper
      for (const card of sels(root, "article.jobTuple, .cust-job-tuple, .srp-jobtuple-wrapper, [class*='jobTuple'], [class*='job-Tuple'], div[class*='styles_job']")) {
        const a = sel(card, "a.title, a[class*='title'], a[href*='/job/'], a[href*='job-listings']") as HTMLAnchorElement | null;
        if (!a) continue;
        const title = clean(sel(card, ".title, .row1 a, h2, [class*='desig'], [class*='title']")?.textContent || a.textContent);
        if (!title) continue;
        const company = clean(sel(card, ".subTitle, .comp-name, [class*='company'], [class*='comp']")?.textContent || "");
        const loc = clean(sel(card, ".locWdth, .loc-wrap, .loc, [class*='location'], [class*='loc']")?.textContent || "");
        const url = abs(a.getAttribute("href"));
        if (!url) continue;
        out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
      }
      return uniqByUrl(out);
    },
    nextPage(root) {
      const a = sel(root, "a.fright, a[class*='next'], a[class*='nxt']") as HTMLAnchorElement | null;
      return a && a.href ? a.href : null;
    },
  },
];

function uniqByUrl(cards: ListCard[]): ListCard[] {
  const seen = new Set<string>();
  const out: ListCard[] = [];
  for (const c of cards) { if (seen.has(c.url)) continue; seen.add(c.url); out.push(c); }
  return out;
}

export function pickSiteAdapter(): SiteAdapter | null {
  const host = window.location.hostname;
  for (const s of SITES) if (s.hostMatch.test(host)) return s;
  return null;
}

// Known aggregator domains — on these we relax title/URL filtering
const AGGREGATOR_RX = /jobrapido|indeed|glassdoor|monster|simplyhired|ziprecruiter|linkedin.*\/jobs|naukri|reed\.co\.uk|seek\.com|stepstone|totaljobs|cwjobs|jobsite|jobs\.ch|jobup\.ch|jobscout24|kalaydo|stellenanzeigen|adecco|jobs\.de|xing\.com\/jobs|hh\.ru|wellfound|angel\.co|builtin|dice|finn\.no|nav\.no|arbeidsplassen|jobbnorge|webcruiter|jobindex\.dk|jobnet\.dk|arbetsformedlingen|monster\.se|arbetspaket|hh\.ru|rabota\.ru/i;

export function genericListExtract(root: Document = document): ListCard[] {
  const TITLE_RX = /(software|engineer|developer|data|ai|ml|cloud|devops|backend|frontend|fullstack|stack|architect|scientist|analyst|sre|platform|security|product|qa|sdet|mobile|android|ios|principal|staff|lead|director|head|consultant|specialist|manager|intern|trainee|designer|coordinator|associate|executive|officer|researcher|administrator|recruiter|hr|operations|delivery|solution)/i;
  const HREF_RX = /\/(jobs?|vacanc|career|stellen|joboffer|positions?|opening|view|posting|job-listing|apply|jobpreview|rc\/clk)/i;
  const isAggregator = AGGREGATOR_RX.test(location.hostname);
  // Also relax filtering on employer careers/jobs pages
  const isCareersPage = /\/(jobs|careers|positions|openings|vacancies|opportunities|open-roles)(\/|$|\?)/i.test(location.pathname);
  const out: ListCard[] = [];
  const seen = new Set<string>();
  for (const a of Array.from(root.querySelectorAll("a[href]"))) {
    const href = a.getAttribute("href") || "";
    if (!HREF_RX.test(href) && !isAggregator) continue;
    if (isAggregator && (!href || href === "#" || href.startsWith("javascript:"))) continue;
    const url = abs(href);
    if (!url || seen.has(url)) continue;
    const card = (a.closest("tr, article, li, div, section") || a) as Element;
    const title = clean(card.querySelector("h2, h3, [class*='title'], [class*='Title']")?.textContent || a.textContent);
    if (!title || title.length < 3) continue;
    // On aggregator sites accept any title; otherwise require pattern match
    if (!isAggregator && !isCareersPage && !TITLE_RX.test(title)) continue;
    const company = clean(card.querySelector("[class*='company'], [class*='Company'], [class*='employer']")?.textContent || "");
    const loc = clean(card.querySelector("[class*='location'], [class*='Location'], [class*='city']")?.textContent || "");
    out.push({ title, company, location: loc, url, snippet: clean(card.textContent || "").slice(0, 240) });
    seen.add(url);
  }
  return out;
}
