import { db } from "../lib/db";
import { fingerprintRecord, type GeneralRecord } from "../lib/generalSchema";
import { emptyJob, fingerprint, type Job } from "../lib/schema";
import { handleGeneralMessage, handleJobMessage, resumeInterruptedRuns, retryFailed, startCrawlRun, startGeneralCrawlRun } from "./bulk";
import { installKeepaliveListener } from "./keepalive";

installKeepaliveListener();

// A service worker can be torn down at any moment — crash, extension update,
// memory pressure, a closed laptop lid. Nothing used to re-read the run rows
// on the way back up, so a 900-URL crawl that was 400 in lost the other 500
// permanently and its row span as "running" forever. Both of these fire on a
// fresh worker, and a run with nothing left is simply closed.
chrome.runtime.onStartup.addListener(() => {
  void resumeInterruptedRuns();
});
void resumeInterruptedRuns();

chrome.runtime.onInstalled.addListener(async () => {
  const cur = await chrome.storage.local.get(["autoSave", "showBanner", "theme"]);
  if (cur.autoSave === undefined) await chrome.storage.local.set({ autoSave: false });
  if (cur.showBanner === undefined) await chrome.storage.local.set({ showBanner: true });
  if (cur.theme === undefined) await chrome.storage.local.set({ theme: "dark" });
  // open side panel on action click
  try {
    await (chrome as any).sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true });
  } catch {}
});

chrome.action?.onClicked?.addListener(async (tab) => {
  if (tab?.windowId == null) return;
  try { await (chrome as any).sidePanel?.open?.({ windowId: tab.windowId }); } catch {}
});

async function saveJob(job: Job) {
  job.id = fingerprint(job);
  job.saved_at = Date.now();
  await db.jobs.put(job);
  return job;
}

async function saveRecord(rec: GeneralRecord) {
  rec.id = fingerprintRecord(rec);
  rec.saved_at = Date.now();
  await db.records.put(rec);
  return rec;
}

chrome.runtime.onMessage.addListener((msg: any, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg?.type) {
        case "SAVE_JOB": {
          sendResponse({ ok: true, job: await saveJob(msg.job) });
          break;
        }
        case "JOB_FOUND": {
          handleJobMessage(msg, sender);
          sendResponse({ ok: true });
          break;
        }
        case "RECORD_FOUND": {
          handleGeneralMessage(msg, sender);
          sendResponse({ ok: true });
          break;
        }
        case "EXTRACT_GENERAL_ACTIVE": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab?.id) { sendResponse({ ok: false, reason: "no-tab" }); break; }
          await chrome.tabs.sendMessage(tab.id, { type: "DISMISS_OVERLAYS" }).catch(() => {});
          await chrome.tabs.sendMessage(tab.id, { type: "EXPAND_CONTENT" }).catch(() => {});
          await new Promise(r => setTimeout(r, 600));
          const r = await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_GENERAL" });
          if (r && r.record) await saveRecord(r.record);
          sendResponse({ ok: true, result: r });
          break;
        }
        case "EXTRACT_GENERAL_LIST_ACTIVE": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab?.id) { sendResponse({ ok: false, reason: "no-tab" }); break; }
          const r = await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_GENERAL_LIST" });
          sendResponse({ ok: true, result: r });
          break;
        }
        case "CRAWL_GENERAL_URLS": {
          const runId = await startGeneralCrawlRun(msg.urls || [], msg.options || {});
          sendResponse({ ok: true, runId });
          break;
        }
        case "SAVE_LIST": {
          const cards = msg.cards || [];
          let count = 0;
          const runId = await db.runs.add({
            started_at: Date.now(),
            source_url: msg.source_url || "",
            total: cards.length,
            done: cards.length,
            ok: cards.length,
            failed: 0,
            status: "done",
            finished_at: Date.now(),
            type: "list-snapshot",
          });
          for (const c of cards) {
            const job: Job = {
              ...emptyJob(),
              title: c.title || "",
              company: c.company || "",
              location: c.location || "",
              description: c.snippet || "",
              apply_url: c.url || "",
              job_url: c.url || "",
              source_domain: msg.source_domain || "",
              source_ats: "list-page",
              confidence: "0.50",
              scraped_at: new Date().toISOString(),
            } as Job;
            await saveJob(job);
            count++;
          }
          sendResponse({ ok: true, count, runId });
          break;
        }
        case "EXTRACT_ACTIVE_TAB": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab?.id) { sendResponse({ ok: false, reason: "no-tab" }); break; }
          // Expand content first for richer extraction
          await chrome.tabs.sendMessage(tab.id, { type: "DISMISS_OVERLAYS" }).catch(() => {});
          await chrome.tabs.sendMessage(tab.id, { type: "EXPAND_CONTENT" }).catch(() => {});
          await new Promise(r => setTimeout(r, 600));
          const r = await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_NOW" });
          if (r && r.job) await saveJob(r.job);
          sendResponse({ ok: true, result: r });
          break;
        }
        case "EXTRACT_LIST_ACTIVE": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab?.id) { sendResponse({ ok: false, reason: "no-tab" }); break; }
          const r = await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_LIST" });
          sendResponse({ ok: true, result: r });
          break;
        }
        case "GET_PAGE_INFO_ACTIVE": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab?.id) { sendResponse({ ok: false, reason: "no-tab" }); break; }
          try {
            const r = await chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_INFO" });
            sendResponse(r);
          } catch {
            sendResponse({ ok: false, reason: "content-script-not-ready" });
          }
          break;
        }
        case "FULL_SCRAPE_ACTIVE": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab?.id) { sendResponse({ ok: false, reason: "no-tab" }); break; }

          // Step 1: Dismiss overlays and expand content
          await chrome.tabs.sendMessage(tab.id, { type: "DISMISS_OVERLAYS" }).catch(() => {});
          await chrome.tabs.sendMessage(tab.id, { type: "EXPAND_CONTENT" }).catch(() => {});
          await new Promise(r => setTimeout(r, 500));

          // Step 2: Walk every results page, not just the one on screen.
          //
          // The dashboard sends `maxPages: 100` and toasts "paginate -> collect
          // -> deep-scrape". This handler used to send a single EXTRACT_LIST and
          // never read `msg.maxPages`, so a 36-page board returned 25 leads and
          // the other 875 were silently never requested. The content script has
          // implemented AUTO_PAGINATE all along; nothing sent it.
          let allCards: any[] = [];
          const maxPages = Math.max(1, Number(msg.maxPages) || 1);
          if (maxPages > 1) {
            try {
              const paged = await chrome.tabs.sendMessage(tab.id, {
                type: "AUTO_PAGINATE",
                maxPages,
                delayMs: msg.delayMs || 1500,
              });
              if (paged?.ok && Array.isArray(paged.cards)) allCards = paged.cards;
            } catch (e) {
              console.warn("[JH] AUTO_PAGINATE failed, falling back to one page:", e);
            }
          }
          if (allCards.length === 0) {
            try {
              const listResult = await chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_LIST" });
              if (listResult?.cards) allCards = listResult.cards;
            } catch (e) {
              console.warn("[JH] EXTRACT_LIST failed:", e);
            }
          }

          if (allCards.length === 0) {
            sendResponse({ ok: false, error: "No jobs found on this page", totalCards: 0, deepScraped: 0, totalUrls: 0 });
            break;
          }

          // Step 3: Save all card snapshots
          const domain = tab.url ? new URL(tab.url).hostname : "";
          let saveCount = 0;
          for (const c of allCards) {
            try {
              await saveJob({
                ...emptyJob(),
                title: c.title || "", company: c.company || "", location: c.location || "",
                description: c.snippet || "", apply_url: c.url || "", job_url: c.url || "",
                source_domain: domain, source_ats: "full-pipeline", confidence: "0.50",
                scraped_at: new Date().toISOString(),
              } as Job);
              saveCount++;
            } catch {}
          }

          // Step 4: Start deep-scrape for each URL.
          // Per-host throttle (1 concurrent / 1.5s) is what matters; overall concurrency
          // up to 4 lets multi-host runs proceed in parallel.
          const urls = allCards.map((c: any) => c.url).filter(Boolean);
          let runId: number | null = null;
          if (urls.length > 0) {
            try {
              runId = await startCrawlRun(urls, {
                concurrency: msg.concurrency || 4,
                perHostConcurrency: msg.perHostConcurrency || 1,
                perHostDelayMs: msg.perHostDelayMs || 1500,
                timeoutMs: msg.timeoutMs || 45000,
                retries: msg.retries ?? 2,
              });
            } catch (e) {
              console.warn("[JH] deep-crawl would not start:", e);
            }
          }

          // `startCrawlRun` returns as soon as the run row exists; the crawl
          // itself is deliberately not awaited. Reporting `deepScraped =
          // urls.length` here told the operator "25/25 deep-scraped" before a
          // single tab had opened — and if every one of them then 403'd, they
          // had already been told the run succeeded. Report what is true now:
          // how many were queued, and the run to watch.
          sendResponse({
            ok: true,
            totalCards: saveCount,
            queued: urls.length,
            totalUrls: urls.length,
            runId,
            pages: maxPages,
          });
          break;
        }
        case "CRAWL_URLS": {
          const runId = await startCrawlRun(msg.urls || [], msg.options || {});
          sendResponse({ ok: true, runId });
          break;
        }
        case "RETRY_FAILED": {
          const runId = await retryFailed(msg.runId, msg.options || {});
          sendResponse({ ok: true, runId });
          break;
        }
        case "CANCEL_RUN": {
          const r = await db.runs.get(msg.runId);
          if (r) { r.status = "cancelled"; await db.runs.put(r); }
          sendResponse({ ok: true });
          break;
        }
        case "OPEN_SIDEPANEL": {
          const w = await chrome.windows.getCurrent();
          if (w.id != null) await (chrome as any).sidePanel?.open?.({ windowId: w.id });
          sendResponse({ ok: true });
          break;
        }
        default:
          sendResponse({ ok: false, reason: "unknown-type" });
      }
    } catch (e: any) {
      sendResponse({ ok: false, error: String(e?.message || e) });
    }
  })();
  return true;
});
