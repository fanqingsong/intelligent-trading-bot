# Pipeline steps

Offline analysis is a Postgres-backed multi-step pipeline. Each Kedro node reads/writes `market_frames` (symbol + kind) unless noted. Jobs are executed by the **pipeline worker** and controlled from the Web UI:

- **Watchlist** — `train_update` (with train) or `daily_predict` (skip train) for one or many symbols
- **Pipeline** — run any subset of offline steps with live logs
- **Backtest** — run `predict_rolling` and/or `simulate`, then view results

Node implementations live under `kedro_pipeline/nodes/` (`inference.py`, `backtest.py`); domain logic sits in sibling packages (`features`, `labels`, `classifiers`, `signals`, `backtesting`, `orchestration`). There is no CLI entry point for individual steps.

## Step overview

| Step | UI | Input → Output |
|------|----|----------------|
| `download` | Watchlist / Pipeline | data sources → Postgres `klines` |
| `merge` | Watchlist / Pipeline | `klines` → Postgres `data` |
| `features` | Watchlist / Pipeline | `data` → Postgres `features` (+ optional `.txt` sidecar) |
| `labels` | Watchlist / Pipeline | `features` → Postgres `matrix` |
| `train` | Watchlist train / Pipeline | `matrix` → MLflow Model Registry (`itb_{symbol}_*`) |
| `predict` | Watchlist / Pipeline | `matrix` + models → Postgres `predictions` |
| `signals` | Watchlist / Pipeline | `predictions` → Postgres `signals` |
| `output` | Pipeline | signals → adapters (e.g. `trader_simulation` → `transactions.txt`) |
| `predict_rolling` | Backtest | walk-forward predictions; each step logs a new MLflow version tagged `rolling_step` |
| `simulate` | Backtest | signals → `signal_models.txt` grid search |

Logical file names in config (`merge_file_name`, etc.) remain for sidecar path resolution; table payloads live in Postgres.

## Job presets

| Kind | Steps | Trigger |
|------|--------|---------|
| `train_update` | download → … → train → predict → signals | Watchlist「更新模型」 |
| `daily_predict` | download → … → predict → signals (no train) | 「一键预测」/ post-market schedule |

Jobs accept `config_overrides` (symbol, data_sources, mlflow prefix) so the shared JSONC template is not rewritten per symbol.

## Download and merge

`download` retrieves A-share daily bars (`venue: ashare`) and upserts Postgres `klines`. Existing history is appended with a short overlap window.

`merge` joins sources onto `freq`, optionally with `merge_trading_days_only: true`.

## Features, labels, train, predict, signals

- **features** — evaluate `feature_sets`
- **labels** — evaluate `label_sets`
- **train** — fit `train_feature_sets` × algorithms → MLflow
- **predict** — apply trained models
- **signals** — per-algorithm thresholds + `majority_vote`

## Outputs

`output` runs `output_sets` (e.g. `trader_simulation`). These adapters do not add feature columns; they record local simulation transactions when a BUY/SELL flip occurs.

## Backtest steps

### `predict_rolling`

Walk-forward train/predict. Parameters live in `rolling_predict`.

### `simulate`

Grid-search trade thresholds on existing signals. Parameters live in `simulate_model`. Results append to `{symbol}/signal_models.txt` and are shown on the **Backtest** page.
