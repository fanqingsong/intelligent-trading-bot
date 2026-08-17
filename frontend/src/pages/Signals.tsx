import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import Gauge from "../components/Gauge";
import JobProgress, { type JobProgressInfo } from "../components/JobProgress";
import PrefectLink from "../components/PrefectLink";
import SignalChart from "../components/SignalChart";
import { ALGOS, badgeClass, recClass } from "../lib/status";

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
  has_signal?: boolean;
  train_status?: string;
  predict_status?: string;
  last_error?: string;
  last_train_job_id?: string;
  last_trained_at?: string | null;
  last_predicted_at?: string | null;
  last_data_downloaded_at?: string | null;
  predict_job?: JobProgressInfo | null;
  train_job?: JobProgressInfo | null;
  job_progress?: JobProgressInfo | null;
};

type SuggestItem = {
  code: string;
  name: string;
  exchange: string;
  label: string;
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
  train: string;
};

type TrainBatch = {
  batch_id: number;
  status: string;
  total: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  skipped: number;
  current_symbol: string;
  last_error?: string;
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
  train: "",
};

const VOTE_OPTIONS: { value: VoteFilter; label: string }[] = [
  { value: "", label: "全部" },
  { value: "BUY", label: "BUY" },
  { value: "SELL", label: "SELL" },
  { value: "HOLD", label: "HOLD" },
];

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

function normalizeVote(vote?: string): "BUY" | "SELL" | "HOLD" {
  if (vote === "BUY" || vote === "SELL") return vote;
  return "HOLD";
}

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return "—";
  }
}

function hasTradeSignal(it: WatchItem): boolean {
  if (typeof it.has_signal === "boolean") return it.has_signal;
  return Boolean(it.signal_available) && (it.vote === "BUY" || it.vote === "SELL");
}

function voteLabel(it: WatchItem): "BUY" | "SELL" | "HOLD" | null {
  if (!it.signal_available) return null;
  return normalizeVote(it.vote);
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
  if (f.closeMin || f.closeMax || f.hasSignal || f.train) return true;
  return ALGOS.some((a) => f.algos[a]);
}

function stopRowClick(e: MouseEvent) {
  e.stopPropagation();
}

export default function SignalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState<WatchItem[]>([]);
  const [signalData, setSignalData] = useState<any>(null);
  const [modelData, setModelData] = useState<any>(null);
  const [mlflowInfo, setMlflowInfo] = useState<{ tracking_uri: string; ui_url: string | null } | null>(
    null,
  );
  const [models, setModels] = useState<any[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [symbol, setSymbol] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState("");
  // Watchlist management state
  const [query, setQuery] = useState("");
  const [suggestSelected, setSuggestSelected] = useState<SuggestItem | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestItem[]>([]);
  const [openSuggest, setOpenSuggest] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const blurTimer = useRef<number | null>(null);
  const suggestSeq = useRef(0);
  const [showTable, setShowTable] = useState(false);
  const [showAssets, setShowAssets] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [filters, setFilters] = useState<TableFilters>(EMPTY_FILTERS);
  const [logs, setLogs] = useState<string[]>([]);
  const [prefectUrl, setPrefectUrl] = useState<string | null>(null);
  const [schedule, setSchedule] = useState<any>(null);
  const [cronEdit, setCronEdit] = useState("0 16 * * 1-5");
  const [trainBatch, setTrainBatch] = useState<TrainBatch | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const logViewRef = useRef<HTMLDivElement | null>(null);

  const selected = useMemo(
    () => (detailOpen ? items.find((it) => it.symbol === symbol) || null : null),
    [items, symbol, detailOpen],
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

      if (filters.vote && voteLabel(it) !== filters.vote) return false;

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

      if (filters.train && (it.train_status || "") !== filters.train) return false;

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

  const training = useMemo(
    () =>
      !!trainBatch ||
      items.some((it) => it.train_status === "running" || it.train_status === "queued"),
    [items, trainBatch],
  );


  const loadBoard = useCallback(async () => {
    try {
      const [wl, sch, active] = await Promise.all([
        api.watchlist(),
        api.getSchedule().catch(() => null),
        api.watchlistTrainActive().catch(() => ({ batch: null })),
      ]);
      const list: WatchItem[] = wl.items || [];
      setItems(list);
      if (sch) {
        setSchedule(sch);
        setCronEdit(sch.predict_cron || "0 16 * * 1-5");
      }
      setTrainBatch(active.batch || null);
      setSymbol((prev) => {
        if (prev && list.some((it) => it.symbol === prev)) return prev;
        const fromQuery = searchParams.get("symbol") || "";
        if (fromQuery && list.some((it) => it.symbol === fromQuery)) return fromQuery;
        return prev;
      });
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }, [searchParams]);

  const loadHistory = useCallback(
    async (sym?: string) => {
      const s = sym || symbol;
      if (!s) {
        setSignalData(null);
        return;
      }
      try {
        const res = await api.signals(180, s);
        setSignalData(res);
      } catch (e: any) {
        setError(String(e.message || e));
      }
    },
    [symbol],
  );

  const loadModelAssets = useCallback(async (sym: string) => {
    if (!sym) {
      setModelData(null);
      setModels([]);
      return;
    }
    try {
      const [local, mf] = await Promise.all([
        api.models(sym),
        api.mlflowModels(sym),
      ]);
      setModelData(local);
      setModels(mf.models || []);
      setExpanded(null);
      setVersions([]);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }, []);

  const attachLogs = (jobId: string, uiUrl?: string | null) => {
    esRef.current?.close();
    setLogs([]);
    if (uiUrl) setPrefectUrl(uiUrl);
    const es = new EventSource(api.logsUrl(jobId));
    esRef.current = es;
    es.addEventListener("log", (ev) => {
      setLogs((prev) => [...prev.slice(-400), (ev as MessageEvent).data]);
    });
    es.addEventListener("done", () => {
      es.close();
      void loadBoard();
    });
    es.addEventListener("error", () => {
      es.close();
    });
    api
      .getJob(jobId)
      .then((j) => {
        if (j.prefect_ui_url) setPrefectUrl(j.prefect_ui_url);
      })
      .catch(() => undefined);
  };

  const canStopTrain = (it?: WatchItem | null) => {
    if (!it) return false;
    if (it.train_status === "running") return true;
    if (it.train_status === "queued" && it.last_train_job_id) return true;
    if (trainBatch && trainBatch.current_symbol === it.symbol) return true;
    return false;
  };

  const runWatchlistJobs = useCallback(
    async (mode: "data" | "predict", symbols?: string[]) => {
      setBusy(mode);
      setError("");
      setInfo("");
      try {
        const res = await api.watchlistPredict(symbols, undefined, mode);
        const first = (res.jobs || [])[0];
        if (first?.job_id) attachLogs(first.job_id, first.prefect_ui_url);
        await loadBoard();
        if (symbols?.length === 1) {
          await loadHistory(symbols[0]);
        }
        const nJobs = (res.jobs || []).length;
        const skipped = res.skipped || [];
        const label = mode === "data" ? "数据更新" : "预测";
        if (nJobs) {
          setInfo(`${symbols![0]}：已提交${label}`);
        }
        if (skipped.length) {
          const sym = symbols![0];
          if (mode === "data") {
            setError(`全部失败：${skipped.map((s: any) => s.symbol).join(", ")}`);
          } else {
            setError(`${sym}：未训练，请先更新模型`);
          }
        }
      } catch (e: any) {
        setError(String(e.message || e));
      } finally {
        setBusy("");
      }
    },
    [loadBoard, loadHistory],
  );

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

  const trainOne = async (sym: string) => {
    setBusy(`train-${sym}`);
    setError("");
    setInfo("");
    try {
      const res = await api.watchlistTrain(sym);
      if (res.job_id) attachLogs(res.job_id, res.prefect_ui_url);
      setInfo(`${sym}：已提交模型更新`);
      await loadBoard();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const cancelTrainOne = async (sym: string) => {
    setBusy(`cancel-${sym}`);
    setError("");
    setInfo("");
    try {
      await api.watchlistTrainSymbolCancel(sym);
      setInfo(`${sym}：已停止模型更新`);
      await loadBoard();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  // --- Watchlist management ---

  const pickSuggest = (item: SuggestItem) => {
    setSuggestSelected(item);
    setQuery(item.label);
    setOpenSuggest(false);
  };

  const onSuggestKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!openSuggest || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (h + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (h - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pickSuggest(suggestions[highlight]);
    } else if (e.key === "Escape") {
      setOpenSuggest(false);
    }
  };

  const addSymbol = async () => {
    const q = (suggestSelected?.code || query).trim();
    if (!q) return;
    setBusy("add");
    setError("");
    setInfo("");
    try {
      await api.watchlistAdd(q);
      setQuery("");
      setSuggestSelected(null);
      await loadBoard();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const removeSymbol = async (sym: string) => {
    setBusy(`del-${sym}`);
    setError("");
    setInfo("");
    try {
      await api.watchlistDelete(sym);
      if (detailOpen && symbol === sym) closeDetail();
      await loadBoard();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const importIndex = async (index: "sse50" | "csi300", label: string) => {
    if (!window.confirm(`将导入${label}全部成分股到关注列表（已存在的会跳过），是否继续？`)) {
      return;
    }
    setBusy(`import-${index}`);
    setError("");
    setInfo("");
    try {
      const res = await api.watchlistImport(index);
      setInfo(
        `已导入${res.index_name}：新增 ${res.added} 只，跳过 ${res.skipped} 只（共 ${res.total} 只成分股）`,
      );
      await loadBoard();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const saveSchedule = async () => {
    setBusy("schedule");
    try {
      const sch = await api.putSchedule({
        predict_enabled: !!schedule?.predict_enabled,
        predict_cron: cronEdit,
        timezone: schedule?.timezone || "Asia/Shanghai",
      });
      setSchedule(sch);
      setInfo("调度已保存");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const toggleSchedule = async () => {
    setBusy("schedule");
    try {
      const sch = await api.putSchedule({
        predict_enabled: !schedule?.predict_enabled,
        predict_cron: cronEdit,
        timezone: schedule?.timezone || "Asia/Shanghai",
      });
      setSchedule(sch);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

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

  // Debounced suggestion search
  useEffect(() => {
    const q = query.trim();
    if (!q || (suggestSelected && suggestSelected.label === query)) {
      setSuggestions([]);
      setSuggestLoading(false);
      return;
    }
    const seq = ++suggestSeq.current;
    setSuggestLoading(true);
    const t = window.setTimeout(async () => {
      try {
        const res = await api.watchlistSuggest(q);
        if (seq !== suggestSeq.current) return;
        setSuggestions(res.items || []);
        setHighlight(0);
        setOpenSuggest(true);
      } catch {
        if (seq !== suggestSeq.current) return;
        setSuggestions([]);
      } finally {
        if (seq === suggestSeq.current) setSuggestLoading(false);
      }
    }, 220);
    return () => window.clearTimeout(t);
  }, [query, suggestSelected]);

  useEffect(() => {
    api.mlflowInfo().then(setMlflowInfo).catch(() => {});
    void loadBoard();
    return () => {
      esRef.current?.close();
    };
    // Mount-only: polling must not restart (and kill SSE) when query changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let inFlight = false;
    const ms = predicting || training ? 2000 : 5000;

    const tick = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        await loadBoard();
      } finally {
        inFlight = false;
        if (!cancelled) {
          timer = window.setTimeout(tick, ms);
        }
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [loadBoard, predicting, training]);

  useEffect(() => {
    const fromQuery = searchParams.get("symbol");
    if (!fromQuery) return;
    setSymbol(fromQuery);
    setDetailOpen(true);
  }, [searchParams]);

  useEffect(() => {
    if (detailOpen && symbol) {
      void loadHistory(symbol);
      void loadModelAssets(symbol);
    }
  }, [detailOpen, symbol, loadHistory, loadModelAssets]);

  const wasPredicting = useRef(false);
  useEffect(() => {
    if (wasPredicting.current && !predicting && detailOpen && symbol) {
      void loadHistory(symbol);
      void loadModelAssets(symbol);
    }
    wasPredicting.current = predicting;
  }, [predicting, detailOpen, symbol, loadHistory, loadModelAssets]);

  useEffect(() => {
    const el = logViewRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  useEffect(() => {
    const url = selected?.job_progress?.prefect_ui_url;
    if (url) setPrefectUrl(url);
  }, [selected?.job_progress?.prefect_ui_url]);

  const openDetail = (sym: string) => {
    setSymbol(sym);
    setDetailOpen(true);
    setShowTable(false);
    setShowAssets(false);
    setSearchParams({ symbol: sym }, { replace: true });
  };

  const closeDetail = useCallback(() => {
    setDetailOpen(false);
    setSearchParams({}, { replace: true });
  }, [setSearchParams]);

  useEffect(() => {
    if (!detailOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDetail();
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [detailOpen, closeDetail]);

  const setFilter = <K extends keyof TableFilters>(key: K, value: TableFilters[K]) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
  };

  const setAlgoFilter = (algo: (typeof ALGOS)[number], value: VoteFilter) => {
    setFilters((prev) => ({ ...prev, algos: { ...prev.algos, [algo]: value } }));
  };

  const active = filtersActive(filters);
  const anyBusy = !!busy;

  return (
    <div>
      <h1 className="page-title">Signals</h1>
      <p className="page-sub">
        训练模型 · 更新数据 · 预测信号 · 盘后调度
        {mlflowInfo?.ui_url ? (
          <>
            {" · "}
            <a href={mlflowInfo.ui_url} target="_blank" rel="noreferrer">
              MLflow UI ↗
            </a>
          </>
        ) : null}
      </p>
      {error && <p className="error">{error}</p>}
      {info && <p className="muted">{info}</p>}

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3 style={{ marginTop: 0 }}>股票管理</h3>
        <div className="btn-row" style={{ flexWrap: "wrap", marginBottom: "0.75rem" }}>
          <div style={{ position: "relative", flex: 1, minWidth: 200 }}>
            <input
              className="input suggest-input"
              placeholder="输入代码或名称搜索…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSuggestSelected(null);
              }}
              onFocus={() => suggestions.length > 0 && setOpenSuggest(true)}
              onBlur={() => {
                blurTimer.current = window.setTimeout(() => setOpenSuggest(false), 150);
              }}
              onKeyDown={onSuggestKeyDown}
              disabled={anyBusy}
            />
            {suggestLoading && (
              <span className="muted" style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", fontSize: "0.8rem" }}>
                …
              </span>
            )}
            {openSuggest && suggestions.length > 0 && (
              <ul className="suggest-list">
                {suggestions.map((s, i) => (
                  <li
                    key={s.code}
                    className={`suggest-item${i === highlight ? " highlight" : ""}`}
                    onMouseDown={() => pickSuggest(s)}
                  >
                    <span className="suggest-code">{s.code}</span>
                    <span className="suggest-name">{s.name}</span>
                    <span className="suggest-ex">{s.exchange}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button
            type="button"
            className="btn"
            disabled={anyBusy || !query.trim()}
            onClick={addSymbol}
          >
            加入列表
          </button>
          <button
            type="button"
            className="btn"
            disabled={anyBusy}
            onClick={() => importIndex("sse50", "上证50")}
          >
            导入上证50
          </button>
          <button
            type="button"
            className="btn"
            disabled={anyBusy}
            onClick={() => importIndex("csi300", "沪深300")}
          >
            导入沪深300
          </button>
        </div>

        <h3>盘后定时预测</h3>
        <div className="btn-row" style={{ flexWrap: "wrap", marginBottom: 0 }}>
          <label className="muted" style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <input
              type="checkbox"
              checked={!!schedule?.predict_enabled}
              onChange={toggleSchedule}
              disabled={anyBusy}
            />
            启用（默认 cron: 工作日 16:00 Asia/Shanghai）
          </label>
          <input
            className="input"
            style={{ maxWidth: 180 }}
            value={cronEdit}
            onChange={(e) => setCronEdit(e.target.value)}
            placeholder="0 16 * * 1-5"
          />
          <button type="button" className="btn" disabled={anyBusy} onClick={saveSchedule}>
            保存调度
          </button>
          <span className="muted">时区 {schedule?.timezone || "Asia/Shanghai"}</span>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="panel">
          <p className="muted">暂无关注股票，请在上方搜索并添加股票，或导入指数成分股</p>
        </div>
      ) : (
        <div className="panel">
          <div className="signals-table-toolbar">
            <span className="muted">
              显示 {filteredItems.length} / {items.length} · 点击行查看信号与模型详情
            </span>
            <div className="btn-row" style={{ margin: 0 }}>
              {active && (
                <button type="button" className="btn" onClick={() => setFilters(EMPTY_FILTERS)}>
                  清除过滤
                </button>
              )}
            </div>
          </div>
          <div className="table-wrap signals-table-wrap">
            <table className="data signals-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>最近训练</th>
                  <th>最近数据</th>
                  <th>最近预测</th>
                  <th>操作</th>
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
                  <th />
                  <th />
                  <th />
                  <th />
                </tr>
              </thead>
              <tbody>
                {filteredItems.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="muted signals-empty-filter">
                      无匹配行，请调整过滤条件
                    </td>
                  </tr>
                ) : (
                  filteredItems.map((it) => {
                    const isActive = detailOpen && symbol === it.symbol;
                    return (
                      <tr
                        key={it.symbol}
                        className={`signals-row${isActive ? " active" : ""}`}
                      >
                        <td>
                          <strong className="gauge-card-symbol">{it.symbol}</strong>
                        </td>
                        <td className="muted">{it.name || it.exchange || "—"}</td>
                        <td className="muted" style={{ whiteSpace: "nowrap" }}>
                          {formatTime(it.last_trained_at)}
                        </td>
                        <td className="muted" style={{ whiteSpace: "nowrap" }}>
                          {formatTime(it.last_data_downloaded_at)}
                        </td>
                        <td className="muted" style={{ whiteSpace: "nowrap" }}>
                          {formatTime(it.last_predicted_at)}
                        </td>
                        <td onClick={stopRowClick}>
                          <div className="btn-row" style={{ gap: "0.35rem" }}>
                            <button
                              type="button"
                              className="btn"
                              disabled={anyBusy}
                              onClick={() => openDetail(it.symbol)}
                            >
                              详情
                            </button>
                            <button
                              type="button"
                              className="btn danger"
                              disabled={anyBusy}
                              onClick={() => removeSymbol(it.symbol)}
                              title="从关注列表移除"
                            >
                              ×
                            </button>
                          </div>
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

      {logs.length > 0 && (
        <div className="panel" style={{ marginTop: "1rem" }}>
          <h3 style={{ marginTop: 0 }}>
            Job 日志 <PrefectLink url={prefectUrl} />
          </h3>
          <div className="log-view" ref={logViewRef}>
            {logs.join("\n")}
          </div>
        </div>
      )}

      {selected &&
        createPortal(
          <div className="modal-overlay" role="presentation" onClick={closeDetail}>
            <div
              className="modal-dialog signal-detail-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="signal-detail-title"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="btn-row signal-detail-bar">
                <div style={{ flex: 1 }}>
                  <h3 id="signal-detail-title" style={{ margin: 0 }}>
                    {selected.symbol}{" "}
                    <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>
                      {selected.name || selected.exchange}
                    </span>
                  </h3>
                  <p className="muted" style={{ margin: "0.35rem 0 0" }}>
                    训练{" "}
                    <span className={badgeClass(selected.train_status || "untrained")}>
                      {selected.train_status || "untrained"}
                    </span>
                    {" · "}
                    最新信号{" "}
                    {(() => {
                      const v = voteLabel(selected);
                      return v ? <span className={recClass(v)}>{v}</span> : <span>无信号</span>;
                    })()}
                    {selected.close != null && <> · 收盘 {Number(selected.close).toFixed(2)}</>}
                    {" · "}
                    共 {signalData?.total_rows ?? "—"} 行信号
                  </p>
                </div>
                <select
                  className="input"
                  style={{ maxWidth: 220 }}
                  value={symbol}
                  onChange={(e) => {
                    const next = e.target.value;
                    setSymbol(next);
                    setSearchParams({ symbol: next }, { replace: true });
                  }}
                >
                  {items.map((it) => (
                    <option key={it.symbol} value={it.symbol}>
                      {it.symbol} {it.name || ""}
                    </option>
                  ))}
                </select>
                {canStopTrain(selected) ? (
                  <button
                    className="btn"
                    disabled={anyBusy}
                    onClick={() => cancelTrainOne(selected.symbol)}
                    title="停止当前股票的训练任务"
                  >
                    停止更新
                  </button>
                ) : (
                  <button
                    className="btn primary"
                    disabled={anyBusy || !!trainBatch}
                    onClick={() => trainOne(selected.symbol)}
                    title="训练/更新当前股票模型"
                  >
                    更新模型
                  </button>
                )}
                <button
                  className="btn"
                  disabled={anyBusy || predicting}
                  onClick={() => updateDataOne(selected.symbol)}
                  title="仅下载当前股票最新行情"
                >
                  更新数据
                </button>
                <button
                  className="btn primary"
                  disabled={anyBusy || predicting}
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
                <button className="btn" onClick={() => setShowAssets((v) => !v)}>
                  {showAssets ? "隐藏模型资产" : "模型资产"}
                </button>
                <button
                  type="button"
                  className="btn modal-close"
                  onClick={closeDetail}
                  aria-label="关闭"
                  title="关闭"
                >
                  ×
                </button>
              </div>

              {selected.job_progress &&
                (selected.train_status === "running" ||
                  selected.train_status === "queued" ||
                  selected.predict_status === "running" ||
                  selected.predict_status === "queued") && (
                  <JobProgress job={selected.job_progress} />
                )}
              {selected.last_error &&
                selected.train_status !== "running" &&
                selected.train_status !== "queued" && (
                  <p className="error" style={{ fontSize: "0.85rem" }}>
                    {selected.last_error}
                  </p>
                )}

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
              <SignalChart rows={signalData?.rows || []} height={400} />

              {showTable && (
                <>
                  <h3 style={{ marginTop: "1.25rem" }}>信号明细</h3>
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          {(signalData?.columns || []).map((c: string) => (
                            <th key={c}>{c}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {[...(signalData?.rows || [])].reverse().map((row: any, i: number) => (
                          <tr key={i}>
                            {(signalData?.columns || []).map((c: string) => (
                              <td key={c}>{String(row[c] ?? "")}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {(signalData?.rows || []).length === 0 && (
                    <p className="muted">Postgres 中尚无信号数据</p>
                  )}
                </>
              )}

              {showAssets && (
                <>
                  <h3 style={{ marginTop: "1.25rem" }}>MLflow registered models</h3>
                  {models.length === 0 && (
                    <p className="muted">No registered models for this symbol yet.</p>
                  )}
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
                            <tr style={{ cursor: "pointer" }} onClick={() => toggleVersions(m.name)}>
                              <td>{m.column}</td>
                              <td>v{m.latest_version}</td>
                              <td>
                                {(m.aliases || []).map((a: string) => (
                                  <span key={a} className="chip chip-good">
                                    {a}
                                  </span>
                                ))}
                                {(m.aliases || []).length === 0 && <span className="muted">—</span>}
                              </td>
                              <td>{metricChips(m.metrics)}</td>
                              <td>{m.n_versions}</td>
                            </tr>
                            {expanded === m.name && (
                              <tr>
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
                                                .filter(([k]) =>
                                                  [
                                                    "algo",
                                                    "label",
                                                    "n_rows",
                                                    "n_features",
                                                    "eval_split",
                                                  ].includes(k),
                                                )
                                                .map(([k, val]) => `${k}=${String(val)}`)
                                                .join("  ·  ") || "—"}
                                            </td>
                                          </tr>
                                        ))}
                                        {versions.length === 0 && (
                                          <tr>
                                            <td colSpan={5} className="muted">
                                              Loading…
                                            </td>
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

                  <h3 style={{ marginTop: "1.25rem" }}>Local staging files</h3>
                  {(modelData?.files || []).length === 0 && <p className="muted">No model files</p>}
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
                        {(modelData?.files || []).map((f: any) => (
                          <tr key={f.name}>
                            <td>{f.name}</td>
                            <td>{f.size}</td>
                            <td>{f.mtime ? new Date(f.mtime * 1000).toLocaleString() : "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <h3 style={{ marginTop: "1.25rem" }}>Postgres frames</h3>
                  <ul className="muted">
                    {(modelData?.frames || []).map((f: any) => (
                      <li key={f.kind}>
                        {f.kind}: {f.rows} rows
                      </li>
                    ))}
                    {(modelData?.frames || []).length === 0 && <li>No frames yet</li>}
                  </ul>
                </>
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
