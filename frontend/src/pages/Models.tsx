import { Fragment, useEffect, useState } from "react";
import { api } from "../api";

const METRIC_KEYS = ["auc", "ap", "f1", "precision", "recall", "accuracy", "mae", "mape", "r2"];

function metricChips(metrics: Record<string, number>) {
  const entries = Object.entries(metrics || {}).filter(([k]) => METRIC_KEYS.includes(k));
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <div className="chip-row">
      {entries.map(([k, v]) => (
        <span key={k} className="chip" title={k}>
          <strong>{k}</strong> {typeof v === "number" ? v.toFixed(3) : String(v)}
        </span>
      ))}
    </div>
  );
}

export default function ModelsPage() {
  const [data, setData] = useState<any>(null);
  const [info, setInfo] = useState<{ tracking_uri: string; ui_url: string | null } | null>(null);
  const [models, setModels] = useState<any[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [items, setItems] = useState<any[]>([]);
  const [symbol, setSymbol] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.mlflowInfo().then(setInfo).catch(() => {});
    api
      .watchlist()
      .then((r) => {
        const list = r.items || [];
        setItems(list);
        if (!symbol && list.length) setSymbol(list[0].symbol);
      })
      .catch((e) => setError(String(e.message || e)));
  }, []);

  useEffect(() => {
    if (!symbol) return;
    api.models(symbol).then(setData).catch((e) => setError(String(e.message || e)));
    api
      .mlflowModels(symbol)
      .then((r) => setModels(r.models || []))
      .catch((e) => setError(String(e.message || e)));
  }, [symbol]);

  async function toggleVersions(name: string) {
    if (expanded === name) {
      setExpanded(null);
      setVersions([]);
      return;
    }
    setExpanded(name);
    setVersions([]);
    try {
      const r = await api.mlflowVersions(name);
      setVersions(r.versions || []);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }

  return (
    <div>
      <h1 className="page-title">Models</h1>
      <p className="page-sub">
        {info?.ui_url ? (
          <>
            MLflow registry ·{" "}
            <a href={info.ui_url} target="_blank" rel="noreferrer">
              open tracking UI ↗
            </a>
          </>
        ) : (
          "Trained model artifacts and prediction metrics"
        )}
      </p>
      {error && <p className="error">{error}</p>}

      <div className="btn-row" style={{ marginBottom: "1rem" }}>
        <select
          className="input"
          style={{ maxWidth: 220 }}
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
        >
          {items.length === 0 && <option value="">No watchlist symbols</option>}
          {items.map((it) => (
            <option key={it.symbol} value={it.symbol}>
              {it.symbol} {it.name || ""}
            </option>
          ))}
        </select>
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>MLflow registered models</h3>
        {models.length === 0 && <p className="muted">No registered models for this symbol yet.</p>}
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Model</th>
                <th>Latest</th>
                <th>Alias</th>
                <th>Metrics (latest run)</th>
                <th>Versions</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m: any) => (
                <Fragment key={m.name}>
                  <tr
                    key={m.name}
                    style={{ cursor: "pointer" }}
                    onClick={() => toggleVersions(m.name)}
                  >
                    <td>{m.column}</td>
                    <td>v{m.latest_version}</td>
                    <td>
                      {(m.aliases || []).map((a: string) => (
                        <span key={a} className="chip chip-good">{a}</span>
                      ))}
                      {(m.aliases || []).length === 0 && <span className="muted">—</span>}
                    </td>
                    <td>{metricChips(m.metrics)}</td>
                    <td>{m.n_versions}</td>
                  </tr>
                  {expanded === m.name && (
                    <tr key={`${m.name}-detail`}>
                      <td colSpan={5}>
                        <div className="table-wrap">
                          <table className="data">
                            <thead>
                              <tr>
                                <th>Version</th>
                                <th>Status</th>
                                <th>Alias</th>
                                <th>Metrics</th>
                                <th>Key params</th>
                              </tr>
                            </thead>
                            <tbody>
                              {(versions.length ? versions : []).map((v: any) => (
                                <tr key={v.version}>
                                  <td>v{v.version}</td>
                                  <td>{v.status}</td>
                                  <td>{(v.aliases || []).join(", ") || "—"}</td>
                                  <td>{metricChips(v.metrics)}</td>
                                  <td className="muted" style={{ maxWidth: 360 }}>
                                    {Object.entries(v.params || {})
                                      .filter(([k]) => ["algo", "label", "n_rows", "n_features", "eval_split"].includes(k))
                                      .map(([k, val]) => `${k}=${String(val)}`)
                                      .join("  ·  ") || "—"}
                                  </td>
                                </tr>
                              ))}
                              {versions.length === 0 && (
                                <tr>
                                  <td colSpan={5} className="muted">Loading…</td>
                                </tr>
                              )}
                            </tbody>
                          </table>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>Local staging files</h3>
        {(data?.files || []).length === 0 && <p className="muted">No model files</p>}
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Name</th>
                <th>Size</th>
                <th>Modified</th>
              </tr>
            </thead>
            <tbody>
              {(data?.files || []).map((f: any) => (
                <tr key={f.name}>
                  <td>{f.name}</td>
                  <td>{f.size}</td>
                  <td>{f.mtime ? new Date(f.mtime * 1000).toLocaleString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h3>Postgres frames</h3>
        <ul className="muted">
          {(data?.frames || []).map((f: any) => (
            <li key={f.kind}>
              {f.kind}: {f.rows} rows
            </li>
          ))}
          {(data?.frames || []).length === 0 && <li>No frames yet</li>}
        </ul>
      </div>
    </div>
  );
}
