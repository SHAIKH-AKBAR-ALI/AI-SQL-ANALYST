"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Answer } from "./api";
import { chartData, resultCSV } from "./result-data";

export default function ResultView({ answer }: { answer: Answer }) {
  const result = answer.result;
  const chart = result ? chartData(result) : null;
  function download() {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([resultCSV(result)], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `sales-result-v${result.dataset_version}${result.truncated ? "-partial" : ""}.csv`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="answer-content">
    <p className="answer-text">{answer.answer}</p>
    {answer.status === "clarify" && <p className="notice">Add these details to your question, then ask again.</p>}
    {answer.warning && <p className="notice">{answer.warning}</p>}
    {result && <>
      <p className="meta">{result.currency} · {result.business_timezone} · Dataset v{result.dataset_version}</p>
      {result.truncated && <p className="notice">Partial results: output limit reached. Export includes only visible rows. Refine your question for a complete result.</p>}
      {chart && <figure className="result-chart">
        <figcaption>{result.columns[1]} by {result.columns[0]} · approximate visual; exact values below</figcaption>
        <ResponsiveContainer width="100%" height={250} minWidth={0}>
          <BarChart data={chart} accessibilityLayer margin={{ top: 16, right: 16, bottom: 8, left: 8 }}>
            <CartesianGrid vertical={false} stroke="#dce4ee" /><XAxis dataKey="label" tick={{ fontSize: 11, fill: '#53657c' }} /><YAxis tick={{ fontSize: 11, fill: '#53657c' }} />
            <Tooltip /><Bar dataKey="value" fill="#2459a8" radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </figure>}
      <div className="table-scroll" role="region" aria-label="Query results" tabIndex={0}>
        <table><caption>Verified result · {result.row_count} rows</caption>
          <thead><tr>{result.columns.map((column, i) => <th key={i} scope="col">{column}</th>)}</tr></thead>
          <tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell === null ? "Unavailable (NULL)" : String(cell)}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {!result.rows.length && <p>No rows matched these filters.</p>}
      <button type="button" className="secondary" onClick={download}>Export {result.truncated ? "partial " : ""}result CSV</button>
    </>}
    <details><summary>Filters, assumptions & SQL</summary>
      <p>{answer.filters.join(" · ") || "No additional filters stated."}</p>
      <p>{answer.assumptions.join(" · ") || "No additional assumptions stated."}</p>
      {answer.sql && <pre><code>{answer.sql}</code></pre>}
    </details>
  </div>;
}
