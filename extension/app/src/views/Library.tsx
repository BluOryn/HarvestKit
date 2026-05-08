import React, { useMemo, useRef, useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { useVirtualizer } from "@tanstack/react-virtual";
import { db } from "../lib/db";
import { useUI } from "../stores/ui";
import { Star, Trash2, Download, FileJson, Filter, FileSpreadsheet } from "lucide-react";
import type { Job } from "../lib/schema";
import type { GeneralRecord } from "../lib/generalSchema";
import { toCSV, toGeneralCSV, toNDJSON, downloadBlob } from "../lib/export";

type SortKey = "saved_at" | "title" | "company" | "location";
const SOURCES = ["", "list-page", "greenhouse", "lever", "ashby", "personio", "workday", "smartrecruiters", "linkedin", "jobs.ch"];

export function Library() {
  const mode = useUI((s) => s.mode);
  return mode === "general" ? <GeneralLibrary /> : <JobsLibrary />;
}

function JobsLibrary() {
  const allJobs = useLiveQuery(() => db.jobs.toArray(), [], []) || [];
  const selectJob = useUI((s) => s.selectJob);
  const toast = useUI((s) => s.toast);

  const [q, setQ] = useState("");
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [sourceFilter, setSourceFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("saved_at");
  const [starredOnly, setStarredOnly] = useState(false);

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase();
    let out = allJobs.filter((j) => {
      if (remoteOnly && (j.remote_type || "").toLowerCase() !== "remote") return false;
      if (sourceFilter && j.source_ats !== sourceFilter) return false;
      if (starredOnly && !j.starred) return false;
      if (!ql) return true;
      return (
        (j.title || "").toLowerCase().includes(ql) ||
        (j.company || "").toLowerCase().includes(ql) ||
        (j.location || "").toLowerCase().includes(ql) ||
        (j.tech_stack || "").toLowerCase().includes(ql) ||
        (j.description || "").toLowerCase().includes(ql)
      );
    });
    out.sort((a, b) => {
      if (sortKey === "saved_at") return (b.saved_at || 0) - (a.saved_at || 0);
      const av = String((a as any)[sortKey] || "").toLowerCase();
      const bv = String((b as any)[sortKey] || "").toLowerCase();
      return av < bv ? -1 : av > bv ? 1 : 0;
    });
    return out;
  }, [allJobs, q, remoteOnly, sourceFilter, sortKey, starredOnly]);

  const parentRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 78,
    overscan: 8,
  });

  async function toggleStar(j: Job) {
    j.starred = !j.starred;
    await db.jobs.put(j);
  }
  async function remove(j: Job) {
    if (!j.id) return;
    await db.jobs.delete(j.id);
  }
  async function exportCSV() {
    const data = filtered.length ? filtered : allJobs;
    const blob = new Blob([toCSV(data)], { type: "text/csv;charset=utf-8" });
    await downloadBlob(`jobharvester-${Date.now()}.csv`, blob);
    toast(`Exported ${data.length} rows.`, "green");
  }
  async function exportJSON() {
    const data = filtered.length ? filtered : allJobs;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    await downloadBlob(`jobharvester-${Date.now()}.json`, blob);
    toast(`Exported ${data.length} rows.`, "green");
  }
  async function exportNDJSON() {
    const data = filtered.length ? filtered : allJobs;
    const blob = new Blob([toNDJSON(data)], { type: "application/x-ndjson" });
    await downloadBlob(`jobharvester-${Date.now()}.ndjson`, blob);
    toast(`Exported ${data.length} rows.`, "green");
  }
  async function clearAll() {
    if (!confirm(`Delete all ${allJobs.length} jobs?`)) return;
    await db.jobs.clear();
    toast("Library cleared.", "green");
  }

  return (
    <>
      <div className="search-bar">
        <input className="input" placeholder="Search title, company, location, skills, description…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" style={{ width: 130 }} value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
          {SOURCES.map((s) => <option key={s} value={s}>{s || "any source"}</option>)}
        </select>
        <select className="input" style={{ width: 130 }} value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
          <option value="saved_at">newest</option>
          <option value="title">title</option>
          <option value="company">company</option>
          <option value="location">location</option>
        </select>
      </div>
      <div className="section" style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"8px 14px" }}>
        <div className="row-actions" style={{flexWrap:"wrap"}}>
          <button className={`btn ${remoteOnly ? "primary" : ""}`} onClick={() => setRemoteOnly(!remoteOnly)}><Filter size={14}/> Remote only</button>
          <button className={`btn ${starredOnly ? "primary" : ""}`} onClick={() => setStarredOnly(!starredOnly)}><Star size={14}/> Starred</button>
          <span className="chip">{filtered.length} / {allJobs.length}</span>
        </div>
        <div className="row-actions">
          <button className="btn" onClick={exportCSV}><Download size={14}/>CSV</button>
          <button className="btn" onClick={exportJSON}><FileJson size={14}/>JSON</button>
          <button className="btn" onClick={exportNDJSON}><FileSpreadsheet size={14}/>NDJSON</button>
          <button className="btn danger" onClick={clearAll}><Trash2 size={14}/></button>
        </div>
      </div>
      <div ref={parentRef} className="list" style={{ overflow: "auto", height: "calc(100% - 56px - 56px)" }}>
        {filtered.length === 0 ? <div className="empty">Nothing matches. Adjust filters or scrape some pages.</div> : (
          <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
            {rowVirtualizer.getVirtualItems().map((vi) => {
              const j = filtered[vi.index];
              return (
                <div key={j.id} className="row" style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${vi.start}px)`, height: vi.size }}
                     onClick={() => selectJob(j.id || null)}>
                  <div>
                    <div className="title">{j.title || "(no title)"}</div>
                    <div className="sub">
                      {j.company && <span>{j.company}</span>}
                      {j.location && <span>· {j.location}</span>}
                      {(j.salary_min || j.salary_max) && <span className="chip blue">{j.salary_currency || "€"} {j.salary_min || ""}–{j.salary_max || ""}</span>}
                      {j.remote_type && <span className="chip green">{j.remote_type}</span>}
                      {j.source_ats && <span className="chip">{j.source_ats}</span>}
                      {j.recruiter_email && <span className="chip purple">📧 {j.recruiter_email}</span>}
                      {j.tech_stack && <span className="chip yellow">{j.tech_stack.split(",").slice(0,3).join(", ")}</span>}
                    </div>
                    <div className="url">{j.job_url}</div>
                  </div>
                  <div className="actions" onClick={(e) => e.stopPropagation()}>
                    <button className={`btn icon ghost ${j.starred ? "star on" : "star"}`} title="Star" onClick={() => toggleStar(j)}><Star size={14} /></button>
                    <button className="btn icon ghost" title="Delete" onClick={() => remove(j)}><Trash2 size={14} /></button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

function GeneralLibrary() {
  const allRecords = useLiveQuery(() => db.records.toArray(), [], []) || [];
  const toast = useUI((s) => s.toast);
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase();
    let out = allRecords.filter((r) => {
      if (!ql) return true;
      return (
        (r.name || "").toLowerCase().includes(ql) ||
        (r.address || "").toLowerCase().includes(ql) ||
        (r.category || "").toLowerCase().includes(ql) ||
        (r.description || "").toLowerCase().includes(ql)
      );
    });
    out.sort((a, b) => (b.saved_at || 0) - (a.saved_at || 0));
    return out;
  }, [allRecords, q]);

  const parentRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 78,
    overscan: 8,
  });

  async function remove(r: GeneralRecord) {
    if (!r.id) return;
    await db.records.delete(r.id);
  }
  async function exportCSV() {
    const data = filtered.length ? filtered : allRecords;
    const blob = new Blob([toGeneralCSV(data)], { type: "text/csv;charset=utf-8" });
    await downloadBlob(`general-${Date.now()}.csv`, blob);
    toast(`Exported ${data.length} rows.`, "green");
  }
  async function exportJSON() {
    const data = filtered.length ? filtered : allRecords;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    await downloadBlob(`general-${Date.now()}.json`, blob);
    toast(`Exported ${data.length} rows.`, "green");
  }
  async function clearAll() {
    if (!confirm(`Delete all ${allRecords.length} records?`)) return;
    await db.records.clear();
    toast("Records cleared.", "green");
  }

  return (
    <>
      <div className="search-bar">
        <input className="input" placeholder="Search name, address, category, description…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="section" style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"8px 14px" }}>
        <div className="row-actions"><span className="chip">{filtered.length} / {allRecords.length}</span></div>
        <div className="row-actions">
          <button className="btn" onClick={exportCSV}><Download size={14}/>CSV</button>
          <button className="btn" onClick={exportJSON}><FileJson size={14}/>JSON</button>
          <button className="btn danger" onClick={clearAll}><Trash2 size={14}/></button>
        </div>
      </div>
      <div ref={parentRef} className="list" style={{ overflow: "auto", height: "calc(100% - 56px - 56px)" }}>
        {filtered.length === 0 ? <div className="empty">No records yet. Switch on a Yelp-style page and hit "Scrape ALL listings".</div> : (
          <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
            {rowVirtualizer.getVirtualItems().map((vi) => {
              const r = filtered[vi.index];
              return (
                <div key={r.id} className="row" style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${vi.start}px)`, height: vi.size }}>
                  <div>
                    <div className="title">{r.name || "(no name)"}</div>
                    <div className="sub">
                      {r.category && <span className="chip">{r.category.split(",")[0]}</span>}
                      {r.address && <span>· {r.address}</span>}
                      {r.rating && <span className="chip yellow">★ {r.rating}{r.review_count ? ` (${r.review_count})` : ""}</span>}
                      {r.phone && <span className="chip green">📞 {r.phone}</span>}
                      {r.price_range && <span className="chip blue">{r.price_range}</span>}
                    </div>
                    <div className="url">{r.source_url}</div>
                  </div>
                  <div className="actions">
                    <button className="btn icon ghost" title="Delete" onClick={() => remove(r)}><Trash2 size={14} /></button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
