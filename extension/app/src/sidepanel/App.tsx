import React, { useEffect } from "react";
import { LayoutDashboard, Database, Activity, Settings as Cog, ChevronDown, Search, Sparkles } from "lucide-react";
import { useUI } from "../stores/ui";
import { Dashboard } from "../views/Dashboard";
import { Library } from "../views/Library";
import { Runs } from "../views/Runs";
import { Settings } from "../views/Settings";
import { Toasts } from "../components/Toasts";
import { JobDrawer } from "../components/JobDrawer";

export function App() {
  const { view, setView, mode, setMode } = useUI();
  useEffect(() => {
    function key(e: KeyboardEvent) {
      if ((e.target as HTMLElement)?.tagName === "INPUT" || (e.target as HTMLElement)?.tagName === "TEXTAREA") return;
      if (e.key === "1") setView("dashboard");
      if (e.key === "2") setView("library");
      if (e.key === "3") setView("runs");
      if (e.key === "4") setView("settings");
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="logo">JH</div>
        <div>
          <div className="title">JobHarvester</div>
          <div className="meta" style={{ marginLeft: 0, fontSize: 11 }}>v2.1 — local-first</div>
        </div>
        <div style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
          <button
            onClick={() => setMode("jobs")}
            className={mode === "jobs" ? "btn primary" : "btn ghost"}
            style={{ padding: "4px 10px", fontSize: 11 }}
            title="Job-posting mode"
          >Jobs</button>
          <button
            onClick={() => setMode("general")}
            className={mode === "general" ? "btn primary" : "btn ghost"}
            style={{ padding: "4px 10px", fontSize: 11 }}
            title="General scrape (businesses, places, listings)"
          >General</button>
        </div>
      </header>
      <nav className="tabs" role="tablist">
        <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}><LayoutDashboard size={14}/>Dashboard</button>
        <button className={view === "library" ? "active" : ""} onClick={() => setView("library")}><Database size={14}/>Library</button>
        <button className={view === "runs" ? "active" : ""} onClick={() => setView("runs")}><Activity size={14}/>Runs</button>
        <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}><Cog size={14}/>Settings</button>
      </nav>
      <main className="view">
        {view === "dashboard" && <Dashboard />}
        {view === "library" && <Library />}
        {view === "runs" && <Runs />}
        {view === "settings" && <Settings />}
      </main>
      <JobDrawer />
      <Toasts />
    </div>
  );
}
