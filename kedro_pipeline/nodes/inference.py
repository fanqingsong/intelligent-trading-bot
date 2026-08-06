"""Inference pipeline nodes (download → merge → features → labels → train →
predict → signals → output).

Each node is a pure function: it receives DataFrames from the DataCatalog plus
the full ``config`` dict (a Kedro param), and returns DataFrames. No
``App.config`` / ``App.model_store`` globals. This is a faithful port of the
legacy ``pipeline/steps/*.py`` ``run_<step>`` functions — the file I/O moves to
the catalog, the window/IO boilerplate to :mod:`kedro_pipeline.catalog.paths`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import numpy as np
import pandas as pd
import pandas.api.types as ptypes

from shared.collectors import get_download_functions
from kedro_pipeline.orchestration.generators import (
    generate_feature_set,
    output_feature_set,
    predict_feature_set,
    train_feature_set,
)
from kedro_pipeline.classifiers.model_store import score_to_label_algo_pair
from shared.types import Venue
from kedro_pipeline.common.utils import (
    compute_scores,
    compute_scores_regression,
    merge_data_sources,
)

from ..catalog.paths import select_window
from .helpers import append_feature_list, new_model_store, store_scores
from .incremental import (
    apply_incremental_columns,
    can_use_incremental,
    compute_window_size,
    resolve_last_rows,
)


def _symbol_from_config(config: dict) -> str:
    from shared.collectors.collector_ashare import normalize_ashare_symbol

    raw = str(config.get("symbol") or "")
    try:
        return normalize_ashare_symbol(raw)
    except ValueError:
        return raw


def _coerce_binary_label(series: pd.Series) -> pd.Series:
    """Cast classification labels to nullable Int64 (0/1).

    After Postgres JSONB round-trips or incremental stitch, bool labels often
    become float/object (NaN upcasts). Classifiers then raise
    ``Unknown label type: unknown``.
    """
    if ptypes.is_integer_dtype(series) and not ptypes.is_bool_dtype(series):
        return series
    numeric = pd.to_numeric(series, errors="coerce")
    # Keep NaNs; train_feature_set drops them. Round 0.0/1.0 → 0/1.
    return numeric.round().astype("Int64")


def _load_existing_frame(config: dict, kind: str) -> pd.DataFrame:
    from shared.db.frames import load_frame

    symbol = _symbol_from_config(config)
    if not symbol:
        return pd.DataFrame()
    try:
        return load_frame(symbol, kind, time_column=config["time_column"])
    except Exception as exc:
        print(f"WARNING: could not load existing {kind} for incremental update: {exc}")
        return pd.DataFrame()


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #

def download(config: dict) -> bool:
    """Fetch raw data for the configured venue (side effect: writes raw files).

    Returns a sentinel consumed by :func:`merge` to enforce DAG ordering.
    Mirrors ``pipeline/steps/download.py``.
    """
    now = datetime.now()
    data_sources = config["data_sources"]
    venue = Venue(config.get("venue"))
    download_klines_fn = get_download_functions(venue)
    download_klines_fn(config, data_sources)

    elapsed = datetime.now() - now
    print(f"\nFinished downloading {len(data_sources)} data sources from {venue} in {str(elapsed).split('.')[0]}")
    return True


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #

def merge(raw_sources, config: dict) -> pd.DataFrame:
    """Merge raw klines from Postgres into one frame.

    Each ``data_sources[].folder`` is treated as a symbol; rows are loaded from
    ``market_frames`` kind=klines, windowed, then merged on ``time_column``.
    """
    from shared.collectors.collector_ashare import normalize_ashare_symbol
    from shared.db.frames import load_frame

    now = datetime.now()
    time_column = config["time_column"]

    data_sources = config.get("data_sources", [])
    if not data_sources:
        print("ERROR: Data sources are not defined. Nothing to merge.")
        return pd.DataFrame()

    for ds in data_sources:
        quote = ds.get("folder")
        if not quote:
            print("ERROR. Folder is not specified.")
            continue
        try:
            symbol = normalize_ashare_symbol(quote)
        except ValueError:
            symbol = str(quote)

        print(f"Reading klines from Postgres for symbol={symbol}")
        df = load_frame(symbol, "klines", time_column=time_column)
        if df.empty:
            print(f"ERROR: No klines in Postgres for symbol={symbol}")
            return pd.DataFrame()
        print(f"Loaded {len(df)} records.")

        df = select_window(df, config)
        ds["df"] = df

    freq = config["freq"]
    merge_interpolate = config.get("merge_interpolate", False)
    trading_days_only = config.get("merge_trading_days_only", False)
    df_out = merge_data_sources(
        data_sources, time_column, freq, merge_interpolate, trading_days_only=trading_days_only,
    )
    df_out = df_out.reset_index(drop=(df_out.index.name in df_out.columns))

    elapsed = datetime.now() - now
    print(f"Stored merged output with {len(df_out)} records. Finished in {str(elapsed).split('.')[0]}")
    return df_out


# --------------------------------------------------------------------------- #
# features
# --------------------------------------------------------------------------- #

def features(merged_data: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Apply feature generators. Mirrors ``pipeline/steps/features.py``."""
    now = datetime.now()
    df = select_window(merged_data, config)
    print(f"Input data size {len(df)} records.")

    feature_sets = config.get("feature_sets", [])
    if not feature_sets:
        print("ERROR: no feature sets defined. Nothing to process.")
        return df

    # Known train feature names (if configured) let us reuse Postgres history.
    required = list(config.get("train_features") or [])
    existing = _load_existing_frame(config, "features")
    last_rows = resolve_last_rows(config, len(df), len(existing))
    use_incremental = last_rows > 0 and can_use_incremental(
        existing, df, config["time_column"], required_columns=required or None,
    )

    model_store = new_model_store(config)

    all_features: list[str] = []
    if use_incremental:
        win = compute_window_size(config, last_rows)
        compute_df = df.tail(win).copy().reset_index(drop=True)
        print(f"Incremental features: recompute last {last_rows} rows (window={win}).")
        for i, fs in enumerate(feature_sets):
            fs_now = datetime.now()
            print(f"Start feature set {i}/{len(feature_sets)}. Generator {fs.get('generator')}...")
            compute_df, new_features = generate_feature_set(
                compute_df, fs, config, model_store, last_rows=0,
            )
            all_features.extend(new_features)
            print(
                f"Finished feature set {i}/{len(feature_sets)}. Features: {len(new_features)}. "
                f"Time: {str((datetime.now() - fs_now)).split('.')[0]}"
            )
        updated_tail = compute_df.tail(last_rows)
        df = apply_incremental_columns(
            df, existing, updated_tail, config["time_column"], all_features,
        )
    else:
        if last_rows == 0:
            print("Full feature recompute (no usable existing features frame).")
        for i, fs in enumerate(feature_sets):
            fs_now = datetime.now()
            print(f"Start feature set {i}/{len(feature_sets)}. Generator {fs.get('generator')}...")
            df, new_features = generate_feature_set(df, fs, config, model_store, last_rows=0)
            all_features.extend(new_features)
            print(
                f"Finished feature set {i}/{len(feature_sets)}. Features: {len(new_features)}. "
                f"Time: {str((datetime.now() - fs_now)).split('.')[0]}"
            )

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    na_df = df[df[all_features].isna().any(axis=1)] if all_features else pd.DataFrame()
    if len(na_df) > 0:
        print(f"WARNING: There exist {len(na_df)} rows with NULLs in some feature columns")

    append_feature_list(config, "feature_file_name", all_features)

    elapsed = datetime.now() - now
    print(f"Finished generating {len(all_features)} features in {str(elapsed).split('.')[0]}")
    return df


# --------------------------------------------------------------------------- #
# labels
# --------------------------------------------------------------------------- #

def labels(features_data: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Compute labels. Mirrors ``pipeline/steps/labels.py``."""
    now = datetime.now()
    df = select_window(features_data, config)
    print(f"Input data size {len(df)} records.")

    label_sets = config.get("label_sets", [])
    if not label_sets:
        print("ERROR: no label sets defined. Nothing to process.")
        return df

    required = list(config.get("labels") or [])
    existing = _load_existing_frame(config, "matrix")
    last_rows = resolve_last_rows(config, len(df), len(existing))
    use_incremental = last_rows > 0 and can_use_incremental(
        existing, df, config["time_column"], required_columns=required or None,
    )

    model_store = new_model_store(config)

    all_features: list[str] = []
    if use_incremental:
        win = compute_window_size(config, last_rows)
        compute_df = df.tail(win).copy().reset_index(drop=True)
        print(f"Incremental labels: recompute last {last_rows} rows (window={win}).")
        for i, fs in enumerate(label_sets):
            fs_now = datetime.now()
            print(f"Start label set {i}/{len(label_sets)}. Generator {fs.get('generator')}...")
            compute_df, new_features = generate_feature_set(
                compute_df, fs, config, model_store, last_rows=0,
            )
            all_features.extend(new_features)
            print(
                f"Finished label set {i}/{len(label_sets)}. Labels: {len(new_features)}. "
                f"Time: {str((datetime.now() - fs_now)).split('.')[0]}"
            )
        updated_tail = compute_df.tail(last_rows)
        df = apply_incremental_columns(
            df, existing, updated_tail, config["time_column"], all_features,
        )
    else:
        if last_rows == 0:
            print("Full label recompute (no usable existing matrix frame).")
        for i, fs in enumerate(label_sets):
            fs_now = datetime.now()
            print(f"Start label set {i}/{len(label_sets)}. Generator {fs.get('generator')}...")
            df, new_features = generate_feature_set(df, fs, config, model_store, last_rows=0)
            all_features.extend(new_features)
            print(
                f"Finished label set {i}/{len(label_sets)}. Labels: {len(new_features)}. "
                f"Time: {str((datetime.now() - fs_now)).split('.')[0]}"
            )

    df.replace([np.inf, -np.inf], np.nan, inplace=True)

    append_feature_list(config, "matrix_file_name", all_features)

    elapsed = datetime.now() - now
    print(f"Finished generating {len(all_features)} labels in {str(elapsed).split('.')[0]}")
    return df


# --------------------------------------------------------------------------- #
# train
# --------------------------------------------------------------------------- #

def train(matrix_data: pd.DataFrame, config: dict) -> dict:
    """Train models for all label×algo combinations and persist to MLflow.

    Mirrors ``pipeline/steps/train.py``. The returned dict is consumed by
    :func:`predict` (in-memory within one run). Each model is trained inside
    ``ModelStore.training_run`` so framework autolog and pyfunc registration
    share one MLflow run.
    """
    now = datetime.now()
    time_column = config["time_column"]

    # Window (train mode).
    is_train = config.get("train")
    window_size = config.get("train_length") if is_train else config.get("predict_length")
    features_horizon = config.get("features_horizon")
    if window_size:
        window_size += features_horizon

    df = matrix_data
    if window_size:
        df = df.tail(window_size).reset_index(drop=True)
    print(f"Input data size {len(df)} records.")

    train_features_all = config.get("train_features")
    labels_all = config["labels"]

    out_columns = [time_column, "open", "high", "low", "close", "volume", "close_time"]
    out_columns = [x for x in out_columns if x in df.columns]
    all_features = train_features_all + labels_all
    df = df[out_columns + [x for x in all_features if x not in out_columns]]

    for label in labels_all:
        df[label] = _coerce_binary_label(df[label])

    label_horizon = config["label_horizon"]
    train_length = config.get("train_length")

    if label_horizon:
        df = df.head(-label_horizon)
    if train_length:
        df = df.tail(train_length)

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    na_df = df[df[train_features_all].isna().any(axis=1)]
    if len(na_df) > 0:
        print(f"WARNING: There exist {len(na_df)} rows with NULLs in some feature columns")
    df = df.reset_index(drop=True)

    train_feature_sets = config.get("train_feature_sets", [])
    if not train_feature_sets:
        print("ERROR: no train feature sets defined. Nothing to process.")
        return {}

    print(f"Start training models for {len(df)} input records.")
    model_store = new_model_store(config)

    models: dict[str, tuple] = {}
    for i, fs in enumerate(train_feature_sets):
        fs_now = datetime.now()
        print(f"Start train feature set {i}/{len(train_feature_sets)}. Generator {fs.get('generator')}...")
        fs_models = train_feature_set(df, fs, config, model_store=model_store)
        models.update(fs_models)
        print(f"Finished train feature set {i}/{len(train_feature_sets)}. Time: {str((datetime.now() - fs_now)).split('.')[0]}")

    print(f"Stored {len(models)} model pairs to MLflow registry (prefix '{model_store.registry_prefix}').")

    elapsed = datetime.now() - now
    print(f"Finished training models in {str(elapsed).split('.')[0]}")
    return models


# --------------------------------------------------------------------------- #
# predict
# --------------------------------------------------------------------------- #

def predict(matrix_data: pd.DataFrame, trained_models, config: dict) -> pd.DataFrame:
    """Apply trained models to features and compute prediction scores.

    Mirrors ``pipeline/steps/predict.py``. ``trained_models`` is an optional
    in-memory dict from :func:`train` (DAG placeholder). Actual model weights
    come from :class:`ModelStore` / MLflow, so daily-predict jobs that skip
    ``train`` still work (catalog uses OptionalMemoryDataset).
    """
    _ = trained_models  # DAG / same-run cache hint only; ModelStore is source of truth
    now = datetime.now()
    time_column = config["time_column"]

    df = select_window(matrix_data, config)
    print(f"Input data size {len(df)} records.")

    train_features_all = config.get("train_features")
    labels_all = config["labels"]

    out_columns = [time_column, "open", "high", "low", "close", "volume", "close_time"]
    out_columns = [x for x in out_columns if x in df.columns]
    labels_present = set(labels_all).issubset(df.columns)
    all_features = (train_features_all + labels_all) if labels_present else train_features_all
    df = df[out_columns + [x for x in all_features if x not in out_columns]]

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    na_df = df[df[train_features_all].isna().any(axis=1)]
    if len(na_df) > 0:
        print(f"WARNING: There exist {len(na_df)} rows with NULLs in some feature columns. These rows will be removed.")
        df = df.dropna(subset=train_features_all).reset_index(drop=True)

    train_feature_sets = config.get("train_feature_sets", [])
    if not train_feature_sets:
        print("ERROR: no train feature sets defined. Nothing to process.")
        return pd.DataFrame()

    model_store = new_model_store(config)

    labels_hat_df = pd.DataFrame()
    for i, fs in enumerate(train_feature_sets):
        fs_now = datetime.now()
        print(f"Start train feature set {i}/{len(train_feature_sets)}. Generator {fs.get('generator')}...")
        fs_out_df, _ = predict_feature_set(df, fs, config, model_store)
        labels_hat_df = pd.concat([labels_hat_df, fs_out_df], axis=1)
        print(f"Finished train feature set {i}/{len(train_feature_sets)}. Time: {str((datetime.now() - fs_now)).split('.')[0]}")

    out_df = labels_hat_df.join(df[out_columns + (labels_all if labels_present else [])])

    # Compute and store scores sidecar.
    score_lines: list[str] = []
    for score_column_name in labels_hat_df.columns:
        label_column, _ = score_to_label_algo_pair(score_column_name)
        df_scores = pd.DataFrame({"y_true": out_df[label_column], "y_predicted": out_df[score_column_name]}).dropna()
        y_true = df_scores["y_true"]
        y_predicted = df_scores["y_predicted"]
        if ptypes.is_float_dtype(y_true) and ptypes.is_float_dtype(y_predicted):
            score = compute_scores_regression(y_true, y_predicted)
        else:
            score = compute_scores(y_true.astype(int), y_predicted)
        score_lines.append(f"{score_column_name}: {score}")
    store_scores(config, "predict_file_name", score_lines)

    elapsed = datetime.now() - now
    print(f"Finished predicting in {str(elapsed).split('.')[0]}")
    return out_df


# --------------------------------------------------------------------------- #
# signals
# --------------------------------------------------------------------------- #

def signals(predictions: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Generate signal columns from predictions. Mirrors ``pipeline/steps/signals.py``."""
    now = datetime.now()
    time_column = config["time_column"]
    df = select_window(predictions, config)
    print(f"Input data size {len(df)} records.")

    signal_sets = config.get("signal_sets", [])
    if not signal_sets:
        print("ERROR: no signal sets defined. Nothing to process.")
        return df

    model_store = new_model_store(config)

    all_features: list[str] = []
    for i, fs in enumerate(signal_sets):
        fs_now = datetime.now()
        print(f"Start feature set {i}/{len(signal_sets)}. Generator {fs.get('generator')}...")
        df, new_features = generate_feature_set(df, fs, config, model_store, last_rows=0)
        all_features.extend(new_features)
        print(f"Finished feature set {i}/{len(signal_sets)}. Features: {len(new_features)}. Time: {str((datetime.now() - fs_now)).split('.')[0]}")

    df.replace([np.inf, -np.inf], np.nan, inplace=True)

    out_columns = [time_column, "open", "high", "low", "close"]
    out_columns.extend(config.get("labels"))
    out_columns = [x for x in out_columns if x in df.columns]
    out_columns.extend(all_features)
    out_df = df[out_columns]

    elapsed = datetime.now() - now
    print(f"Finished signal generation in {str(elapsed).split('.')[0]}")
    return out_df


# --------------------------------------------------------------------------- #
# output (sink)
# --------------------------------------------------------------------------- #

def output(signals_data: pd.DataFrame, config: dict) -> None:
    """Execute output generators (e.g. trader simulation). Sink node.

    Mirrors ``pipeline/steps/output.py``.
    """
    now = datetime.now()
    time_column = config["time_column"]
    df = signals_data.set_index(time_column, inplace=False)

    model_store = new_model_store(config)

    output_sets = config.get("output_sets", [])
    for os_cfg in output_sets:
        try:
            asyncio.run(output_feature_set(df, os_cfg, config, model_store))
        except Exception as e:
            print(f"Error in output function: {e}. Generator: {os_cfg.get('generator')}. Output config: {os_cfg}")
            return

    elapsed = datetime.now() - now
    print(f"Finished executing outputs in {str(elapsed).split('.')[0]}")
