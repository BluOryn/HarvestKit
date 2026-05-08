/**
 * smartDetector.ts — Universal card detection using DOM structural analysis.
 * Finds repeated card patterns without relying on specific CSS class names.
 */
import { clean } from "../lib/utils";
import type { ListCard } from "../lib/messages";

function structuralFP(el: Element): string {
  const tag = el.tagName;
  const childTags = Array.from(el.children).map((c) => c.tagName).join(",").slice(0, 60);
  const links = el.querySelectorAll("a").length;
  return `${tag}|${childTags}|L${links}`;
}

export function findRepeatedGroups(root: Element, minCount = 3): { elements: Element[]; score: number }[] {
  const candidates: { elements: Element[]; score: number }[] = [];
  for (const container of root.querySelectorAll("ul, ol, div, section, main, table, tbody, article, [role='list'], [role='feed'], [role='table'], [role='grid']")) {
    const children = Array.from(container.children).filter((c) => !["SCRIPT","STYLE","BR","HR"].includes(c.tagName));
    if (children.length < minCount) continue;
    const groups = new Map<string, Element[]>();
    for (const c of children) { const fp = structuralFP(c); if (!groups.has(fp)) groups.set(fp, []); groups.get(fp)!.push(c); }
    for (const [, elements] of groups) {
      if (elements.length < minCount) continue;
      const score = scoreGroup(elements);
      if (score > 0.3) candidates.push({ elements, score });
    }
  }
  return candidates.sort((a, b) => b.score - a.score);
}

function scoreGroup(elements: Element[]): number {
  let score = 0;
  const sample = elements.slice(0, 10);
  const withLinks = sample.filter((el) => el.querySelector("a[href]"));
  score += (withLinks.length / sample.length) * 0.3;
  const lens = sample.map((el) => (el.textContent || "").trim().length);
  const avg = lens.reduce((a, b) => a + b, 0) / lens.length;
  const cv = avg > 0 ? Math.sqrt(lens.reduce((a, b) => a + (b - avg) ** 2, 0) / lens.length) / avg : 999;
  if (cv < 0.5) score += 0.2; else if (cv < 1.0) score += 0.1;
  if (avg > 20 && avg < 500) score += 0.15;
  return Math.min(1, score);
}

function findTitle(el: Element): string {
  for (const sel of ["h1","h2","h3","h4"]) {
    const h = el.querySelector(sel);
    if (h && h.textContent!.trim().length > 2) return h.textContent!.trim();
  }
  for (const child of el.querySelectorAll("*")) {
    const cls = ((child as HTMLElement).className || "").toString().toLowerCase();
    if (/title|heading|name|position/i.test(cls)) {
      const t = child.textContent!.trim();
      if (t.length > 2 && t.length < 200) return t;
    }
  }
  const a = el.querySelector("a[href]");
  if (a && a.textContent!.trim().length > 5) return a.textContent!.trim();
  return "";
}

function findByHint(el: Element, hints: string[]): string {
  for (const child of el.querySelectorAll("*")) {
    const cls = ((child as HTMLElement).className || "").toString().toLowerCase();
    if (hints.some((h) => cls.includes(h))) {
      const t = child.textContent!.trim();
      if (t.length > 0 && t.length < 200) return t;
    }
  }
  return "";
}

export function smartExtractCards(root: Document = document): { found: boolean; cards: ListCard[] } {
  const groups = findRepeatedGroups(root.body || root.documentElement);
  if (groups.length === 0) return { found: false, cards: [] };
  const best = groups[0];
  const cards: ListCard[] = [];
  const seen = new Set<string>();
  for (const el of best.elements) {
    const a = el.querySelector("a[href]") as HTMLAnchorElement | null;
    if (!a) continue;
    const href = a.getAttribute("href") || "";
    if (!href || href === "#" || href.startsWith("javascript:")) continue;
    let url: string;
    try { url = new URL(href, location.href).href; } catch { continue; }
    if (seen.has(url)) continue; seen.add(url);
    const title = clean(findTitle(el));
    if (!title) continue;
    cards.push({
      title,
      company: clean(findByHint(el, ["company", "employer", "org"])),
      location: clean(findByHint(el, ["location", "city", "place"])),
      url,
      snippet: clean(el.textContent || "").replace(/\s+/g, " ").slice(0, 300),
    });
  }
  return { found: cards.length >= 2, cards };
}
