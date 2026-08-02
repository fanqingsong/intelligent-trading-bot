import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import JobProgress, { type JobProgressInfo } from "../components/JobProgress";
import PrefectLink from "../components/PrefectLink";
import { badgeClass } from "../lib/status";

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

type WatchItem = {
  symbol: string;
  name: string;
  exchange: string;
  train_status: string;
  predict_status: string;
  last_error?: string;
  last_train_job_id?: string;
  last_predict_job_id?: string;
  job_progress?: JobProgressInfo | null;
  train_job?: JobProgressInfo | null;
  predict_job?: JobProgressInfo | null;
};

export default function ModelsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<any>(null);
  const [info, setInfo] = useState<{ tracking_uri: string; ui_url: string | null } | null>(null);
  const [models, setModels] = useState<any[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [items, setItems] = useState<WatchItem[]>([]);
  const [symbol, setSymbol] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [prefectUrl, setPrefectUrl] = useState<string | null>(null);
  const [schedule, setSchedule] = useState<any>(null);
  const [cronEdit, setCronEdit] = useState("0 16 * * 1-5");
  const [trainBatch, setTrainBatch] = useState<{
    batch_id: number;
    status: string;
    total: number;
    queued: number;
    running: number;
    completed: number;
    failed: number;
    skipped: number;
    current_symbol: string;
  } | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const logViewRef = useRef<HTMLDivElement | null>(null);

  const loadWatchlist = useCallback(async () => {
    try {
      const [wl, sch, active] = await Promise.all([
        api.watchlist(),
        api.getSchedule(),
        api.watchlistTrainActive().catch(() => ({ batch: null })),
      ]);
      const list: WatchItem[] = wl.items || [];
      setItems(list);
      setSchedule(sch);
      setCronEdit(sch.predict_cron || "0 16 * * 1-5");
      setTrainBatch(active.batch || null);
      setSymbol((prev) => {
        if (prev && list.some((it) => it.symbol === prev)) return prev;
        const fromQuery = searchParams.get("symbol") || "";
        if (fromQuery && list.some((it) => it.symbol === fromQuery)) return fromQuery;
        return list[0]?.symbol ?? "";
      });
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }, [searchParams]);

  useEffect(() => {
    api.mlflowInfo().then(setInfo).catch(() => {});
    loadWatchlist();
    return () => {
      esRef.current?.close();
    };
    // Mount-only: polling must not restart (and kill SSE) when query changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasInflightJobs =
    !!trainBatch ||
    items.some(
      (it) =>
        it.train_status === "running" ||
        it.train_status === "queued" ||
        it.predict_status === "running" ||
        it.predict_status === "queued",
    );

  // Poll faster while any train/predict job is in flight so step progress feels live.
  useEffect(() => {
    const ms = hasInflightJobs ? 2000 : 5000;
    const t = window.setInterval(() => {
      api
        .watchlist()
        .then((wl) => setItems(wl.items || []))
        .catch(() => {});
      api.getSchedule().then(setSchedule).catch(() => {});
      api
        .watchlistTrainActive()
        .then((r) => setTrainBatch(r.batch || null))
        .catch(() => {});
    }, ms);
    return () => window.clearInterval(t);
  }, [hasInflightJobs]);

  useEffect(() => {
    const fromQuery = searchParams.get("symbol");
    if (fromQuery) setSymbol(fromQuery);
  }, [searchParams]);

  useEffect(() => {
    if (!symbol) return;
    api.models(symbol).then(setData).catch((e) => setError(String(e.message || e)));
    api
      .mlflowModels(symbol)
      .then((r) => setModels(r.models || []))
      .catch((e) => setError(String(e.message || e)));
  }, [symbol]);

  useEffect(() => {
    const el = logViewRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  const selectSymbol = (sym: string) => {
    setSymbol(sym);
    if (sym) setSearchParams({ symbol: sym }, { replace: true });
    else setSearchParams({}, { replace: true });
  };

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
      loadWatchlist();
    });
    es.addEventListener("error", () => {
      es.close();
    });
    api.getJob(jobId).then((j) => {
      if (j.prefect_ui_url) setPrefectUrl(j.prefect_ui_url);
    }).catch(() => undefined);
  };

  const trainOne = async (sym: string) => {
    setBusy(`train-${sym}`);
    setError("");
    try {
      const res = await api.watchlistTrain(sym);
      if (res.job_id) attachLogs(res.job_id, res.prefect_ui_url);
      await loadWatchlist();
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
      if (first?.job_id) attachLogs(first.job_id, first.prefect_ui_url);
      if ((res.skipped || []).length && !(res.jobs || []).length) {
        setError(
          `Skipped untrained: ${(res.skipped || []).map((s: any) => s.symbol).join(", ")}`,
        );
      }
      await loadWatchlist();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const trainAll = async () => {
    setBusy("train-all");
    setError("");
    try {
      const res = await api.watchlistTrainAll();
      if (res.batch_id != null) {
        setTrainBatch({
          batch_id: res.batch_id,
          status: res.status,
          total: res.total,
          queued: res.queued,
          running: res.running,
          completed: res.completed,
          failed: res.failed,
          skipped: res.skipped,
          current_symbol: res.current_symbol,
        });
      }
      if (res.total === 0) {
        setError("关注列表为空，无法批量更新模型");
      }
      await loadWatchlist();
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

  const current = items.find((it) => it.symbol === symbol);

  // Keep Prefect link in sync with selected symbol's live job.
  useEffect(() => {
    const url = current?.job_progress?.prefect_ui_url;
    if (url) setPrefectUrl(url);
  }, [current?.job_progress?.prefect_ui_url]);

  return (
    <div>
      <h1 className="page-title">Models</h1>
      <p className="page-sub">
        更新模型 · 更新全部模型 · 一键/盘后预测 · Job 日志
        {info?.ui_url ? (
          <>
            {" · "}
            <a href={info.ui_url} target="_blank" rel="noreferrer">
              MLflow UI ↗
            </a>
          </>
        ) : null}
      </p>
      {error && <p className="error">{error}</p>}

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3 style={{ marginTop: 0 }}>运维操作</h3>
        <div className="btn-row" style={{ flexWrap: "wrap", marginBottom: "0.75rem" }}>
          <select
            className="input"
            style={{ maxWidth: 220 }}
            value={symbol}
            onChange={(e) => selectSymbol(e.target.value)}
          >
            {items.length === 0 && <option value="">暂无关注股票</option>}
            {items.map((it) => (
              <option key={it.symbol} value={it.symbol}>
                {it.symbol} {it.name || ""}
              </option>
            ))}
          </select>
          {current && (
            <>
              <span className={badgeClass(current.train_status)}>{current.train_status}</span>
              <span className={badgeClass(current.predict_status)}>{current.predict_status}</span>
            </>
          )}
          <button
            className="btn primary"
            disabled={!!busy || !symbol}
            onClick={() => trainOne(symbol)}
          >
            更新模型
          </button>
          <button
            className="btn primary"
            disabled={!!busy || items.length === 0 || !!trainBatch}
            onClick={trainAll}
            title="按关注列表顺序串行更新；宕机重启后自动续跑"
          >
            {trainBatch ? "批量更新进行中…" : "更新全部模型"}
          </button>
          <button className="btn" disabled={!!busy || items.length === 0} onClick={predictAll}>
            一键预测
          </button>
        </div>
        {trainBatch && (
          <p className="muted" style={{ marginTop: 0, marginBottom: "0.75rem" }}>
            批量训练 #{trainBatch.batch_id}：已完成 {trainBatch.completed}/{trainBatch.total}
            {trainBatch.failed ? ` · 失败 ${trainBatch.failed}` : ""}
            {trainBatch.current_symbol ? ` · 当前 ${trainBatch.current_symbol}` : ""}
            {" · 支持断点续作"}
          </p>
        )}
        {current?.job_progress && <JobProgress job={current.job_progress} />}
        {current?.last_error &&
          current.train_status !== "running" &&
          current.train_status !== "queued" && (
          <p className="error" style={{ fontSize: "0.85rem" }}>
            {current.last_error}
          </p>
        )}

        <h3>盘后定时预测</h3>
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

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>关注股票状态</h3>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>股票</th>
                <th>训练</th>
                <th>预测</th>
                <th>进度</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.symbol}
                  style={{
                    cursor: "pointer",
                    background: symbol === it.symbol ? "rgba(255,255,255,0.04)" : undefined,
                  }}
                  onClick={() => selectSymbol(it.symbol)}
                >
                  <td>
                    <strong>{it.symbol}</strong>
                    <div className="muted">{it.name || it.exchange}</div>
                  </td>
                  <td>
                    <span className={badgeClass(it.train_status)}>{it.train_status}</span>
                  </td>
                  <td>
                    <span className={badgeClass(it.predict_status)}>{it.predict_status}</span>
                  </td>
                  <td style={{ minWidth: 140 }}>
                    {it.job_progress ? (
                      <JobProgress job={it.job_progress} compact />
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <button
                      className="btn primary"
                      disabled={!!busy}
                      onClick={(e) => {
                        e.stopPropagation();
                        selectSymbol(it.symbol);
                        trainOne(it.symbol);
                      }}
                    >
                      更新模型
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {items.length === 0 && <p className="muted">列表为空，请先到 Watchlist 添加股票</p>}
      </div>

      {logs.length > 0 && (
        <div className="panel" style={{ marginBottom: "1rem" }}>
          <h3 style={{ marginTop: 0 }}>
            Job 日志 <PrefectLink url={prefectUrl} />
          </h3>
          <div className="log-view" ref={logViewRef}>
            {logs.join("\n")}
          </div>
        </div>
      )}

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
                  <tr style={{ cursor: "pointer" }} onClick={() => toggleVersions(m.name)}>
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
                                        ["algo", "label", "n_rows", "n_features", "eval_split"].includes(k),
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
