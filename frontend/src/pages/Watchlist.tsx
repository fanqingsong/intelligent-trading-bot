import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { badgeClass } from "../lib/status";

type SuggestItem = {
  code: string;
  name: string;
  exchange: string;
  label: string;
};

type WatchItem = {
  symbol: string;
  name: string;
  exchange: string;
  train_status: string;
  predict_status: string;
  last_error?: string;
};

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<SuggestItem | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestItem[]>([]);
  const [openSuggest, setOpenSuggest] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState("");
  const blurTimer = useRef<number | null>(null);
  const suggestSeq = useRef(0);

  const load = useCallback(async () => {
    try {
      const wl = await api.watchlist();
      setItems(wl.items || []);
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
      if (blurTimer.current) window.clearTimeout(blurTimer.current);
    };
  }, [load]);

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
      await load();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  return (
    <div>
      <h1 className="page-title">Watchlist</h1>
      <p className="page-sub">维护关注股票 · 训练与信号见 Models / Signals</p>
      {error && <p className="error">{error}</p>}
      {info && <p className="muted">{info}</p>}

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
        </div>
        <div className="btn-row" style={{ marginTop: "0.75rem", flexWrap: "wrap" }}>
          <button
            className="btn"
            disabled={!!busy}
            onClick={() => importIndex("sse50", "上证50")}
          >
            {busy === "import-sse50" ? "导入中…" : "导入上证50"}
          </button>
          <button
            className="btn"
            disabled={!!busy}
            onClick={() => importIndex("csi300", "沪深300")}
          >
            {busy === "import-csi300" ? "导入中…" : "导入沪深300"}
          </button>
          <span className="muted" style={{ alignSelf: "center" }}>
            从中证指数拉取成分股，已存在则跳过
          </span>
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
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.symbol}>
                  <td>
                    <strong>{it.symbol}</strong>
                    <div className="muted">{it.name || it.exchange}</div>
                    {it.last_error && (
                      <div className="error" style={{ fontSize: "0.8rem" }}>
                        {it.last_error}
                      </div>
                    )}
                  </td>
                  <td>
                    <Link to={`/models?symbol=${encodeURIComponent(it.symbol)}`}>
                      <span className={badgeClass(it.train_status)}>{it.train_status}</span>
                    </Link>
                  </td>
                  <td>
                    <Link to={`/models?symbol=${encodeURIComponent(it.symbol)}`}>
                      <span className={badgeClass(it.predict_status)}>{it.predict_status}</span>
                    </Link>
                  </td>
                  <td>
                    <button
                      className="btn"
                      disabled={!!busy}
                      onClick={() => removeSymbol(it.symbol)}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {items.length === 0 && <p className="muted">列表为空，先添加股票</p>}
      </div>
    </div>
  );
}
