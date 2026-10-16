"use client";

import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import AuthPanel from "./auth-panel";
import DemoQuestions from "./demo-questions";
import ImportPanel from "./import-panel";
import { request, type Dataset } from "./api";

export default function Home() {
  const [session, setSession] = useState<Session | null>(null);
  const [mode, setMode] = useState<"demo" | "private">("demo");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selected, setSelected] = useState("");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [loadedScope, setLoadedScope] = useState("");
  const token = session?.access_token;
  const userId = session?.user.id;
  useEffect(() => {
    const controller = new AbortController();
    if (mode === "private" && !token) return () => controller.abort();
    request<{ datasets: Dataset[] }>(mode === "demo" ? "/demo/datasets" : "/datasets", mode === "private" ? token : undefined, undefined, controller.signal)
      .then(body => {
        if (controller.signal.aborted) return;
        const rows = body.datasets.filter(item => mode === "demo" ? item.is_demo : !item.is_demo);
        setDatasets(rows); setLoadedScope(mode === "demo" ? "demo" : session!.user.id); setSelected(previous => rows.some(item => item.id === previous) ? previous : rows[0]?.id || ""); setError("");
      }).catch(error => { if (!controller.signal.aborted) setError(error.message || "Could not load datasets. Try refreshing."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [mode, token, reload]);
  function changeMode(value: "demo" | "private") { if (value === mode) return; setMode(value); setDatasets([]); setLoadedScope(""); setSelected(""); setError(""); setLoading(true); setShowImport(false); }
  const dataset = datasets.find(item => item.id === selected);
  // Hide account-scoped components immediately on logout/account change, before requests settle.
  const canShow = mode === "demo" || (!!session && loadedScope === userId);
  return <div className="app-shell">
    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <header className="topbar"><a href="/" className="brand"><span className="brand-mark" aria-hidden="true">हि</span>Hisaab<span>AI</span></a><span className="tagline">Apne data se sawaal karo.</span><span className="beta-label">Sales workspace</span></header>
    <main id="workspace">
      <div className="intro"><p className="eyebrow">From your bahi-khata to a clear answer</p><h1>Know what your sales<br />are telling you.</h1><p>Choose your data. Ask a sawaal. See the numbers behind every answer.</p></div>
      <nav className="mode-tabs" aria-label="Workspace"><button type="button" aria-pressed={mode === "demo"} onClick={() => changeMode("demo")}>Try Demo <span>No login needed</span></button><button type="button" aria-pressed={mode === "private"} onClick={() => changeMode("private")}>Your workspace <span>Sign in for private data</span></button></nav>
      <div hidden={mode !== "private"}><AuthPanel onSession={setSession} /></div>
      {mode === "private" && token && !canShow && <p role="status">{error || "Loading your workspace…"}<button className="secondary" onClick={() => setReload(value => value + 1)}>Retry</button></p>}
      {canShow && (mode === "demo" || token) && <div key={`${mode}-${userId || "visitor"}`}>
        <section className="dataset-panel" aria-labelledby="dataset-title"><div className="section-heading"><div><p className="eyebrow">Start with your data</p><h2 id="dataset-title">{mode === "demo" ? "Fictional sales ledger" : "Your datasets"}</h2></div><div className="actions"><button className="secondary" type="button" onClick={() => { setLoading(true); setReload(value => value + 1); }}>Refresh</button>{mode === "private" && <button type="button" onClick={() => setShowImport(value => !value)}>{showImport ? "Close upload" : "Upload sales file"}</button>}</div></div>
          <p role="status">{loading ? "Loading datasets…" : error}</p>
          {!loading && !error && !datasets.length && <p>No private datasets yet. Upload your first sales file to get started.</p>}
          {!!datasets.length && <label className="dataset-select">Selected dataset<select value={selected} onChange={event => setSelected(event.target.value)}>{datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
          {dataset && <div className="ledger-strip"><div><span>Rows in ledger</span><strong>{dataset.row_count}</strong></div><div><span>Sales period</span><strong>{dataset.date_start || "No sales"} → {dataset.date_end || "—"}</strong></div><div><span>Currency / version</span><strong>{dataset.currency} / v{dataset.version}</strong></div><div><span>Access</span><strong>{dataset.is_demo ? "Fictional · read only" : "Only your account"}</strong></div></div>}
          {mode === "demo" && <p className="meta">Milk, Potato & Notebook · fictional CSV-based ledger · <a href="/demo-sales.csv" download>Download source CSV</a></p>}
        </section>
        {showImport && token && <ImportPanel token={token} datasets={datasets} onImported={id => { setSelected(id); setLoading(true); setReload(value => value + 1); }} />}
        {dataset && <DemoQuestions key={`${dataset.id}-${dataset.version}`} dataset={dataset} token={mode === "private" ? token : undefined} />}
      </div>}
      <footer>HisaabAI Agent · Sales totals come from your data. Revenue is not profit.</footer>
    </main>
  </div>;
}
