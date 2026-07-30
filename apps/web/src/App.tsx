import { NavLink, Route, Routes } from "react-router-dom";
import AnalyzePage from "./pages/Analyze";
import Dashboard from "./pages/Dashboard";
import ConfigPage from "./pages/Config";
import PipelinePage from "./pages/Pipeline";
import DataPage from "./pages/Data";
import ModelsPage from "./pages/Models";
import TraderPage from "./pages/Trader";
import BacktestPage from "./pages/Backtest";
import SignalsPage from "./pages/Signals";

const primaryLinks = [["/", "Analyze"]] as const;

const advancedLinks = [
  ["/dashboard", "Dashboard"],
  ["/config", "Config"],
  ["/pipeline", "Pipeline"],
  ["/data", "Data"],
  ["/models", "Models"],
  ["/trader", "Trader"],
  ["/backtest", "Backtest"],
  ["/signals", "Signals"],
] as const;

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          Intelligent Trading Bot
          <span>A-share one-click analysis</span>
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
              {to === "/trader" ? " (off)" : ""}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<AnalyzePage />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/trader" element={<TraderPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/signals" element={<SignalsPage />} />
        </Routes>
      </main>
    </div>
  );
}
