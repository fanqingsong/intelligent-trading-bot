import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import ConfigPage from "./pages/Config";
import PipelinePage from "./pages/Pipeline";
import DataPage from "./pages/Data";
import ModelsPage from "./pages/Models";
import TraderPage from "./pages/Trader";
import BacktestPage from "./pages/Backtest";
import SignalsPage from "./pages/Signals";

const links = [
  ["/", "Dashboard"],
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
          <span>Microservice control plane</span>
        </div>
        <nav className="nav">
          {links.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}>
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
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
