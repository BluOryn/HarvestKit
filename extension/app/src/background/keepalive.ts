/**
 * Keeps the MV3 service worker alive while a crawl is running.
 *
 * Chrome tears an idle service worker down after ~30 s. `runCrawl` is a
 * long-lived async loop living entirely in the worker, so a multi-minute
 * deep-crawl was being killed part-way through: tabs stayed open, the run row
 * stayed "running" forever, and the remaining URLs were silently dropped.
 *
 * `chrome.alarms` is the supported way to wake a worker back up (the `alarms`
 * permission is already declared in manifest.json). A ≤1-minute alarm plus a
 * periodic extension-API call resets the idle timer for as long as work is
 * outstanding, and everything is torn down the moment the last job finishes.
 */

const ALARM_NAME = "jh-keepalive";
// Chrome clamps `periodInMinutes` to a 30-second floor, so this is the shortest
// period that is honoured rather than silently rounded up.
const ALARM_PERIOD_MINUTES = 0.5;
// Any extension-API round trip resets the 30 s idle timer.
const PING_INTERVAL_MS = 20_000;

let activeHolds = 0;
let pingTimer: ReturnType<typeof setInterval> | null = null;

function ping() {
  // Cheap, always-available API call. Errors are ignored: the point is the
  // round trip, not the result.
  chrome.runtime.getPlatformInfo().catch(() => {});
}

function start() {
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: ALARM_PERIOD_MINUTES });
  if (pingTimer == null) pingTimer = setInterval(ping, PING_INTERVAL_MS);
  ping();
}

function stop() {
  chrome.alarms.clear(ALARM_NAME).catch(() => {});
  if (pingTimer != null) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
}

/** Register the alarm listener once, at worker startup. */
export function installKeepaliveListener() {
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name !== ALARM_NAME) return;
    // Waking up is the whole job. If nothing is holding, clear the alarm so a
    // worker restarted for an unrelated reason doesn't keep itself alive.
    if (activeHolds === 0) stop();
  });
}

/**
 * Hold the worker awake for the duration of `work`. Nestable: concurrent crawls
 * share one alarm and it only clears when the last one finishes.
 */
export async function withKeepalive<T>(work: () => Promise<T>): Promise<T> {
  if (activeHolds === 0) start();
  activeHolds++;
  try {
    return await work();
  } finally {
    activeHolds--;
    if (activeHolds === 0) stop();
  }
}
