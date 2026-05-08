import { extract } from "./extractor";
import { detectJobPage } from "./detector";
import { pickSiteAdapter, genericListExtract, type SiteAdapter } from "./sites";
import { Paginator, detectPagination } from "./paginator";
import { expandAllContent, dismissOverlays } from "./expandContent";
import { smartExtractCards } from "./smartDetector";
import { extractGeneral, extractGeneralCards } from "./generalExtract";
import type { ListCard } from "../lib/messages";

const BANNER_ID = "__jh_banner__";
const STYLE_ID = "__jh_style__";
let activePaginator: Paginator | null = null;

// ---------------------------------------------------------------------------
// Banner styling
// ---------------------------------------------------------------------------
function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const css = `
  .__jh_banner{position:fixed;top:14px;right:14px;z-index:2147483647;
    background:linear-gradient(180deg,#0c0d10,#15171c);color:#e8eaed;
    padding:12px 14px;border:1px solid #262a33;border-radius:14px;
    font:13px/1.45 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    box-shadow:0 14px 40px rgba(0,0,0,.45);max-width:380px}
  .__jh_banner b{color:#fff}
  .__jh_banner .__jh_meta{opacity:.78;margin:4px 0 8px;font-size:12px}
  .__jh_btn{display:inline-block;margin:4px 6px 0 0;padding:7px 11px;border:0;border-radius:8px;
    cursor:pointer;font-weight:600;font-size:12px;transition:filter .15s}
  .__jh_btn.primary{background:#2a6df4;color:#fff}
  .__jh_btn.green{background:#23b07a;color:#fff}
  .__jh_btn.fire{background:linear-gradient(135deg,#f59e0b,#ef4444,#8b5cf6);color:#fff;font-size:13px;padding:9px 14px}
  .__jh_btn.gray{background:#22252c;color:#cbd2dc}
  .__jh_btn:hover{filter:brightness(1.12)}
  .__jh_close{background:transparent;color:#9aa0a6;border:0;cursor:pointer;float:right;font-size:14px;margin-left:6px}
  .__jh_progress{font-size:11px;color:#7eb8ff;margin-top:6px}
  `;
  const s = document.createElement("style");
  s.id = STYLE_ID; s.textContent = css;
  document.documentElement.appendChild(s);
}

function escapeHTML(s: any) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" } as any)[c]);
}

function showBanner(html: string, wire: (root: HTMLElement) => void) {
  injectStyle();
  document.getElementById(BANNER_ID)?.remove();
  const div = document.createElement("div");
  div.id = BANNER_ID;
  div.className = "__jh_banner";
  div.innerHTML = html;
  document.body.appendChild(div);
  wire(div);
}

function updateProgress(msg: string) {
  const el = document.getElementById("__jh_progress");
  if (el) el.textContent = msg;
}

// ---------------------------------------------------------------------------
// Card extraction (unified: adapter → generic → smart)
// ---------------------------------------------------------------------------
function listCards(): { adapter: SiteAdapter | null; cards: ListCard[] } {
  const adapter = pickSiteAdapter();
  let cards = adapter ? adapter.extract(document) : [];
  if (cards.length < 2) cards = genericListExtract(document);
  // Smart fallback: structural analysis when CSS selectors fail
  if (cards.length < 2) {
    const smart = smartExtractCards(document);
    if (smart.found) cards = smart.cards;
  }
  return { adapter, cards };
}

// ---------------------------------------------------------------------------
// Single job page flow
// ---------------------------------------------------------------------------
async function runDetail() {
  let r = extract();
  if (!r.job) {
    await new Promise((res) => setTimeout(res, 1500));
    r = extract();
  }
  if (!r.job || !r.job.title) return false;

  // Expand hidden content and re-extract for richer data
  const expanded = await expandAllContent(document);
  if (expanded > 0) r = extract();
  if (!r.job) return false;

  const job = r.job;
  chrome.runtime.sendMessage({ type: "JOB_FOUND", job, detection: r.detection });
  const cfg = await chrome.storage.local.get(["autoSave", "showBanner"]);
  if (cfg.autoSave) chrome.runtime.sendMessage({ type: "SAVE_JOB", job });
  if (cfg.showBanner === false) return true;

  const conf = String(((r.detection?.confidence ?? 0) * 100).toFixed(0));
  showBanner(
    `<button class="__jh_close" data-x>×</button>
     <b>JobHarvester</b> detected a job posting (${conf}%).<br/>
     <div class="__jh_meta">${escapeHTML(job.title || "Untitled")} · ${escapeHTML(job.company || "")}<br/>${escapeHTML(job.location || "")}</div>
     <button class="__jh_btn primary" data-save>Save to library</button>
     <button class="__jh_btn gray" data-open>Open side panel</button>`,
    (root) => {
      root.querySelector("[data-x]")?.addEventListener("click", () => root.remove());
      root.querySelector("[data-save]")?.addEventListener("click", () => {
        chrome.runtime.sendMessage({ type: "SAVE_JOB", job });
        root.remove();
      });
      root.querySelector("[data-open]")?.addEventListener("click", () => {
        chrome.runtime.sendMessage({ type: "OPEN_SIDEPANEL" });
      });
    }
  );
  return true;
}

// ---------------------------------------------------------------------------
// List page flow (with pagination)
// ---------------------------------------------------------------------------
async function runList() {
  const { adapter, cards } = listCards();
  if (cards.length < 2) return false;
  const cfg = await chrome.storage.local.get(["showBanner"]);
  if (cfg.showBanner === false) return true;

  const pag = detectPagination(document);
  const adapterTag = adapter ? `<span class="__jh_meta">${escapeHTML(adapter.name)}</span> ` : "";
  const pagTag = pag.type !== "none" ? `<br/><span class="__jh_meta">📄 Pagination: ${pag.type}</span>` : "";

  showBanner(
    `<button class="__jh_close" data-x>×</button>
     <b>JobHarvester</b> found <b>${cards.length}</b> listings on this page.
     ${adapterTag}${pagTag}
     <div class="__jh_meta">Save snapshot, deep-scrape every page, or scrape ALL pages.</div>
     <button class="__jh_btn primary" data-snap>Save snapshot</button>
     <button class="__jh_btn green" data-deep>Deep-scrape all</button>
     ${pag.type !== "none" ? '<button class="__jh_btn fire" data-paginate>🔥 Scrape ALL pages</button>' : ""}
     <button class="__jh_btn gray" data-open>Side panel</button>
     <div id="__jh_progress" class="__jh_progress"></div>`,
    (root) => {
      root.querySelector("[data-x]")?.addEventListener("click", () => {
        if (activePaginator) activePaginator.abort();
        root.remove();
      });
      root.querySelector("[data-snap]")?.addEventListener("click", () => {
        chrome.runtime.sendMessage({ type: "SAVE_LIST", cards, source_domain: location.hostname, source_url: location.href });
        updateProgress(`✅ Saved ${cards.length} job snapshots.`);
      });
      root.querySelector("[data-deep]")?.addEventListener("click", () => {
        chrome.runtime.sendMessage({ type: "CRAWL_URLS", urls: cards.map((c: any) => c.url), options: { concurrency: 3 } });
        updateProgress(`🔄 Deep-scraping ${cards.length} jobs in background…`);
      });
      root.querySelector("[data-paginate]")?.addEventListener("click", async () => {
        const btn = root.querySelector("[data-paginate]") as HTMLButtonElement;
        if (btn) { btn.disabled = true; btn.textContent = "Scraping…"; }
        await runPaginatedScrape();
      });
      root.querySelector("[data-open]")?.addEventListener("click", () => {
        chrome.runtime.sendMessage({ type: "OPEN_SIDEPANEL" });
      });
    }
  );
  return true;
}

// ---------------------------------------------------------------------------
// Full paginated scrape pipeline
// ---------------------------------------------------------------------------
async function runPaginatedScrape() {
  activePaginator = new Paginator({
    maxPages: 100,
    delayMs: 1800,
    onProgress: (p) => updateProgress(`📄 Page ${p.page}: ${p.status} | Total: ${p.total || 0} jobs`),
  });

  const allCards = await activePaginator.collectAll(() => listCards().cards);
  activePaginator = null;

  if (allCards.length === 0) {
    updateProgress("❌ No jobs found across pages.");
    return;
  }

  // Save snapshots
  await chrome.runtime.sendMessage({ type: "SAVE_LIST", cards: allCards, source_domain: location.hostname, source_url: location.href });
  updateProgress(`✅ Saved ${allCards.length} jobs from all pages. Deep-scraping…`);

  // Deep-scrape each. Per-host 1 concurrent + 1.5s delay = no rate-limit damage on jobs.ch.
  chrome.runtime.sendMessage({
    type: "CRAWL_URLS",
    urls: allCards.map((c) => c.url),
    options: { concurrency: 4, perHostConcurrency: 1, perHostDelayMs: 1500, timeoutMs: 45000, retries: 2 },
  });
  updateProgress(`🔄 Deep-scraping ${allCards.length} jobs in background tabs…`);
}

// ---------------------------------------------------------------------------
// Message handlers
// ---------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((msg: any, _s: any, sendResponse: any) => {
  if (msg?.type === "EXTRACT_NOW") {
    (async () => {
      await expandAllContent(document);
      sendResponse(extract());
    })();
    return true;
  }
  if (msg?.type === "DETECT_NOW") { sendResponse(detectJobPage()); return true; }
  if (msg?.type === "EXTRACT_LIST") {
    const { adapter, cards } = listCards();
    sendResponse({ ok: true, isList: cards.length >= 2, adapter: adapter?.name || "generic", cards });
    return true;
  }
  if (msg?.type === "AUTO_PAGINATE") {
    (async () => {
      try {
        const all = await new Paginator({ maxPages: msg.maxPages || 10, delayMs: msg.delayMs || 1500 })
          .collectAll(() => listCards().cards);
        sendResponse({ ok: true, cards: all, total: all.length });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    })();
    return true;
  }
  if (msg?.type === "EXPAND_CONTENT") {
    (async () => { sendResponse({ ok: true, expanded: await expandAllContent(document) }); })();
    return true;
  }
  if (msg?.type === "DISMISS_OVERLAYS") {
    (async () => { sendResponse({ ok: true, dismissed: await dismissOverlays(document) }); })();
    return true;
  }
  if (msg?.type === "EXTRACT_GENERAL") {
    (async () => {
      await dismissOverlays(document);
      await expandAllContent(document);
      sendResponse(extractGeneral());
    })();
    return true;
  }
  if (msg?.type === "EXTRACT_GENERAL_LIST") {
    sendResponse({ ok: true, cards: extractGeneralCards() });
    return true;
  }
  if (msg?.type === "GET_PAGE_INFO") {
    const pag = detectPagination(document);
    const { cards } = listCards();
    const det = detectJobPage();
    sendResponse({ ok: true, isJob: det.isJob, isList: cards.length >= 2, cardCount: cards.length, pagination: { type: pag.type, hasMore: pag.hasMore }, detection: det });
    return true;
  }
  return false;
});

// ---------------------------------------------------------------------------
// SPA observer
// ---------------------------------------------------------------------------
let lastHref = location.href;
const obs = new MutationObserver(() => {
  if (location.href !== lastHref) { lastHref = location.href; setTimeout(main, 1200); }
});
obs.observe(document.documentElement, { childList: true, subtree: true });

function waitForJobContent(timeoutMs = 8000): Promise<void> {
  return new Promise((resolve) => {
    const start = Date.now();
    const check = () => {
      if (document.querySelector('script[type="application/ld+json"]')) return resolve();
      if (document.querySelector('[itemtype*="schema.org/JobPosting" i]')) return resolve();
      const cards = document.querySelectorAll(
        "a[href*='/job/'], a[href*='/joboffer/'], a[href*='jobs/view'], a[href*='job-detail'], " +
        "article[data-at='job-item'], div.job_seen_beacon, [data-jk], [data-advert], li.jobs-search-results__list-item"
      );
      if (cards.length >= 3) return resolve();
      if (Date.now() - start > timeoutMs) return resolve();
      requestAnimationFrame(check);
    };
    check();
  });
}

async function main() {
  await dismissOverlays(document);
  await waitForJobContent();
  const handled = await runDetail();
  if (!handled) await runList();
}
main();

// Debug surface
(window as any).__JH = {
  detect: detectJobPage,
  extract: () => extract(),
  list: () => listCards(),
  paginate: (max = 5) => new Paginator({ maxPages: max }).collectAll(() => listCards().cards),
  expand: () => expandAllContent(document),
  dismiss: () => dismissOverlays(document),
  smart: () => smartExtractCards(document),
};
console.debug("[JobHarvester] content script loaded — use window.__JH.detect(), .extract(), .list(), .paginate(), .expand(), .smart()");
