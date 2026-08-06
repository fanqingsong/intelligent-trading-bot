const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const DEFAULT_TIMEOUT_MS = 30_000;

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const user = import.meta.env.VITE_ITB_USER as string | undefined;
  const teams = import.meta.env.VITE_ITB_TEAMS as string | undefined;
  const admin = import.meta.env.VITE_ITB_ADMIN as string | undefined;
  if (user) headers["X-ITB-User"] = user;
  if (teams) headers["X-ITB-Teams"] = teams;
  if (admin) headers["X-ITB-Admin"] = admin;
  return headers;
}

type RequestOptions = RequestInit & { timeoutMs?: number };

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const timeoutMs = init?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const { timeoutMs: _timeoutMs, signal, ...rest } = init || {};
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...rest,
      signal: signal ?? controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(rest.headers || {}),
      },
    });
    if (!res.ok) {
      const text = await res.text();
      let message = text || res.statusText;
      try {
        const body = JSON.parse(text);
        if (typeof body?.detail === "string") message = body.detail;
        else if (body?.detail != null) message = JSON.stringify(body.detail);
      } catch {
        /* keep raw text */
      }
      throw new Error(message);
    }
    return res.json() as Promise<T>;
  } catch (e: unknown) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}s）：${path}`);
    }
    throw e;
  } finally {
    window.clearTimeout(timer);
  }
}

export type JobRef = {
  job_id: string;
  status?: string;
  steps?: string[];
  team?: string;
  prefect_flow_run_id?: string | null;
  prefect_ui_url?: string | null;
};

export const api = {
  dashboard: () => request<any>("/api/dashboard"),
  health: () => request<any>("/health"),
  prefectInfo: () =>
    request<{
      api_url: string | null;
      ui_url: string;
      redis_mirror: boolean;
      job_source: string;
      rbac_enabled: boolean;
    }>("/api/prefect/info"),
  getConfig: () => request<{ path: string; content: string; parsed: any }>("/api/config"),
  putConfig: (content: string) =>
    request("/api/config", { method: "PUT", body: JSON.stringify({ content }) }),
  watchlist: (opts?: { includeSignals?: boolean }) => {
    const q = opts?.includeSignals === false ? "?include_signals=false" : "";
    return request<{ items: any[] }>(`/api/watchlist${q}`, { timeoutMs: 60_000 });
  },
  watchlistSuggest: (q: string, limit = 15) =>
    request<{ query: string; items: { code: string; name: string; exchange: string; label: string }[] }>(
      `/api/watchlist/suggest?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  watchlistAdd: (symbol: string) =>
    request<any>("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol }) }),
  watchlistImport: (index: "sse50" | "csi300") =>
    request<{
      index: string;
      index_name: string;
      total: number;
      added: number;
      skipped: number;
      items: any[];
      skipped_items: { symbol: string; reason: string }[];
    }>("/api/watchlist/import", {
      method: "POST",
      body: JSON.stringify({ index }),
      // Index constituent fetch (akshare) can exceed 30s when the upstream is slow.
      timeoutMs: 120_000,
    }),
  watchlistDelete: (symbol: string) =>
    request(`/api/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" }),
  watchlistTrain: (symbol: string, team?: string) => {
    const q = team ? `?team=${encodeURIComponent(team)}` : "";
    return request<JobRef & { symbol: string; steps: string[] }>(
      `/api/watchlist/${encodeURIComponent(symbol)}/train${q}`,
      { method: "POST" },
    );
  },
  watchlistTrainAll: (symbols?: string[], team?: string) =>
    request<{
      batch_id: number | null;
      status: string;
      total: number;
      queued: number;
      running: number;
      completed: number;
      failed: number;
      skipped: number;
      current_symbol: string;
      last_error?: string;
      resumed?: boolean;
      deduped?: boolean;
      steps: string[];
    }>("/api/watchlist/train", {
      method: "POST",
      body: JSON.stringify({ symbols: symbols ?? null, team: team ?? null }),
    }),
  watchlistTrainActive: () =>
    request<{
      batch: {
        batch_id: number;
        status: string;
        total: number;
        queued: number;
        running: number;
        completed: number;
        failed: number;
        skipped: number;
        current_symbol: string;
        last_error?: string;
        note?: string;
      } | null;
    }>("/api/watchlist/train/active"),
  watchlistTrainCancel: () =>
    request<{
      batch: {
        batch_id: number;
        status: string;
        total: number;
        queued: number;
        running: number;
        completed: number;
        failed: number;
        skipped: number;
        current_symbol: string;
        last_error?: string;
      };
    }>("/api/watchlist/train/cancel", { method: "POST" }),
  watchlistTrainSymbolCancel: (symbol: string) =>
    request<{
      symbol: string;
      status: string;
      job_id: string;
      cancelled: boolean;
      message?: string;
    }>(`/api/watchlist/${encodeURIComponent(symbol)}/train/cancel`, { method: "POST" }),
  watchlistPredict: (
    symbols?: string[],
    team?: string,
    mode: "data" | "predict" | "full" = "predict",
  ) =>
    request<{
      batch_id: number;
      jobs: JobRef[];
      skipped: any[];
      mode?: string;
      kind?: string;
      batched?: boolean;
    }>("/api/watchlist/predict", {
      method: "POST",
      body: JSON.stringify({ symbols: symbols ?? null, team: team ?? null, mode }),
    }),
  watchlistPredictCancel: () =>
    request<{
      batch: {
        batch_id: number | null;
        status: string;
        total: number;
        queued: number;
        running: number;
        completed: number;
        failed: number;
        skipped: number;
        current_symbol: string;
      };
    }>("/api/watchlist/predict/cancel", { method: "POST" }),
  watchlistSignals: (symbol: string) =>
    request<any>(`/api/watchlist/${encodeURIComponent(symbol)}/signals`),
  getSchedule: () => request<any>("/api/schedule"),
  putSchedule: (body: { predict_enabled?: boolean; predict_cron?: string; timezone?: string }) =>
    request("/api/schedule", { method: "PUT", body: JSON.stringify(body) }),
  listSamples: () => request<{ samples: string[] }>("/api/config/samples"),
  loadSample: (name: string) =>
    request(`/api/config/load-sample/${encodeURIComponent(name)}`, { method: "POST" }),
  pipelineSteps: () =>
    request<{
      pipeline: string[];
      train_update: string[];
      data_update: string[];
      infer: string[];
      daily_predict: string[];
      backtest: string[];
      all: string[];
    }>("/api/pipeline/steps"),
  createJob: (steps: string[], config_overrides?: Record<string, unknown>, team?: string) =>
    request<JobRef>("/api/pipeline/jobs", {
      method: "POST",
      body: JSON.stringify({ steps, config_overrides, team }),
    }),
  listJobs: () => request<{ jobs: JobRef[]; source?: string }>("/api/pipeline/jobs"),
  getJob: (id: string) => request<JobRef & Record<string, any>>(`/api/pipeline/jobs/${id}`),
  models: (symbol?: string) => {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    return request<any>(`/api/models${q}`);
  },
  mlflowInfo: () => request<{ tracking_uri: string; ui_url: string | null; registry_prefix: string }>("/api/mlflow/info"),
  mlflowModels: (symbol?: string) => {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    return request<{ models: any[] }>(`/api/mlflow/models${q}`);
  },
  mlflowVersions: (name: string) =>
    request<{ name: string; versions: any[] }>(`/api/mlflow/models/${encodeURIComponent(name)}/versions`),
  mlflowRuns: (symbol?: string, limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (symbol) params.set("symbol", symbol);
    return request<{ runs: any[] }>(`/api/mlflow/runs?${params}`);
  },
  dataFiles: (symbol?: string) => {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    return request<{ base: string; files: any[]; symbol: string }>(`/api/data/files${q}`);
  },
  preview: (file: string, rows = 20, symbol?: string) => {
    const params = new URLSearchParams({ file, rows: String(rows) });
    if (symbol) params.set("symbol", symbol);
    return request<any>(`/api/data/preview?${params}`);
  },
  signals: (rows = 50, symbol?: string) => {
    const params = new URLSearchParams({ rows: String(rows) });
    if (symbol) params.set("symbol", symbol);
    return request<any>(`/api/signals/recent?${params}`);
  },
  backtestResults: (tail = 80) =>
    request<{
      symbol: string;
      metrics_path: string;
      metrics: string;
      simulate_path: string;
      simulate_text: string;
      simulate_rows: Record<string, string>[];
    }>(`/api/backtest/results?tail=${tail}`),
  logsUrl: (jobId: string) => `${API_BASE}/api/pipeline/jobs/${jobId}/logs`,
};

export { API_BASE };
