"""Incremental feature/label helpers for inference nodes.

Talib's ``last_rows`` stream path is disabled (and for multi-row tails is
slower than one vectorised pass). Instead we recompute only a trailing window
of size ``features_horizon + last_rows``, then stitch those columns onto the
previously persisted frame loaded from Postgres.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


def resolve_last_rows(config: dict, input_len: int, existing_len: int) -> int:
    """How many trailing rows to recompute. ``0`` means full recompute."""
    if existing_len <= 0 or input_len <= 0:
        return 0
    overlap = int(config.get("append_overlap_records") or 5)
    label_h = int(config.get("label_horizon") or 0)
    predict_len = int(config.get("predict_length") or 0)
    new_rows = max(0, input_len - existing_len)
    last_rows = max(predict_len, new_rows, 1) + label_h + overlap
    if last_rows >= input_len:
        return 0
    return int(last_rows)


def can_use_incremental(
    existing: pd.DataFrame,
    df: pd.DataFrame,
    time_column: str,
    required_columns: Iterable[str] | None = None,
) -> bool:
    """True when *existing* can seed an incremental update of *df*."""
    if existing is None or existing.empty or df is None or df.empty:
        return False
    if time_column not in existing.columns or time_column not in df.columns:
        return False
    if required_columns:
        missing = [c for c in required_columns if c not in existing.columns]
        if missing:
            return False
    return True


def apply_incremental_columns(
    df: pd.DataFrame,
    existing: pd.DataFrame,
    updated_tail: pd.DataFrame,
    time_column: str,
    columns: list[str],
) -> pd.DataFrame:
    """Fill *columns* on *df* from *existing*, then overlay *updated_tail*."""
    out = df.copy()
    out[time_column] = pd.to_datetime(out[time_column], errors="coerce")
    cols = [c for c in columns if c]

    if existing is not None and not existing.empty:
        ex = existing.copy()
        ex[time_column] = pd.to_datetime(ex[time_column], errors="coerce")
        present = [c for c in cols if c in ex.columns]
        if present:
            # Drop any stale copies before merge.
            out = out.drop(columns=[c for c in present if c in out.columns], errors="ignore")
            out = out.merge(ex[[time_column] + present], on=time_column, how="left")

    if updated_tail is None or updated_tail.empty:
        return out.reset_index(drop=True)

    upd = updated_tail.copy()
    upd[time_column] = pd.to_datetime(upd[time_column], errors="coerce")
    out = out.set_index(time_column)
    upd = upd.set_index(time_column)
    for c in cols:
        if c not in upd.columns:
            continue
        if c not in out.columns:
            out[c] = pd.NA
        common = upd.index.intersection(out.index)
        if len(common):
            out.loc[common, c] = upd.loc[common, c]
        missing = upd.index.difference(out.index)
        if len(missing):
            # Append brand-new timestamps (should be rare inside a windowed df).
            out = pd.concat([out, upd.loc[missing]], axis=0)
        # Avoid bool→float upcast from NaN merge leaving classifiers with
        # continuous y (sklearn: "Unknown label type: unknown").
        if pd.api.types.is_bool_dtype(upd[c]) or str(upd[c].dtype) == "boolean":
            out[c] = out[c].astype("boolean")
        elif pd.api.types.is_integer_dtype(upd[c]):
            out[c] = pd.to_numeric(out[c], errors="coerce").round().astype("Int64")
    return out.sort_index().reset_index()


def compute_window_size(config: dict, last_rows: int) -> int:
    """Rows needed as input so rolling/future windows stay valid."""
    horizon = int(config.get("features_horizon") or 0)
    label_h = int(config.get("label_horizon") or 0)
    return max(last_rows + horizon + label_h, last_rows)
