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
  analyze: (symbol: string) =>
    request<{ job_id: string; status: string; symbol: string; steps: string[] }>("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  analyzeSuggest: (q: string, limit = 15) =>
    request<{ query: string; items: { code: string; name: string; exchange: string; label: string }[] }>(
      `/api/analyze/suggest?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  analyzeResult: (symbol?: string) => {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    return request<any>(`/api/analyze/result${q}`);
  },
  listSamples: () => request<{ samples: string[] }>("/api/config/samples"),
  loadSample: (name: string) =>
    request(`/api/config/load-sample/${encodeURIComponent(name)}`, { method: "POST" }),
  pipelineSteps: () =>
    request<{ pipeline: string[]; backtest: string[]; all: string[] }>("/api/pipeline/steps"),
  createJob: (steps: string[]) =>
    request<{ job_id: string; status: string; steps: string[] }>("/api/pipeline/jobs", {
      method: "POST",
      body: JSON.stringify({ steps }),
    }),
  listJobs: () => request<{ jobs: any[] }>("/api/pipeline/jobs"),
  getJob: (id: string) => request<any>(`/api/pipeline/jobs/${id}`),
  models: () => request<any>("/api/models"),
  dataFiles: () => request<{ base: string; files: any[] }>("/api/data/files"),
  preview: (file: string, rows = 20) =>
    request<any>(`/api/data/preview?file=${encodeURIComponent(file)}&rows=${rows}`),
  signals: (rows = 50) => request<any>(`/api/signals/recent?rows=${rows}`),
  traderStatus: () => request<any>("/api/trader/status"),
  traderControl: (action: string) =>
    request<{ message?: string; state?: any; ok?: boolean }>(`/api/trader/${action}`, {
      method: "POST",
    }),
  logsUrl: (jobId: string) => `${API_BASE}/api/pipeline/jobs/${jobId}/logs`,
};

export { API_BASE };
