/**
 * Smoke test: Simulate listExtractor logic against Jobrapido HTML
 * fetched via HTTP. Verifies the data-advert fast-path works.
 */
import { JSDOM } from "jsdom";

const TARGET_URL = "https://in.jobrapido.com/?w=ai";

function stripHtml(s) {
  return (s || "").replace(/<[^>]*>/g, "").trim();
}

function clean(s) {
  if (s == null) return "";
  return String(s).replace(/\s+/g, " ").trim();
}

async function main() {
  console.log("Fetching", TARGET_URL, "...");
  const resp = await fetch(TARGET_URL, {
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
    },
  });
  const html = await resp.text();
  console.log(`Fetched ${html.length} chars\n`);

  const dom = new JSDOM(html, { url: TARGET_URL });
  const document = dom.window.document;
  const loc = new URL(TARGET_URL);

  // --- Test 1: data-advert count ---
  const advertEls = document.querySelectorAll("[data-advert]");
  console.log(`Test 1: [data-advert] elements found: ${advertEls.length}`);
  console.assert(advertEls.length > 0, "FAIL: No data-advert elements found!");

  // --- Test 2: Full extraction simulation (with fixes) ---
  const cards = [];
  const seen = new Set();
  for (const el of advertEls) {
    let advert;
    try { advert = JSON.parse(el.getAttribute("data-advert")); } catch { continue; }
    if (!advert) continue;

    const title = clean(stripHtml(advert.title) || el.querySelector(".result-item__title, h3")?.textContent || "");
    const company = clean(stripHtml(advert.company) || "");
    const location = clean(stripHtml(advert.location) || "");

    let url = "";
    const link = el.querySelector("a.result-item__link, a[href*='open.app.jobrapido'], a[href*='jobpreview'], a[href]");
    if (link) {
      const href = link.getAttribute("href") || "";
      if (href && !href.includes("[[") && !href.includes("{{")) {
        try { url = new URL(href, TARGET_URL).href; } catch {}
      }
    }
    if ((!url || url.includes("[[")) && advert.advertId) {
      url = `${loc.origin}/jobpreview/${advert.advertId}`;
    }
    if (!url || seen.has(url)) continue;
    seen.add(url);
    cards.push({ title: title || `Job #${advert.advertId || ""}`, company, location, url: url.substring(0, 80) });
  }

  console.log(`\nTest 2: Full extraction: ${cards.length} unique cards`);
  for (const c of cards.slice(0, 5)) {
    console.log(`  → ${c.title} | ${c.company} | ${c.location}`);
    console.log(`    ${c.url}`);
  }
  if (cards.length > 5) console.log(`  ... and ${cards.length - 5} more`);

  // --- Test 3: Title HTML stripping ---
  const rawTitle = advertEls[0] ? JSON.parse(advertEls[0].getAttribute("data-advert")).title : "";
  const stripped = stripHtml(rawTitle);
  console.log(`\nTest 3: HTML stripping`);
  console.log(`  Raw:      "${rawTitle}"`);
  console.log(`  Stripped:  "${stripped}"`);
  console.assert(!stripped.includes("<"), "FAIL: HTML tags not stripped from title!");

  // --- Test 4: Detector simulation ---
  const isAggregator = /jobrapido\.com/.test(loc.hostname);
  const hasSearchQuery = /[?&](w|q|keywords?|search)=/i.test(loc.search);
  let confidence = 0;
  const reasons = [];
  if (isAggregator) { confidence += 0.7; reasons.push("aggregator-domain"); }
  if (hasSearchQuery) { confidence += 0.2; reasons.push("search-query"); }
  if (advertEls.length > 0) { confidence += 0.5; reasons.push(`data-advert:${advertEls.length}`); }
  console.log(`\nTest 4: Detector → isJob: ${confidence >= 0.7}, confidence: ${confidence}, reasons: [${reasons.join(", ")}]`);

  // --- Summary ---
  console.log("\n" + "=".repeat(50));
  const pass = cards.length >= 3 && confidence >= 0.7 && !stripped.includes("<");
  if (pass) {
    console.log(`✅ ALL TESTS PASSED — ${cards.length} cards, confidence=${confidence}`);
  } else {
    console.log("❌ TESTS FAILED");
    console.log(`   Cards: ${cards.length}, Confidence: ${confidence}`);
    process.exit(1);
  }
}

main().catch((e) => { console.error("Fatal:", e); process.exit(1); });
