import { useEffect, useState } from "react";
import { api } from "../api";

export default function Dashboard() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  const load = () => {
    api
      .dashboard()
      .then(setData)
      .catch((e) => setError(String(e.message || e)));
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const services = data?.health?.services || {};

  return (
    <div>
      <h1 className="page-title">Dashboard</h1>
      <p className="page-sub">
        {data?.description || "Service health and recent jobs"}
        {data?.symbol ? ` · ${data.symbol}` : ""}
        {data?.freq ? ` · ${data.freq}` : ""}
      </p>
      {error && <p className="error">{error}</p>}

      <div className="grid grid-3" style={{ marginBottom: "1rem" }}>
        {Object.entries(services).map(([name, status]) => (
          <div className="panel" key={name}>
            <h3>{name}</h3>
            <div className={`stat ${String(status) === "ok" ? "ok" : "danger"}`}>
              {String(status)}
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        <h3>Recent jobs</h3>
        {(data?.recent_jobs || []).length === 0 && <p className="muted">No jobs yet</p>}
        <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
          {(data?.recent_jobs || []).map((j: any) => (
            <li key={j.job_id} style={{ marginBottom: "0.4rem" }}>
              <code>{j.job_id.slice(0, 8)}</code>{" "}
              <span
                className={`badge ${
                  j.status === "completed" ? "ok" : j.status === "failed" ? "fail" : "run"
                }`}
              >
                {j.status}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
