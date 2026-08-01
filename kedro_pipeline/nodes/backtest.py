"""Backtest pipeline nodes (predict_rolling → simulate).

Faithful ports of ``pipeline/steps/predict_rolling.py`` and
``pipeline/steps/simulate.py``: no ``App`` globals, models served by an
explicit ``ModelStore`` (MLflow-backed).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pandas.api.types as ptypes
from joblib import Parallel, delayed
from sklearn.model_selection import ParameterGrid

from kedro_pipeline.backtesting.metrics import simulated_trade_performance
from kedro_pipeline.orchestration.generators import generate_feature_set, predict_feature_set, train_feature_set
from kedro_pipeline.classifiers.model_store import score_to_label_algo_pair
from kedro_pipeline.common.utils import compute_scores, compute_scores_regression, find_index

from ..catalog.paths import resolve_data_path
from .helpers import new_model_store, store_scores


# --------------------------------------------------------------------------- #
# predict_rolling
# --------------------------------------------------------------------------- #

def predict_rolling(matrix_data: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Rolling train+predict over the matrix; mirrors run_predict_rolling.

    Each step trains a fresh model (logged to MLflow as a new version tagged
    with the rolling step) and predicts the next horizon slice.
    """
    now = datetime.now()
    time_column = config["time_column"]
    df = matrix_data
    print(f"Finished loading {len(df)} records with {len(df.columns)} columns.")

    rp_config = config["rolling_predict"]
    data_start = rp_config.get("data_start", None)
    data_end = rp_config.get("data_end", None)

    if data_start:
        if isinstance(data_start, str):
            df = df[df[time_column] >= data_start]
        elif isinstance(data_start, int):
            df = df.iloc[data_start:]
    if data_end:
        if isinstance(data_end, str):
            df = df[df[time_column] < data_end]
        elif isinstance(data_end, int):
            df = df.iloc[:-data_end]
    df = df.reset_index(drop=True)
    print(f"Input data size {len(df)} records. Range: [{df.iloc[0][time_column]}, {df.iloc[-1][time_column]}]")

    # Resolve the rolling loop parameters (any one may be inferred).
    prediction_start = rp_config.get("prediction_start", None)
    if isinstance(prediction_start, str):
        prediction_start = find_index(df, prediction_start)
    prediction_size = rp_config.get("prediction_size")
    prediction_steps = rp_config.get("prediction_steps")

    if not prediction_start:
        prediction_start = len(df) - prediction_size * prediction_steps
    elif not prediction_size:
        prediction_size = (len(df) - prediction_start) // prediction_steps
    elif not prediction_steps:
        prediction_steps = (len(df) - prediction_start) // prediction_size

    if len(df) - prediction_start < prediction_steps * prediction_size:
        raise ValueError(
            f"Not enough data for {prediction_steps} steps each of size {prediction_size} "
            f"starting from {prediction_start}."
        )

    train_features_all = config.get("train_features")
    labels_all = config.get("labels")

    out_columns = [time_column, "open", "high", "low", "close", "volume", "close_time"]
    out_columns = [x for x in out_columns if x in df.columns]
    labels_present = set(labels_all).issubset(df.columns)
    all_features = (train_features_all + labels_all) if labels_present else train_features_all
    df = df[out_columns + [x for x in all_features if x not in out_columns]]

    for label in labels_all:
        if np.issubdtype(df[label].dtype, bool):
            df[label] = df[label].astype(int)

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df = df.reset_index(drop=True)

    print(f"Start index: {prediction_start}. Steps: {prediction_steps}. Step size: {prediction_size}")

    train_feature_sets = config.get("train_feature_sets", [])
    if not train_feature_sets:
        print("ERROR: no train feature sets defined. Nothing to process.")
        return pd.DataFrame()

    label_horizon = config["label_horizon"]
    train_length = config.get("train_length")

    use_multiprocessing = rp_config.get("use_multiprocessing", False)
    max_workers = rp_config.get("max_workers", None)
    parallel = Parallel(n_jobs=max_workers, backend="loky", verbose=13) if use_multiprocessing else None

    model_store = new_model_store(config)
    labels_hat_df = pd.DataFrame()

    print("Starting rolling predict loop...")
    for step in range(prediction_steps):
        predict_start = prediction_start + step * prediction_size
        predict_end = predict_start + prediction_size
        predict_df = df.iloc[predict_start:predict_end]

        train_end = predict_start - label_horizon - 1
        train_start = max(0, train_end - train_length) if train_length else 0
        train_df = df.iloc[train_start:train_end].dropna(subset=train_features_all)

        print(
            f"\n===>>> Step {step}/{prediction_steps}. Train [{train_start},{train_end}]. "
            f"Predict [{predict_start},{predict_end}]"
        )
        step_start = datetime.now()

        predict_labels_df = _execute_train_predict_step(
            config, train_df, predict_df, parallel, model_store, step
        )
        labels_hat_df = pd.concat([labels_hat_df, predict_labels_df])

        print(f"End step {step}/{prediction_steps}. Time: {str((datetime.now() - step_start)).split('.')[0]}")

    print(f"\nFinished {prediction_steps} steps. Predicted rows: {len(labels_hat_df)}.")

    out_df = labels_hat_df.join(df[out_columns + labels_all])

    # Score sidecar (same as predict).
    score_lines: list[str] = []
    for score_column_name in labels_hat_df.columns:
        label_column, _ = score_to_label_algo_pair(score_column_name)
        df_scores = pd.DataFrame(
            {"y_true": out_df[label_column], "y_predicted": out_df[score_column_name]}
        ).dropna()
        y_true = df_scores["y_true"]
        y_predicted = df_scores["y_predicted"]
        if ptypes.is_float_dtype(y_true) and ptypes.is_float_dtype(y_predicted):
            score = compute_scores_regression(y_true, y_predicted)
        else:
            score = compute_scores(y_true.astype(int), y_predicted)
        score_lines.append(f"{score_column_name}: {score}")
    store_scores(config, "predict_file_name", score_lines)

    elapsed = datetime.now() - now
    print(f"Finished rolling prediction in {str(elapsed).split('.')[0]}")
    return out_df


def _execute_train_predict_step(config, train_df, predict_df, parallel, model_store, step):
    """One rolling step: train all feature sets, persist, then predict.

    Port of ``predict_rolling.execute_train_predict_step``. ``model_store`` is
    passed explicitly (no global). ``step`` tags the logged MLflow versions.
    """
    train_feature_sets = config.get("train_feature_sets", [])

    print(f"Start train all models from {len(train_feature_sets)} feature sets. Train size: {len(train_df)}")
    models: dict[str, tuple] = {}
    if isinstance(parallel, Parallel):
        results = parallel(delayed(train_feature_set)(train_df, fs, config) for fs in train_feature_sets)
        for fs_models in results:
            models.update(fs_models)
    else:
        for fs in train_feature_sets:
            models.update(train_feature_set(train_df, fs, config))

    # Persist each trained pair to MLflow (new version per rolling step).
    for score_column_name, meta in models.items():
        model_store.put_model_pair(
            score_column_name,
            meta["pair"],
            tags={"rolling_step": str(step)},
            metrics=meta.get("metrics"),
            params=meta.get("params"),
            sample_X=meta.get("sample_X"),
            algo=meta.get("algo"),
        )
    print(f"Stored {len(models)} model pairs to MLflow registry (rolling step {step}).")

    print(f"Start predictions for {len(predict_df)} records.")
    out_df = pd.DataFrame()
    for fs in train_feature_sets:
        fs_out_df, _ = predict_feature_set(predict_df, fs, config, model_store)
        out_df = pd.concat([out_df, fs_out_df], axis=1)
    return out_df


# --------------------------------------------------------------------------- #
# simulate
# --------------------------------------------------------------------------- #

def simulate(signals_data: pd.DataFrame, config: dict) -> None:
    """Grid-search trade parameters via backtesting. Mirrors run_simulate.

    Writes the top-N parameter/performance rows to the ``signal_models.txt``
    sidecar. Sink node (returns None).
    """
    now = datetime.now()
    time_column = config["time_column"]
    df = signals_data

    simulate_config = config["simulate_model"]
    data_start = simulate_config.get("data_start", None)
    data_end = simulate_config.get("data_end", None)

    if data_start:
        if isinstance(data_start, str):
            df = df[df[time_column] >= data_start]
        elif isinstance(data_start, int):
            df = df.iloc[data_start:]
    if data_end:
        if isinstance(data_end, str):
            df = df[df[time_column] < data_end]
        elif isinstance(data_end, int):
            df = df.iloc[:-data_end]
    df = df.reset_index(drop=True)
    print(f"Input data size {len(df)} records. Range: [{df.iloc[0][time_column]}, {df.iloc[-1][time_column]}]")

    parameter_grid = simulate_config.get("grid")
    direction = simulate_config.get("direction", "")
    if direction not in ["long", "short"]:
        raise ValueError(f"Unknown direction '{direction}'. Only 'long' or 'short' are possible.")
    topn_to_store = simulate_config.get("topn_to_store", 10)

    for key in ["buy_signal_threshold", "buy_signal_threshold_2", "sell_signal_threshold", "sell_signal_threshold_2"]:
        if isinstance(parameter_grid.get(key), str):
            parameter_grid[key] = eval(parameter_grid.get(key))  # noqa: S307 - legacy eval of configured ranges

    if simulate_config.get("buy_sell_equal"):
        parameter_grid["sell_signal_threshold"] = [None]
        parameter_grid["sell_signal_threshold_2"] = [None]

    months_in_simulation = (df[time_column].iloc[-1] - df[time_column].iloc[0]) / timedelta(days=365 / 12)

    generator_name = simulate_config.get("signal_generator")
    signal_generator = next(
        (ss for ss in config.get("signal_sets", []) if ss.get("generator") == generator_name), None
    )
    if not signal_generator:
        raise ValueError(f"Signal generator '{generator_name}' not found among 'signal_sets'")

    model_store = new_model_store(config)

    performances = []
    for parameters in ParameterGrid([parameter_grid]):
        if simulate_config.get("buy_sell_equal"):
            parameters["sell_signal_threshold"] = -parameters["buy_signal_threshold"]
            if parameters.get("buy_signal_threshold_2") is not None:
                parameters["sell_signal_threshold_2"] = -parameters["buy_signal_threshold_2"]

        signal_generator["config"]["parameters"].update(parameters)
        df, _ = generate_feature_set(df, signal_generator, config, model_store, last_rows=0)

        buy_signal_column = signal_generator["config"]["names"][0]
        sell_signal_column = signal_generator["config"]["names"][1]

        performance, long_performance, short_performance = simulated_trade_performance(
            df, buy_signal_column, sell_signal_column, "close"
        )
        if direction == "long":
            performance = long_performance
        elif direction == "short":
            performance = short_performance

        performance["#transactions/M"] = round(performance["#transactions"] / months_in_simulation, 2)
        performance["profit/M"] = round(performance["profit"] / months_in_simulation, 2)
        performance["%profit/M"] = round(performance["%profit"] / months_in_simulation, 2)

        performances.append({"model": parameters, "performance": performance})

    performances = sorted(performances, key=lambda x: x["performance"]["%profit/M"], reverse=True)
    performances = performances[:topn_to_store]

    keys = list(performances[0]["model"].keys()) + list(performances[0]["performance"].keys())
    lines = []
    for p in performances:
        record = list(p["model"].values()) + list(p["performance"].values())
        lines.append(",".join(str(v) for v in record))

    out_path = resolve_data_path(config, "signal_models_file_name").with_suffix(".txt").resolve()
    add_header = not out_path.is_file()
    with open(out_path, "a+", encoding="utf-8") as f:
        if add_header:
            f.write(",".join(keys) + "\n")
        f.write("\n".join(lines) + "\n\n")

    print(f"Simulation results stored in: {out_path}. Lines: {len(lines)}.")
    elapsed = datetime.now() - now
    print(f"Finished simulation in {str(elapsed).split('.')[0]}")
