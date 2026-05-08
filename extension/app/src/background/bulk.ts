/**
 * bulk.ts — deep-crawl orchestrator.
 *
 * Visits URLs in hidden tabs and waits for the content script to fire JOB_FOUND.
 * Per-host concurrency cap + min-delay throttle prevents the rate-limit problem
 * (jobs.ch was returning 33 list rows but only ~18 deep-scraped because 3 tabs hit
 * the same host simultaneously and got soft-blocked / consent-walled).
 *
 * Design:
 *  - one global semaphore (overall concurrency)
 *  - one per-host queue (default 1 concurrent per host, 1500 ms delay between launches)
 *  - retry on timeout / extraction-empty up to 2x with exponential backoff
 *  - persist every failure to db.failures so the UI can re-run
 */
import { db, type Run, type Failure } from "../lib/db";
import type { Job } from "../lib/schema";
import { fingerprint } from "../lib/schema";
import type { GeneralRecord } from "../lib/generalSchema";
import { fingerprintRecord } from "../lib/generalSchema";

type Pending = {
  url: string;
  resolve: (job: Job | null) => void;
  reject: (err: Error) => void;
  timer: number;
};

type GeneralPending = {
  url: string;
  resolve: (rec: GeneralRecord | null) => void;
  reject: (err: Error) => void;
  timer: number;
};

type CrawlOpts = {
  concurrency?: number;       // overall (default 4)
  perHostConcurrency?: number; // per-host (default 1)
  perHostDelayMs?: number;     // min spacing between requests to same host (default 1500)
  timeoutMs?: number;          // per-URL hard timeout (default 45000)
  retries?: number;            // attempts after first failure (default 2)
};

const PENDING = new Map<number, Pending>();
const GENERAL_PENDING = new Map<number, GeneralPending>();

const DEFAULT_TIMEOUT_MS = 45000;
const DEFAULT_PER_HOST_DELAY_MS = 1500;
const DEFAULT_RETRIES = 2;

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function hostOf(u: string): string {
  try { return new URL(u).hostname; } catch { return u; }
}

export async function startCrawlRun(urls: string[], opts: CrawlOpts = {}) {
  const concurrency = clamp(opts.concurrency ?? 4, 1, 8);
  const perHostConcurrency = clamp(opts.perHostConcurrency ?? 1, 1, 4);
  const perHostDelayMs = Math.max(0, opts.perHostDelayMs ?? DEFAULT_PER_HOST_DELAY_MS);
  const timeoutMs = Math.max(10000, opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const retries = clamp(opts.retries ?? DEFAULT_RETRIES, 0, 5);

  const id = await db.runs.add({
    started_at: Date.now(),
    source_url: urls[0] || "",
    total: urls.length,
    done: 0,
    ok: 0,
    failed: 0,
    status: "running",
    type: "deep-crawl",
  });
  void runCrawl(id as number, urls, { concurrency, perHostConcurrency, perHostDelayMs, timeoutMs, retries });
  return id as number;
}

export async function retryFailed(runId: number, opts: CrawlOpts = {}) {
  const failures = await db.failures.where({ run_id: runId, resolved: 0 }).toArray();
  if (failures.length === 0) return null;
  const urls = Array.from(new Set(failures.map((f) => f.url)));
  return startCrawlRun(urls, opts);
}

function clamp(n: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, n)); }

async function runCrawl(runId: number, urls: string[], opts: Required<Omit<CrawlOpts, never>>) {
  const update = async (patch: Partial<Run>) => {
    const r = await db.runs.get(runId);
    if (!r) return;
    Object.assign(r, patch);
    await db.runs.put(r);
  };
  const incOk = async () => { const r = await db.runs.get(runId); if (r) { r.ok = (r.ok || 0) + 1; r.done = (r.done || 0) + 1; await db.runs.put(r); } };
  const incFail = async () => { const r = await db.runs.get(runId); if (r) { r.failed = (r.failed || 0) + 1; r.done = (r.done || 0) + 1; await db.runs.put(r); } };

  // Per-host queues
  const hostQueues = new Map<string, string[]>();
  const hostInflight = new Map<string, number>();
  const hostLastLaunch = new Map<string, number>();
  for (const u of urls) {
    const h = hostOf(u);
    if (!hostQueues.has(h)) hostQueues.set(h, []);
    hostQueues.get(h)!.push(u);
  }
  // Hosts with smaller queues finish first → interleave so global concurrency is well-used.
  const hostKeys = Array.from(hostQueues.keys());

  let globalInflight = 0;
  const inflightLimit = opts.concurrency;
  let cancelled = false;

  // Wait helper — releases when a slot frees up
  let resolveSlot: (() => void) | null = null;
  const waitSlot = () => new Promise<void>((res) => { resolveSlot = res; });
  const releaseSlot = () => { const r = resolveSlot; resolveSlot = null; if (r) r(); };

  const tryDequeueFor = (h: string): string | null => {
    const q = hostQueues.get(h);
    if (!q || q.length === 0) return null;
    if ((hostInflight.get(h) || 0) >= opts.perHostConcurrency) return null;
    const last = hostLastLaunch.get(h) || 0;
    if (Date.now() - last < opts.perHostDelayMs) return null;
    return q.shift()!;
  };

  const tryDequeueAny = (): { host: string; url: string } | null => {
    for (const h of hostKeys) {
      const u = tryDequeueFor(h);
      if (u) return { host: h, url: u };
    }
    return null;
  };

  const totalQueued = () => Array.from(hostQueues.values()).reduce((a, q) => a + q.length, 0);

  while ((totalQueued() > 0 || globalInflight > 0) && !cancelled) {
    const r = await db.runs.get(runId);
    if (r?.status === "cancelled") { cancelled = true; break; }

    // Launch as many as possible
    while (globalInflight < inflightLimit) {
      const next = tryDequeueAny();
      if (!next) break;
      globalInflight++;
      hostInflight.set(next.host, (hostInflight.get(next.host) || 0) + 1);
      hostLastLaunch.set(next.host, Date.now());
      void (async () => {
        try {
          const job = await visitWithRetry(runId, next.url, opts);
          if (job) {
            job.id = fingerprint(job);
            job.saved_at = Date.now();
            await db.jobs.put(job);
            await incOk();
          } else {
            await incFail();
          }
        } catch (e: any) {
          await incFail();
          await recordFailure(runId, next.url, String(e?.message || e));
        } finally {
          globalInflight--;
          hostInflight.set(next.host, Math.max(0, (hostInflight.get(next.host) || 0) - 1));
          releaseSlot();
        }
      })();
    }

    // Wait either for a slot to free up, or for a per-host delay to elapse.
    // Use min(perHostDelay - elapsed for any non-empty host) as the cap.
    if (globalInflight > 0 && totalQueued() === 0) {
      // All inflight; just wait for one to finish.
      await waitSlot();
    } else if (globalInflight === 0 && totalQueued() > 0) {
      // Nothing inflight but we couldn't launch — must be perHostDelay gating.
      let minWait = opts.perHostDelayMs;
      for (const h of hostKeys) {
        const q = hostQueues.get(h);
        if (!q || q.length === 0) continue;
        const last = hostLastLaunch.get(h) || 0;
        const wait = Math.max(0, opts.perHostDelayMs - (Date.now() - last));
        if (wait < minWait) minWait = wait;
      }
      await sleep(Math.max(50, minWait));
    } else {
      // Both — race a slot release against the smallest per-host delay.
      let minWait = opts.perHostDelayMs;
      for (const h of hostKeys) {
        const q = hostQueues.get(h);
        if (!q || q.length === 0) continue;
        if ((hostInflight.get(h) || 0) >= opts.perHostConcurrency) continue;
        const last = hostLastLaunch.get(h) || 0;
        const wait = Math.max(0, opts.perHostDelayMs - (Date.now() - last));
        if (wait < minWait) minWait = wait;
      }
      await Promise.race([waitSlot(), sleep(Math.max(50, minWait))]);
    }
  }

  await update({ status: cancelled ? "cancelled" : "done", finished_at: Date.now() });
}

async function visitWithRetry(runId: number, url: string, opts: { timeoutMs: number; retries: number }): Promise<Job | null> {
  let lastError: string = "";
  for (let attempt = 0; attempt <= opts.retries; attempt++) {
    try {
      const job = await visitAndExtract(url, opts.timeoutMs);
      if (job && (job.title || job.description)) {
        // Mark prior failures resolved if any
        await db.failures.where({ run_id: runId, url, resolved: 0 }).modify({ resolved: 1 });
        return job;
      }
      lastError = "no-content";
    } catch (e: any) {
      lastError = String(e?.message || e);
    }
    // exponential backoff, jittered
    if (attempt < opts.retries) {
      const base = 2000 * Math.pow(2, attempt);
      await sleep(base + Math.random() * 1000);
    }
  }
  await recordFailure(runId, url, lastError || "unknown");
  return null;
}

async function recordFailure(runId: number, url: string, reason: string) {
  const existing = await db.failures.where({ run_id: runId, url }).first();
  if (existing) {
    existing.attempts = (existing.attempts || 0) + 1;
    existing.last_attempt_at = Date.now();
    existing.reason = reason;
    existing.resolved = 0;
    await db.failures.put(existing);
  } else {
    const f: Failure = { run_id: runId, url, reason, attempts: 1, last_attempt_at: Date.now(), resolved: 0 };
    await db.failures.add(f);
  }
}

function visitAndExtract(url: string, timeoutMs: number): Promise<Job | null> {
  return new Promise((resolve, reject) => {
    chrome.tabs.create({ url, active: false }, (tab) => {
      if (chrome.runtime.lastError || !tab?.id) {
        reject(new Error(chrome.runtime.lastError?.message || "tab-create-failed"));
        return;
      }
      const tabId = tab.id;
      const timer = setTimeout(async () => {
        try { await chrome.tabs.remove(tabId); } catch {}
        PENDING.delete(tabId);
        reject(new Error("timeout"));
      }, timeoutMs) as unknown as number;
      PENDING.set(tabId, { url, resolve, reject, timer });
    });
  });
}

export function handleJobMessage(msg: any, sender: chrome.runtime.MessageSender) {
  const tabId = sender.tab?.id;
  if (!tabId) return;
  const entry = PENDING.get(tabId);
  if (!entry) return;
  if (msg?.type === "JOB_FOUND") {
    clearTimeout(entry.timer);
    PENDING.delete(tabId);
    chrome.tabs.remove(tabId).catch(() => {});
    entry.resolve(msg.job);
  }
}

// ---------------------------------------------------------------------------
// General-mode crawl (LocalBusiness/Restaurant/Place)
// ---------------------------------------------------------------------------

export async function startGeneralCrawlRun(urls: string[], opts: CrawlOpts = {}) {
  const concurrency = clamp(opts.concurrency ?? 4, 1, 8);
  const perHostConcurrency = clamp(opts.perHostConcurrency ?? 1, 1, 4);
  const perHostDelayMs = Math.max(0, opts.perHostDelayMs ?? DEFAULT_PER_HOST_DELAY_MS);
  const timeoutMs = Math.max(10000, opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const retries = clamp(opts.retries ?? DEFAULT_RETRIES, 0, 5);

  const id = await db.runs.add({
    started_at: Date.now(),
    source_url: urls[0] || "",
    total: urls.length,
    done: 0,
    ok: 0,
    failed: 0,
    status: "running",
    type: "general-scrape",
    mode: "general",
  });
  void runGeneralCrawl(id as number, urls, { concurrency, perHostConcurrency, perHostDelayMs, timeoutMs, retries });
  return id as number;
}

async function runGeneralCrawl(runId: number, urls: string[], opts: Required<Omit<CrawlOpts, never>>) {
  const update = async (patch: Partial<Run>) => {
    const r = await db.runs.get(runId);
    if (!r) return;
    Object.assign(r, patch);
    await db.runs.put(r);
  };
  const incOk = async () => { const r = await db.runs.get(runId); if (r) { r.ok = (r.ok || 0) + 1; r.done = (r.done || 0) + 1; await db.runs.put(r); } };
  const incFail = async () => { const r = await db.runs.get(runId); if (r) { r.failed = (r.failed || 0) + 1; r.done = (r.done || 0) + 1; await db.runs.put(r); } };

  const hostQueues = new Map<string, string[]>();
  const hostInflight = new Map<string, number>();
  const hostLastLaunch = new Map<string, number>();
  for (const u of urls) {
    const h = hostOf(u);
    if (!hostQueues.has(h)) hostQueues.set(h, []);
    hostQueues.get(h)!.push(u);
  }
  const hostKeys = Array.from(hostQueues.keys());

  let globalInflight = 0;
  let cancelled = false;
  let resolveSlot: (() => void) | null = null;
  const waitSlot = () => new Promise<void>((res) => { resolveSlot = res; });
  const releaseSlot = () => { const r = resolveSlot; resolveSlot = null; if (r) r(); };

  const tryDequeueFor = (h: string): string | null => {
    const q = hostQueues.get(h);
    if (!q || q.length === 0) return null;
    if ((hostInflight.get(h) || 0) >= opts.perHostConcurrency) return null;
    const last = hostLastLaunch.get(h) || 0;
    if (Date.now() - last < opts.perHostDelayMs) return null;
    return q.shift()!;
  };
  const tryDequeueAny = (): { host: string; url: string } | null => {
    for (const h of hostKeys) { const u = tryDequeueFor(h); if (u) return { host: h, url: u }; }
    return null;
  };
  const totalQueued = () => Array.from(hostQueues.values()).reduce((a, q) => a + q.length, 0);

  while ((totalQueued() > 0 || globalInflight > 0) && !cancelled) {
    const r = await db.runs.get(runId);
    if (r?.status === "cancelled") { cancelled = true; break; }
    while (globalInflight < opts.concurrency) {
      const next = tryDequeueAny();
      if (!next) break;
      globalInflight++;
      hostInflight.set(next.host, (hostInflight.get(next.host) || 0) + 1);
      hostLastLaunch.set(next.host, Date.now());
      void (async () => {
        try {
          const rec = await visitGeneralWithRetry(runId, next.url, opts);
          if (rec) {
            rec.id = fingerprintRecord(rec);
            rec.saved_at = Date.now();
            await db.records.put(rec);
            await incOk();
          } else {
            await incFail();
          }
        } catch (e: any) {
          await incFail();
          await recordFailure(runId, next.url, String(e?.message || e));
        } finally {
          globalInflight--;
          hostInflight.set(next.host, Math.max(0, (hostInflight.get(next.host) || 0) - 1));
          releaseSlot();
        }
      })();
    }
    if (globalInflight > 0 && totalQueued() === 0) {
      await waitSlot();
    } else if (globalInflight === 0 && totalQueued() > 0) {
      let minWait = opts.perHostDelayMs;
      for (const h of hostKeys) {
        const q = hostQueues.get(h); if (!q || q.length === 0) continue;
        const last = hostLastLaunch.get(h) || 0;
        const wait = Math.max(0, opts.perHostDelayMs - (Date.now() - last));
        if (wait < minWait) minWait = wait;
      }
      await sleep(Math.max(50, minWait));
    } else {
      let minWait = opts.perHostDelayMs;
      for (const h of hostKeys) {
        const q = hostQueues.get(h); if (!q || q.length === 0) continue;
        if ((hostInflight.get(h) || 0) >= opts.perHostConcurrency) continue;
        const last = hostLastLaunch.get(h) || 0;
        const wait = Math.max(0, opts.perHostDelayMs - (Date.now() - last));
        if (wait < minWait) minWait = wait;
      }
      await Promise.race([waitSlot(), sleep(Math.max(50, minWait))]);
    }
  }
  await update({ status: cancelled ? "cancelled" : "done", finished_at: Date.now() });
}

async function visitGeneralWithRetry(runId: number, url: string, opts: { timeoutMs: number; retries: number }): Promise<GeneralRecord | null> {
  let lastError = "";
  for (let attempt = 0; attempt <= opts.retries; attempt++) {
    try {
      const rec = await visitGeneral(url, opts.timeoutMs);
      if (rec && rec.name) {
        await db.failures.where({ run_id: runId, url, resolved: 0 }).modify({ resolved: 1 });
        return rec;
      }
      lastError = "no-content";
    } catch (e: any) {
      lastError = String(e?.message || e);
    }
    if (attempt < opts.retries) await sleep(2000 * Math.pow(2, attempt) + Math.random() * 1000);
  }
  await recordFailure(runId, url, lastError || "unknown");
  return null;
}

function visitGeneral(url: string, timeoutMs: number): Promise<GeneralRecord | null> {
  return new Promise((resolve, reject) => {
    chrome.tabs.create({ url, active: false }, (tab) => {
      if (chrome.runtime.lastError || !tab?.id) {
        reject(new Error(chrome.runtime.lastError?.message || "tab-create-failed"));
        return;
      }
      const tabId = tab.id;
      const timer = setTimeout(async () => {
        try { await chrome.tabs.remove(tabId); } catch {}
        GENERAL_PENDING.delete(tabId);
        reject(new Error("timeout"));
      }, timeoutMs) as unknown as number;
      GENERAL_PENDING.set(tabId, { url, resolve, reject, timer });
      // Trigger extraction shortly after page settles.
      setTimeout(async () => {
        try {
          const r = await chrome.tabs.sendMessage(tabId, { type: "EXTRACT_GENERAL" });
          const entry = GENERAL_PENDING.get(tabId);
          if (!entry) return;
          clearTimeout(entry.timer);
          GENERAL_PENDING.delete(tabId);
          chrome.tabs.remove(tabId).catch(() => {});
          entry.resolve(r?.record || null);
        } catch (e: any) {
          // entry will time out and reject
        }
      }, Math.min(8000, timeoutMs / 3));
    });
  });
}

export function handleGeneralMessage(msg: any, sender: chrome.runtime.MessageSender) {
  const tabId = sender.tab?.id;
  if (!tabId) return;
  const entry = GENERAL_PENDING.get(tabId);
  if (!entry) return;
  if (msg?.type === "RECORD_FOUND") {
    clearTimeout(entry.timer);
    GENERAL_PENDING.delete(tabId);
    chrome.tabs.remove(tabId).catch(() => {});
    entry.resolve(msg.record);
  }
}
