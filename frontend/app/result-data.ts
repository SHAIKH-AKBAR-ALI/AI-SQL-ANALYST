import type { Cell, QueryResult } from "./api";

export function csvCell(value: Cell): string {
  let text = value === null ? "" : String(value);
  // Protect every field, including column labels, after leading control/space bytes.
  if (/^[\s\u0000-\u001f]*[=+\-@]/u.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

export function resultCSV(result: QueryResult): string {
  return "\ufeff" + [result.columns, ...result.rows].map(row => row.map(csvCell).join(",")).join("\r\n");
}

export function chartData(result: QueryResult): { label: string; value: number }[] | null {
  if (result.truncated || result.columns.length !== 2 || result.rows.length < 2 || result.rows.length > 30) return null;
  const [label, metric] = result.columns;
  // Only revenue/count comparisons have a known common scale. Mixed-unit quantities stay tables.
  if (!/^(product_name|sale_date|month|unit)$/i.test(label) || !/^(revenue|amount|sales|total_revenue|total_sales|count|sale_count)$/i.test(metric)) return null;
  const labels = new Set<string>();
  const data = [];
  for (const row of result.rows) {
    if (typeof row[0] !== "string" || !row[0] || labels.has(row[0]) ||
        !["string", "number"].includes(typeof row[1]) || !/^-?\d+(\.\d+)?$/.test(String(row[1]))) return null;
    const value = Number(row[1]);
    if (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) return null;
    labels.add(row[0]);
    data.push({ label: row[0], value });
  }
  return data;
}
