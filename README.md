# Intelligent trading bot (A-share)

ML-based analysis for **China A-shares (沪深)**：Watchlist 日常信号 + 手动模型更新，输出多算法 BUY / SELL / HOLD（本地模拟，不下实盘单）。

## Features

* Watchlist：维护关注股票，手动「更新模型」，一键/盘后定时「预测」
* 四算法并行：`svc` / `gb` / `nn` / `lc`，分列信号 + 多数投票汇总
* Offline batch pipeline (Kedro)：download → merge → features → labels → train → predict → signals
* 表格数据存 **Postgres**（`market_frames`）；模型存 **MLflow**（按 symbol 隔离）
* Daily frequency (`1D`) with trading-day-only merge for A-shares
* Backtesting in the UI: walk-forward `predict_rolling` and threshold `simulate`

## Quick start

```bash
bin/start_dev.sh          # 依赖未变时跳过镜像重建
bin/start_dev.sh --build  # 强制重建镜像
```

- Web UI: http://localhost:5174
- API docs: http://localhost:8000/docs
- MLflow UI: http://localhost:5000
- Postgres: `localhost:5433` (user/pass/db: `itb` / `itb` / `itb`)
- Default config template: `configs/config-ashare-1d.jsonc`（首次启动复制为 `config-dev.jsonc`）

Optional — migrate existing CSV under `./data` into Postgres:

```bash
DATABASE_URL=postgresql+psycopg://itb:itb@localhost:5432/itb \
  python3 scripts/migrate_csv_to_postgres.py --seed-watchlist
```

Stop with `bin/stop_dev.sh`.

## Daily workflow

1. Open http://localhost:5174 — home page is **Watchlist**
2. Add stocks by code or Chinese name (typeahead)
3. Click **更新模型** on a symbol (runs download→…→train→predict→signals for all algorithms). Adding a stock does **not** auto-train.
4. Click **一键预测** for the whole list (skips train; untrained symbols are skipped)
5. Optional: enable post-market schedule (default cron `0 16 * * 1-5` Asia/Shanghai)

## Web UI

| Page | Purpose |
|------|---------|
| **Watchlist** | Stock list, train / predict, per-algo + vote signals, schedule |
| **Dashboard** | Service health and recent jobs |
| **Config** | Edit / save active JSONC; load sample configs |
| **Pipeline** | Run any subset of offline steps with live logs |
| **Data** | Browse Postgres frames (`klines` / `data` / `features` / …) |
| **Models** | Train one / train-all (checkpoint resume) · MLflow · staging files |
| **Backtest** | `predict_rolling` + `simulate` |
| **Signals** | Recent signal rows from Postgres |

## Layout

| Folder | Role |
|--------|------|
| `frontend/` | React UI |
| `backend/` | FastAPI BFF / control plane + APScheduler + watchlist ORM |
| `kedro_pipeline/` | Pipeline worker, Kedro nodes, Prefect flow wrapper, modular ML packages |
| `conf/` | Kedro DataCatalog / parameters |
| `shared/` | Cross-service contract: config helpers, collectors, market frames |
| `scripts/migrate_csv_to_postgres.py` | One-shot CSV → Postgres migration |
| `alembic/` | Schema migrations |

## Microservices

| Service | Port | Role |
|---------|------|------|
| frontend | 5174 | React UI |
| backend | 8000 | BFF / schedule sync / watchlist API |
| pipeline | 8001 | Job enqueue API (HTTP → Prefect) |
| prefect-server | 4200 | Prefect UI + durable queue |
| prefect-worker | — | Serves Kedro jobs + daily-predict cron |
| mlflow | 5000 | Model Registry + tracking UI |
| redis | 6379 | Job state/logs mirror for SSE UI |
| postgres | 5432 | Watchlist + market_frames |

Orchestration split: **Prefect** queues/schedules/concurrency/recovery + per-node task DAG; **Kedro** owns node logic/catalog. UI deep-links Prefect runs; job lists are hybrid Redis+Prefect. Optional BFF team RBAC via `X-ITB-*` headers. See [docs/prefect.md](docs/prefect.md).

Job presets:

| Kind | Steps | Trigger |
|------|--------|---------|
| `train_update` | download→…→train→predict→signals | 「更新模型」/ 「更新全部模型」 |
| `daily_predict` | download→…→predict→signals (no train) | 「一键预测」/ 盘后定时 |

Train-all is sequential and checkpointed in Postgres (`batch_runs` / `symbol_run_links`); see [docs/train-all.md](docs/train-all.md).

More docs: [docs/ashare.md](docs/ashare.md), [docs/buy-sell-prediction.md](docs/buy-sell-prediction.md), [docs/scripts.md](docs/scripts.md), [docs/configuration.md](docs/configuration.md), [docs/data-inputs.md](docs/data-inputs.md), [docs/prefect.md](docs/prefect.md), [docs/train-all.md](docs/train-all.md).

## Notes

* Venue is **`ashare` only** (Shanghai / Shenzhen via akshare).
* No exchange API keys are required.
* Live trading is **not** enabled (local `trader_simulation` only).
* All offline steps are driven from the Web UI / API — there is no CLI entry point.
* MLflow registry prefix is `itb_{symbol}_` so multi-symbol models do not collide.
