import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api";

type AnalyzeResult = {
  symbol: string;
  available: boolean;
  recommendation?: string;
  trade_score?: number | null;
  close?: number | null;
  latest?: Record<string, unknown> | null;
  total_rows?: number;
};

type SuggestItem = {
  code: string;
  name: string;
  exchange: string;
  label: string;
};

export default function AnalyzePage() {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<SuggestItem | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestItem[]>([]);
  const [openSuggest, setOpenSuggest] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState("0");
  const [currentStep, setCurrentStep] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const logEndRef = useRef<HTMLDivElement | null>(null);
  const blurTimer = useRef<number | null>(null);
  const suggestSeq = useRef(0);

  useEffect(() => {
    return () => {
      esRef.current?.close();
      if (blurTimer.current) window.clearTimeout(blurTimer.current);
    };
  }, []);

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
        const res = await api.analyzeSuggest(q);
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
    setSuggestions([]);
    setOpenSuggest(false);
    setError("");
  };

  const loadResult = async (code?: string) => {
    try {
      const r = await api.analyzeResult(code);
      setResult(r);
    } catch {
      /* ignore until job finishes */
    }
  };

  const attachLogs = (id: string, code: string) => {
    esRef.current?.close();
    setLogs([]);
    const es = new EventSource(api.logsUrl(id));
    esRef.current = es;
    es.addEventListener("log", (ev) => {
      setLogs((prev) => [...prev, (ev as MessageEvent).data]);
    });
    es.addEventListener("done", (ev) => {
      try {
        const job = JSON.parse((ev as MessageEvent).data);
        setStatus(job.status);
        setProgress(job.progress || "100");
        setCurrentStep(job.current_step || "");
        if (job.status === "completed") {
          loadResult(code);
        }
      } catch {
        setStatus("done");
      }
      setRunning(false);
      es.close();
    });
    es.addEventListener("error", () => {
      /* EventSource also fires on normal close */
    });
  };

  const resolveSymbol = (): string => {
    if (selected?.code) return selected.code;
    const m = query.match(/(\d{6})/);
    if (m) return m[1];
    return query.trim();
  };

  const go = async () => {
    setError("");
    setResult(null);
    setOpenSuggest(false);
    const symbol = resolveSymbol();
    if (!symbol) {
      setError("请输入股票代码或名称，并从提示中选择");
      return;
    }
    setRunning(true);
    setStatus("starting");
    setProgress("0");
    setCurrentStep("");
    try {
      const res = await api.analyze(symbol);
      setJobId(res.job_id);
      setStatus(res.status);
      if (!selected || selected.code !== res.symbol) {
        setSelected({
          code: res.symbol,
          name: selected?.name || res.symbol,
          exchange: res.symbol[0] === "6" || res.symbol[0] === "9" ? "SH" : "SZ",
          label: `${res.symbol}${selected?.name ? ` ${selected.name}` : ""}`.trim(),
        });
        setQuery(`${res.symbol}${selected?.name ? ` ${selected.name}` : ""}`.trim());
      }
      attachLogs(res.job_id, res.symbol);
      const poll = setInterval(async () => {
        try {
          const j = await api.getJob(res.job_id);
          setStatus(j.status);
          setProgress(j.progress || "0");
          setCurrentStep(j.current_step || "");
          if (j.status === "completed" || j.status === "failed") {
            clearInterval(poll);
            setRunning(false);
            if (j.status === "completed") loadResult(res.symbol);
            if (j.status === "failed") setError(j.error || "Pipeline failed");
          }
        } catch {
          clearInterval(poll);
        }
      }, 2000);
    } catch (e: any) {
      setRunning(false);
      setError(String(e.message || e));
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (openSuggest && suggestions.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlight((h) => (h + 1) % suggestions.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlight((h) => (h - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        pick(suggestions[highlight]);
        return;
      }
      if (e.key === "Escape") {
        setOpenSuggest(false);
        return;
      }
    }
    if (e.key === "Enter" && !running) go();
  };

  const rec = result?.recommendation || "—";
  const recClass =
    rec === "BUY" ? "ok" : rec === "SELL" ? "danger" : "";

  return (
    <div>
      <h1 className="page-title">Analyze</h1>
      <p className="page-sub">输入股票代码或名称，一键完成拉数 → 训练 → 信号报告（沪深日线）</p>
      {error && <p className="error">{error}</p>}

      <div className="panel analyze-hero">
        <label className="analyze-label" htmlFor="stock-code">
          股票代码 / 名称
        </label>
        <div className="analyze-row">
          <div className="analyze-suggest-wrap">
            <input
              id="stock-code"
              className="analyze-input"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelected(null);
                setOpenSuggest(true);
              }}
              onFocus={() => {
                if (suggestions.length) setOpenSuggest(true);
              }}
              onBlur={() => {
                blurTimer.current = window.setTimeout(() => setOpenSuggest(false), 150);
              }}
              onKeyDown={onKeyDown}
              placeholder="例如 600519 或 贵州茅台"
              disabled={running}
              maxLength={32}
              autoComplete="off"
              role="combobox"
              aria-expanded={openSuggest}
              aria-autocomplete="list"
            />
            {openSuggest && (suggestions.length > 0 || suggestLoading) && (
              <ul className="analyze-suggest-list" role="listbox">
                {suggestLoading && suggestions.length === 0 && (
                  <li className="analyze-suggest-empty">搜索中…（首次加载股票列表可能较慢）</li>
                )}
                {suggestions.map((item, idx) => (
                  <li key={item.code}>
                    <button
                      type="button"
                      className={`analyze-suggest-item ${idx === highlight ? "active" : ""}`}
                      onMouseDown={(ev) => {
                        ev.preventDefault();
                        pick(item);
                      }}
                      onMouseEnter={() => setHighlight(idx)}
                      role="option"
                      aria-selected={idx === highlight}
                    >
                      <span className="suggest-code">{item.code}</span>
                      <span className="suggest-name">{item.name}</span>
                      <span className="suggest-ex">{item.exchange}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button
            className="btn primary analyze-go"
            onClick={go}
            disabled={running || !query.trim()}
          >
            {running ? "Running…" : "Go"}
          </button>
        </div>
        <p className="muted" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
          支持代码或中文名称；输入时出现候选列表，用方向键选择后回车确认，再点 Go。
          {selected ? ` 已选：${selected.label}` : ""}
        </p>
      </div>

      {(jobId || running) && (
        <div className="panel" style={{ marginBottom: "1rem" }}>
          <h3>进度</h3>
          <p>
            {jobId && (
              <>
                Job <code>{jobId.slice(0, 8)}</code> ·{" "}
              </>
            )}
            <span
              className={`badge ${
                status === "completed" ? "ok" : status === "failed" ? "fail" : "run"
              }`}
            >
              {status || "—"}
            </span>
            {" · "}
            {progress}%
            {currentStep ? ` · ${currentStep}` : ""}
          </p>
          <div className="log-view">
            {logs.join("\n") || "Waiting for job…"}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {result?.available && (
        <div className="panel">
          <h3>最新信号 · {result.symbol}</h3>
          <div className="grid grid-3" style={{ marginBottom: "0.5rem" }}>
            <div>
              <div className="muted">建议</div>
              <div className={`stat ${recClass}`}>{rec}</div>
            </div>
            <div>
              <div className="muted">Score</div>
              <div className="stat">
                {result.trade_score == null ? "—" : result.trade_score.toFixed(4)}
              </div>
            </div>
            <div>
              <div className="muted">收盘价</div>
              <div className="stat">{result.close ?? "—"}</div>
            </div>
          </div>
          <p className="muted" style={{ marginBottom: 0 }}>
            基于 signals.csv 最新一行（共 {result.total_rows} 行）。正分偏多、负分偏空；超过阈值生成 BUY/SELL。
          </p>
        </div>
      )}
    </div>
  );
}
