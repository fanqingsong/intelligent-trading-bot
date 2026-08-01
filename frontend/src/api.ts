const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
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
}

export const api = {
  dashboard: () => request<any>("/api/dashboard"),
  health: () => request<any>("/health"),
  getConfig: () => request<{ path: string; content: string; parsed: any }>("/api/config"),
  putConfig: (content: string) =>
    request("/api/config", { method: "PUT", body: JSON.stringify({ content }) }),
  watchlist: () => request<{ items: any[] }>("/api/watchlist"),
  watchlistSuggest: (q: string, limit = 15) =>
    request<{ query: string; items: { code: string; name: string; exchange: string; label: string }[] }>(
      `/api/watchlist/suggest?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  watchlistAdd: (symbol: string) =>
    request<any>("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol }) }),
  watchlistDelete: (symbol: string) =>
    request(`/api/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" }),
  watchlistTrain: (symbol: string) =>
    request<{ job_id: string; symbol: string; steps: string[] }>(
      `/api/watchlist/${encodeURIComponent(symbol)}/train`,
      { method: "POST" },
    ),
  watchlistPredict: (symbols?: string[]) =>
    request<{ batch_id: number; jobs: any[]; skipped: any[] }>("/api/watchlist/predict", {
      method: "POST",
      body: JSON.stringify({ symbols: symbols ?? null }),
    }),
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
      daily_predict: string[];
      backtest: string[];
      all: string[];
    }>("/api/pipeline/steps"),
  createJob: (steps: string[], config_overrides?: Record<string, unknown>) =>
    request<{ job_id: string; status: string; steps: string[] }>("/api/pipeline/jobs", {
      method: "POST",
      body: JSON.stringify({ steps, config_overrides }),
    }),
  listJobs: () => request<{ jobs: any[] }>("/api/pipeline/jobs"),
  getJob: (id: string) => request<any>(`/api/pipeline/jobs/${id}`),
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
