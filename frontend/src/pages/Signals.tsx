import { useEffect, useState } from "react";
import { api } from "../api";

export default function SignalsPage() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  const load = () => {
    api
      .signals(80)
      .then(setData)
      .catch((e) => setError(String(e.message || e)));
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      <h1 className="page-title">Signals</h1>
      <p className="page-sub">
        Recent signal rows {data?.path ? `· ${data.path}` : ""}
      </p>
      {error && <p className="error">{error}</p>}

      <div className="btn-row">
        <button className="btn" onClick={load}>
          Refresh
        </button>
        <span className="muted">Total rows: {data?.total_rows ?? "—"}</span>
      </div>

      <div className="panel">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                {(data?.columns || []).map((c: string) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data?.rows || []).map((row: any, i: number) => (
                <tr key={i}>
                  {(data?.columns || []).map((c: string) => (
                    <td key={c}>{String(row[c] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {(data?.rows || []).length === 0 && <p className="muted">No signals file yet</p>}
      </div>
    </div>
  );
}
