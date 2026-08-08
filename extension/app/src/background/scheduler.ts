/**
 * Per-host work scheduler shared by the job crawl and the general crawl.
 *
 * Both crawlers previously carried their own byte-identical copy of this loop,
 * and both copies had the same lost-wakeup bug: a single `resolveSlot` slot
 * meant a task finishing between "check conditions" and "start waiting" had its
 * notification dropped, and a second waiter overwrote the first. This version
 * keeps a waiter list and a monotonically increasing signal counter, so a wake
 * that arrives before anyone waits is never lost.
 */

export type SchedulerOpts = {
  concurrency: number;
  perHostConcurrency: number;
  perHostDelayMs: number;
};

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function hostOf(u: string): string {
  try {
    return new URL(u).hostname;
  } catch {
    return u;
  }
}

class Signal {
  private waiters: Array<() => void> = [];
  private version = 0;

  /** Snapshot the current version; pass it to `wait` to detect missed signals. */
  snapshot(): number {
    return this.version;
  }

  /** Resolve immediately if a signal landed since `seen`. */
  wait(seen: number, timeoutMs: number): Promise<void> {
    if (this.version !== seen) return Promise.resolve();
    return new Promise<void>((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        this.waiters = this.waiters.filter((w) => w !== finish);
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(finish, Math.max(1, timeoutMs));
      this.waiters.push(finish);
    });
  }

  notify(): void {
    this.version++;
    const pending = this.waiters;
    this.waiters = [];
    for (const w of pending) w();
  }
}

/**
 * Run `handle` over `urls` with a global concurrency cap plus a per-host
 * in-flight cap and minimum spacing. Resolves when every URL has been handled
 * or `isCancelled()` returns true.
 */
export async function runHostScheduled(
  urls: string[],
  opts: SchedulerOpts,
  handle: (url: string) => Promise<void>,
  isCancelled: () => Promise<boolean>,
): Promise<{ cancelled: boolean }> {
  const queues = new Map<string, string[]>();
  for (const u of urls) {
    const h = hostOf(u);
    const q = queues.get(h);
    if (q) q.push(u);
    else queues.set(h, [u]);
  }
  const hostKeys = [...queues.keys()];
  const inflightPerHost = new Map<string, number>();
  const lastLaunch = new Map<string, number>();

  const signal = new Signal();
  let globalInflight = 0;
  let cancelled = false;

  const queuedTotal = () => {
    let n = 0;
    for (const q of queues.values()) n += q.length;
    return n;
  };

  const dequeue = (): { host: string; url: string } | null => {
    for (const h of hostKeys) {
      const q = queues.get(h);
      if (!q || q.length === 0) continue;
      if ((inflightPerHost.get(h) ?? 0) >= opts.perHostConcurrency) continue;
      if (Date.now() - (lastLaunch.get(h) ?? 0) < opts.perHostDelayMs) continue;
      return { host: h, url: q.shift()! };
    }
    return null;
  };

  /** Shortest wait until some host becomes launchable. */
  const nextReadyInMs = (): number => {
    let best = opts.perHostDelayMs;
    for (const h of hostKeys) {
      const q = queues.get(h);
      if (!q || q.length === 0) continue;
      if ((inflightPerHost.get(h) ?? 0) >= opts.perHostConcurrency) continue;
      const wait = Math.max(0, opts.perHostDelayMs - (Date.now() - (lastLaunch.get(h) ?? 0)));
      if (wait < best) best = wait;
    }
    return best;
  };

  while ((queuedTotal() > 0 || globalInflight > 0) && !cancelled) {
    if (await isCancelled()) {
      cancelled = true;
      break;
    }

    // Take a snapshot BEFORE launching, so a task that finishes during this
    // iteration bumps the version and the subsequent wait returns immediately.
    const seen = signal.snapshot();

    while (globalInflight < opts.concurrency) {
      const next = dequeue();
      if (!next) break;
      globalInflight++;
      inflightPerHost.set(next.host, (inflightPerHost.get(next.host) ?? 0) + 1);
      lastLaunch.set(next.host, Date.now());
      void handle(next.url)
        .catch(() => {
          /* handle() is responsible for its own error accounting */
        })
        .finally(() => {
          globalInflight--;
          inflightPerHost.set(next.host, Math.max(0, (inflightPerHost.get(next.host) ?? 0) - 1));
          signal.notify();
        });
    }

    if (queuedTotal() === 0 && globalInflight === 0) break;

    // Wake on either a finished task or the next host becoming launchable.
    const timeout = queuedTotal() > 0 ? Math.max(25, nextReadyInMs()) : 30_000;
    await signal.wait(seen, timeout);
  }

  // Let in-flight work settle so counters are final before the caller marks the
  // run "done". Bounded so a wedged tab can't hang the run forever.
  const settleDeadline = Date.now() + 5_000;
  while (globalInflight > 0 && Date.now() < settleDeadline) {
    await signal.wait(signal.snapshot(), 250);
  }

  return { cancelled };
}

export { sleep };
