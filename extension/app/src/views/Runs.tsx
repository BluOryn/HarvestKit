import React, { useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { db } from "../lib/db";
import { Square, Trash2, RefreshCw, ChevronDown, ChevronRight } from "lucide-react";

export function Runs() {
  const runs = useLiveQuery(() => db.runs.orderBy("started_at").reverse().toArray(), [], []) || [];
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  const cancel = async (id?: number) => {
    if (id == null) return;
    await chrome.runtime.sendMessage({ type: "CANCEL_RUN", runId: id });
  };
  const purge = async () => {
    if (!confirm("Delete all run records? (Job library is preserved.)")) return;
    await db.runs.clear();
    await db.failures.clear();
  };
  const retry = async (id?: number) => {
    if (id == null) return;
    await chrome.runtime.sendMessage({
      type: "RETRY_FAILED",
      runId: id,
      options: { concurrency: 4, perHostConcurrency: 1, perHostDelayMs: 1800, timeoutMs: 60000, retries: 2 },
    });
  };
  const toggle = (id?: number) => {
    if (id == null) return;
    setExpanded((p) => ({ ...p, [id]: !p[id] }));
  };

  return (
    <>
      <div className="section" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3>Runs</h3>
        <div className="row-actions">
          <button className="btn danger" onClick={purge}><Trash2 size={14}/></button>
        </div>
      </div>
      {runs.length === 0 ? <div className="empty">No runs yet. Start a deep crawl from the dashboard.</div> : (
        <div>
          {runs.map((r) => {
            const pct = r.total ? Math.round((r.done / r.total) * 100) : 0;
            const isLive = r.status === "running" || r.status === "queued";
            const id = r.id!;
            const isOpen = !!expanded[id];
            return (
              <div key={id} className="section" style={{ borderTop: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <button
                      className="btn ghost"
                      onClick={() => toggle(id)}
                      style={{ padding: 2, background: "transparent", border: 0, cursor: "pointer" }}
                      title={isOpen ? "Collapse failures" : "Expand failures"}
                    >
                      {isOpen ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}
                    </button>
                    <strong>{r.type}</strong>
                    <span className={`chip ${r.status === "done" ? "green" : r.status === "failed" ? "red" : r.status === "cancelled" ? "yellow" : "blue"}`}>{r.status}</span>
                  </div>
                  <div style={{ color: "var(--text-mute)", fontSize: 11 }}>
                    {new Date(r.started_at).toLocaleString()}
                  </div>
                </div>
                <div className="progress"><i style={{ width: `${pct}%` }} /></div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, color: "var(--text-dim)", fontSize: 12 }}>
                  <span>{r.done} / {r.total} · ✅ {r.ok} · ❌ {r.failed}</span>
                  <div className="row-actions" style={{ display: "flex", gap: 6 }}>
                    {r.failed > 0 && !isLive && (
                      <button className="btn ghost" onClick={() => retry(id)} title="Retry failed URLs">
                        <RefreshCw size={12}/> retry {r.failed}
                      </button>
                    )}
                    {isLive && (
                      <button className="btn ghost" onClick={() => cancel(id)}><Square size={12}/> cancel</button>
                    )}
                  </div>
                </div>
                {r.source_url && <div className="url" style={{ fontSize: 11, color: "var(--text-mute)", marginTop: 6, wordBreak: "break-all" }}>{r.source_url}</div>}
                {isOpen && <FailureList runId={id} />}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

function FailureList({ runId }: { runId: number }) {
  const failures = useLiveQuery(
    () => db.failures.where("run_id").equals(runId).reverse().sortBy("last_attempt_at"),
    [runId],
    []
  ) || [];
  if (failures.length === 0) {
    return <div className="empty" style={{ marginTop: 8, fontSize: 11 }}>No failure records.</div>;
  }
  return (
    <div style={{ marginTop: 8, padding: 8, background: "rgba(255,80,80,.04)", borderRadius: 6, border: "1px solid var(--line)" }}>
      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: "var(--text-dim)" }}>
        {failures.length} failed · {failures.filter((f) => f.resolved).length} resolved on retry
      </div>
      {failures.slice(0, 50).map((f) => (
        <div key={f.id} style={{ fontSize: 11, padding: "3px 0", borderBottom: "1px solid var(--line)", display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
          <a href={f.url} target="_blank" rel="noreferrer" style={{ color: f.resolved ? "var(--text-mute)" : "var(--accent)", textDecoration: f.resolved ? "line-through" : "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {f.url}
          </a>
          <span style={{ color: "var(--text-mute)" }}>{f.reason} · {f.attempts}x</span>
        </div>
      ))}
      {failures.length > 50 && <div style={{ fontSize: 11, color: "var(--text-mute)", marginTop: 4 }}>+ {failures.length - 50} more…</div>}
    </div>
  );
}
