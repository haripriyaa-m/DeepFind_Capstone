/** Typed client for backend/main.py. Shapes mirror the README contracts. */

export type MatchType = "semantic" | "keyword" | "hybrid";

export interface SearchResult {
  file_name: string;
  file_path: string;
  file_type: string;
  snippet: string;
  score: number; // 0..1, raw fused score (what the order is based on)
  match_type: MatchType;
  // ranking detail (older backends omit these)
  rank?: number;                   // 1-based position
  match_percent?: number;          // 0..100, how well it matches; never rises down the list
  keyword_rank?: number | null;    // position in the keyword list
  keyword_score?: number | null;   // raw keyword (BM25) score
  semantic_rank?: number | null;   // position in the meaning list
  similarity?: number | null;      // 0..1 similarity between the query and the file's picture/vector
  start_sec?: number | null;       // videos: where in the video the best match is (seconds)
}

export interface Health {
  ok: boolean;
  ready: boolean;
  error: string | null;
  version?: string;
}

export interface Status {
  ready: boolean;
  error?: string | null;
  message: string;
  cpu_percent?: number;
  ram_percent?: number;
  on_battery?: boolean;
  paused?: boolean;
  indexed_files?: number;
  tier2_queue?: number;
  work_queue?: number;
  current_file?: string | null;
  watching?: string[];
  errors?: number;
  tier2_processed?: number;
  tier2_failed?: number;
  tier2_current?: string | null;
  ignored_files?: string[];
  vision_available?: boolean;
  semantic_available?: boolean;
  semantic_error?: string | null;
  db_rows?: number;
  idle?: boolean;
  pending_changes?: number;        // changes remembered for the next scheduled run
  index_schedule?: IndexSchedule;
  last_index_run?: number | null;  // epoch seconds
  next_index_run?: number | null;  // epoch seconds, null when there is no timer
  ocr_engine?: OcrEngineId;
}

export type IndexSchedule = "realtime" | "15m" | "30m" | "1h" | "6h" | "24h" | "manual";
export type OcrEngineId = "rapidocr" | "tesseract" | "off";

export interface Settings {
  watched_folders: string[];
  cpu_limit: number;
  ram_limit: number;
  pause_on_battery: boolean;
  index_schedule?: IndexSchedule;
  ocr_engine?: OcrEngineId;
  auto_update_check?: boolean;
  ocr_model?: string;
  ocr_languages?: string[];
  image_model?: string;
  last_index_run?: number;
  invalid_folders?: string[];
}

export type UpdateState =
  "idle" | "checking" | "up_to_date" | "available" | "downloading" | "ready" | "installing" | "error";

export interface UpdateInfo {
  current: string;
  state: UpdateState;
  configured: boolean;          // false when the build has no update source
  available: boolean;           // a newer version exists
  latest: { version: string; notes: string; page: string; published: string; size: number } | null;
  checked_at: number | null;    // epoch seconds
  error: string;
  progress: { done: number; total: number };   // bytes
  can_self_update: boolean;     // false for the portable zip / running from source
  blocker: string;
  just_updated: boolean;        // first launch after an update
  repo?: string;                // owner/name updates are read from
  repo_default?: string;        // the repository built into this version (update_source.json)
  repo_custom?: boolean;        // the user chose another one in Settings
  token_set?: boolean;          // a GitHub token is stored (private repository)
}

export interface Library {
  total: number;
  types: Record<string, number>;
  folders?: Record<string, number>;
}

export interface OcrEngineInfo {
  id: OcrEngineId;
  label: string;
  description: string;
  available: boolean;
  detail: string;
  installable?: boolean;     // can DeepFind install it for you (Windows + Tesseract)
  install_hint?: string;     // how to install it yourself
}

export interface ModelsInfo {
  root: string;
  installed: boolean;
  parts: { text: boolean; vision: boolean; ocr: boolean };
  ocr_engine?: OcrEngineId;
  ocr_engines?: OcrEngineInfo[];
  tesseract_job?: { state: "idle" | "running" | "done" | "failed"; message: string };
  job: { state: "idle" | "running" | "done" | "failed"; message: string; step: number; steps: number };
}

// ---- Settings -> Models ----
export type ModelRole = "ocr" | "image";
export type ModelKind = "rapidocr" | "tesseract" | "tess-lang" | "clip";
export interface CatalogItem {
  id: string; role: ModelRole; kind: ModelKind; name: string; custom: boolean;
  summary: string; good: string; weak: string; languages?: string;
  size_mb: number; speed: number; accuracy: number;          // speed/accuracy 1-5 (0 = unknown)
  code?: string; requires?: string; dim?: number; text_repo?: string; image_repo?: string;
  status: "available" | "queued" | "downloading" | "installed" | "failed";
  error: string; active: boolean;
}
export interface ActiveModels {
  ocr_engine: OcrEngineId; ocr_model: string; ocr_languages: string[]; image_model: string;
}
export interface Catalog {
  roles: { id: ModelRole; label: string; description: string }[];
  items: CatalogItem[];
  active: ActiveModels;
  jobs: { queue: string[]; current: string | null; message: string; progress: { done: number; total: number }; errors: Record<string, string> };
  tesseract_path: string;
  models_dir: string;
  rebuilding?: boolean;
}
export interface CustomModelIn {
  kind: "tess-lang" | "rapidocr" | "clip"; name: string; note?: string; languages?: string;
  code?: string; url?: string; det_url?: string; rec_url?: string; keys_url?: string;
  repo?: string; image_repo?: string; text_file?: string; image_file?: string; dim?: number;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// The UI is served by the backend itself (same origin); vite dev proxies /api.
const base = "";

async function req<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), init?.timeoutMs ?? 15000);
  const outer = init?.signal;
  if (outer) outer.addEventListener("abort", () => ctrl.abort(), { once: true });
  try {
    const res = await fetch(base + path, {
      ...init,
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (!res.ok) {
      let msg = res.statusText;
      try {
        const body = await res.json();
        msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
      } catch { /* not json */ }
      throw new ApiError(res.status, msg);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  health: () => req<Health>("/api/health", { timeoutMs: 3000 }),
  status: () => req<Status>("/api/status", { timeoutMs: 5000 }),
  search: (q: string, limit = 50, signal?: AbortSignal) =>
    req<{ query: string; results: SearchResult[] }>(
      `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`, { signal, timeoutMs: 30000 }),
  settings: () => req<Settings>("/api/settings"),
  saveSettings: (s: Partial<Settings>) =>
    req<Settings>("/api/settings", { method: "POST", body: JSON.stringify(s) }),
  reindex: () => req<{ ok: boolean }>("/api/reindex", { method: "POST" }),
  indexNow: () => req<{ ok: boolean }>("/api/index-now", { method: "POST" }),
  reveal: (file_path: string) =>
    req<{ ok: boolean }>("/api/reveal", { method: "POST", body: JSON.stringify({ file_path }) }),
  open: (file_path: string) =>
    req<{ ok: boolean }>("/api/open", { method: "POST", body: JSON.stringify({ file_path }) }),
  library: () => req<Library>("/api/library"),
  models: (refresh = false) => req<ModelsInfo>(`/api/models${refresh ? "?refresh=true" : ""}`),
  downloadModels: () => req<ModelsInfo>("/api/models/download", { method: "POST" }),
  installTesseract: () => req<ModelsInfo>("/api/ocr/install-tesseract", { method: "POST" }),
  catalog: () => req<Catalog>("/api/catalog"),
  downloadCatalog: (ids: string[]) =>
    req<Catalog>("/api/catalog/download", { method: "POST", body: JSON.stringify({ ids }) }),
  cancelCatalog: (id: string) => req<Catalog>("/api/catalog/cancel", { method: "POST", body: JSON.stringify({ id }) }),
  removeModel: (id: string) => req<Catalog>("/api/catalog/remove", { method: "POST", body: JSON.stringify({ id }) }),
  addCustomModel: (m: CustomModelIn) =>
    req<Catalog>("/api/catalog/custom", { method: "POST", body: JSON.stringify(m) }),
  deleteCustomModel: (id: string) =>
    req<Catalog>("/api/catalog/custom/delete", { method: "POST", body: JSON.stringify({ id }) }),
  setActiveModels: (a: Partial<ActiveModels>) =>
    req<Catalog>("/api/catalog/active", { method: "POST", body: JSON.stringify(a), timeoutMs: 30000 }),
  setUpdateToken: (token: string) =>
    req<UpdateInfo>("/api/update/token", { method: "POST", body: JSON.stringify({ token }) }),
  setUpdateRepo: (repo: string) =>
    req<UpdateInfo>("/api/update/repo", { method: "POST", body: JSON.stringify({ repo }) }),
  update: () => req<UpdateInfo>("/api/update", { timeoutMs: 5000 }),
  checkUpdate: () => req<UpdateInfo>("/api/update/check", { method: "POST", timeoutMs: 30000 }),
  downloadUpdate: () => req<UpdateInfo>("/api/update/download", { method: "POST" }),
  installUpdate: () => req<UpdateInfo>("/api/update/install", { method: "POST" }),
  ackUpdate: () => req<{ ok: boolean }>("/api/update/ack", { method: "POST" }),
  thumbUrl: (path: string, size = 160, t?: number | null) =>
    `${base}/api/thumb?path=${encodeURIComponent(path)}&size=${size}${t ? `&t=${t}` : ""}`,
};