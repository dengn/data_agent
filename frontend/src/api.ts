const BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

/* ---- Chat ---- */

export interface ChatRequest {
  question: string;
  session_id?: string;
}

export interface ChatResponse {
  answer: string;
  sources: { name: string; type: string; id: string }[];
  skill_used: string;
  sql_query: string;
  confidence: number;
  error: string;
}

export function chat(body: ChatRequest) {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/* ---- Data Sources ---- */

export interface DataSourceInfo {
  id: string;
  name: string;
  type: string;
  source_type: string;
  description: string | null;
  created_at: string | null;
}

export interface DataSourceDetail extends DataSourceInfo {
  table_name: string | null;
  columns: { name: string; type: string }[] | null;
  row_count: number | null;
  sample_rows: Record<string, unknown>[] | null;
}

export interface UploadResponse {
  source_id: string;
  name: string;
  type: string;
  table_name: string | null;
  row_count: number | null;
  message: string;
}

export function listSources() {
  return request<DataSourceInfo[]>("/api/datasources");
}

export function getSource(id: string) {
  return request<DataSourceDetail>(`/api/datasources/${id}`);
}

export function deleteSource(id: string) {
  return request<{ message: string }>(`/api/datasources/${id}`, {
    method: "DELETE",
  });
}

export function uploadFile(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return request<UploadResponse>("/api/datasources/upload", {
    method: "POST",
    body: fd,
  });
}
