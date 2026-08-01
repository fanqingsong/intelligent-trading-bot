"""Shared dataframe / scoring helpers used by Kedro nodes and generators."""
from __future__ import annotations

import importlib

import dateparser
import numpy as np
import pandas as pd
import pytz
from pandas.tseries.frequencies import to_offset
from sklearn import metrics

# Re-export feature helpers historically imported via ``from kedro_pipeline.common.utils import *``.
from kedro_pipeline.features.gen_features import *  # noqa: F401,F403


def freq_to_timedelta(freq: str):
    """Given a pandas freq string, return its duration (timedelta)."""
    if freq.endswith("B"):
        freq = freq[:-1] + "D"
    return pd.Timedelta(to_offset(freq)).to_pytimedelta()


def find_index(df: pd.DataFrame, date_str: str, column_name: str = "timestamp"):
    """Return index of the record with the specified datetime string."""
    d = dateparser.parse(date_str)
    try:
        res = df[df[column_name] == d]
    except TypeError:
        if d.tzinfo is None or d.tzinfo.utcoffset(d) is None:
            d = d.replace(tzinfo=pytz.utc)
        else:
            d = d.replace(tzinfo=None)
        res = df[df[column_name] == d]

    if res is None or len(res) == 0:
        raise ValueError(
            f"Cannot find date '{date_str}' in the column '{column_name}'. "
            "Either it does not exist or wrong format"
        )
    return res.index[0]


def resolve_generator_name(gen_name: str):
    """Resolve ``module.path:func_name`` to a callable, or None."""
    mod_and_func = gen_name.split(":", 1)
    mod_name = mod_and_func[0] if len(mod_and_func) > 1 else None
    func_name = mod_and_func[-1]
    if not mod_name:
        return None
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        return None
    if mod is None:
        return None
    try:
        return getattr(mod, func_name)
    except AttributeError:
        return None


def merge_data_sources(
    data_sources: list,
    time_column: str,
    freq: str,
    merge_interpolate: bool,
    trading_days_only: bool = False,
    close_column: str = "close",
):
    """Merge multiple source frames onto a common time raster."""
    for ds in data_sources:
        df = ds.get("df")

        if time_column in df.columns:
            df = df.set_index(time_column)
        elif df.index.name == time_column:
            pass
        else:
            print("ERROR: Timestamp column is absent.")
            return

        if ds["column_prefix"]:
            df.columns = [
                ds["column_prefix"] + "_" + col if not col.startswith(ds["column_prefix"] + "_") else col
                for col in df.columns
            ]

        ds["start"] = df.first_valid_index()
        ds["end"] = df.last_valid_index()
        ds["df"] = df

    range_start = min(ds["start"] for ds in data_sources)
    range_end = min(ds["end"] for ds in data_sources)
    index = pd.date_range(range_start, range_end, freq=freq)

    df_out = pd.DataFrame(index=index)
    df_out.index.name = time_column
    df_out.insert(0, time_column, df_out.index)

    for ds in data_sources:
        df_out = df_out.join(ds["df"])

    if merge_interpolate:
        num_columns = df_out.select_dtypes((float, int)).columns.tolist()
        for col in num_columns:
            df_out[col] = df_out[col].interpolate()

    if trading_days_only and close_column in df_out.columns:
        before = len(df_out)
        df_out = df_out.dropna(subset=[close_column])
        dropped = before - len(df_out)
        if dropped:
            print(f"Dropped {dropped} non-trading-day rows (empty {close_column})")

    return df_out


def compute_scores(y_true, y_hat):
    """Compute classification scores and return them as dict."""
    y_true = y_true.astype(int)
    y_hat_class = np.where(y_hat.values > 0.5, 1, 0)

    try:
        auc = metrics.roc_auc_score(y_true, y_hat.fillna(value=0))
    except ValueError:
        auc = 0.0

    try:
        ap = metrics.average_precision_score(y_true, y_hat.fillna(value=0))
    except ValueError:
        ap = 0.0

    f1 = metrics.f1_score(y_true, y_hat_class)
    precision = metrics.precision_score(y_true, y_hat_class)
    recall = metrics.recall_score(y_true, y_hat_class)

    scores = dict(auc=auc, ap=ap, f1=f1, precision=precision, recall=recall)
    return {key: round(float(value), 3) for (key, value) in scores.items()}


def compute_scores_regression(y_true, y_hat):
    """Compute regression scores. Input columns must be numeric."""
    try:
        mae = metrics.mean_absolute_error(y_true, y_hat)
    except ValueError:
        mae = np.nan

    try:
        mape = metrics.mean_absolute_percentage_error(y_true, y_hat)
    except ValueError:
        mape = np.nan

    try:
        r2 = metrics.r2_score(y_true, y_hat)
    except ValueError:
        r2 = np.nan

    y_true_class = np.where(y_true.values > 0.0, +1, -1)
    y_hat_class = np.where(y_hat.values > 0.0, +1, -1)

    try:
        auc = metrics.roc_auc_score(y_true_class, y_hat_class)
    except ValueError:
        auc = 0.0

    try:
        ap = metrics.average_precision_score(y_true_class, y_hat_class)
    except ValueError:
        ap = 0.0

    f1 = metrics.f1_score(y_true_class, y_hat_class)
    precision = metrics.precision_score(y_true_class, y_hat_class)
    recall = metrics.recall_score(y_true_class, y_hat_class)

    scores = dict(
        mae=mae, mape=mape, r2=r2,
        auc=auc, ap=ap, f1=f1, precision=precision, recall=recall,
    )
    return {key: round(float(value), 3) for (key, value) in scores.items()}


def first_location_of_crossing_threshold(df, horizon, threshold, close_column_name, price_column_name):
    """Distance to first future bar that crosses a percentage threshold from close."""

    def fn_high(x):
        if len(x) < 2:
            return np.nan
        p = x[0, 0]
        p_threshold = p * (1 + (threshold / 100.0))
        idx = np.argmax(x[1:, 1] > p_threshold)
        if idx == 0 and x[1, 1] <= p_threshold:
            return np.nan
        return idx

    def fn_low(x):
        if len(x) < 2:
            return np.nan
        p = x[0, 0]
        p_threshold = p * (1 + (threshold / 100.0))
        idx = np.argmax(x[1:, 1] < p_threshold)
        if idx == 0 and x[1, 1] >= p_threshold:
            return np.nan
        return idx

    rl = df[[close_column_name, price_column_name]].rolling(
        horizon + 1, min_periods=(horizon // 2), method="table"
    )

    if threshold > 0:
        df_out = rl.apply(fn_high, raw=True, engine="numba")
    elif threshold < 0:
        df_out = rl.apply(fn_low, raw=True, engine="numba")
    else:
        raise ValueError("Threshold cannot be zero.")

    df_out = df_out.shift(-horizon)
    return df_out.iloc[:, 0]
