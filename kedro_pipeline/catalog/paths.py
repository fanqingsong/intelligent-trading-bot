"""Shared helpers for Kedro nodes.

* train/predict windowing (``train_length`` vs ``predict_length`` plus ``features_horizon``)
* path resolution under ``<data_folder>/<symbol>/`` for sidecar ``.txt`` files
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def resolve_data_path(params: dict, file_key: str) -> Path:
    """Resolve ``<data_folder>/<symbol>/<params[file_key]>`` for sidecars."""
    data_folder = Path(params["data_folder"])
    symbol = params["symbol"]
    file_name = params[file_key]
    return (data_folder / symbol / file_name).resolve()


def select_window(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Apply the shared train/predict window to a frame."""
    is_train = params.get("train")
    window_size = params.get("train_length") if is_train else params.get("predict_length")
    features_horizon = params.get("features_horizon")
    if window_size:
        window_size += features_horizon
        df = df.tail(window_size).reset_index(drop=True)
    return df
