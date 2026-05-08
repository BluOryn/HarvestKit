// Detect and extract from job-list / search-results pages.
// Returns { isList, cards: [{title, company, location, url, snippet}] }
import { clean } from "../lib/utils.js";

// Broader title regex — covers common job roles across domains
const TITLE_RX = /(software|engineer|developer|data|ai|ml|cloud|devops|backend|frontend|fullstack|stack|architect|scientist|analyst|sre|platform|security|product|qa|sdet|mobile|android|ios|principal|staff|lead|director|head|manager|consultant|designer|administrator|coordinator|specialist|intern|trainee|associate|executive|officer|researcher|strategist|advisor|planner|recruiter|hr|operations|delivery|solution|generative|automation|observability|testing|servicenow|vertex)/i;

// Known job-aggregator hostnames — on these we relax URL-path restrictions
const AGGREGATOR_RX = /jobrapido\.com|indeed\.com|glassdoor\.com|monster\.com|simplyhired\.com|ziprecruiter\.com|linkedin\.com\/jobs|naukri\.com|reed\.co\.uk|seek\.com|stepstone\./i;

// Link-href pattern that signals a job URL
const JOB_LINK_RX = /\/(jobs?|vacanc|career|stellen|joboffer|positions?|opening|posting|jobpreview|view)/i;

/**
 * Fast-path: extract cards from Jobrapido pages using `data-advert` JSON attrs.
 * Returns cards array or null if not applicable.
 */
function stripHtml(s) {
  return (s || "").replace(/<[^>]*>/g, "").trim();
}

function extractJobrapido(root) {
  const items = root.querySelectorAll("[data-advert]");
  if (items.length === 0) return null;

  const cards = [];
  const seen = new Set();
  for (const el of items) {
    let advert;
    try { advert = JSON.parse(el.getAttribute("data-advert")); } catch { continue; }
    if (!advert) continue;

    const title = clean(stripHtml(advert.title) || el.querySelector(".result-item__title, h3, [class*='title']")?.textContent || "");
    const company = clean(stripHtml(advert.company) || el.querySelector(".result-item__company-label, [class*='company']")?.textContent || "");
    const loc = clean(stripHtml(advert.location) || el.querySelector(".result-item__location-label, [class*='location']")?.textContent || "");

    // Get the link — prefer resolved tracking redirect URL, fall back to advertId preview URL
    // Note: before Angular hydrates, hrefs may be template literals like [[advert.openAdvertUrl]]
    let url = "";
    const link = el.querySelector("a.result-item__link, a[href*='open.app.jobrapido'], a[href*='jobpreview'], a[href]");
    if (link) {
      const href = link.getAttribute("href") || "";
      // Skip Angular template literals that haven't been resolved yet
      if (href && !href.includes("[[") && !href.includes("{{")) {
        try { url = new URL(href, location.href).href; } catch {}
      }
    }
    // Always prefer advertId-based URL for uniqueness when link isn't resolved
    if ((!url || url.includes("[[")) && advert.advertId) {
      url = `${location.origin}/jobpreview/${advert.advertId}`;
    }
    if (!url || seen.has(url)) continue;
    seen.add(url);

    const snippet = clean(el.innerText || el.textContent || "").replace(/\s+/g, " ").slice(0, 240);
    cards.push({ title: title || `Job #${advert.advertId || ""}`, company, location: loc, url, snippet });
  }
  return cards.length >= 1 ? cards : null;
}

export async function detectAndExtractList(root = document) {
  // --- Jobrapido fast-path ---
  const rapidoCards = extractJobrapido(root);
  if (rapidoCards && rapidoCards.length >= 1) {
    return { isList: true, cards: rapidoCards };
  }

  const isAggregator = AGGREGATOR_RX.test(location.hostname);

  // Group anchors that look like job postings
  const linkCandidates = Array.from(root.querySelectorAll("a[href]"))
    .filter((a) => {
      const href = a.getAttribute("href") || "";
      if (JOB_LINK_RX.test(href)) return true;
      // On aggregator sites, accept broader links (tracking redirects, etc.)
      if (isAggregator && href && !href.startsWith("#") && !href.startsWith("javascript:")) return true;
      return false;
    });

  // Common card container selectors — includes aggregator-specific patterns
  const cardSelectors = [
    "article",
    "li",
    "div[class*='job']",
    "div[class*='Job']",
    "div[class*='card']",
    "div[class*='listing']",
    "div[class*='result']",
    "div[data-testid*='job']",
    "div[data-test*='job']",
    // Aggregator-specific
    ".result-item",
    ".job_seen_beacon",
    ".jobsearch-ResultsList > div",
    "[data-advert]",
    "[data-job-id]",
    "[data-jk]",
  ];

  const cardSet = new Set();
  for (const sel of cardSelectors) {
    for (const el of root.querySelectorAll(sel)) {
      const a = el.querySelector("a[href]");
      if (!a) continue;
      const href = a.getAttribute("href") || "";
      if (!href) continue;
      // On aggregator sites, accept any link; elsewhere require a job-ish path
      if (!isAggregator && !JOB_LINK_RX.test(href) && !/open\.app\.|jobpreview|redirect/i.test(href)) continue;
      cardSet.add(el);
    }
  }

  // Threshold: a list page has many similar cards
  const cards = Array.from(cardSet);
  if (cards.length < 3 && linkCandidates.length < 3) {
    return { isList: false, cards: [] };
  }

  const out = [];
  const seen = new Set();
  const containers = cards.length >= 3 ? cards : linkCandidates.map((a) => a.closest("article,li,div,section") || a);

  for (const c of containers) {
    const a = c.tagName === "A" ? c : c.querySelector("a[href]");
    if (!a) continue;
    const href = a.getAttribute("href") || "";
    let abs;
    try { abs = new URL(href, location.href).href; } catch { continue; }
    if (seen.has(abs)) continue;

    const text = (c.innerText || c.textContent || "").trim();
    const title = clean(
      c.querySelector("h2, h3, [class*='title'], [class*='Title'], [data-testid*='title']")?.textContent ||
        a.textContent || ""
    );
    // On aggregator sites, accept any non-empty title; otherwise require TITLE_RX match
    if (!title) continue;
    if (!isAggregator && !TITLE_RX.test(title)) continue;
    const company = clean(
      c.querySelector("[class*='company'], [class*='Company'], [data-testid*='company'], [class*='employer']")?.textContent || ""
    );
    const location = clean(
      c.querySelector("[class*='location'], [class*='Location'], [data-testid*='location'], [class*='city']")?.textContent || ""
    );
    out.push({
      title,
      company,
      location,
      url: abs,
      snippet: text.replace(/\s+/g, " ").slice(0, 240),
    });
    seen.add(abs);
  }

  if (out.length >= 3) {
    return { isList: true, cards: out };
  }

  // --- Fallback: Smart structural detection ---
  // When CSS selectors fail (obfuscated classes, unknown sites), use
  // structural analysis to find repeated card-like patterns in the DOM.
  try {
    const { smartExtractCards } = await import("./smartDetector.js");
    const smart = smartExtractCards(root);
    if (smart.found && smart.cards.length >= 2) {
      return { isList: true, cards: smart.cards };
    }
  } catch (e) {
    // smartDetector not available — continue
  }

  return { isList: out.length >= 3, cards: out };
}
