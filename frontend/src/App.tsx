import { Navigate, NavLink, Route, Routes, useSearchParams } from "react-router-dom";
import WatchlistPage from "./pages/Watchlist";
import Dashboard from "./pages/Dashboard";
import ConfigPage from "./pages/Config";
import PipelinePage from "./pages/Pipeline";
import DataPage from "./pages/Data";
import BacktestPage from "./pages/Backtest";
import SignalsPage from "./pages/Signals";

const primaryLinks = [
  ["/", "Signals"],
  ["/watchlist", "Watchlist"],
] as const;

const advancedLinks = [
  ["/dashboard", "Dashboard"],
  ["/config", "Config"],
  ["/pipeline", "Pipeline"],
  ["/data", "Data"],
  ["/backtest", "Backtest"],
] as const;

function ModelsRedirect() {
  const [searchParams] = useSearchParams();
  const symbol = searchParams.get("symbol");
  const to = symbol ? `/?symbol=${encodeURIComponent(symbol)}` : "/";
  return <Navigate to={to} replace />;
}

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          Intelligent Trading Bot
          <span>A-share watchlist signals</span>
        </div>
        <nav className="nav">
          {primaryLinks.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}>
              {label}
            </NavLink>
          ))}
          <div className="nav-section">Advanced</div>
          {advancedLinks.map(([to, label]) => (
            <NavLink key={to} to={to}>
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<SignalsPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/signals" element={<Navigate to="/" replace />} />
          <Route path="/models" element={<ModelsRedirect />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
        </Routes>
      </main>
    </div>
  );
}
