import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { api } from "../api";
import Gauge from "../components/Gauge";
import JobProgress, { type JobProgressInfo } from "../components/JobProgress";
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
  predict_status?: string;
  predict_job?: JobProgressInfo | null;
  job_progress?: JobProgressInfo | null;
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
  { value: "HOLD", label: "无信号" },
];

function hasTradeSignal(it: WatchItem): boolean {
  return Boolean(it.signal_timestamp) && (it.vote === "BUY" || it.vote === "SELL");
}

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
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
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

      if (filters.hasSignal === "yes" && !hasTradeSignal(it)) return false;
      if (filters.hasSignal === "no" && hasTradeSignal(it)) return false;

      return true;
    });
  }, [sortedItems, filters]);

  const predicting = useMemo(
    () =>
      items.some(
        (it) => it.predict_status === "running" || it.predict_status === "queued",
      ),
    [items],
  );

  const predictStats = useMemo(() => {
    let queued = 0;
    let running = 0;
    let failed = 0;
    for (const it of items) {
      if (it.predict_status === "queued") queued += 1;
      else if (it.predict_status === "running") running += 1;
      else if (it.predict_status === "failed") failed += 1;
    }
    return { queued, running, failed, active: queued + running };
  }, [items]);

  const activePredictJob = useMemo(() => {
    const jobOf = (it: WatchItem) => it.job_progress || it.predict_job || null;
    const running = items.find((it) => it.predict_status === "running" && jobOf(it));
    if (running) return jobOf(running);
    // Prefer the queued job that already has step progress over a pure "排队中".
    let best: JobProgressInfo | null = null;
    let bestProgress = -1;
    for (const it of items) {
      if (it.predict_status !== "queued") continue;
      const job = jobOf(it);
      if (!job) continue;
      const p = Number(job.progress || 0);
      if (p > bestProgress || (p === bestProgress && job.current_step)) {
        best = job;
        bestProgress = p;
      }
    }
    return best;
  }, [items]);

  const activeBatchLabel = useMemo(() => {
    if (activePredictJob?.kind === "download") return "数据更新";
    return "预测";
  }, [activePredictJob]);

  const loadBoard = useCallback(async () => {
    try {
      const r = await api.watchlist();
      const list = r.items || [];
      setItems(list);
      setSymbol((prev) => prev || (list[0]?.symbol ?? ""));
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

  const runWatchlistJobs = useCallback(
    async (mode: "data" | "predict", symbols?: string[]) => {
      setBusy(true);
      setError("");
      setInfo("");
      try {
        const res = await api.watchlistPredict(symbols, undefined, mode);
        await loadBoard();
        if (symbols?.length === 1) {
          await loadHistory(symbols[0]);
        }
        const nJobs = (res.jobs || []).length;
        const skipped = res.skipped || [];
        const label = mode === "data" ? "数据更新" : "预测";
        const scope = symbols?.length === 1 ? `${symbols[0]}：` : "";
        const batched = Boolean(res.batched) || (res.jobs || []).some((j: any) => j.batch);
        if (nJobs) {
          if (symbols?.length === 1) {
            setInfo(`${scope}已提交${label}`);
          } else if (batched) {
            const nSyms = (res.jobs || []).find((j: any) => j.batch)?.symbols?.length;
            setInfo(
              nSyms
                ? `已提交批量${label}（1 个任务 · ${nSyms} 只股票）`
                : `已提交批量${label}（1 个任务）`,
            );
          } else {
            setInfo(`已提交 ${nJobs} 只股票的${label}`);
          }
        }
        if (skipped.length) {
          const syms = skipped.map((s: any) => s.symbol).join(", ");
          if (mode === "data") {
            setError(nJobs ? `部分失败：${syms}` : `全部失败：${syms}`);
          } else {
            setError(
              nJobs
                ? `部分跳过（需先训练）：${syms}`
                : symbols?.length === 1
                  ? `${symbols[0]}：未训练，请先到 Models 更新模型`
                  : `全部跳过（需先到 Models 更新模型）：${syms}`,
            );
          }
        }
      } catch (e: any) {
        setError(String(e.message || e));
      } finally {
        setBusy(false);
      }
    },
    [loadBoard, loadHistory],
  );

  const updateDataAll = useCallback(() => runWatchlistJobs("data"), [runWatchlistJobs]);
  const predictAll = useCallback(() => runWatchlistJobs("predict"), [runWatchlistJobs]);
  const updateDataOne = useCallback(
    (sym: string) => {
      if (!sym) return;
      void runWatchlistJobs("data", [sym]);
    },
    [runWatchlistJobs],
  );
  const predictOne = useCallback(
    (sym: string) => {
      if (!sym) return;
      void runWatchlistJobs("predict", [sym]);
    },
    [runWatchlistJobs],
  );

  const cancelPredict = useCallback(async () => {
    setBusy(true);
    setError("");
    setInfo("");
    try {
      await api.watchlistPredictCancel();
      setInfo("已取消进行中的任务");
      await loadBoard();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }, [loadBoard]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let inFlight = false;

    const tick = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        await loadBoard();
      } finally {
        inFlight = false;
        if (!cancelled) {
          timer = window.setTimeout(tick, predicting ? 2000 : 5000);
        }
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [loadBoard, predicting]);

  useEffect(() => {
    if (symbol) loadHistory(symbol);
  }, [symbol, loadHistory]);

  // After a predict batch settles, refresh the open chart/history.
  const wasPredicting = useRef(false);
  useEffect(() => {
    if (wasPredicting.current && !predicting && symbol) {
      loadHistory(symbol);
    }
    wasPredicting.current = predicting;
  }, [predicting, symbol, loadHistory]);

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
        仅展示最新行情日的买卖信号 · HOLD/过期预测视为无信号 · 点击行查看详情
      </p>
      {error && <p className="error">{error}</p>}
      {info && <p className="muted">{info}</p>}

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
            <div className="btn-row" style={{ margin: 0 }}>
              {active && (
                <button type="button" className="btn" onClick={() => setFilters(EMPTY_FILTERS)}>
                  清除过滤
                </button>
              )}
              <button
                type="button"
                className="btn"
                disabled={busy || predicting || items.length === 0}
                onClick={updateDataAll}
                title="对关注列表仅下载最新行情（download）"
              >
                更新数据
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={busy || predicting || items.length === 0}
                onClick={predictAll}
                title="对关注列表执行 merge→…→predict→signals（不重新下载）"
              >
                预测
              </button>
              {predicting && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={cancelPredict}
                  title="取消排队中/运行中的任务"
                >
                  取消
                </button>
              )}
            </div>
          </div>
          {predicting && (
            <p className="muted" style={{ margin: "0 0 0.65rem" }}>
              {activeBatchLabel}批量进行中：运行 {predictStats.running} · 排队 {predictStats.queued}
              {predictStats.failed ? ` · 失败 ${predictStats.failed}` : ""}
              （worker 并发有限，排队属正常；可点「取消」）
            </p>
          )}
          {activePredictJob && <JobProgress job={activePredictJob} />}
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
                    const signal = hasTradeSignal(it);
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
                          {signal ? (
                            <span className={recClass(vote)}>{vote}</span>
                          ) : (
                            <span className="muted">无信号</span>
                          )}
                        </td>
                        <td className="signals-score">{formatScore(score)}</td>
                        {ALGOS.map((a) => {
                          const rec = it.algorithms?.[a]?.recommendation || "HOLD";
                          const sc = it.algorithms?.[a]?.trade_score;
                          const algoSignal = rec === "BUY" || rec === "SELL";
                          return (
                            <td key={a} title={sc != null ? String(sc) : ""}>
                              {algoSignal ? (
                                <span className={recClass(rec)}>{rec}</span>
                              ) : (
                                <span className="muted">—</span>
                              )}
                            </td>
                          );
                        })}
                        <td>{it.close != null ? Number(it.close).toFixed(2) : "—"}</td>
                        <td className="muted">
                          {signal && it.signal_timestamp
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
                最新信号{" "}
                {hasTradeSignal(selected) ? (
                  <span className={recClass(selected.vote)}>{selected.vote}</span>
                ) : (
                  <span>无信号</span>
                )}
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
            <button
              className="btn"
              disabled={busy || predicting}
              onClick={() => updateDataOne(selected.symbol)}
              title="仅下载当前股票最新行情"
            >
              更新数据
            </button>
            <button
              className="btn primary"
              disabled={busy || predicting}
              onClick={() => predictOne(selected.symbol)}
              title="对当前股票执行预测（不重新下载）"
            >
              预测
            </button>
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
