"""Runtime defaults shared by the pipeline worker.

``App.config`` is deep-copied as the base for each job; ``App.transaction``
holds in-process state for ``trader_simulation`` output adapters.
"""
from __future__ import annotations

from pathlib import Path

from kedro_pipeline.classifiers.model_store import ModelStore

PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent


class App:
    """Globally visible variables used by offline pipeline adapters."""

    # Trade simulator (trader_simulation output adapter)
    transaction = None

    model_store: ModelStore | None = None

    config = {
        "venue": "ashare",
        "merge_file_name": "data.csv",
        "feature_file_name": "features.csv",
        "matrix_file_name": "matrix.csv",
        "predict_file_name": "predictions.csv",
        "signal_file_name": "signals.csv",
        "signal_models_file_name": "signal_models",
        "model_folder": "MODELS",
        "mlflow_tracking_uri": "http://localhost:5000",
        "mlflow_experiment_name": "itb_default",
        "mlflow_registry_prefix": "itb_",
        "mlflow_default_alias": "Production",
        "mlflow_log_input_example": True,
        "mlflow_eval_split": "in_sample",
        "time_column": "timestamp",
        "data_folder": "/app/data",
        "symbol": "600519",
        "freq": "1D",
        "data_sources": [],
        "feature_sets": [],
        "label_sets": [],
        "label_horizon": 0,
        "train_length": 0,
        "train_features": [],
        "labels": [],
        "algorithms": [],
        "features_horizon": 10,
        "signal_sets": [],
        "trade_model": {},
        "simulate_model": {},
    }
