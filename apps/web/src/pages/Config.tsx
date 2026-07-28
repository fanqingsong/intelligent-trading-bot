import { useEffect, useState } from "react";
import { api } from "../api";

export default function ConfigPage() {
  const [content, setContent] = useState("");
  const [path, setPath] = useState("");
  const [samples, setSamples] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      const [cfg, s] = await Promise.all([api.getConfig(), api.listSamples()]);
      setContent(cfg.content);
      setPath(cfg.path);
      setSamples(s.samples);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    setMsg("");
    setError("");
    try {
      await api.putConfig(content);
      setMsg("Saved.");
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  const loadSample = async (name: string) => {
    setError("");
    try {
      await api.loadSample(name);
      await load();
      setMsg(`Loaded ${name}`);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  return (
    <div>
      <h1 className="page-title">Config</h1>
      <p className="page-sub">Edit JSONC configuration · {path || "…"}</p>
      {error && <p className="error">{error}</p>}
      {msg && <p className="muted">{msg}</p>}

      <div className="btn-row">
        <button className="btn primary" onClick={save}>
          Save
        </button>
        <button className="btn" onClick={load}>
          Reload
        </button>
        <select
          className="btn"
          defaultValue=""
          onChange={(e) => {
            if (e.target.value) loadSample(e.target.value);
            e.target.value = "";
          }}
        >
          <option value="">Load sample…</option>
          {samples.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <textarea
        className="editor"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
      />
    </div>
  );
}
