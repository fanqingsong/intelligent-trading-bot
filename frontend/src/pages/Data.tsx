import { useEffect, useState } from "react";
import { api } from "../api";

export default function DataPage() {
  const [files, setFiles] = useState<any[]>([]);
  const [preview, setPreview] = useState<any>(null);
  const [selected, setSelected] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [symbol, setSymbol] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
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
    if (!symbol) {
      setFiles([]);
      return;
    }
    api
      .dataFiles(symbol)
      .then((r) => setFiles(r.files))
      .catch((e) => setError(String(e.message || e)));
  }, [symbol]);

  const open = async (name: string) => {
    setSelected(name);
    setError("");
    try {
      const p = await api.preview(name, 30, symbol);
      setPreview(p);
    } catch (e: any) {
      setError(String(e.message || e));
      setPreview(null);
    }
  };

  return (
    <div>
      <h1 className="page-title">Data</h1>
      <p className="page-sub">Browse Postgres market_frames by symbol / kind</p>
      {error && <p className="error">{error}</p>}

      <div className="btn-row" style={{ marginBottom: "1rem" }}>
        <select
          className="input"
          style={{ maxWidth: 220 }}
          value={symbol}
          onChange={(e) => {
            setSymbol(e.target.value);
            setSelected("");
            setPreview(null);
          }}
        >
          {items.length === 0 && <option value="">No watchlist symbols</option>}
          {items.map((it) => (
            <option key={it.symbol} value={it.symbol}>
              {it.symbol} {it.name || ""}
            </option>
          ))}
        </select>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "280px 1fr", gap: "1rem" }}>
        <div className="panel">
          <h3>Frames</h3>
          {files.length === 0 && <p className="muted">No frames</p>}
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {files.map((f) => (
              <li key={f.name} style={{ marginBottom: "0.35rem" }}>
                <button
                  className="btn"
                  style={{ width: "100%", textAlign: "left" }}
                  onClick={() => open(f.name)}
                >
                  {f.name} <span className="muted">({f.size} rows)</span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="panel">
          <h3>Preview {selected && `· ${selected}`}</h3>
          {!preview && <p className="muted">Select a frame</p>}
          {preview && (
            <>
              <p className="muted">Total rows: {preview.total_rows}</p>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      {(preview.columns || []).map((c: string) => (
                        <th key={c}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(preview.rows || []).map((row: any, i: number) => (
                      <tr key={i}>
                        {(preview.columns || []).map((c: string) => (
                          <td key={c}>{String(row[c] ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
