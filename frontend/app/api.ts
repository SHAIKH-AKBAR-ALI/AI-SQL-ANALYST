export const api = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
export type Dataset = {
  id: string; name: string; is_demo: boolean; version: number; currency: string;
  row_count: number; date_start: string | null; date_end: string | null;
};
export type Cell = string | number | boolean | null;
export type QueryResult = {
  columns: string[]; rows: Cell[][]; currency: string; business_timezone: string;
  row_count: number; truncated: boolean; dataset_version: number;
};
export type Answer = {
  status: "answered" | "clarify" | "unsupported" | "rejected";
  answer: string; sql: string | null; assumptions: string[]; filters: string[];
  result: QueryResult | null; warning: string | null;
  conversation_id: string;
};

export async function request<T>(path: string, token?: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${api}${path}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store",
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(80000)]) : AbortSignal.timeout(80000),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Request failed. Check your entries and try again.");
  return value as T;
}
