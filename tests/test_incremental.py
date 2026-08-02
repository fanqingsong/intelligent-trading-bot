"""Tests for incremental feature/label helpers and vectorised frame records."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from kedro_pipeline.nodes.incremental import (
    apply_incremental_columns,
    can_use_incremental,
    compute_window_size,
    resolve_last_rows,
)
from shared.db.frames import _df_to_records


def test_resolve_last_rows_full_when_no_existing():
    cfg = {"append_overlap_records": 5, "label_horizon": 5, "predict_length": 60}
    assert resolve_last_rows(cfg, input_len=1000, existing_len=0) == 0


def test_resolve_last_rows_uses_predict_and_overlap():
    cfg = {"append_overlap_records": 5, "label_horizon": 5, "predict_length": 60}
    # max(60, 0, 1) + 5 + 5 = 70
    assert resolve_last_rows(cfg, input_len=1000, existing_len=1000) == 70


def test_resolve_last_rows_accounts_for_new_rows():
    cfg = {"append_overlap_records": 2, "label_horizon": 1, "predict_length": 3}
    # new_rows=10 → max(3, 10, 1) + 1 + 2 = 13
    assert resolve_last_rows(cfg, input_len=110, existing_len=100) == 13


def test_resolve_last_rows_falls_back_to_full_when_too_large():
    cfg = {"append_overlap_records": 5, "label_horizon": 5, "predict_length": 60}
    assert resolve_last_rows(cfg, input_len=50, existing_len=50) == 0


def test_can_use_incremental_requires_columns():
    ts = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({"timestamp": ts, "close": range(5)})
    existing = pd.DataFrame({"timestamp": ts, "close": range(5), "feat_a": range(5)})
    assert can_use_incremental(existing, df, "timestamp", ["feat_a"])
    assert not can_use_incremental(existing, df, "timestamp", ["feat_missing"])


def test_apply_incremental_columns_overlays_tail():
    ts = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({"timestamp": ts, "close": [1, 2, 3, 4, 5]})
    existing = pd.DataFrame({
        "timestamp": ts,
        "close": [1, 2, 3, 4, 5],
        "feat": [10, 20, 30, 40, 50],
    })
    updated_tail = pd.DataFrame({
        "timestamp": ts[-2:],
        "close": [4, 5],
        "feat": [400, 500],
    })
    out = apply_incremental_columns(df, existing, updated_tail, "timestamp", ["feat"])
    assert list(out["feat"]) == [10, 20, 30, 400, 500]


def test_compute_window_size():
    cfg = {"features_horizon": 60, "label_horizon": 5}
    assert compute_window_size(cfg, last_rows=70) == 70 + 60 + 5


def test_df_to_records_vectorised_no_iterrows():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "close": [1.0, np.nan],
        "flag": [True, False],
    })
    records = _df_to_records(df, symbol="600519", kind="features", time_column="timestamp")
    assert len(records) == 2
    assert records[0]["symbol"] == "600519"
    assert records[0]["kind"] == "features"
    assert records[0]["payload"]["close"] == 1.0
    assert records[0]["payload"]["flag"] is True
    assert records[1]["payload"]["close"] is None
    assert isinstance(records[0]["ts"], datetime)
    assert records[0]["ts"].tzinfo is not None


def test_coerce_binary_label_from_float():
    from kedro_pipeline.nodes.inference import _coerce_binary_label

    s = pd.Series([0.0, 1.0, np.nan, 1.0])
    out = _coerce_binary_label(s)
    assert str(out.dtype) == "Int64"
    assert list(out.dropna().astype(int)) == [0, 1, 1]


def test_as_classifier_y_from_float():
    from kedro_pipeline.orchestration.generators import _as_classifier_y

    s = pd.Series([0.0, 1.0, 0.0, 1.0])
    out = _as_classifier_y(s)
    assert out.dtype == int or np.issubdtype(out.dtype, np.integer)
    assert set(out.unique().tolist()) <= {0, 1}


def test_train_feature_set_parallel_label_algo():
    from kedro_pipeline.orchestration.generators import train_feature_set

    n = 120
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "f1": rng.normal(size=n),
        "f2": rng.normal(size=n),
        "y1": rng.integers(0, 2, size=n),
        "y2": rng.integers(0, 2, size=n),
    })
    config = {
        "train_features": ["f1", "f2"],
        "labels": ["y1", "y2"],
        "algorithms": [
            {
                "name": "lc",
                "algo": "lc",
                "params": {"is_scale": True, "length": 80},
                "train": {"C": 1.0, "max_iter": 50},
            },
            {
                "name": "gb",
                "algo": "gb",
                "params": {"is_scale": False, "length": 80},
                "train": {"objective": "binary", "verbosity": -1, "num_iterations": 5},
            },
        ],
        "train_parallel": {"use_multiprocessing": True, "max_workers": 2},
        "mlflow_eval_split": "in_sample",
    }
    models = train_feature_set(
        df, {"generator": "train_features", "config": {}}, config, model_store=None,
    )
    assert set(models) == {"y1_lc", "y1_gb", "y2_lc", "y2_gb"}
