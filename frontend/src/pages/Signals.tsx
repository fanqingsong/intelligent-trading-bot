import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import Gauge from "../components/Gauge";
import SignalChart from "../components/SignalChart";
import { ALGOS, recClass } from "../lib/status";

type AlgoSummary = {
  recommendation?: string;
  trade_score?: number | null;
};

type WatchItem = {
  symbol: string;
  name: string;
  exchange: string;
  vote?: string;
  algorithms?: Record<string, AlgoSummary>;
  close?: number | null;
  signal_timestamp?: string | null;
  signal_available?: boolean;
};

function avgScore(algos?: Record<string, AlgoSummary>): number | null {
  if (!algos) return null;
  const scores = ALGOS.map((a) => algos[a]?.trade_score).filter(
    (s): s is number => typeof s === "number" && Number.isFinite(s),
  );
  if (!scores.length) return null;
  return scores.reduce((a, b) => a + b, 0) / scores.length;
}

function voteFallbackScore(vote?: string): number {
  if (vote === "BUY") return 0.55;
  if (vote === "SELL") return -0.55;
  return 0;
}

function normalizeVote(vote?: string): "BUY" | "SELL" | "HOLD" {
  if (vote === "BUY" || vote === "SELL") return vote;
  return "HOLD";
}

function formatScore(score: number | null): string {
  if (score == null || !Number.isFinite(score)) return "—";
  return score.toFixed(3);
}

export default function SignalsPage() {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [data, setData] = useState<any>(null);
  const [symbol, setSymbol] = useState("");
  const [error, setError] = useState("");
  const [showTable, setShowTable] = useState(false);

  const selected = useMemo(
    () => items.find((it) => it.symbol === symbol) || null,
    [items, symbol],
  );

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      const sa = avgScore(a.algorithms) ?? voteFallbackScore(a.vote);
      const sb = avgScore(b.algorithms) ?? voteFallbackScore(b.vote);
      if (sb !== sa) return sb - sa;
      return a.symbol.localeCompare(b.symbol);
    });
  }, [items]);

  const loadBoard = useCallback(async () => {
    try {
      const r = await api.watchlist();
      const list = r.items || [];
      setItems(list);
      setSymbol((prev) => prev || (list[0]?.symbol ?? ""));
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }, []);

  const loadHistory = useCallback(
    async (sym?: string) => {
      const s = sym || symbol;
      if (!s) {
        setData(null);
        return;
      }
      try {
        const res = await api.signals(180, s);
        setData(res);
      } catch (e: any) {
        setError(String(e.message || e));
      }
    },
    [symbol],
  );

  useEffect(() => {
    loadBoard();
    const t = window.setInterval(loadBoard, 5000);
    return () => window.clearInterval(t);
  }, [loadBoard]);

  useEffect(() => {
    if (symbol) loadHistory(symbol);
  }, [symbol, loadHistory]);

  const openDetail = (sym: string) => {
    setSymbol(sym);
    window.requestAnimationFrame(() => {
      document.getElementById("signal-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  return (
    <div>
      <h1 className="page-title">Signals</h1>
      <p className="page-sub">
        按 Score 降序 · 显示 BUY / SELL / HOLD · 点击行查看详情与历史买卖点
      </p>
      {error && <p className="error">{error}</p>}

      {items.length === 0 ? (
        <div className="panel">
          <p className="muted">暂无关注股票，请先到 Watchlist 添加</p>
        </div>
      ) : (
        <div className="panel">
          <div className="table-wrap signals-table-wrap">
            <table className="data signals-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>信号</th>
                  <th>Score</th>
                  {ALGOS.map((a) => (
                    <th key={a}>{a.toUpperCase()}</th>
                  ))}
                  <th>收盘</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((it) => {
                  const score = avgScore(it.algorithms);
                  const vote = normalizeVote(it.vote);
                  const active = symbol === it.symbol;
                  return (
                    <tr
                      key={it.symbol}
                      className={`signals-row${active ? " active" : ""}`}
                      onClick={() => openDetail(it.symbol)}
                    >
                      <td>
                        <strong className="gauge-card-symbol">{it.symbol}</strong>
                      </td>
                      <td className="muted">{it.name || it.exchange || "—"}</td>
                      <td>
                        <span className={recClass(vote)}>{vote}</span>
                      </td>
                      <td className="signals-score">{formatScore(score)}</td>
                      {ALGOS.map((a) => {
                        const rec = it.algorithms?.[a]?.recommendation || "HOLD";
                        const sc = it.algorithms?.[a]?.trade_score;
                        return (
                          <td key={a} title={sc != null ? String(sc) : ""}>
                            <span className={recClass(rec)}>{rec}</span>
                          </td>
                        );
                      })}
                      <td>{it.close != null ? Number(it.close).toFixed(2) : "—"}</td>
                      <td className="muted">
                        {it.signal_timestamp
                          ? new Date(it.signal_timestamp).toLocaleString()
                          : "无信号"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selected && (
        <div className="panel signal-detail" id="signal-detail">
          <div className="btn-row signal-detail-bar">
            <div style={{ flex: 1 }}>
              <h3 style={{ margin: 0 }}>
                {selected.symbol}{" "}
                <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>
                  {selected.name || selected.exchange}
                </span>
              </h3>
              <p className="muted" style={{ margin: "0.35rem 0 0" }}>
                最新投票{" "}
                <span className={recClass(selected.vote)}>{selected.vote || "—"}</span>
                {selected.close != null && <> · 收盘 {Number(selected.close).toFixed(2)}</>}
                {" · "}
                共 {data?.total_rows ?? "—"} 行信号
              </p>
            </div>
            <select
              className="input"
              style={{ maxWidth: 220 }}
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            >
              {items.map((it) => (
                <option key={it.symbol} value={it.symbol}>
                  {it.symbol} {it.name || ""}
                </option>
              ))}
            </select>
            <button className="btn" onClick={() => loadHistory()}>
              刷新
            </button>
            <button className="btn" onClick={() => setShowTable((v) => !v)}>
              {showTable ? "隐藏表格" : "明细表格"}
            </button>
          </div>

          <div className="algo-gauge-row">
            {ALGOS.map((a) => {
              const algo = selected.algorithms?.[a];
              return (
                <div key={a} className="algo-gauge-cell">
                  <Gauge
                    value={algo?.trade_score ?? voteFallbackScore(algo?.recommendation)}
                    recommendation={algo?.recommendation || "HOLD"}
                    label={a.toUpperCase()}
                    size={132}
                  />
                </div>
              );
            })}
          </div>

          <h3 style={{ marginTop: "1.25rem" }}>历史买卖点 · K 线</h3>
          <SignalChart rows={data?.rows || []} height={400} />

          {showTable && (
            <>
              <h3 style={{ marginTop: "1.25rem" }}>信号明细</h3>
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
                    {[...(data?.rows || [])].reverse().map((row: any, i: number) => (
                      <tr key={i}>
                        {(data?.columns || []).map((c: string) => (
                          <td key={c}>{String(row[c] ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {(data?.rows || []).length === 0 && (
                <p className="muted">Postgres 中尚无信号数据</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
