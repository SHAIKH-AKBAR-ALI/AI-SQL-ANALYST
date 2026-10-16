"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { request, type Answer, type Dataset } from "./api";
import ResultView from "./result-view";

type History = { id: string; conversation_id: string; question: string; clarification: string | null; status: string; dataset_version: number; current_version: number; sql_text: string | null; created_at: string };
type Import = { id: string; status: string; dataset_version: number; accepted_rows: number; rejected_rows: number };
const examples = ["October 2026 mein total sales kitni hui?", "Show revenue by product_name in October 2026", "अक्टूबर 2026 में कितने लीटर दूध बिका?"];

export default function DemoQuestions({ dataset, token }: { dataset: Dataset; token?: string }) {
  const [question, setQuestion] = useState(dataset.is_demo ? examples[0] : "What is total revenue?");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [messages, setMessages] = useState<{ question: string; answer: Answer }[]>([]);
  const [history, setHistory] = useState<History[]>([]);
  const [imports, setImports] = useState<Import[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [conversation, setConversation] = useState<string>();
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (!token || dataset.is_demo) return;
    const controller = new AbortController();
    Promise.all([
      request<{ history: History[] }>(`/datasets/${dataset.id}/history`, token, undefined, controller.signal),
      request<{ imports: Import[] }>(`/datasets/${dataset.id}/imports`, token, undefined, controller.signal),
    ]).then(([runs, files]) => { if (!controller.signal.aborted) { setHistory(runs.history); setImports(files.imports); setHistoryError(""); } })
      .catch(error => { if (!controller.signal.aborted) setHistoryError(error.message || "History unavailable. Refresh to retry."); });
    return () => controller.abort();
  }, [dataset.id, dataset.is_demo, token, refresh]);
  async function ask(event: FormEvent) {
    event.preventDefault(); if (busy || !question.trim()) return;
    setBusy(true); setError("");
    const controller = new AbortController(); active.current = controller;
    const asked = question.trim();
    try {
      const answer = await request<Answer>(`/${dataset.is_demo ? "demo/" : ""}datasets/${dataset.id}/ask`, dataset.is_demo ? undefined : token,
        { question: asked, ...(conversation ? { conversation_id: conversation } : {}), ...(!dataset.is_demo ? { ai_consent: consent } : {}) }, controller.signal);
      if (controller.signal.aborted) return;
      setConversation(answer.conversation_id);
      setMessages(previous => [...previous, { question: asked, answer }]);
    } catch (error) { setError(controller.signal.aborted ? "Question cancelled. Current provider call may take a moment to stop." : error instanceof Error ? error.message : "Question failed. Try again."); }
    finally { setBusy(false); setRefresh(value => value + 1); }
  }
  return <section className="panel" aria-labelledby="ask-title">
    <div className="section-heading"><div><p className="eyebrow">Ask in Hindi, Hinglish or English</p><h2 id="ask-title">Your data, your sawaal.</h2></div><span className="badge">{dataset.is_demo ? "Fictional demo" : "Private dataset"}</span></div>
    <p>{conversation ? "Follow up or answer the clarification below. Recent questions provide context; values are queried again." : "Start with the period, metric and product you need."}</p>
    <button className="secondary" type="button" disabled={busy} onClick={() => { setConversation(undefined); setMessages([]); setError(""); }}>New conversation</button>
    {dataset.is_demo && <div className="examples">{examples.map(example => <button type="button" className="example" key={example} disabled={busy} onClick={() => setQuestion(example)}>{example}</button>)}</div>}
    <div className="chat-log" role="log" aria-label="Questions and answers" aria-live="polite">{messages.map((message, i) => <article className="exchange" key={i}><p className="question-bubble">{message.question}</p><ResultView answer={message.answer} /></article>)}</div>
    <form onSubmit={ask}><label htmlFor="question">Your question<textarea id="question" required rows={3} maxLength={2000} disabled={busy} value={question} onChange={event => setQuestion(event.target.value)} /></label>
      {!dataset.is_demo && <label className="check"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} />Send my question, recent conversation context and query results to Groq to generate this answer. Files stay outside the AI prompt.</label>}
      <button disabled={busy || !question.trim() || (!dataset.is_demo && !consent)}>{busy ? "Finding answer…" : dataset.is_demo ? "Ask demo" : "Ask your data"}</button>
      {busy && <button className="secondary" type="button" onClick={() => active.current?.abort()}>Cancel question</button>}
      <p role="status">{busy ? "Reviewing question and checking your data…" : error}</p>
    </form>
    {!dataset.is_demo && <details className="history"><summary>Saved question & import history</summary><p className="meta">Last 100 entries. Question, SQL and version are saved; result rows are not retained. Earlier dataset versions cannot be rerun after replacement.</p>
      <button className="secondary" type="button" onClick={() => setRefresh(value => value + 1)}>Refresh history</button>
      {historyError && <p role="status">{historyError}</p>}
      {!historyError && !history.length && <p>No saved questions yet. Ask your first question above.</p>}
      {history.map(run => <details key={run.id} className="history-entry"><summary>{run.question} · {run.status} · v{run.dataset_version}{run.dataset_version !== run.current_version ? " (replaced version)" : ""}</summary><p className="meta">{new Date(run.created_at).toLocaleString()}</p>{run.clarification && <p>{run.clarification}</p>}{run.sql_text && <pre><code>{run.sql_text}</code></pre>}<button className="secondary" type="button" disabled={busy} onClick={() => { setConversation(undefined); setQuestion(run.question); }}>Use question with current data</button>{run.dataset_version === run.current_version && <button className="secondary" type="button" disabled={busy} onClick={() => { setConversation(run.conversation_id); setQuestion(""); setMessages([]); }}>Resume conversation</button>}</details>)}
      <h3>Imports</h3>{imports.map(item => <p key={item.id}>Version {item.dataset_version} · {item.status} · {item.accepted_rows} accepted / {item.rejected_rows} rejected</p>)}
    </details>}
    {dataset.is_demo && <p className="meta">Demo context expires after 30 minutes of inactivity or server restart. Sign in for saved private conversations.</p>}
  </section>;
}
