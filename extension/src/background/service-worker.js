import { putJob, listJobs, clearJobs, deleteJob, countJobs } from "./store.js";
import { crawl, handleJobMessage } from "./bulk.js";

chrome.runtime.onInstalled.addListener(async () => {
  const cur = await chrome.storage.local.get(["autoSave", "showBanner"]);
  if (cur.autoSave === undefined) await chrome.storage.local.set({ autoSave: false });
  if (cur.showBanner === undefined) await chrome.storage.local.set({ showBanner: true });
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "SAVE_JOB") {
        const job = await putJob(msg.job);
        sendResponse({ ok: true, job });
        return;
      }
      if (msg.type === "SAVE_LIST") {
        const cards = msg.cards || [];
        const saved = [];
        for (const c of cards) {
          const job = {
            title: c.title || "",
            company: c.company || "",
            location: c.location || "",
            description: c.snippet || "",
            apply_url: c.url || "",
            job_url: c.url || "",
            source_domain: msg.source_domain || "",
            source_ats: "list-page",
            confidence: "0.50",
          };
          saved.push(await putJob(job));
        }
        sendResponse({ ok: true, count: saved.length });
        return;
      }
      if (msg.type === "EXTRACT_LIST_ACTIVE") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) { sendResponse({ ok: false, reason: "no-tab" }); return; }
        const r = await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_LIST" });
        sendResponse({ ok: true, result: r });
        return;
      }
      if (msg.type === "JOB_FOUND") {
        handleJobMessage(msg, sender);
        sendResponse({ ok: true });
        return;
      }
      if (msg.type === "LIST_JOBS") {
        const jobs = await listJobs();
        sendResponse({ ok: true, jobs });
        return;
      }
      if (msg.type === "CLEAR_JOBS") {
        await clearJobs();
        sendResponse({ ok: true });
        return;
      }
      if (msg.type === "DELETE_JOB") {
        await deleteJob(msg.fingerprint);
        sendResponse({ ok: true });
        return;
      }
      if (msg.type === "COUNT_JOBS") {
        sendResponse({ ok: true, count: await countJobs() });
        return;
      }
      if (msg.type === "CRAWL_URLS") {
        const results = await crawl(msg.urls || [], msg.options || {});
        sendResponse({ ok: true, results });
        return;
      }
      if (msg.type === "EXTRACT_ACTIVE_TAB") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) {
          sendResponse({ ok: false, reason: "no-tab" });
          return;
        }
        // Expand content first for richer extraction
        await chrome.tabs.sendMessage(tab.id, { type: "DISMISS_OVERLAYS" }).catch(() => {});
        await chrome.tabs.sendMessage(tab.id, { type: "EXPAND_CONTENT" }).catch(() => {});
        await new Promise(r => setTimeout(r, 600));
        const r = await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_NOW" });
        if (r && r.job) {
          await putJob(r.job);
        }
        sendResponse({ ok: true, result: r });
        return;
      }

      // --- NEW: Auto-paginate active tab ---
      if (msg.type === "PAGINATE_ACTIVE_TAB") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) { sendResponse({ ok: false, reason: "no-tab" }); return; }
        const r = await chrome.tabs.sendMessage(tab.id, {
          type: "AUTO_PAGINATE",
          maxPages: msg.maxPages || 10,
          delayMs: msg.delayMs || 1500,
        });
        if (r && r.ok && r.cards) {
          // Save all paginated cards
          for (const c of r.cards) {
            await putJob({
              title: c.title || "",
              company: c.company || "",
              location: c.location || "",
              description: c.snippet || "",
              apply_url: c.url || "",
              job_url: c.url || "",
              source_domain: tab.url ? new URL(tab.url).hostname : "",
              source_ats: "paginated",
              confidence: "0.50",
            });
          }
        }
        sendResponse(r);
        return;
      }

      // --- NEW: Get page info (for popup UI) ---
      if (msg.type === "GET_PAGE_INFO_ACTIVE") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) { sendResponse({ ok: false, reason: "no-tab" }); return; }
        try {
          const r = await chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_INFO" });
          sendResponse(r);
        } catch {
          sendResponse({ ok: false, reason: "content-script-not-ready" });
        }
        return;
      }

      // --- NEW: Full pipeline (paginate → collect → deep-scrape each) ---
      if (msg.type === "FULL_SCRAPE_ACTIVE") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) { sendResponse({ ok: false, reason: "no-tab" }); return; }

        // Step 1: Paginate and collect all cards
        const pagResult = await chrome.tabs.sendMessage(tab.id, {
          type: "AUTO_PAGINATE",
          maxPages: msg.maxPages || 10,
        });
        const allCards = (pagResult && pagResult.cards) || [];

        if (allCards.length === 0) {
          // Try single-page extraction
          const listResult = await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_LIST" });
          if (listResult && listResult.cards) allCards.push(...listResult.cards);
        }

        // Save snapshots
        for (const c of allCards) {
          await putJob({
            title: c.title || "",
            company: c.company || "",
            location: c.location || "",
            description: c.snippet || "",
            apply_url: c.url || "",
            job_url: c.url || "",
            source_domain: tab.url ? new URL(tab.url).hostname : "",
            source_ats: "full-pipeline",
            confidence: "0.50",
          });
        }

        // Step 2: Deep-scrape each URL
        const urls = allCards.map((c) => c.url).filter(Boolean);
        let deepResults = { results: [] };
        if (urls.length > 0) {
          deepResults = await crawl(urls, { concurrency: msg.concurrency || 3 });
        }

        const ok = (deepResults.results || deepResults || []).filter((x) => x.ok).length;
        sendResponse({
          ok: true,
          totalCards: allCards.length,
          deepScraped: ok,
          totalUrls: urls.length,
        });
        return;
      }

      sendResponse({ ok: false, reason: "unknown-type" });
    } catch (e) {
      sendResponse({ ok: false, error: String((e && e.message) || e) });
    }
  })();
  return true; // async
});
