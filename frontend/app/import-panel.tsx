"use client";

import { useState } from "react";
import { request, type Dataset } from "./api";

type Preview = { sheets: string[]; sheet: string | null; requires_sheet: boolean; headers: string[]; preview: string[][]; row_count: number; proposed_mapping: Record<string, string>; types: Record<string, string> };
type Report = { valid: boolean; accepted_rows: number; rejected_rows: number; errors: { row: number; message: string }[]; errors_truncated: boolean; confirmations: string[]; diagnostics: { row: number; message: string }[]; diagnostic_count: number };
const fields = ["sale_date", "product_name", "quantity", "unit", "unit_price", "amount", "currency"];

export default function ImportPanel({ token, datasets, onImported }: { token: string; datasets: Dataset[]; onImported: (id: string) => void }) {
  const [file, setFile] = useState<{ filename: string; content: string } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [sheet, setSheet] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [dateFormat, setDateFormat] = useState("ISO");
  const [unit, setUnit] = useState("");
  const [name, setName] = useState("");
  const [amountConfirmed, setAmountConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [target, setTarget] = useState("");
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  function invalidate() { setReport(null); setConfirmed(false); setReplaceConfirmed(false); setMessage(""); }
  function options() {
    return { ...file, sheet: sheet || null, mapping, date_format: dateFormat, default_unit: unit || null,
      currency: "INR", amount_confirmed: amountConfirmed, adjustment_note: note };
  }
  async function chooseFile(selected?: File) {
    invalidate(); setPreview(null); setFile(null); setSheet(""); setMapping({}); setAmountConfirmed(false); setNote("");
    if (!selected) return;
    if (!/\.(xlsx|csv)$/i.test(selected.name) || selected.size > 5 * 1024 * 1024 || selected.size === 0) {
      setMessage("Choose a nonempty CSV or XLSX file up to 5 MiB."); return;
    }
    setBusy(true);
    try {
      const content = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("Cannot read file. Choose it again.")); reader.readAsDataURL(selected);
      });
      const body = { filename: selected.name, content };
      setFile(body); setName(selected.name.replace(/\.[^.]+$/, ""));
      const value = await request<Preview>("/imports/preview", token, body);
      setPreview(value); setSheet(value.sheet || ""); setMapping(value.proposed_mapping);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Preview failed."); }
    finally { setBusy(false); }
  }
  async function chooseSheet(value: string) {
    invalidate(); setSheet(value); setPreview(null); setBusy(true);
    try {
      const next = await request<Preview>("/imports/preview", token, { ...file, sheet: value });
      setPreview(next); setMapping(next.proposed_mapping);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Sheet preview failed. Choose file again."); }
    finally { setBusy(false); }
  }
  async function validate() {
    setBusy(true); invalidate();
    try { setReport(await request<Report>("/imports/validate", token, options())); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Validation failed."); }
    finally { setBusy(false); }
  }
  async function commit() {
    if (!report?.valid || !confirmed || busy) return;
    const dataset = datasets.find(item => item.id === target);
    setBusy(true); setMessage("");
    try {
      const result = await request<{ dataset_id: string; accepted_rows: number; dataset_version: number; duplicate: boolean }>("/imports/commit", token,
        { ...options(), name, confirmed, replace_dataset_id: target || null, expected_version: dataset?.version || null, replace_confirmed: replaceConfirmed });
      setMessage(`${result.duplicate ? "Already imported" : "Import committed"}: ${result.accepted_rows} rows · version ${result.dataset_version}.`);
      setReport(null); setConfirmed(false); setReplaceConfirmed(false); onImported(result.dataset_id);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Import failed. Existing data is preserved."); }
    finally { setBusy(false); }
  }
  return <section className="panel" aria-labelledby="upload-title">
    <div className="section-heading"><div><p className="eyebrow">Your sales workbook</p><h2 id="upload-title">Preview before you import</h2></div><span className="badge">Private</span></div>
    <p>UTF-8 CSV or XLSX · up to 5 MiB, 10,000 rows, 50 columns. First row must contain column names. INR only.</p>
    <label className="upload-box">Choose sales file<input type="file" accept=".csv,.xlsx" disabled={busy} onChange={event => void chooseFile(event.target.files?.[0])} /></label>
    {preview && <fieldset disabled={busy}>
      <label>Sheet<select value={sheet} onChange={event => void chooseSheet(event.target.value)}><option value="" disabled>Choose a sheet</option>{preview.sheets.map(item => <option key={item}>{item}</option>)}</select></label>
      {!preview.requires_sheet && <>
        <p className="meta">Preview only · {preview.row_count} data rows · first ten shown</p>
        <div className="table-scroll" role="region" aria-label="File preview" tabIndex={0}><table><caption>Uploaded sheet preview</caption><thead><tr>{preview.headers.map(header => <th key={header} scope="col">{header}<small>{preview.types[header]}</small></th>)}</tr></thead><tbody>{preview.preview.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j}>{value || "—"}</td>)}</tr>)}</tbody></table></div>
        <h3>Confirm column meanings</h3><div className="mapping-grid">{fields.map(field => <label key={field}>{field.replaceAll("_", " ")}{["sale_date", "product_name", "quantity", "amount"].includes(field) ? " *" : " (optional)"}<select value={mapping[field] || ""} onChange={event => { invalidate(); setMapping(previous => { const next = { ...previous }; if (event.target.value) next[field] = event.target.value; else delete next[field]; return next; }); }}><option value="">Not mapped</option>{preview.headers.map(header => <option key={header}>{header}</option>)}</select></label>)}</div>
        <div className="mapping-grid"><label>Date order<select value={dateFormat} onChange={event => { invalidate(); setDateFormat(event.target.value); }}><option value="ISO">YYYY-MM-DD (ISO)</option><option value="DMY">DD/MM/YYYY</option><option value="MDY">MM/DD/YYYY</option></select></label>
          <label>Unit if no unit column<select value={unit} onChange={event => { invalidate(); setUnit(event.target.value); }}><option value="">Use mapped unit column</option>{["litre", "kg", "piece"].map(item => <option key={item}>{item}</option>)}</select></label></div>
        <label className="check"><input type="checkbox" checked={amountConfirmed} onChange={event => { invalidate(); setAmountConfirmed(event.target.checked); }} />Amount means final line revenue, including tax/discounts. It is not profit.</label>
        <label>Tax, discount or rounding explanation (required when amount differs from quantity × price)<input maxLength={300} value={note} onChange={event => { invalidate(); setNote(event.target.value); }} /></label>
        <button type="button" className="secondary" onClick={() => void validate()}>Validate all rows</button>
      </>}
    </fieldset>}
    {report && <div className="validation" role="status"><h3>{report.valid ? "Ready to confirm" : "Changes needed"}</h3><p>{report.accepted_rows} valid · {report.rejected_rows} rejected · {report.diagnostic_count} amount differences. Nothing imported yet.</p>
      {report.confirmations.map(item => <p key={item} className="notice">{item}</p>)}
      {report.errors.map((item, i) => <p key={i}>Row {item.row}: {item.message}</p>)}
      {report.errors_truncated && <p>First 100 errors shown. Correct file and validate again.</p>}
      <details><summary>Amount diagnostics ({report.diagnostic_count})</summary>{report.diagnostics.map(item => <p key={item.row}>Row {item.row}: {item.message}</p>)}</details>
    </div>}
    {report?.valid && <fieldset disabled={busy}>
      <label>Dataset name<input required maxLength={200} value={name} onChange={event => { setName(event.target.value); setConfirmed(false); }} /></label>
      <label>Import destination<select value={target} onChange={event => { setTarget(event.target.value); setReplaceConfirmed(false); setConfirmed(false); }}><option value="">Create new dataset</option>{datasets.filter(item => !item.is_demo).map(item => <option key={item.id} value={item.id}>Replace {item.name} · v{item.version} · {item.row_count} rows</option>)}</select></label>
      {target && <label className="check"><input type="checkbox" checked={replaceConfirmed} onChange={event => setReplaceConfirmed(event.target.checked)} />Replace all current rows. Earlier result versions cannot be reproduced after replacement.</label>}
      <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I reviewed mappings and all rows. Import {report.accepted_rows} rows.</label>
      <button type="button" disabled={!confirmed || !name.trim() || (!!target && !replaceConfirmed)} onClick={() => void commit()}>{target ? "Confirm replacement" : "Confirm import"}</button>
    </fieldset>}
    <p role="status">{busy ? "Processing your file…" : message}</p>
  </section>;
}
