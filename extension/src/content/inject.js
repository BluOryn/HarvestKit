import { extract } from "./extractor.js";
import { detectJobPage } from "./detector.js";
import { detectAndExtractList } from "./listExtractor.js";
import { Paginator, detectPagination } from "./paginator.js";
import { expandAllContent, dismissOverlays } from "./expandContent.js";

const BANNER_ID = "__jobharvester_banner__";
let activePaginator = null;

// ---------------------------------------------------------------------------
// UI Banners
// ---------------------------------------------------------------------------
function showBanner(job) {
  if (document.getElementById(BANNER_ID)) return;
  const div = document.createElement("div");
  div.id = BANNER_ID;
  div.style.cssText = `
    position:fixed; top:12px; right:12px; z-index:2147483647;
    background:#111; color:#fff; padding:10px 14px; border-radius:10px;
    font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    box-shadow:0 8px 24px rgba(0,0,0,.25); max-width:340px;
  `;
  div.innerHTML = `
    <b>JobHarvester</b> detected this page.<br/>
    <span style="opacity:.85">${escapeHTML(job.title || "Unknown role")} · ${escapeHTML(job.company || "Unknown company")}</span><br/>
    <button id="__jh_save" style="margin-top:8px;background:#6cf;color:#000;border:0;padding:6px 10px;border-radius:6px;cursor:pointer;font-weight:600">Save to library</button>
    <button id="__jh_dismiss" style="margin-top:8px;background:#333;color:#fff;border:0;padding:6px 10px;border-radius:6px;cursor:pointer">Dismiss</button>
  `;
  document.body.appendChild(div);
  div.querySelector("#__jh_save").addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "SAVE_JOB", job });
    div.remove();
  });
  div.querySelector("#__jh_dismiss").addEventListener("click", () => div.remove());
}

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function showListBanner(list) {
  if (document.getElementById(BANNER_ID)) return;
  const pagination = detectPagination(document);
  const paginationInfo = pagination.type !== "none"
    ? `<br/><span style="opacity:.7">📄 Pagination detected: ${pagination.type}</span>`
    : "";

  const div = document.createElement("div");
  div.id = BANNER_ID;
  div.style.cssText = `
    position:fixed; top:12px; right:12px; z-index:2147483647;
    background:#111; color:#fff; padding:10px 14px; border-radius:10px;
    font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    box-shadow:0 8px 24px rgba(0,0,0,.25); max-width:380px;
  `;
  div.innerHTML = `
    <b>JobHarvester</b> found <strong>${list.cards.length}</strong> job listings here.${paginationInfo}<br/>
    <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px;">
      <button id="__jh_save_list" style="background:#6cf;color:#000;border:0;padding:6px 10px;border-radius:6px;cursor:pointer;font-weight:600">Save snapshot</button>
      <button id="__jh_crawl_list" style="background:#6f6;color:#000;border:0;padding:6px 10px;border-radius:6px;cursor:pointer;font-weight:600">Deep-scrape all</button>
      ${pagination.type !== "none" ? `<button id="__jh_paginate" style="background:#f90;color:#000;border:0;padding:6px 10px;border-radius:6px;cursor:pointer;font-weight:600">Scrape ALL pages</button>` : ""}
      <button id="__jh_dismiss" style="background:#333;color:#fff;border:0;padding:6px 10px;border-radius:6px;cursor:pointer">✕</button>
    </div>
    <div id="__jh_progress" style="margin-top:6px;font-size:11px;opacity:.7"></div>
  `;
  document.body.appendChild(div);

  div.querySelector("#__jh_save_list").addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "SAVE_LIST", cards: list.cards, source_domain: location.hostname });
    updateProgress(`✅ Saved ${list.cards.length} jobs from this page.`);
  });

  div.querySelector("#__jh_crawl_list").addEventListener("click", async () => {
    const urls = list.cards.map((c) => c.url);
    updateProgress(`🔄 Deep-scraping ${urls.length} jobs…`);
    const out = await chrome.runtime.sendMessage({ type: "CRAWL_URLS", urls, options: { concurrency: 3 } });
    const ok = (out.results || []).filter((x) => x.ok).length;
    updateProgress(`✅ Done. ${ok}/${urls.length} extracted with full details.`);
  });

  const paginateBtn = div.querySelector("#__jh_paginate");
  if (paginateBtn) {
    paginateBtn.addEventListener("click", async () => {
      paginateBtn.disabled = true;
      paginateBtn.textContent = "Scraping…";
      await runPaginatedScrape();
    });
  }

  div.querySelector("#__jh_dismiss").addEventListener("click", () => {
    if (activePaginator) activePaginator.abort();
    div.remove();
  });
}

function updateProgress(msg) {
  const el = document.getElementById("__jh_progress");
  if (el) el.textContent = msg;
}

// ---------------------------------------------------------------------------
// Core scraping flows
// ---------------------------------------------------------------------------

/**
 * Paginated scrape: collect cards from all pages, then deep-scrape each.
 */
async function runPaginatedScrape() {
  activePaginator = new Paginator({
    maxPages: 10,
    delayMs: 1500,
    onProgress: (p) => {
      updateProgress(`📄 Page ${p.page}: ${p.status} | Total: ${p.total || 0} jobs`);
    },
  });

  const allCards = await activePaginator.collectAll(() => detectAndExtractList());
  activePaginator = null;

  if (allCards.length === 0) {
    updateProgress("❌ No jobs found across pages.");
    return;
  }

  // Save all cards as snapshots
  await chrome.runtime.sendMessage({
    type: "SAVE_LIST",
    cards: allCards,
    source_domain: location.hostname,
  });
  updateProgress(`✅ Saved ${allCards.length} jobs from all pages. Starting deep-scrape…`);

  // Deep-scrape each URL for full details
  const urls = allCards.map((c) => c.url);
  const out = await chrome.runtime.sendMessage({
    type: "CRAWL_URLS",
    urls,
    options: { concurrency: 3 },
  });
  const ok = (out.results || []).filter((x) => x.ok).length;
  updateProgress(`🎉 Complete! ${ok}/${urls.length} jobs scraped with full descriptions.`);
}

// ---------------------------------------------------------------------------
// Main run function
// ---------------------------------------------------------------------------
async function run() {
  // Step 0: Dismiss overlays (cookie banners, modals)
  await dismissOverlays(document);

  // Step 1: Try single job extraction
  let result = extract();
  if (!result.job) {
    await new Promise((r) => setTimeout(r, 1500));
    result = extract();
  }

  // Step 1b: Before showing single job, expand content for richer extraction
  if (result.job && result.job.title) {
    const expanded = await expandAllContent(document);
    if (expanded > 0) {
      // Re-extract after expansion for richer data
      result = extract();
    }
  }

  // If we got a valid job with a title, handle it as a single job page
  if (result.job && result.job.title) {
    chrome.runtime.sendMessage({ type: "JOB_FOUND", job: result.job, detection: result.detection });
    chrome.storage.local.get(["autoSave", "showBanner"]).then((cfg) => {
      if (cfg.autoSave) chrome.runtime.sendMessage({ type: "SAVE_JOB", job: result.job });
      if (cfg.showBanner !== false) showBanner(result.job);
    });
    return;
  }

  // Step 2: Try list page detection
  const list = await detectAndExtractList();
  if (list.isList) {
    chrome.storage.local.get(["showBanner"]).then((cfg) => {
      if (cfg.showBanner !== false) showListBanner(list);
    });
  }
}

// ---------------------------------------------------------------------------
// Message handlers (popup, background, side panel)
// ---------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "EXTRACT_NOW") {
    (async () => {
      await expandAllContent(document);
      const r = extract();
      sendResponse(r);
    })();
    return true;
  }
  if (msg.type === "DETECT_NOW") {
    sendResponse(detectJobPage());
    return true;
  }
  if (msg.type === "EXTRACT_LIST") {
    (async () => {
      const r = await detectAndExtractList();
      sendResponse(r);
    })();
    return true;
  }
  if (msg.type === "AUTO_PAGINATE") {
    (async () => {
      try {
        const allCards = await new Paginator({
          maxPages: msg.maxPages || 10,
          delayMs: msg.delayMs || 1500,
        }).collectAll(() => detectAndExtractList());
        sendResponse({ ok: true, cards: allCards, total: allCards.length });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    })();
    return true;
  }
  if (msg.type === "EXPAND_CONTENT") {
    (async () => {
      const count = await expandAllContent(document);
      sendResponse({ ok: true, expanded: count });
    })();
    return true;
  }
  if (msg.type === "DISMISS_OVERLAYS") {
    (async () => {
      const count = await dismissOverlays(document);
      sendResponse({ ok: true, dismissed: count });
    })();
    return true;
  }
  if (msg.type === "GET_PAGE_INFO") {
    (async () => {
    const pagination = detectPagination(document);
    const list = await detectAndExtractList();
    const det = detectJobPage();
    sendResponse({
      ok: true,
      isJob: det.isJob,
      isList: list.isList,
      cardCount: list.cards ? list.cards.length : 0,
      pagination: { type: pagination.type, hasMore: pagination.hasMore },
      detection: det,
    });
    })();
    return true;
  }
});

// ---------------------------------------------------------------------------
// SPA navigation observer
// ---------------------------------------------------------------------------
let lastHref = location.href;
const obs = new MutationObserver(() => {
  if (location.href !== lastHref) {
    lastHref = location.href;
    setTimeout(run, 1200);
  }
});
obs.observe(document.documentElement, { childList: true, subtree: true });

run();
