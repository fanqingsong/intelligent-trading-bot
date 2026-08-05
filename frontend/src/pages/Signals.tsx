import { useCallback, useEffect, useMemo, useState, type MouseEvent } from "react";
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

type VoteFilter = "" | "BUY" | "SELL" | "HOLD";

type TableFilters = {
  symbol: string;
  name: string;
  vote: VoteFilter;
  scoreMin: string;
  scoreMax: string;
  algos: Record<(typeof ALGOS)[number], VoteFilter>;
  closeMin: string;
  closeMax: string;
  hasSignal: "" | "yes" | "no";
};

const EMPTY_FILTERS: TableFilters = {
  symbol: "",
  name: "",
  vote: "",
  scoreMin: "",
  scoreMax: "",
  algos: { svc: "", gb: "", nn: "", lc: "" },
  closeMin: "",
  closeMax: "",
  hasSignal: "",
};

const VOTE_OPTIONS: { value: VoteFilter; label: string }[] = [
  { value: "", label: "全部" },
  { value: "BUY", label: "BUY" },
  { value: "SELL", label: "SELL" },
  { value: "HOLD", label: "HOLD" },
];

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

function parseBound(raw: string): number | null {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function includesText(haystack: string, needle: string): boolean {
  const q = needle.trim().toLowerCase();
  if (!q) return true;
  return haystack.toLowerCase().includes(q);
}

function inRange(value: number | null, minRaw: string, maxRaw: string): boolean {
  const min = parseBound(minRaw);
  const max = parseBound(maxRaw);
  if (min == null && max == null) return true;
  if (value == null || !Number.isFinite(value)) return false;
  if (min != null && value < min) return false;
  if (max != null && value > max) return false;
  return true;
}

function filtersActive(f: TableFilters): boolean {
  if (f.symbol || f.name || f.vote || f.scoreMin || f.scoreMax) return true;
  if (f.closeMin || f.closeMax || f.hasSignal) return true;
  return ALGOS.some((a) => f.algos[a]);
}

function stopRowClick(e: MouseEvent) {
  e.stopPropagation();
}

export default function SignalsPage() {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [data, setData] = useState<any>(null);
  const [symbol, setSymbol] = useState("");
  const [error, setError] = useState("");
  const [showTable, setShowTable] = useState(false);
  const [filters, setFilters] = useState<TableFilters>(EMPTY_FILTERS);

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

  const filteredItems = useMemo(() => {
    return sortedItems.filter((it) => {
      if (!includesText(it.symbol, filters.symbol)) return false;
      if (!includesText(`${it.name || ""} ${it.exchange || ""}`, filters.name)) return false;

      const vote = normalizeVote(it.vote);
      if (filters.vote && vote !== filters.vote) return false;

      const score = avgScore(it.algorithms);
      if (!inRange(score, filters.scoreMin, filters.scoreMax)) return false;

      for (const a of ALGOS) {
        const want = filters.algos[a];
        if (!want) continue;
        const rec = normalizeVote(it.algorithms?.[a]?.recommendation);
        if (rec !== want) return false;
      }

      if (!inRange(it.close ?? null, filters.closeMin, filters.closeMax)) return false;

      if (filters.hasSignal === "yes" && !it.signal_timestamp) return false;
      if (filters.hasSignal === "no" && it.signal_timestamp) return false;

      return true;
    });
  }, [sortedItems, filters]);

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

  const setFilter = <K extends keyof TableFilters>(key: K, value: TableFilters[K]) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
  };

  const setAlgoFilter = (algo: (typeof ALGOS)[number], value: VoteFilter) => {
    setFilters((prev) => ({ ...prev, algos: { ...prev.algos, [algo]: value } }));
  };

  const active = filtersActive(filters);

  return (
    <div>
      <h1 className="page-title">Signals</h1>
      <p className="page-sub">
        按 Score 降序 · 表头过滤审查 · 点击行查看详情与历史买卖点
      </p>
      {error && <p className="error">{error}</p>}

      {items.length === 0 ? (
        <div className="panel">
          <p className="muted">暂无关注股票，请先到 Watchlist 添加</p>
        </div>
      ) : (
        <div className="panel">
          <div className="signals-table-toolbar">
            <span className="muted">
              显示 {filteredItems.length} / {items.length}
            </span>
            {active && (
              <button type="button" className="btn" onClick={() => setFilters(EMPTY_FILTERS)}>
                清除过滤
              </button>
            )}
          </div>
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
                <tr className="signals-filter-row">
                  <th>
                    <input
                      className="input signals-filter-input"
                      placeholder="代码"
                      value={filters.symbol}
                      onChange={(e) => setFilter("symbol", e.target.value)}
                      onClick={stopRowClick}
                    />
                  </th>
                  <th>
                    <input
                      className="input signals-filter-input"
                      placeholder="名称"
                      value={filters.name}
                      onChange={(e) => setFilter("name", e.target.value)}
                      onClick={stopRowClick}
                    />
                  </th>
                  <th>
                    <select
                      className="input signals-filter-input"
                      value={filters.vote}
                      onChange={(e) => setFilter("vote", e.target.value as VoteFilter)}
                      onClick={stopRowClick}
                    >
                      {VOTE_OPTIONS.map((o) => (
                        <option key={o.value || "all"} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th>
                    <div className="signals-filter-range">
                      <input
                        className="input signals-filter-input"
                        type="number"
                        step="any"
                        placeholder="≥"
                        value={filters.scoreMin}
                        onChange={(e) => setFilter("scoreMin", e.target.value)}
                        onClick={stopRowClick}
                      />
                      <input
                        className="input signals-filter-input"
                        type="number"
                        step="any"
                        placeholder="≤"
                        value={filters.scoreMax}
                        onChange={(e) => setFilter("scoreMax", e.target.value)}
                        onClick={stopRowClick}
                      />
                    </div>
                  </th>
                  {ALGOS.map((a) => (
                    <th key={a}>
                      <select
                        className="input signals-filter-input"
                        value={filters.algos[a]}
                        onChange={(e) => setAlgoFilter(a, e.target.value as VoteFilter)}
                        onClick={stopRowClick}
                      >
                        {VOTE_OPTIONS.map((o) => (
                          <option key={o.value || "all"} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </th>
                  ))}
                  <th>
                    <div className="signals-filter-range">
                      <input
                        className="input signals-filter-input"
                        type="number"
                        step="any"
                        placeholder="≥"
                        value={filters.closeMin}
                        onChange={(e) => setFilter("closeMin", e.target.value)}
                        onClick={stopRowClick}
                      />
                      <input
                        className="input signals-filter-input"
                        type="number"
                        step="any"
                        placeholder="≤"
                        value={filters.closeMax}
                        onChange={(e) => setFilter("closeMax", e.target.value)}
                        onClick={stopRowClick}
                      />
                    </div>
                  </th>
                  <th>
                    <select
                      className="input signals-filter-input"
                      value={filters.hasSignal}
                      onChange={(e) =>
                        setFilter("hasSignal", e.target.value as "" | "yes" | "no")
                      }
                      onClick={stopRowClick}
                    >
                      <option value="">全部</option>
                      <option value="yes">有信号</option>
                      <option value="no">无信号</option>
                    </select>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredItems.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="muted signals-empty-filter">
                      无匹配行，请调整过滤条件
                    </td>
                  </tr>
                ) : (
                  filteredItems.map((it) => {
                    const score = avgScore(it.algorithms);
                    const vote = normalizeVote(it.vote);
                    const isActive = symbol === it.symbol;
                    return (
                      <tr
                        key={it.symbol}
                        className={`signals-row${isActive ? " active" : ""}`}
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
                  })
                )}
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
