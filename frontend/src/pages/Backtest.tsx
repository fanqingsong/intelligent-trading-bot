import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import PrefectLink from "../components/PrefectLink";

export default function BacktestPage() {
  const [steps, setSteps] = useState<string[]>(["predict_rolling", "simulate"]);
  const [selected, setSelected] = useState<Record<string, boolean>>({
    predict_rolling: true,
    simulate: true,
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const [prefectUrl, setPrefectUrl] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [results, setResults] = useState<any>(null);
  const esRef = useRef<EventSource | null>(null);

  const loadResults = () => {
    api
      .backtestResults()
      .then(setResults)
      .catch(() => undefined);
  };

  useEffect(() => {
    api.pipelineSteps().then((s) => setSteps(s.backtest));
    loadResults();
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
      setPrefectUrl(res.prefect_ui_url || null);
      setStatus(res.status || "queued");
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
        loadResults();
      });
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  const rows: Record<string, string>[] = results?.simulate_rows || [];
  const columns = rows.length ? Object.keys(rows[0]) : [];

  return (
    <div>
      <h1 className="page-title">Backtest</h1>
      <p className="page-sub">
        Rolling walk-forward predict (`predict_rolling`) and threshold grid search (`simulate`)
      </p>
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
        <button className="btn" onClick={loadResults}>
          Refresh results
        </button>
        {jobId && (
          <span className="muted">
            Job <code>{jobId.slice(0, 8)}</code> · {status}{" "}
            <PrefectLink url={prefectUrl} />
          </span>
        )}
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>Logs</h3>
        <div className="log-view">{logs.join("\n") || "Waiting…"}</div>
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>Simulate results (signal_models)</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          {results?.simulate_path || "—"}
        </p>
        {rows.length === 0 ? (
          <p className="muted">No simulate results yet. Run `simulate` (optionally after `predict_rolling`).</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    {columns.map((c) => (
                      <td key={c}>{r[c]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Prediction metrics</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          {results?.metrics_path || "—"}
        </p>
        <pre className="log-view">{results?.metrics || "No metrics file yet"}</pre>
      </div>
    </div>
  );
}
