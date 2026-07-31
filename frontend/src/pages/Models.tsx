import { useEffect, useState } from "react";
import { api } from "../api";

export default function ModelsPage() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .models()
      .then(setData)
      .catch((e) => setError(String(e.message || e)));
  }, []);

  return (
    <div>
      <h1 className="page-title">Models</h1>
      <p className="page-sub">{data?.model_path || "Trained model artifacts and prediction metrics"}</p>
      {error && <p className="error">{error}</p>}

      <div className="panel" style={{ marginBottom: "1rem" }}>
        <h3>Files</h3>
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
                  <td>{new Date(f.mtime * 1000).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h3>Prediction metrics</h3>
        <pre className="log-view">{data?.metrics || "No metrics file yet"}</pre>
      </div>
    </div>
  );
}
