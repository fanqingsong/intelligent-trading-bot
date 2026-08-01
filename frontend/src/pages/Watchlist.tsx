import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api";

type SuggestItem = {
  code: string;
  name: string;
  exchange: string;
  label: string;
};

type AlgoSummary = {
  recommendation?: string;
  trade_score?: number | null;
};

type WatchItem = {
  symbol: string;
  name: string;
  exchange: string;
  train_status: string;
  predict_status: string;
  last_error?: string;
  last_trained_at?: string | null;
  last_predicted_at?: string | null;
  vote?: string;
  algorithms?: Record<string, AlgoSummary>;
  signal_available?: boolean;
  close?: number | null;
  signal_timestamp?: string | null;
  last_train_job_id?: string;
  last_predict_job_id?: string;
};

const ALGOS = ["svc", "gb", "nn", "lc"] as const;

function badgeClass(status: string) {
  if (status === "ready" || status === "completed") return "badge ok";
  if (status === "running" || status === "queued") return "badge run";
  if (status === "failed" || status === "skipped") return "badge fail";
  return "badge";
}

function recClass(rec?: string) {
  if (rec === "BUY") return "rec buy";
  if (rec === "SELL") return "rec sell";
  return "rec hold";
}

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<SuggestItem | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestItem[]>([]);
  const [openSuggest, setOpenSuggest] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [schedule, setSchedule] = useState<any>(null);
  const [cronEdit, setCronEdit] = useState("0 16 * * 1-5");
  const esRef = useRef<EventSource | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);
  const blurTimer = useRef<number | null>(null);
  const suggestSeq = useRef(0);

  const load = useCallback(async () => {
    try {
      const [wl, sch] = await Promise.all([api.watchlist(), api.getSchedule()]);
      setItems(wl.items || []);
      setSchedule(sch);
      setCronEdit(sch.predict_cron || "0 16 * * 1-5");
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 5000);
    return () => {
      window.clearInterval(t);
      esRef.current?.close();
      if (blurTimer.current) window.clearTimeout(blurTimer.current);
    };
  }, [load]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  useEffect(() => {
    const q = query.trim();
    if (!q || (selected && selected.label === query)) {
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
  }, [query, selected]);

  const pick = (item: SuggestItem) => {
    setSelected(item);
    setQuery(item.label);
    setOpenSuggest(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!openSuggest || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (h + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (h - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(suggestions[highlight]);
    } else if (e.key === "Escape") {
      setOpenSuggest(false);
    }
  };

  const attachLogs = (jobId: string) => {
    esRef.current?.close();
    setLogs([]);
    const es = new EventSource(api.logsUrl(jobId));
    esRef.current = es;
    es.addEventListener("log", (ev) => {
      setLogs((prev) => [...prev.slice(-400), (ev as MessageEvent).data]);
    });
    es.addEventListener("done", () => {
      es.close();
      load();
    });
    es.addEventListener("error", () => {
      es.close();
    });
  };

  const addSymbol = async () => {
    const q = (selected?.code || query).trim();
    if (!q) return;
    setBusy("add");
    setError("");
    try {
      await api.watchlistAdd(q);
      setQuery("");
      setSelected(null);
      await load();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const removeSymbol = async (symbol: string) => {
    setBusy(`del-${symbol}`);
    try {
      await api.watchlistDelete(symbol);
      await load();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const trainOne = async (symbol: string) => {
    setBusy(`train-${symbol}`);
    setError("");
    try {
      const res = await api.watchlistTrain(symbol);
      if (res.job_id) attachLogs(res.job_id);
      await load();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const predictAll = async () => {
    setBusy("predict");
    setError("");
    try {
      const res = await api.watchlistPredict();
      const first = (res.jobs || [])[0];
      if (first?.job_id) attachLogs(first.job_id);
      if ((res.skipped || []).length && !(res.jobs || []).length) {
        setError(
          `Skipped untrained: ${(res.skipped || []).map((s: any) => s.symbol).join(", ")}`,
        );
      }
      await load();
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

  return (
    <div>
      <h1 className="page-title">Watchlist</h1>
      <p className="page-sub">
        维护关注股票 · 手动更新模型 · 一键/盘后预测 · 四算法信号 + 多数投票
      </p>
      {error && <p className="error">{error}</p>}

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <div className="btn-row" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
          <div className="suggest-wrap" style={{ flex: "1 1 280px", maxWidth: 420 }}>
            <input
              className="input suggest-input"
              placeholder="代码或名称，如 600519 / 茅台"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelected(null);
              }}
              onKeyDown={onKeyDown}
              onFocus={() => suggestions.length && setOpenSuggest(true)}
              onBlur={() => {
                blurTimer.current = window.setTimeout(() => setOpenSuggest(false), 150);
              }}
            />
            {openSuggest && (suggestions.length > 0 || suggestLoading) && (
              <ul className="suggest-list" role="listbox">
                {suggestLoading && suggestions.length === 0 && (
                  <li className="suggest-empty">搜索中…</li>
                )}
                {suggestions.map((s, i) => (
                  <li
                    key={s.code}
                    className={`suggest-item ${i === highlight ? "active" : ""}`}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      pick(s);
                    }}
                  >
                    <span className="suggest-code">{s.code}</span>
                    <span className="suggest-name">{s.name}</span>
                    <span className="suggest-ex">{s.exchange}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button className="btn primary" disabled={!!busy} onClick={addSymbol}>
            加入列表
          </button>
          <button className="btn" disabled={!!busy || items.length === 0} onClick={predictAll}>
            一键预测
          </button>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3 style={{ marginTop: 0 }}>盘后定时预测</h3>
        <div className="btn-row" style={{ flexWrap: "wrap" }}>
          <label className="muted" style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <input
              type="checkbox"
              checked={!!schedule?.predict_enabled}
              onChange={toggleSchedule}
              disabled={!!busy}
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
          <button className="btn" disabled={!!busy} onClick={saveSchedule}>
            保存调度
          </button>
          <span className="muted">时区 {schedule?.timezone || "Asia/Shanghai"}</span>
        </div>
      </div>

      <div className="panel">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>股票</th>
                <th>训练</th>
                <th>预测</th>
                <th>投票</th>
                {ALGOS.map((a) => (
                  <th key={a}>{a.toUpperCase()}</th>
                ))}
                <th>收盘</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.symbol}>
                  <td>
                    <strong>{it.symbol}</strong>
                    <div className="muted">{it.name || it.exchange}</div>
                    {it.last_error && <div className="error" style={{ fontSize: "0.8rem" }}>{it.last_error}</div>}
                  </td>
                  <td>
                    <span className={badgeClass(it.train_status)}>{it.train_status}</span>
                  </td>
                  <td>
                    <span className={badgeClass(it.predict_status)}>{it.predict_status}</span>
                  </td>
                  <td>
                    <span className={recClass(it.vote)}>{it.vote || "—"}</span>
                  </td>
                  {ALGOS.map((a) => (
                    <td key={a}>
                      <span className={recClass(it.algorithms?.[a]?.recommendation)}>
                        {it.algorithms?.[a]?.recommendation || "—"}
                      </span>
                    </td>
                  ))}
                  <td>{it.close != null ? Number(it.close).toFixed(2) : "—"}</td>
                  <td>
                    <div className="btn-row">
                      <button
                        className="btn primary"
                        disabled={!!busy}
                        onClick={() => trainOne(it.symbol)}
                      >
                        更新模型
                      </button>
                      <button
                        className="btn"
                        disabled={!!busy}
                        onClick={() => removeSymbol(it.symbol)}
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {items.length === 0 && <p className="muted">列表为空，先添加股票</p>}
      </div>

      {logs.length > 0 && (
        <div className="panel" style={{ marginTop: "1rem" }}>
          <h3 style={{ marginTop: 0 }}>Job 日志</h3>
          <pre className="log">
            {logs.join("\n")}
            <div ref={logEndRef} />
          </pre>
        </div>
      )}
    </div>
  );
}
