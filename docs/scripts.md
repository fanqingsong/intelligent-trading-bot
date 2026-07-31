# Pipeline steps

Offline analysis is a file-based multi-step pipeline. Each step loads an input file, processes it, and writes an output file. Steps are executed by the **pipeline worker** and controlled from the Web UI:

- **Analyze** — run the full pipeline for one A-share
- **Pipeline** — run any subset of offline steps with live logs
- **Backtest** — run `predict_rolling` and/or `simulate`, then view results

Underlying step functions live under `pipeline/steps/` (`run_download`, `run_merge`, …) and are not exposed as a CLI.

## Step overview

| Step | UI | Input → Output |
|------|----|----------------|
| `download` | Analyze / Pipeline | data sources → `{symbol}/klines.csv` |
| `merge` | Analyze / Pipeline | source files → `merge_file_name` (`data.csv`) |
| `features` | Analyze / Pipeline | merged data → `feature_file_name` (`features.csv`) |
| `labels` | Analyze / Pipeline | features → `matrix_file_name` (`matrix.csv`) |
| `train` | Analyze / Pipeline | matrix → `{symbol}/MODELS/` |
| `predict` | Analyze / Pipeline | matrix + models → `predict_file_name` (`predictions.csv`) |
| `signals` | Analyze / Pipeline | predictions → `signal_file_name` (`signals.csv`) |
| `output` | Analyze / Pipeline | signals → adapters (e.g. `trader_simulation` → `transactions.txt`) |
| `predict_rolling` | Backtest | matrix → walk-forward `predictions.csv` (+ `.txt` metrics) |
| `simulate` | Backtest | predictions → `signal_models_file_name` (`.txt` grid search) |

File names are configured via:

- `merge_file_name`, `feature_file_name`, `matrix_file_name`
- `predict_file_name`, `signal_file_name`, `signal_models_file_name`

The file extension determines the format (CSV or Parquet).

## Download and merge

`download` retrieves data from `data_sources`. For A-shares set `"venue": "ashare"` and a 6-digit `folder` (see `configs/config-ashare-1d.jsonc` and [ashare.md](ashare.md)). Existing files are appended with the latest missing rows.

`merge` joins all sources into one table under the `symbol` folder, aligning rows to `freq`. Use `merge_trading_days_only: true` for daily A-share calendars.

## Features, labels, train, predict, signals

- **features** — evaluate `feature_sets` → `feature_file_name`
- **labels** — evaluate `label_sets` → `matrix_file_name`
- **train** — fit models from `train_feature_sets` → `MODELS/`
- **predict** — apply trained models → `predict_file_name` (+ metrics `.txt`)
- **signals** — evaluate `signal_sets` → `signal_file_name`

## Outputs

`output` runs `output_sets` (e.g. `trader_simulation`). These adapters do not add feature columns; they record local simulation transactions when a BUY/SELL flip occurs.

## Backtest steps

### `predict_rolling`

Walk-forward train/predict that mimics live deployment: train on history, predict a short forward window, advance, repeat. Parameters live in `rolling_predict`:

- `data_start` / `data_end`
- `prediction_start`, `prediction_size`, `prediction_steps`
- `use_multiprocessing`, `max_workers`

### `simulate`

Grid-search trade thresholds on (preferably rolling) predictions. Parameters live in `simulate_model`:

- `data_start` / `data_end`
- `direction`: `"long"` or `"short"`
- `topn_to_store`
- `signal_generator`
- `buy_sell_equal`
- `grid.buy_signal_threshold` / `grid.sell_signal_threshold` (lists or Python expressions)

Results append to `{symbol}/signal_models.txt` and are shown on the **Backtest** page.
