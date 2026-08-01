# Configuration and parameterization

## Parameterization

All parameters for scripts, the server, and high-level functions are provided as a JSON object.
This configuration is represented either as a JSON file (with comment support) or as a Python dictionary (еt runtime).
Note that not all parameters and sections are required by every script or function.

## Global parameters

The most important parameters are listed below:

- General parameters:
  - `symbol`: Primary stock code; also scopes Postgres `market_frames` and MLflow registry prefix `itb_{symbol}_`.
  - `description`: A textual description of this configuration.
  - `freq`: Data frequency in `pandas` format (A-share sample uses `1D`).
  - `train`: Boolean flag for train (`true`) vs predict-only (`false`) windowing and whether trainable features fit models.
- Persistence:
  - Pipeline tables (klines/data/features/matrix/predictions/signals) are stored in Postgres `market_frames`.
  - `data_folder`: Local directory for sidecar `.txt` files and optional staging artifacts.
  - `model_folder`: Local staging path under `{data_folder}/{symbol}/`; authoritative models live in MLflow.
- MLflow platform (Tracking + Registry):
  - `mlflow_tracking_uri`: Tracking server URL. In docker the `MLFLOW_TRACKING_URI` env var wins (`http://mlflow:5000`); locally `http://localhost:5000`. The MLflow UI is served at this URL.
  - `mlflow_experiment_name`: Experiment name, overridden per symbol to `itb_{symbol}`.
  - `mlflow_registry_prefix`: Registered models are named `{prefix}{label}_{algo}` (default `itb_`, so e.g. `itb_600519_high_30_gb`).
  - `mlflow_default_alias`: Alias promoted on each new model version (default `Production`). Loading resolves this alias, falling back to the latest version on backends without alias support.
  - `mlflow_log_input_example`: When `true`, each model is logged with an inferred signature + small input example.
  - `mlflow_eval_split`: Provenance tag recorded with metrics (default `in_sample` — metrics are computed on the training set; set up a holdout split here in future).
- Data providers:
  - `venue`: Data provider connector. Currently supported: `ashare` (China A-shares via akshare).
  - `time_column`: Column name used for timestamps (defaults to `"timestamp"`).
  - `merge_trading_days_only`: If `true`, drop rows with an empty primary `close` after merge (required for A-share daily calendars). Defaults to `false`. See [ashare.md](ashare.md).
- Output:
  - Use `output_sets` with `trader_simulation` for local buy/sell simulation (no live trading).

## Analysis table parameters

During analysis, all data is represented as a DataFrame configured via the following parameters.
These parameters define the required shape of the DataFrame used by offline pipeline nodes:

- `label_horizon`: The minimum number of future rows required to compute a label (i.e., the prediction horizon).
  During training, rows lacking sufficient future data are excluded.
- `features_horizon`: The minimum number of past rows required for a feature to be valid. For example, computing a 10-day moving average requires 10 previous rows.
  Consequently, the first `features_horizon` rows are considered invalid and excluded from analysis.
  This value should be derived from the feature definitions.
- `train_length`: Default upper limit for the training dataset size.
  This serves as a global maximum for all ML features, though individual features may override it.
  A value of `0` indicates that all available data should be used.
- `predict_length`: The minimum number of rows kept up-to-date and valid in online mode.
  To ensure validity, these rows must be preceded by at least `features_horizon` historical rows.
- `append_overlap_records`: In online mode, the server requests additional records beyond those strictly missing.
  This parameter specifies the size of that overlap buffer.
  Overlapping records are re-evaluated and overwrite previous values, which helps mitigate connection errors or minor discrepancies in recently received data.

## Parameter sections

The model registry is defined as a list named `model_registry`, where each entry contains `name` and `file` attributes.

Feature definitions are organized into four sections:
- `feature_sets`
- `label_sets`: Labels required during training.
- `train_feature_sets`: Trainable features.
- `signal_sets`: Features evaluated after ML features.

Features rely on the following global parameters:
- `train_features`: A list of column names selected by default as input for training algorithms (in both train and predict modes).
  Individual trainable features may override this list with their own specific inputs.
- `labels`: A list of all labels (unless overridden by individual trainable algorithms).
- `algorithms`: **Obsolete.** Use either `train_feature_sets` or standard `feature_sets` to define trainable features.

Outputs are defined in `output_sets`, which is a list of dictionaries passed to output adapters after analysis.
For example, trading or notification adapters can be configured here.

Additional sections may describe utilities such as `rolling_predict` or `simulate_model`.
