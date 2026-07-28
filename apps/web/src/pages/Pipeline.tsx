import { useEffect, useRef, useState } from "react";
import { api } from "../api";

export default function PipelinePage() {
  const [steps, setSteps] = useState<string[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState("0");
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [jobs, setJobs] = useState<any[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api.pipelineSteps().then((s) => {
      setSteps(s.pipeline);
      const init: Record<string, boolean> = {};
      s.pipeline.forEach((x) => {
        init[x] = true;
      });
      setSelected(init);
    });
    api.listJobs().then((r) => setJobs(r.jobs)).catch(() => undefined);
    return () => {
      esRef.current?.close();
    };
  }, []);

  const toggle = (step: string) => {
    setSelected((prev) => ({ ...prev, [step]: !prev[step] }));
  };

  const attachLogs = (id: string) => {
    esRef.current?.close();
    setLogs([]);
    const es = new EventSource(api.logsUrl(id));
    esRef.current = es;
    es.addEventListener("log", (ev) => {
      setLogs((prev) => [...prev, (ev as MessageEvent).data]);
    });
    es.addEventListener("done", (ev) => {
      try {
        const job = JSON.parse((ev as MessageEvent).data);
        setStatus(job.status);
        setProgress(job.progress || "100");
      } catch {
        setStatus("done");
      }
      es.close();
      api.listJobs().then((r) => setJobs(r.jobs));
    });
    es.addEventListener("error", () => {
      // EventSource also fires error on normal close in some browsers
    });
  };

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
      setProgress("0");
      attachLogs(res.job_id);
      const poll = setInterval(async () => {
        try {
          const j = await api.getJob(res.job_id);
          setStatus(j.status);
          setProgress(j.progress || "0");
          if (j.status === "completed" || j.status === "failed") clearInterval(poll);
        } catch {
          clearInterval(poll);
        }
      }, 2000);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  return (
    <div>
      <h1 className="page-title">Pipeline</h1>
      <p className="page-sub">Run offline batch steps with live logs</p>
      {error && <p className="error">{error}</p>}

      <div className="checks">
        {steps.map((s) => (
          <label key={s}>
            <input type="checkbox" checked={!!selected[s]} onChange={() => toggle(s)} />
            {s}
          </label>
        ))}
      </div>

      <div className="btn-row">
        <button className="btn primary" onClick={run}>
          Run selected
        </button>
        {jobId && (
          <span className="muted">
            Job <code>{jobId.slice(0, 8)}</code> · {status} · {progress}%
          </span>
        )}
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>Logs</h3>
        <div className="log-view">{logs.join("\n") || "Waiting for job…"}</div>
      </div>

      <div className="panel">
        <h3>Recent jobs</h3>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>ID</th>
                <th>Status</th>
                <th>Step</th>
                <th>Progress</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id}>
                  <td>
                    <button
                      className="btn"
                      style={{ padding: "0.2rem 0.5rem" }}
                      onClick={() => {
                        setJobId(j.job_id);
                        attachLogs(j.job_id);
                      }}
                    >
                      {j.job_id.slice(0, 8)}
                    </button>
                  </td>
                  <td>{j.status}</td>
                  <td>{j.current_step || "—"}</td>
                  <td>{j.progress}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
