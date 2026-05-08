// Bulk crawler: opens URLs in hidden tabs, waits for content script to extract,
// stores result, closes tab. Concurrency-limited.
// Enhanced: SPA-aware rendering, auto-expand descriptions, smart retry.

import { putJob } from "./store.js";

const PENDING = new Map(); // tabId -> { url, resolve, reject, timer, retried }
const DEFAULT_TIMEOUT_MS = 30000;
const EXPAND_DELAY_MS = 2000;

export async function crawl(urls, opts = {}) {
  const concurrency = Math.max(1, Math.min(opts.concurrency || 3, 8));
  const results = [];
  let cursor = 0;

  async function worker() {
    while (cursor < urls.length) {
      const idx = cursor++;
      const url = urls[idx];
      try {
        const job = await visitAndExtract(url, opts.timeoutMs || DEFAULT_TIMEOUT_MS);
        if (job) {
          await putJob(job);
          results.push({ url, ok: true, title: job.title });
        } else {
          results.push({ url, ok: false, reason: "no-job" });
        }
      } catch (e) {
        results.push({ url, ok: false, reason: String(e && e.message || e) });
      }
      if (opts.onProgress) opts.onProgress({ done: results.length, total: urls.length, latest: results[results.length - 1] });
    }
  }

  await Promise.all(Array.from({ length: concurrency }, worker));
  return results;
}

function visitAndExtract(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    chrome.tabs.create({ url, active: false }, (tab) => {
      if (chrome.runtime.lastError) return reject(chrome.runtime.lastError);
      const tabId = tab.id;

      const timer = setTimeout(async () => {
        const entry = PENDING.get(tabId);
        PENDING.delete(tabId);

        // Before giving up, try one last extraction
        try {
          const result = await chrome.tabs.sendMessage(tabId, { type: "EXTRACT_NOW" });
          if (result && result.job && result.job.title) {
            await chrome.tabs.remove(tabId).catch(() => {});
            resolve(result.job);
            return;
          }
        } catch {}

        try { await chrome.tabs.remove(tabId); } catch {}
        reject(new Error("timeout"));
      }, timeoutMs);

      PENDING.set(tabId, { url, resolve, reject, timer, retried: false });

      // After tab loads, trigger content expansion for richer extraction
      chrome.tabs.onUpdated.addListener(function listener(updatedTabId, changeInfo) {
        if (updatedTabId !== tabId || changeInfo.status !== "complete") return;
        chrome.tabs.onUpdated.removeListener(listener);

        // Give the page time to render SPA content, then expand + re-extract
        setTimeout(async () => {
          try {
            // Step 1: Dismiss overlays
            await chrome.tabs.sendMessage(tabId, { type: "DISMISS_OVERLAYS" }).catch(() => {});

            // Step 2: Expand collapsed content
            await chrome.tabs.sendMessage(tabId, { type: "EXPAND_CONTENT" }).catch(() => {});

            // Step 3: Wait a bit for expanded content
            await new Promise(r => setTimeout(r, 800));

            // Step 4: Force re-extraction (the content script auto-extracts,
            // but we want to ensure it runs AFTER expansion)
            const result = await chrome.tabs.sendMessage(tabId, { type: "EXTRACT_NOW" });
            if (result && result.job && result.job.title) {
              const entry = PENDING.get(tabId);
              if (entry) {
                clearTimeout(entry.timer);
                PENDING.delete(tabId);
                chrome.tabs.remove(tabId).catch(() => {});
                entry.resolve(result.job);
              }
            }
          } catch (e) {
            // Content script might not be ready yet — the JOB_FOUND message handler
            // will catch it when the auto-run fires
          }
        }, EXPAND_DELAY_MS);
      });
    });
  });
}

export function handleJobMessage(msg, sender) {
  const tabId = sender.tab && sender.tab.id;
  if (!tabId) return;
  const entry = PENDING.get(tabId);
  if (!entry) return;

  if (msg.type === "JOB_FOUND" && msg.job && msg.job.title) {
    clearTimeout(entry.timer);
    PENDING.delete(tabId);
    chrome.tabs.remove(tabId).catch(() => {});
    entry.resolve(msg.job);
  }
}
