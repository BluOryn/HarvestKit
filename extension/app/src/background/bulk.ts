/**
 * bulk.ts — deep-crawl orchestrator.
 *
 * Visits URLs in hidden tabs and waits for the content script to fire JOB_FOUND.
 * Per-host concurrency cap + min-delay throttle prevents the rate-limit problem
 * (jobs.ch was returning 33 list rows but only ~18 deep-scraped because 3 tabs hit
 * the same host simultaneously and got soft-blocked / consent-walled).
 *
 * Design:
 *  - scheduling lives in ./scheduler (shared by the job and general crawls)
 *  - the service worker is held awake for the run (see ./keepalive)
 *  - retry on timeout / extraction-empty up to N times with exponential backoff
 *  - every failure is persisted to db.failures so the UI can re-run it
 */
import { db, type Failure, type Run } from "../lib/db";
import { fingerprintRecord, type GeneralRecord } from "../lib/generalSchema";
import { fingerprint, type Job } from "../lib/schema";
import { withKeepalive } from "./keepalive";
import { runHostScheduled, sleep, type SchedulerOpts } from "./scheduler";

type Pending<T> = {
  url: string;
  resolve: (value: T | null) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

type CrawlOpts = {
  concurrency?: number; // overall (default 4)
  perHostConcurrency?: number; // per-host (default 1)
  perHostDelayMs?: number; // min spacing between requests to same host (default 1500)
  timeoutMs?: number; // per-URL hard timeout (default 45000)
  retries?: number; // attempts after first failure (default 2)
};

type ResolvedOpts = SchedulerOpts & { timeoutMs: number; retries: number };

const PENDING = new Map<number, Pending<Job>>();
const GENERAL_PENDING = new Map<number, Pending<GeneralRecord>>();

const DEFAULT_TIMEOUT_MS = 45000;
const DEFAULT_PER_HOST_DELAY_MS = 1500;
const DEFAULT_RETRIES = 2;

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}

function resolveOpts(opts: CrawlOpts): ResolvedOpts {
  return {
    concurrency: clamp(opts.concurrency ?? 4, 1, 8),
    perHostConcurrency: clamp(opts.perHostConcurrency ?? 1, 1, 4),
    perHostDelayMs: Math.max(0, opts.perHostDelayMs ?? DEFAULT_PER_HOST_DELAY_MS),
    timeoutMs: Math.max(10000, opts.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    retries: clamp(opts.retries ?? DEFAULT_RETRIES, 0, 5),
  };
}

/** De-duplicate the URL list up front — the same posting often appears on
 *  several listing pages, and each duplicate costs a whole tab + page load. */
function uniqueUrls(urls: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of urls) {
    const url = (raw || "").trim();
    if (!url || seen.has(url)) continue;
    seen.add(url);
    out.push(url);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Run bookkeeping
// ---------------------------------------------------------------------------

function makeCounters(runId: number) {
  // Serialise read-modify-write on the run row. Concurrent workers doing
  // get() → mutate → put() lost increments, so `done` under-reported.
  let chain: Promise<void> = Promise.resolve();
  const update = (mutate: (run: Run) => void) => {
    chain = chain.then(async () => {
      const run = await db.runs.get(runId);
      if (!run) return;
      mutate(run);
      await db.runs.put(run);
    });
    return chain;
  };
  return {
    ok: () =>
      update((r) => {
        r.ok = (r.ok || 0) + 1;
        r.done = (r.done || 0) + 1;
      }),
    fail: () =>
      update((r) => {
        r.failed = (r.failed || 0) + 1;
        r.done = (r.done || 0) + 1;
      }),
    patch: (p: Partial<Run>) => update((r) => Object.assign(r, p)),
    flush: () => chain,
  };
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
    const f: Failure = {
      run_id: runId,
      url,
      reason,
      attempts: 1,
      last_attempt_at: Date.now(),
      resolved: 0,
    };
    await db.failures.add(f);
  }
}

// ---------------------------------------------------------------------------
// Job crawl
// ---------------------------------------------------------------------------

export async function startCrawlRun(urls: string[], opts: CrawlOpts = {}) {
  const list = uniqueUrls(urls);
  const resolved = resolveOpts(opts);
  const id = (await db.runs.add({
    started_at: Date.now(),
    source_url: list[0] || "",
    total: list.length,
    done: 0,
    ok: 0,
    failed: 0,
    status: "running",
    type: "deep-crawl",
  })) as number;

  void withKeepalive(() => runCrawl(id, list, resolved));
  return id;
}

export async function retryFailed(runId: number, opts: CrawlOpts = {}) {
  const failures = await db.failures.where({ run_id: runId, resolved: 0 }).toArray();
  if (failures.length === 0) return null;
  return startCrawlRun(
    failures.map((f) => f.url),
    opts,
  );
}

async function runCrawl(runId: number, urls: string[], opts: ResolvedOpts) {
  const counters = makeCounters(runId);

  const { cancelled } = await runHostScheduled(
    urls,
    opts,
    async (url) => {
      try {
        const job = await visitWithRetry(runId, url, opts);
        if (job) {
          job.id = fingerprint(job);
          job.saved_at = Date.now();
          await db.jobs.put(job);
          await counters.ok();
        } else {
          await counters.fail();
        }
      } catch (e: any) {
        await counters.fail();
        await recordFailure(runId, url, String(e?.message || e));
      }
    },
    async () => (await db.runs.get(runId))?.status === "cancelled",
  );

  await counters.flush();
  await counters.patch({ status: cancelled ? "cancelled" : "done", finished_at: Date.now() });
}

async function visitWithRetry(
  runId: number,
  url: string,
  opts: { timeoutMs: number; retries: number },
): Promise<Job | null> {
  let lastError = "";
  for (let attempt = 0; attempt <= opts.retries; attempt++) {
    try {
      const job = await visitAndExtract(url, opts.timeoutMs);
      if (job && (job.title || job.description)) {
        await db.failures.where({ run_id: runId, url, resolved: 0 }).modify({ resolved: 1 });
        return job;
      }
      lastError = "no-content";
    } catch (e: any) {
      lastError = String(e?.message || e);
    }
    // Exponential backoff with jitter — skipped after the final attempt.
    if (attempt < opts.retries) {
      await sleep(2000 * 2 ** attempt + Math.random() * 1000);
    }
  }
  await recordFailure(runId, url, lastError || "unknown");
  return null;
}

// ---------------------------------------------------------------------------
// Hidden-tab visit
// ---------------------------------------------------------------------------

/**
 * Open `url` in a background tab and wait for its content script to report.
 *
 * The content script fires JOB_FOUND on its own, but only when its detector is
 * confident. We also poke it directly once the page has had time to settle, so
 * a page the detector scores just under threshold still gets extracted instead
 * of burning the full timeout.
 */
function visitInTab<T>(
  url: string,
  timeoutMs: number,
  registry: Map<number, Pending<T>>,
  pokeMessage: { type: string },
  readResult: (response: any) => T | null,
): Promise<T | null> {
  return new Promise<T | null>((resolve, reject) => {
    chrome.tabs.create({ url, active: false }, (tab) => {
      if (chrome.runtime.lastError || !tab?.id) {
        reject(new Error(chrome.runtime.lastError?.message || "tab-create-failed"));
        return;
      }
      const tabId = tab.id;

      const settle = (fn: () => void) => {
        const entry = registry.get(tabId);
        if (!entry) return false; // already settled elsewhere
        clearTimeout(entry.timer);
        registry.delete(tabId);
        chrome.tabs.remove(tabId).catch(() => {});
        fn();
        return true;
      };

      const timer = setTimeout(() => {
        settle(() => reject(new Error("timeout")));
      }, timeoutMs);

      registry.set(tabId, { url, resolve, reject, timer });

      // Nudge the content script once the page has had a chance to render.
      setTimeout(
        () => {
          if (!registry.has(tabId)) return;
          chrome.tabs
            .sendMessage(tabId, pokeMessage)
            .then((response) => {
              const value = readResult(response);
              if (value) settle(() => resolve(value));
            })
            .catch(() => {
              // Content script not ready (PDF, chrome:// redirect, still
              // loading). The timeout path handles it.
            });
        },
        Math.min(8000, Math.max(1500, timeoutMs / 3)),
      );
    });
  });
}

function visitAndExtract(url: string, timeoutMs: number): Promise<Job | null> {
  return visitInTab<Job>(url, timeoutMs, PENDING, { type: "EXTRACT_NOW" }, (r) =>
    r?.job && (r.job.title || r.job.description) ? (r.job as Job) : null,
  );
}

function visitGeneral(url: string, timeoutMs: number): Promise<GeneralRecord | null> {
  return visitInTab<GeneralRecord>(url, timeoutMs, GENERAL_PENDING, { type: "EXTRACT_GENERAL" }, (r) =>
    r?.record?.name ? (r.record as GeneralRecord) : null,
  );
}

function handlePush<T>(
  registry: Map<number, Pending<T>>,
  sender: chrome.runtime.MessageSender,
  value: T | null,
) {
  const tabId = sender.tab?.id;
  if (tabId == null) return;
  const entry = registry.get(tabId);
  if (!entry) return;
  clearTimeout(entry.timer);
  registry.delete(tabId);
  chrome.tabs.remove(tabId).catch(() => {});
  entry.resolve(value);
}

export function handleJobMessage(msg: any, sender: chrome.runtime.MessageSender) {
  if (msg?.type === "JOB_FOUND") handlePush(PENDING, sender, msg.job ?? null);
}

export function handleGeneralMessage(msg: any, sender: chrome.runtime.MessageSender) {
  if (msg?.type === "RECORD_FOUND") handlePush(GENERAL_PENDING, sender, msg.record ?? null);
}

// ---------------------------------------------------------------------------
// General-mode crawl (LocalBusiness/Restaurant/Place)
// ---------------------------------------------------------------------------

export async function startGeneralCrawlRun(urls: string[], opts: CrawlOpts = {}) {
  const list = uniqueUrls(urls);
  const resolved = resolveOpts(opts);
  const id = (await db.runs.add({
    started_at: Date.now(),
    source_url: list[0] || "",
    total: list.length,
    done: 0,
    ok: 0,
    failed: 0,
    status: "running",
    type: "general-scrape",
    mode: "general",
  })) as number;

  void withKeepalive(() => runGeneralCrawl(id, list, resolved));
  return id;
}

async function runGeneralCrawl(runId: number, urls: string[], opts: ResolvedOpts) {
  const counters = makeCounters(runId);

  const { cancelled } = await runHostScheduled(
    urls,
    opts,
    async (url) => {
      try {
        const record = await visitGeneralWithRetry(runId, url, opts);
        if (record) {
          record.id = fingerprintRecord(record);
          record.saved_at = Date.now();
          await db.records.put(record);
          await counters.ok();
        } else {
          await counters.fail();
        }
      } catch (e: any) {
        await counters.fail();
        await recordFailure(runId, url, String(e?.message || e));
      }
    },
    async () => (await db.runs.get(runId))?.status === "cancelled",
  );

  await counters.flush();
  await counters.patch({ status: cancelled ? "cancelled" : "done", finished_at: Date.now() });
}

async function visitGeneralWithRetry(
  runId: number,
  url: string,
  opts: { timeoutMs: number; retries: number },
): Promise<GeneralRecord | null> {
  let lastError = "";
  for (let attempt = 0; attempt <= opts.retries; attempt++) {
    try {
      const record = await visitGeneral(url, opts.timeoutMs);
      if (record?.name) {
        await db.failures.where({ run_id: runId, url, resolved: 0 }).modify({ resolved: 1 });
        return record;
      }
      lastError = "no-content";
    } catch (e: any) {
      lastError = String(e?.message || e);
    }
    if (attempt < opts.retries) await sleep(2000 * 2 ** attempt + Math.random() * 1000);
  }
  await recordFailure(runId, url, lastError || "unknown");
  return null;
}
