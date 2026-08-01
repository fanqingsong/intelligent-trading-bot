import { useEffect, useState } from "react";
import { api } from "../api";

export default function SignalsPage() {
  const [data, setData] = useState<any>(null);
  const [items, setItems] = useState<any[]>([]);
  const [symbol, setSymbol] = useState("");
  const [error, setError] = useState("");

  const loadList = () => {
    api
      .watchlist()
      .then((r) => {
        const list = r.items || [];
        setItems(list);
        if (!symbol && list.length) setSymbol(list[0].symbol);
      })
      .catch((e) => setError(String(e.message || e)));
  };

  const load = (sym?: string) => {
    const s = sym || symbol;
    if (!s) {
      setData(null);
      return;
    }
    api
      .signals(80, s)
      .then(setData)
      .catch((e) => setError(String(e.message || e)));
  };

  useEffect(() => {
    loadList();
  }, []);

  useEffect(() => {
    if (symbol) load(symbol);
  }, [symbol]);

  return (
    <div>
      <h1 className="page-title">Signals</h1>
      <p className="page-sub">
        Postgres ``signals`` frames · per-algorithm columns + majority vote
        {data?.symbol ? ` · ${data.symbol}` : ""}
      </p>
      {error && <p className="error">{error}</p>}

      <div className="btn-row">
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
        <button className="btn" onClick={() => load()}>
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
        {(data?.rows || []).length === 0 && <p className="muted">No signals in Postgres yet</p>}
      </div>
    </div>
  );
}
