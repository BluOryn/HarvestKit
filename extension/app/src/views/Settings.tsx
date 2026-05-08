import React, { useEffect, useState } from "react";

type Cfg = { autoSave: boolean; showBanner: boolean; theme: "dark" | "light"; webhookUrl?: string; openaiKey?: string };

export function Settings() {
  const [cfg, setCfg] = useState<Cfg>({ autoSave: false, showBanner: true, theme: "dark" });
  useEffect(() => {
    chrome.storage.local.get(["autoSave","showBanner","theme","webhookUrl","openaiKey"]).then((v) => setCfg({
      autoSave: !!v.autoSave, showBanner: v.showBanner !== false, theme: (v.theme as any) || "dark",
      webhookUrl: v.webhookUrl || "", openaiKey: v.openaiKey || "",
    }));
  }, []);

  const update = async (patch: Partial<Cfg>) => {
    const next = { ...cfg, ...patch };
    setCfg(next);
    await chrome.storage.local.set(next as any);
  };

  return (
    <>
      <div className="section">
        <h3>Detection & UX</h3>
        <label style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 0" }}>
          <input type="checkbox" checked={cfg.autoSave} onChange={(e) => update({ autoSave: e.target.checked })} />
          <div>
            <div style={{ fontWeight: 600 }}>Auto-save when detected</div>
            <div style={{ color: "var(--text-mute)", fontSize: 12 }}>Save jobs silently without clicking the in-page banner.</div>
          </div>
        </label>
        <label style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 0" }}>
          <input type="checkbox" checked={cfg.showBanner} onChange={(e) => update({ showBanner: e.target.checked })} />
          <div>
            <div style={{ fontWeight: 600 }}>Show in-page banner</div>
            <div style={{ color: "var(--text-mute)", fontSize: 12 }}>Discrete top-right banner on detected pages.</div>
          </div>
        </label>
      </div>
      <div className="section">
        <h3>Integrations</h3>
        <label style={{ display: "block", padding: "6px 0" }}>
          <div style={{ fontWeight: 600 }}>Webhook URL (optional)</div>
          <div style={{ color: "var(--text-mute)", fontSize: 12, marginBottom: 6 }}>Future: POST every saved job to your endpoint.</div>
          <input className="input" placeholder="https://example.com/jobharvester" value={cfg.webhookUrl || ""} onChange={(e) => update({ webhookUrl: e.target.value })} />
        </label>
        <label style={{ display: "block", padding: "6px 0" }}>
          <div style={{ fontWeight: 600 }}>OpenAI API key (optional)</div>
          <div style={{ color: "var(--text-mute)", fontSize: 12, marginBottom: 6 }}>Used to enrich ambiguous postings (seniority, role family, normalized skills).</div>
          <input className="input" type="password" placeholder="sk-…" value={cfg.openaiKey || ""} onChange={(e) => update({ openaiKey: e.target.value })} />
        </label>
      </div>
      <div className="section">
        <h3>Keyboard shortcuts</h3>
        <div style={{ color: "var(--text-dim)", fontSize: 12.5 }}>
          <div><span className="kbd">1</span> Dashboard · <span className="kbd">2</span> Library · <span className="kbd">3</span> Runs · <span className="kbd">4</span> Settings</div>
        </div>
      </div>
    </>
  );
}
