import { useEffect, useRef, useState } from "react";
import { api } from "../api";

export default function BacktestPage() {
  const [steps, setSteps] = useState<string[]>(["predict_rolling", "simulate"]);
  const [selected, setSelected] = useState<Record<string, boolean>>({
    predict_rolling: true,
    simulate: true,
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [metrics, setMetrics] = useState("");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api.pipelineSteps().then((s) => setSteps(s.backtest));
    return () => esRef.current?.close();
  }, []);

  const run = async () => {
    setError("");
    const chosen = steps.filter((s) => selected[s]);
    if (!chosen.length) {
      setError("Select at least one step");
      return;
    }
    try {
      const res = await api.createJob(chosen);
      setJobId(res.job_id);
      setStatus(res.status);
      setLogs([]);
      esRef.current?.close();
      const es = new EventSource(api.logsUrl(res.job_id));
      esRef.current = es;
      es.addEventListener("log", (ev) => {
        setLogs((prev) => [...prev, (ev as MessageEvent).data]);
      });
      es.addEventListener("done", async (ev) => {
        try {
          const job = JSON.parse((ev as MessageEvent).data);
          setStatus(job.status);
        } catch {
          setStatus("done");
        }
        es.close();
        try {
          const m = await api.models();
          setMetrics(m.metrics || "");
        } catch {
          /* ignore */
        }
      });
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  return (
    <div>
      <h1 className="page-title">Backtest</h1>
      <p className="page-sub">Rolling predict and trade simulation</p>
      {error && <p className="error">{error}</p>}

      <div className="checks">
        {steps.map((s) => (
          <label key={s}>
            <input
              type="checkbox"
              checked={!!selected[s]}
              onChange={() => setSelected((p) => ({ ...p, [s]: !p[s] }))}
            />
            {s}
          </label>
        ))}
      </div>

      <div className="btn-row">
        <button className="btn primary" onClick={run}>
          Run backtest
        </button>
        {jobId && (
          <span className="muted">
            Job <code>{jobId.slice(0, 8)}</code> · {status}
          </span>
        )}
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>Logs</h3>
        <div className="log-view">{logs.join("\n") || "Waiting…"}</div>
      </div>

      <div className="panel">
        <h3>Metrics / results excerpt</h3>
        <pre className="log-view">{metrics || "Run a job to refresh metrics"}</pre>
      </div>
    </div>
  );
}
