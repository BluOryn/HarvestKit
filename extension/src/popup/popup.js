import { JOB_FIELDS } from "../lib/schema.js";

const $ = (id) => document.getElementById(id);
const status = (msg) => { $("status").textContent = msg || ""; };

async function send(type, payload) {
  return new Promise((resolve) => chrome.runtime.sendMessage({ type, ...payload }, resolve));
}

async function renderList() {
  const r = await send("LIST_JOBS");
  const jobs = (r && r.jobs) || [];
  $("count").textContent = `${jobs.length} jobs`;
  const main = $("list");
  main.innerHTML = "";
  for (const job of jobs.slice().reverse()) {
    const row = document.createElement("div");
    row.className = "row-job";
    row.innerHTML = `
      <div class="meta">
        <div class="title">${esc(job.title || "(no title)")}</div>
        <div class="sub">${esc(job.company || "")} · ${esc(job.location || "")}</div>
        <div class="sub">
          ${job.salary_min || job.salary_max ? `<span class="tag">${esc(job.salary_currency || "")} ${esc(job.salary_min || "")}–${esc(job.salary_max || "")}</span>` : ""}
          ${job.remote_type ? `<span class="tag">${esc(job.remote_type)}</span>` : ""}
          ${job.source_ats ? `<span class="tag">${esc(job.source_ats)}</span>` : ""}
          ${job.recruiter_email ? `<span class="tag">📧 ${esc(job.recruiter_email)}</span>` : ""}
          ${job.tech_stack ? `<span class="tag">${esc(job.tech_stack.split(",").slice(0,4).join(", "))}</span>` : ""}
        </div>
        <div class="sub"><a href="${esc(job.job_url || "#")}" target="_blank">${esc(job.job_url || "")}</a></div>
      </div>
      <button class="x" data-fp="${esc(job.fingerprint)}">✕</button>
    `;
    row.querySelector(".x").addEventListener("click", async () => {
      await send("DELETE_JOB", { fingerprint: job.fingerprint });
      renderList();
    });
    main.appendChild(row);
  }
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toCSV(jobs) {
  const cols = ["fingerprint", ...JOB_FIELDS];
  const esc = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""').replace(/\r?\n/g, " ")}"`;
  const lines = [cols.join(",")];
  for (const j of jobs) lines.push(cols.map((c) => esc(j[c])).join(","));
  return lines.join("\r\n");
}

async function download(name, blob) {
  const url = URL.createObjectURL(blob);
  await chrome.downloads.download({ url, filename: name, saveAs: true });
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

async function settingsLoad() {
  const cfg = await chrome.storage.local.get(["autoSave", "showBanner"]);
  $("auto-save").checked = !!cfg.autoSave;
  $("show-banner").checked = cfg.showBanner !== false;
}

async function updatePageInfo() {
  const info = await send("GET_PAGE_INFO_ACTIVE");
  const pageInfo = $("page-info");
  if (!info || !info.ok) {
    pageInfo.textContent = "⚪ No page detected";
    return;
  }
  const parts = [];
  if (info.isJob) parts.push("📄 Job page");
  if (info.isList) parts.push(`📋 ${info.cardCount} jobs found`);
  if (info.pagination && info.pagination.type !== "none") {
    parts.push(`📄 Pagination: ${info.pagination.type}`);
  }
  pageInfo.textContent = parts.join(" | ") || "⚪ No job content detected";
}

document.addEventListener("DOMContentLoaded", async () => {
  await settingsLoad();
  await renderList();
  await updatePageInfo();

  $("auto-save").addEventListener("change", (e) => chrome.storage.local.set({ autoSave: e.target.checked }));
  $("show-banner").addEventListener("change", (e) => chrome.storage.local.set({ showBanner: e.target.checked }));

  // --- Extract single job from active tab ---
  $("btn-extract").addEventListener("click", async () => {
    status("Extracting…");
    const r = await send("EXTRACT_ACTIVE_TAB");
    if (r && r.ok && r.result && r.result.job) {
      status(`Saved: ${r.result.job.title || "Untitled"}`);
    } else {
      status("No job posting detected on this page.");
    }
    renderList();
  });

  // --- Save list snapshot ---
  $("btn-list").addEventListener("click", async () => {
    status("Scanning page for job cards…");
    const r = await send("EXTRACT_LIST_ACTIVE");
    const list = r && r.result;
    if (!list || !list.isList) { status("No list/cards detected on this page."); return; }
    const tab = (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    await send("SAVE_LIST", { cards: list.cards, source_domain: tab && new URL(tab.url).hostname });
    status(`Saved ${list.cards.length} card snapshots.`);
    renderList();
  });

  // --- Deep-scrape visible cards ---
  $("btn-deep").addEventListener("click", async () => {
    status("Scanning page for job cards…");
    const r = await send("EXTRACT_LIST_ACTIVE");
    const list = r && r.result;
    if (!list || !list.isList) { status("No list detected on this page."); return; }
    const urls = list.cards.map((c) => c.url);
    status(`Deep-crawling ${urls.length} job pages…`);
    const out = await send("CRAWL_URLS", { urls, options: { concurrency: 3 } });
    const ok = (out.results || []).filter((x) => x.ok).length;
    status(`Done. ${ok}/${urls.length} extracted with full fields.`);
    renderList();
  });

  // --- 🔥 FULL PIPELINE: Paginate + Collect + Deep-scrape ---
  $("btn-full").addEventListener("click", async () => {
    status("🚀 Starting full pipeline (paginate → collect → deep-scrape)…");
    $("btn-full").disabled = true;
    $("btn-full").textContent = "Scraping…";

    const out = await send("FULL_SCRAPE_ACTIVE", { maxPages: 10, concurrency: 3 });
    if (out && out.ok) {
      status(`🎉 Done! ${out.totalCards} jobs found, ${out.deepScraped}/${out.totalUrls} deep-scraped.`);
    } else {
      status(`❌ Pipeline failed: ${out && out.error || "unknown error"}`);
    }

    $("btn-full").disabled = false;
    $("btn-full").textContent = "🔥 Scrape Everything";
    renderList();
  });

  // --- Bulk URLs ---
  $("btn-bulk").addEventListener("click", () => {
    $("bulk").classList.toggle("hidden");
  });

  $("bulk-start").addEventListener("click", async () => {
    const urls = $("bulk-urls").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (!urls.length) { status("Paste URLs first."); return; }
    const concurrency = parseInt($("bulk-concurrency").value, 10) || 3;
    $("bulk-progress").textContent = `Starting ${urls.length} URLs…`;
    status("Crawling…");
    const r = await send("CRAWL_URLS", { urls, options: { concurrency } });
    const ok = (r.results || []).filter((x) => x.ok).length;
    $("bulk-progress").textContent = `Done. ${ok}/${urls.length} extracted.`;
    status("");
    renderList();
  });

  // --- Export ---
  $("btn-export-csv").addEventListener("click", async () => {
    const r = await send("LIST_JOBS");
    const blob = new Blob([toCSV(r.jobs || [])], { type: "text/csv" });
    download(`jobharvester-${Date.now()}.csv`, blob);
  });

  $("btn-export-json").addEventListener("click", async () => {
    const r = await send("LIST_JOBS");
    const blob = new Blob([JSON.stringify(r.jobs || [], null, 2)], { type: "application/json" });
    download(`jobharvester-${Date.now()}.json`, blob);
  });

  $("btn-clear").addEventListener("click", async () => {
    if (!confirm("Delete all saved jobs?")) return;
    await send("CLEAR_JOBS");
    renderList();
  });
});
