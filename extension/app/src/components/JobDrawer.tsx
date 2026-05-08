import React from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { db } from "../lib/db";
import { useUI } from "../stores/ui";
import { ExternalLink, X, Star, Tag, FileText } from "lucide-react";
import { JOB_FIELDS } from "../lib/schema";

export function JobDrawer() {
  const id = useUI((s) => s.selectedJobId);
  const close = () => useUI.getState().selectJob(null);
  const job = useLiveQuery(async () => (id ? await db.jobs.get(id) : undefined), [id], undefined);

  if (!id || !job) return null;

  async function toggleStar() {
    if (!job) return;
    job.starred = !job.starred;
    await db.jobs.put(job);
  }

  return (
    <>
      <div className="drawer-backdrop" onClick={close} />
      <aside className="drawer">
        <header>
          <div style={{ flex: 1 }}>
            <h2>{job.title || "(no title)"}</h2>
            <div className="sub" style={{ color: "var(--text-dim)", marginTop: 4 }}>
              {job.company} {job.location ? `· ${job.location}` : ""}
            </div>
            <div className="row-actions" style={{ marginTop: 10 }}>
              {job.apply_url && <a className="btn primary" href={job.apply_url} target="_blank" rel="noreferrer"><ExternalLink size={14}/>Apply</a>}
              {job.job_url && <a className="btn" href={job.job_url} target="_blank" rel="noreferrer"><ExternalLink size={14}/>Job page</a>}
              <button className={`btn ${job.starred ? "primary" : ""}`} onClick={toggleStar}><Star size={14}/>{job.starred ? "Starred" : "Star"}</button>
            </div>
          </div>
          <button className="btn icon ghost" onClick={close}><X size={16}/></button>
        </header>

        <section className="grid">
          {JOB_FIELDS.filter((f) => f !== "description" && f !== "raw_jsonld" && (job as any)[f]).map((f) => (
            <React.Fragment key={f}>
              <div className="k">{f}</div>
              <div className="v">{String((job as any)[f])}</div>
            </React.Fragment>
          ))}
        </section>

        {job.description && (
          <>
            <div className="section" style={{ paddingBottom: 0 }}><h3>Description</h3></div>
            <div className="desc">{job.description}</div>
          </>
        )}
      </aside>
    </>
  );
}
