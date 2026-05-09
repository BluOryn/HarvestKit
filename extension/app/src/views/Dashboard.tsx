import React, { useMemo, useState, useEffect } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { db } from "../lib/db";
import { useUI } from "../stores/ui";
import { Send, MousePointerClick, Sparkles, Flame, Zap, Info } from "lucide-react";

export function Dashboard() {
  const jobs = useLiveQuery(() => db.jobs.toArray(), [], []);
  const records = useLiveQuery(() => db.records.toArray(), [], []);
  const runs = useLiveQuery(() => db.runs.orderBy("started_at").reverse().limit(8).toArray(), [], []);
  const setView = useUI((s) => s.setView);
  const mode = useUI((s) => s.mode);
  const toast = useUI((s) => s.toast);
  const [pageInfo, setPageInfo] = useState<any>(null);
  const [scraping, setScraping] = useState(false);

  // Fetch page info on mount + when mode changes
  useEffect(() => {
    if (mode === "general") {
      // Probe for general-mode list cards on the active tab
      chrome.runtime.sendMessage({ type: "EXTRACT_GENERAL_LIST_ACTIVE" }).then((r: any) => {
        const cards = r?.result?.cards || [];
        setPageInfo({ ok: true, mode: "general", isList: cards.length > 0, cardCount: cards.length });
      }).catch(() => {});
    } else {
      chrome.runtime.sendMessage({ type: "GET_PAGE_INFO_ACTIVE" }).then((r: any) => {
        if (r?.ok) setPageInfo({ ...r, mode: "jobs" });
      }).catch(() => {});
    }
  }, [mode]);

  const stats = useMemo(() => {
    const total = jobs?.length || 0;
    const today = jobs?.filter((j) => (j.saved_at || 0) > Date.now() - 86400000).length || 0;
    const week = jobs?.filter((j) => (j.saved_at || 0) > Date.now() - 7 * 86400000).length || 0;
    const remote = jobs?.filter((j) => (j.remote_type || "").toLowerCase() === "remote").length || 0;
    const withSalary = jobs?.filter((j) => j.salary_min || j.salary_max).length || 0;
    const withRecruiter = jobs?.filter((j) => j.recruiter_email || j.recruiter_linkedin).length || 0;
    const companies: Record<string, number> = {};
    const locations: Record<string, number> = {};
    const ats: Record<string, number> = {};
    for (const j of jobs || []) {
      if (j.company) companies[j.company] = (companies[j.company] || 0) + 1;
      if (j.location) locations[j.location] = (locations[j.location] || 0) + 1;
      if (j.source_ats) ats[j.source_ats] = (ats[j.source_ats] || 0) + 1;
    }
    const top = (m: Record<string, number>, n = 5) =>
      Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, n);
    return { total, today, week, remote, withSalary, withRecruiter, companies: top(companies), locations: top(locations), ats: top(ats) };
  }, [jobs]);

  async function scrapeThis() {
    const r = await chrome.runtime.sendMessage({ type: "EXTRACT_ACTIVE_TAB" });
    if (r?.ok && r.result?.job) {
      toast(`Saved: ${r.result.job.title || "Untitled"}`, "green");
      return;
    }
    const lr = await chrome.runtime.sendMessage({ type: "EXTRACT_LIST_ACTIVE" });
    const list = lr?.result;
    if (list?.cards?.length) {
      const tab = (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
      await chrome.runtime.sendMessage({
        type: "SAVE_LIST",
        cards: list.cards,
        source_domain: tab && new URL(tab.url || "").hostname,
        source_url: tab?.url,
      });
      toast(`No single posting; saved ${list.cards.length} list cards instead.`, "green");
      return;
    }
    toast("No job content detected on this page.", "red");
  }
  async function scrapeList() {
    const r = await chrome.runtime.sendMessage({ type: "EXTRACT_LIST_ACTIVE" });
    const list = r?.result;
    if (!list?.cards?.length) { toast("No list cards found.", "red"); return; }
    const tab = (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    await chrome.runtime.sendMessage({
      type: "SAVE_LIST",
      cards: list.cards,
      source_domain: tab && new URL(tab.url || "").hostname,
      source_url: tab?.url,
    });
    toast(`Saved ${list.cards.length} card snapshots.`, "green");
  }
  async function deepScrape() {
    const r = await chrome.runtime.sendMessage({ type: "EXTRACT_LIST_ACTIVE" });
    const list = r?.result;
    if (!list?.cards?.length) { toast("No list cards found.", "red"); return; }
    const urls = list.cards.map((c: any) => c.url);
    await chrome.runtime.sendMessage({
      type: "CRAWL_URLS",
      urls,
      options: { concurrency: 4, perHostConcurrency: 1, perHostDelayMs: 1500, timeoutMs: 45000, retries: 2 },
    });
    toast(`Deep crawl started for ${urls.length} URLs.`, "green");
    setView("runs");
  }

  async function scrapeGeneralPage() {
    const r = await chrome.runtime.sendMessage({ type: "EXTRACT_GENERAL_ACTIVE" });
    if (r?.ok && r.result?.record) {
      toast(`Saved: ${r.result.record.name || "Unnamed"}`, "green");
      return;
    }
    const lr = await chrome.runtime.sendMessage({ type: "EXTRACT_GENERAL_LIST_ACTIVE" });
    const cards = lr?.result?.cards || [];
    if (!cards.length) { toast("No business/place content detected.", "red"); return; }
    const urls = cards.map((c: any) => c.url).filter(Boolean);
    await chrome.runtime.sendMessage({
      type: "CRAWL_GENERAL_URLS",
      urls,
      options: { concurrency: 4, perHostConcurrency: 1, perHostDelayMs: 1500, timeoutMs: 45000, retries: 2 },
    });
    toast(`General deep-scrape started for ${urls.length} URLs.`, "green");
    setView("runs");
  }

  async function scrapeEverythingGeneral() {
    setScraping(true);
    toast("🔥 General mode: paginate → collect → deep-scrape…", "green");
    try {
      // Step 1: extract list cards from active tab
      const lr = await chrome.runtime.sendMessage({ type: "EXTRACT_GENERAL_LIST_ACTIVE" });
      const cards = lr?.result?.cards || [];
      if (!cards.length) {
        toast("No cards found on this page.", "red");
        return;
      }
      const urls = cards.map((c: any) => c.url).filter(Boolean);
      await chrome.runtime.sendMessage({
        type: "CRAWL_GENERAL_URLS",
        urls,
        options: { concurrency: 4, perHostConcurrency: 1, perHostDelayMs: 1500, timeoutMs: 45000, retries: 2 },
      });
      toast(`General deep-scrape started for ${urls.length} URLs.`, "green");
      setView("runs");
    } catch (e) {
      toast(`General scrape error: ${e}`, "red");
    } finally {
      setScraping(false);
    }
  }

  async function scrapeEverything() {
    setScraping(true);
    toast("🔥 Starting full pipeline: paginate → collect → deep-scrape…", "green");
    try {
      const r = await chrome.runtime.sendMessage({
        type: "FULL_SCRAPE_ACTIVE",
        maxPages: 100,
        concurrency: 4,
        perHostConcurrency: 1,
        perHostDelayMs: 1500,
        timeoutMs: 45000,
        retries: 2,
      });
      if (r?.ok) {
        toast(`🎉 Done! ${r.totalCards} jobs found, ${r.deepScraped}/${r.totalUrls} deep-scraped.`, "green");
      } else {
        toast(`Pipeline failed: ${r?.error || "unknown error"}`, "red");
      }
    } catch (e) {
      toast(`Pipeline error: ${e}`, "red");
    }
    setScraping(false);
  }

  return (
    <>
      {/* Live page info */}
      {pageInfo && (
        <div className="section" style={{paddingBottom: 4}}>
          <div style={{display:"flex",alignItems:"center",gap:6,fontSize:11,color:"var(--accent)",fontWeight:500}}>
            <Info size={12}/>
            {pageInfo.mode === "general" ? (
              pageInfo.isList
                ? `📋 ${pageInfo.cardCount} business listings found`
                : "No business listings detected on this page"
            ) : (
              <>
                {pageInfo.isJob && "📄 Job page detected"}
                {pageInfo.isList && `📋 ${pageInfo.cardCount} jobs found on this page`}
                {pageInfo.pagination?.type !== "none" && ` · 📄 Pagination: ${pageInfo.pagination.type}`}
                {!pageInfo.isJob && !pageInfo.isList && "No job content detected"}
              </>
            )}
          </div>
        </div>
      )}

      <div className="section">
        <h3>Quick actions {mode === "general" && <span className="meta" style={{fontSize:11,color:"var(--accent)"}}>· general mode</span>}</h3>
        <button
          className="btn fire"
          onClick={mode === "general" ? scrapeEverythingGeneral : scrapeEverything}
          disabled={scraping}
          style={{width:"100%",marginBottom:8,padding:"12px 16px",fontSize:14,fontWeight:700,borderRadius:10,background:"linear-gradient(135deg,#f59e0b,#ef4444,#8b5cf6)",border:0,color:"#fff",cursor:scraping?"wait":"pointer",opacity:scraping?.6:1}}
        >
          <Flame size={16}/>{scraping ? "Scraping…" : (mode === "general" ? "🔥 Scrape ALL listings" : "🔥 Scrape Everything")}
        </button>
        {mode === "jobs" ? (
          <div className="row-actions">
            <button className="btn primary" onClick={scrapeThis}><Send size={14}/>Scrape this page</button>
            <button className="btn" onClick={scrapeList}><MousePointerClick size={14}/>Snapshot list</button>
            <button className="btn green" onClick={deepScrape}><Sparkles size={14}/>Deep-scrape list</button>
          </div>
        ) : (
          <div className="row-actions">
            <button className="btn primary" onClick={scrapeGeneralPage}><Send size={14}/>Scrape this page</button>
            <button className="btn green" onClick={scrapeEverythingGeneral}><Sparkles size={14}/>Deep-scrape list</button>
          </div>
        )}
      </div>

      <div className="section">
        <h3>Library at a glance</h3>
        <div className="cards">
          <div className="card"><div className="label">Total jobs</div><div className="value">{stats.total}</div><div className="sub">{stats.week} this week · {stats.today} today</div></div>
          <div className="card"><div className="label">Remote</div><div className="value">{stats.remote}</div><div className="sub">{stats.total ? Math.round(100 * stats.remote / stats.total) : 0}% of library</div></div>
          <div className="card"><div className="label">With salary</div><div className="value">{stats.withSalary}</div><div className="sub">structured pay info</div></div>
          <div className="card"><div className="label">Recruiter contacts</div><div className="value">{stats.withRecruiter}</div><div className="sub">email or LinkedIn</div></div>
          <div className="card"><div className="label">Top company</div><div className="value" style={{fontSize:14, fontWeight:700}}>{stats.companies[0]?.[0] || "—"}</div><div className="sub">{stats.companies[0]?.[1] || 0} jobs</div></div>
          <div className="card"><div className="label">Top location</div><div className="value" style={{fontSize:14, fontWeight:700}}>{stats.locations[0]?.[0] || "—"}</div><div className="sub">{stats.locations[0]?.[1] || 0} jobs</div></div>
        </div>
      </div>

      <div className="section">
        <h3>Top companies</h3>
        {stats.companies.length === 0 ? <div className="empty">Nothing yet. Visit a job page or use a quick action above.</div> : (
          <div>{stats.companies.map(([c,n])=>(<div key={c} style={{display:"flex",justifyContent:"space-between",padding:"4px 0",borderBottom:"1px solid var(--line)"}}><span>{c}</span><span className="chip">{n}</span></div>))}</div>
        )}
      </div>

      <div className="section">
        <h3>Recent runs</h3>
        {(!runs || runs.length === 0) ? <div className="empty">No runs yet.</div> : (
          <div>{runs.map((r) => (
            <div key={r.id} style={{display:"grid",gridTemplateColumns:"1fr auto",alignItems:"center",padding:"8px 0",borderBottom:"1px solid var(--line)"}}>
              <div>
                <div style={{fontWeight:600}}>{r.type} · {r.status}</div>
                <div className="sub" style={{color:"var(--text-mute)",fontSize:11}}>{new Date(r.started_at).toLocaleString()}</div>
              </div>
              <div className="chip">{r.ok}/{r.total}</div>
            </div>
          ))}</div>
        )}
      </div>
    </>
  );
}
