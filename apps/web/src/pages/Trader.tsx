import { useEffect, useState } from "react";
import { api } from "../api";

export default function TraderPage() {
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => {
    api
      .traderStatus()
      .then(setStatus)
      .catch((e) => setError(String(e.message || e)));
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  const act = async (action: string) => {
    setError("");
    setMsg("");
    try {
      const res = (await api.traderControl(action)) as { message?: string; state?: any };
      setMsg(res.message || action);
      setStatus(res.state || (await api.traderStatus()));
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  const latest = status?.latest || {};

  return (
    <div>
      <h1 className="page-title">Trader</h1>
      <p className="page-sub">Online service control and latest analysis snapshot</p>
      {error && <p className="error">{error}</p>}
      {msg && <p className="muted">{msg}</p>}

      <div className="btn-row">
        <button className="btn primary" onClick={() => act("start")}>
          Start
        </button>
        <button className="btn" onClick={() => act("stop")}>
          Stop
        </button>
        <button className="btn" onClick={() => act("pause")}>
          Pause
        </button>
        <button className="btn" onClick={() => act("resume")}>
          Resume
        </button>
        <button className="btn" onClick={() => act("reload-config")}>
          Reload config
        </button>
      </div>

      <div className="grid grid-3" style={{ marginBottom: "1rem" }}>
        <div className="panel">
          <h3>Running</h3>
          <div className={`stat ${status?.running ? "ok" : ""}`}>{String(status?.running)}</div>
        </div>
        <div className="panel">
          <h3>Paused</h3>
          <div className={`stat ${status?.paused ? "warn" : "ok"}`}>{String(status?.paused)}</div>
        </div>
        <div className="panel">
          <h3>Init OK</h3>
          <div className={`stat ${status?.init_ok ? "ok" : "danger"}`}>{String(status?.init_ok)}</div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>Meta</h3>
        <p className="muted">
          Symbol: {status?.symbol || "—"} · Freq: {status?.freq || "—"}
        </p>
        <p className="muted">Started: {status?.started_at || "—"}</p>
        <p className="muted">Last tick: {status?.last_tick_at || "—"}</p>
        {status?.last_error && <p className="error">{status.last_error}</p>}
      </div>

      <div className="panel">
        <h3>Latest values</h3>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Field</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(latest).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td>{String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
