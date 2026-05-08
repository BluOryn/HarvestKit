/**
 * smartDetector.js — Universal job card detection using structural analysis.
 *
 * Instead of relying on hardcoded CSS selectors like div[class*='job'],
 * this module analyzes the DOM structure to find repeated card-like patterns
 * regardless of class naming conventions.
 *
 * Techniques:
 *  1. Repeated-sibling detection (find groups of 3+ identical-structure siblings)
 *  2. Link clustering (group links by URL template pattern)
 *  3. Geometric analysis (find vertically stacked, uniform-width elements)
 *  4. Text density scoring (cards have short title + metadata pattern)
 */
import { clean } from "../lib/utils.js";

// ---------------------------------------------------------------------------
// 1. Structural fingerprint: tag + class-shape (not exact class name)
// ---------------------------------------------------------------------------
function structuralFingerprint(el) {
  if (!el || el.nodeType !== 1) return "";
  const tag = el.tagName;
  const childTags = Array.from(el.children).map((c) => c.tagName).join(",");
  const linkCount = el.querySelectorAll("a").length;
  const depth = getDepth(el);
  return `${tag}|${childTags.length > 60 ? childTags.slice(0, 60) : childTags}|L${linkCount}|D${depth}`;
}

function getDepth(el, max = 6) {
  let d = 0;
  let node = el;
  while (node.firstElementChild && d < max) {
    node = node.firstElementChild;
    d++;
  }
  return d;
}

// ---------------------------------------------------------------------------
// 2. Find repeated sibling groups (the core of universal card detection)
// ---------------------------------------------------------------------------
export function findRepeatedGroups(root = document.body, minCount = 3) {
  const candidates = [];
  // Walk all containers that could hold a list of cards
  const containers = root.querySelectorAll(
    "ul, ol, div, section, main, article, [role='list'], [role='feed']"
  );

  for (const container of containers) {
    const children = Array.from(container.children).filter(
      (c) => c.tagName !== "SCRIPT" && c.tagName !== "STYLE" && c.tagName !== "BR"
    );
    if (children.length < minCount) continue;

    // Group children by structural fingerprint
    const groups = new Map();
    for (const child of children) {
      const fp = structuralFingerprint(child);
      if (!fp) continue;
      if (!groups.has(fp)) groups.set(fp, []);
      groups.get(fp).push(child);
    }

    for (const [fp, elements] of groups) {
      if (elements.length < minCount) continue;
      // Score this group
      const score = scoreCardGroup(elements);
      if (score > 0.3) {
        candidates.push({ container, elements, fingerprint: fp, score });
      }
    }
  }

  // Sort by score descending, return best
  candidates.sort((a, b) => b.score - a.score);
  return candidates;
}

// ---------------------------------------------------------------------------
// 3. Score a group of elements on how "card-like" they are
// ---------------------------------------------------------------------------
function scoreCardGroup(elements) {
  let score = 0;
  const sample = elements.slice(0, 10);

  // A. Each element contains at least one link
  const withLinks = sample.filter((el) => el.querySelector("a[href]"));
  score += (withLinks.length / sample.length) * 0.3;

  // B. Elements have similar text length (cards are uniform)
  const lengths = sample.map((el) => (el.textContent || "").trim().length);
  const avgLen = lengths.reduce((a, b) => a + b, 0) / lengths.length;
  const variance = lengths.reduce((a, b) => a + Math.pow(b - avgLen, 2), 0) / lengths.length;
  const cv = avgLen > 0 ? Math.sqrt(variance) / avgLen : 999; // coefficient of variation
  if (cv < 0.5) score += 0.2;  // very uniform
  if (cv < 1.0) score += 0.1;  // somewhat uniform

  // C. Elements have moderate text content (not too short, not too long)
  if (avgLen > 20 && avgLen < 500) score += 0.15;
  if (avgLen >= 500 && avgLen < 2000) score += 0.05;

  // D. Link URLs share a pattern (e.g., /job/123, /posting/456)
  const urls = withLinks.map((el) => {
    const a = el.querySelector("a[href]");
    return a ? (a.getAttribute("href") || "") : "";
  }).filter(Boolean);
  if (urls.length >= 3) {
    const templateScore = urlPatternScore(urls);
    score += templateScore * 0.25;
  }

  // E. Geometric uniformity (same width, stacked vertically)
  if (typeof elements[0].getBoundingClientRect === "function") {
    const rects = sample.map((el) => el.getBoundingClientRect());
    const widths = rects.map((r) => Math.round(r.width));
    const uniqueWidths = new Set(widths);
    if (uniqueWidths.size <= 2 && widths[0] > 100) score += 0.1;
  }

  return Math.min(1, score);
}

// ---------------------------------------------------------------------------
// 4. URL pattern similarity
// ---------------------------------------------------------------------------
function urlPatternScore(urls) {
  if (urls.length < 3) return 0;
  // Tokenize each URL path and find shared prefix
  const paths = urls.map((u) => {
    try { return new URL(u, location.href).pathname; } catch { return u; }
  });
  const segments = paths.map((p) => p.split("/").filter(Boolean));
  if (segments.length === 0) return 0;

  // Count how many path segments are shared across all URLs
  const minLen = Math.min(...segments.map((s) => s.length));
  let shared = 0;
  for (let i = 0; i < minLen; i++) {
    const unique = new Set(segments.map((s) => s[i]));
    if (unique.size === 1) shared++;
    else break;
  }

  // If most URLs share a common prefix and differ only in last segment (ID), that's a strong signal
  const avgSegments = segments.reduce((a, b) => a + b.length, 0) / segments.length;
  if (shared >= 1 && avgSegments > shared) return 0.8;
  if (shared >= 1) return 0.5;
  return 0.2;
}

// ---------------------------------------------------------------------------
// 5. Extract card data from a detected group
// ---------------------------------------------------------------------------
export function extractCardsFromGroup(elements) {
  const cards = [];
  const seen = new Set();

  for (const el of elements) {
    const a = el.querySelector("a[href]");
    if (!a) continue;
    const href = a.getAttribute("href") || "";
    if (!href || href === "#" || href.startsWith("javascript:")) continue;

    let url;
    try { url = new URL(href, location.href).href; } catch { continue; }
    if (seen.has(url)) continue;
    seen.add(url);

    // Title: largest/first heading-like element, or the link text
    const title = clean(
      findTitle(el) || a.textContent || ""
    );
    if (!title) continue;

    // Company: look for secondary text elements
    const company = clean(findByHint(el, ["company", "employer", "org", "firma", "unternehmen"]) || "");

    // Location: look for location-like elements
    const loc = clean(findByHint(el, ["location", "city", "ort", "standort", "place", "region"]) || "");

    // Date
    const date = clean(findByHint(el, ["date", "posted", "datum", "time", "ago"]) || "");

    const snippet = clean(el.innerText || el.textContent || "").replace(/\s+/g, " ").slice(0, 300);

    cards.push({ title, company, location: loc, date, url, snippet });
  }

  return cards;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function findTitle(el) {
  // Try headings first
  for (const sel of ["h1", "h2", "h3", "h4"]) {
    const h = el.querySelector(sel);
    if (h && h.textContent.trim().length > 2) return h.textContent.trim();
  }
  // Try elements with title-ish class/role
  for (const child of el.querySelectorAll("*")) {
    const cls = (child.className || "").toString().toLowerCase();
    const role = (child.getAttribute("role") || "").toLowerCase();
    if (/title|heading|name|position|rolle/i.test(cls) || role === "heading") {
      const text = child.textContent.trim();
      if (text.length > 2 && text.length < 200) return text;
    }
  }
  // Try the first link with substantive text
  const links = el.querySelectorAll("a[href]");
  for (const link of links) {
    const text = link.textContent.trim();
    if (text.length > 5 && text.length < 200) return text;
  }
  return "";
}

function findByHint(el, hints) {
  for (const child of el.querySelectorAll("*")) {
    const cls = (child.className || "").toString().toLowerCase();
    const dataAttrs = Array.from(child.attributes)
      .filter((a) => a.name.startsWith("data-"))
      .map((a) => a.value.toLowerCase())
      .join(" ");
    const combined = `${cls} ${dataAttrs}`;
    for (const hint of hints) {
      if (combined.includes(hint)) {
        const text = child.textContent.trim();
        if (text.length > 0 && text.length < 200) return text;
      }
    }
  }
  return "";
}

// ---------------------------------------------------------------------------
// Main entry point: detect and extract job cards from any page
// ---------------------------------------------------------------------------
export function smartExtractCards(root = document) {
  const groups = findRepeatedGroups(root.body || root);
  if (groups.length === 0) return { found: false, cards: [] };

  // Use the highest-scoring group
  const best = groups[0];
  const cards = extractCardsFromGroup(best.elements);

  return {
    found: cards.length >= 2,
    cards,
    meta: {
      groupScore: best.score,
      groupSize: best.elements.length,
      fingerprint: best.fingerprint,
    },
  };
}
