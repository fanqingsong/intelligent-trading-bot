# Intelligent trading bot (A-share)

ML-based analysis for **China A-shares (沪深)**：离线训练流水线 + Web 一键分析，输出 BUY / SELL / HOLD 信号（本地模拟，不下实盘单）。

## Features

* Offline batch pipeline: download → merge → features → labels → train → predict → signals → output
* Extensible derived features (TA-Lib and custom Python generators)
* Daily frequency (`1D`) with trading-day-only merge for A-shares
* **One-click Analyze** in the Web UI: enter a stock code or Chinese name, run the full pipeline
* Backtesting in the UI: walk-forward `predict_rolling` and threshold `simulate`

## Quick start

```bash
bin/start_dev.sh
```

- Web UI: http://localhost:5174
- API docs: http://localhost:8000/docs
- Default config: `configs/config-dev.jsonc` (copied from `configs/config-ashare-1d.jsonc` on first start)
- Data directory: `./data`

Stop with `bin/stop_dev.sh`.

## Web UI

| Page | Purpose |
|------|---------|
| **Analyze** | Code / Chinese name → full offline pipeline → BUY / SELL / HOLD |
| **Dashboard** | Service health and recent jobs |
| **Config** | Edit / save active JSONC; load sample configs |
| **Pipeline** | Run any subset of offline steps with live logs |
| **Data** | Browse and preview files under the current symbol |
| **Models** | Trained model artifacts and prediction metrics |
| **Backtest** | `predict_rolling` + `simulate`; show grid-search results |
| **Signals** | Recent rows from `signals.csv` |

### A-share one-click analysis

1. Open http://localhost:5174 — home page is **Analyze**
2. Type a **code** (e.g. `600519`) or **Chinese name** (e.g. `贵州茅台` / `茅台`)
3. Pick a suggestion, then click **Go**
4. When the job completes, the page shows the latest BUY / SELL / HOLD from `signals.csv`

Pipeline summary (default sample `600519`):

1. **Data** — forward-adjusted daily OHLCV via akshare
2. **Features** — TA-Lib SMA / slope / STDDEV on `close` (windows 5/10/20/60)
3. **Labels** — within the next 5 days, first hit **+3%** (`high_30`) or **−3%** (`low_30`)
4. **Model** — two `StandardScaler` + `SVC` classifiers → `high_30_svc` / `low_30_svc`
5. **Signal** — `trade_score = high_30_svc − low_30_svc`; BUY if `≥ 0.08`, SELL if `≤ -0.08`, else HOLD

Full guide: [docs/ashare.md](docs/ashare.md).

## Layout

| Folder | Role |
|--------|------|
| `frontend/` | React UI (BS client) |
| `backend/` | FastAPI BFF / control plane |
| `pipeline/` | Offline pipeline worker (+ `steps/`) |
| `shared/` | Domain logic, collectors, notifiers, runtime config |

## Microservices

| Service | Port | Role |
|---------|------|------|
| frontend | 5174 | React UI |
| backend | 8000 | BFF / control plane |
| pipeline | 8001 | Offline pipeline worker |
| redis | 6379 | Job state and logs |

More docs: [docs/scripts.md](docs/scripts.md) (pipeline steps), [docs/features.md](docs/features.md), [docs/labels.md](docs/labels.md), [docs/configuration.md](docs/configuration.md), [docs/data-inputs.md](docs/data-inputs.md).

## Notes

* Venue is **`ashare` only** (Shanghai / Shenzhen via akshare).
* No exchange API keys are required.
* Live trading is **not** enabled (local `trader_simulation` only).
* All offline steps are driven from the Web UI / API — there is no CLI entry point.
