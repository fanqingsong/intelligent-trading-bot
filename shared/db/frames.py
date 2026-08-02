"""DataFrame ↔ market_frames JSONB persistence."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from shared.db.engine import get_session_factory
from shared.db.models import MarketFrame

FRAME_KINDS = ("klines", "data", "features", "matrix", "predictions", "signals")

# Map logical file names / catalog kinds
KIND_ALIASES = {
    "klines": "klines",
    "klines.csv": "klines",
    "data": "data",
    "data.csv": "data",
    "features": "features",
    "features.csv": "features",
    "matrix": "matrix",
    "matrix.csv": "matrix",
    "predictions": "predictions",
    "predictions.csv": "predictions",
    "signals": "signals",
    "signals.csv": "signals",
}


def normalize_kind(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key in KIND_ALIASES:
        return KIND_ALIASES[key]
    if key.endswith(".csv") or key.endswith(".parquet"):
        return normalize_kind(key.rsplit(".", 1)[0])
    if key not in FRAME_KINDS:
        raise ValueError(f"Unknown frame kind: {kind!r}; expected one of {FRAME_KINDS}")
    return key


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.isoformat()
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        if isinstance(value, np.floating) and (np.isnan(value) or np.isinf(value)):
            return None
        return value.item()
    if hasattr(value, "item"):
        try:
            return _to_jsonable(value.item())
        except Exception:
            pass
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value
    return str(value)


def _normalize_ts(value: Any) -> datetime:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError(f"Invalid timestamp: {value!r}")
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _df_to_records(
    df: pd.DataFrame,
    *,
    symbol: str,
    kind: str,
    time_column: str,
) -> list[dict[str, Any]]:
    """Vectorised DataFrame → market_frames row dicts (no ``iterrows``)."""
    work = df.copy()
    work[time_column] = pd.to_datetime(work[time_column], errors="coerce", utc=True)
    work = work.dropna(subset=[time_column]).sort_values(by=time_column)
    work = work.drop_duplicates(subset=[time_column], keep="last")
    if work.empty:
        return []

    ts_values = [_normalize_ts(v) for v in work[time_column].tolist()]
    payload_df = work.drop(columns=[time_column])
    # NaN/Inf → None for JSONB; keep other dtypes for _to_jsonable.
    payload_df = payload_df.astype(object).where(pd.notnull(payload_df), None)
    raw_payloads = payload_df.to_dict(orient="records")

    records: list[dict[str, Any]] = []
    for ts, payload in zip(ts_values, raw_payloads):
        clean = {str(k): _to_jsonable(v) for k, v in payload.items()}
        records.append({"symbol": symbol, "kind": kind, "ts": ts, "payload": clean})
    return records


def _rows_to_frame(rows: list[MarketFrame], time_column: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    records: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.payload or {})
        payload[time_column] = row.ts
        records.append(payload)
    df = pd.DataFrame.from_records(records)
    if time_column in df.columns:
        df[time_column] = pd.to_datetime(df[time_column], utc=True, errors="coerce")
        # Pipeline / merge historically use tz-naive timestamps.
        df[time_column] = df[time_column].dt.tz_convert(None)
        df = df.sort_values(by=time_column).reset_index(drop=True)
    return df


def load_frame(symbol: str, kind: str, time_column: str = "timestamp") -> pd.DataFrame:
    kind = normalize_kind(kind)
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        rows = session.scalars(
            select(MarketFrame)
            .where(MarketFrame.symbol == symbol, MarketFrame.kind == kind)
            .order_by(MarketFrame.ts.asc())
        ).all()
    return _rows_to_frame(list(rows), time_column)


def load_frame_tail(
    symbol: str,
    kind: str,
    n: int = 1,
    time_column: str = "timestamp",
) -> pd.DataFrame:
    """Load only the last ``n`` rows for a symbol/kind (cheaper than full ``load_frame``)."""
    kind = normalize_kind(kind)
    limit = max(1, int(n))
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        rows = session.scalars(
            select(MarketFrame)
            .where(MarketFrame.symbol == symbol, MarketFrame.kind == kind)
            .order_by(MarketFrame.ts.desc())
            .limit(limit)
        ).all()
    # Query is newest-first; reverse to chronological for callers.
    return _rows_to_frame(list(reversed(rows)), time_column)


def frame_row_count(symbol: str, kind: str) -> int:
    kind = normalize_kind(kind)
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(MarketFrame)
                .where(MarketFrame.symbol == symbol, MarketFrame.kind == kind)
            )
            or 0
        )


def frame_exists(symbol: str, kind: str) -> bool:
    kind = normalize_kind(kind)
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        return (
            session.scalar(
                select(MarketFrame.id)
                .where(MarketFrame.symbol == symbol, MarketFrame.kind == kind)
                .limit(1)
            )
            is not None
        )


def save_frame(
    symbol: str,
    kind: str,
    df: pd.DataFrame,
    time_column: str = "timestamp",
    replace: bool = False,
) -> int:
    """Upsert DataFrame rows into market_frames. Returns number of rows written.

    Default is incremental upsert (no full-table DELETE). Pass ``replace=True``
    only when the caller intentionally wants to wipe the symbol+kind first
    (e.g. klines download after a full rebuild).
    """
    kind = normalize_kind(kind)
    if df is None or df.empty:
        if replace:
            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                session.execute(
                    delete(MarketFrame).where(
                        MarketFrame.symbol == symbol,
                        MarketFrame.kind == kind,
                    )
                )
                session.commit()
        return 0

    if time_column not in df.columns:
        raise ValueError(f"DataFrame missing time column {time_column!r}")

    records = _df_to_records(df, symbol=symbol, kind=kind, time_column=time_column)

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        if replace:
            session.execute(
                delete(MarketFrame).where(
                    MarketFrame.symbol == symbol,
                    MarketFrame.kind == kind,
                )
            )
        if records:
            chunk = 500
            for i in range(0, len(records), chunk):
                batch = records[i : i + chunk]
                stmt = pg_insert(MarketFrame).values(batch)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_market_frame",
                    set_={"payload": stmt.excluded.payload},
                )
                session.execute(stmt)
        session.commit()
    return len(records)


def list_kinds_for_symbol(symbol: str) -> list[dict[str, Any]]:
    """Return available kinds and row counts for a symbol (Data UI)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        rows = session.execute(
            select(MarketFrame.kind, MarketFrame.ts)
            .where(MarketFrame.symbol == symbol)
            .order_by(MarketFrame.kind, MarketFrame.ts)
        ).all()

    counts: dict[str, dict[str, Any]] = {}
    for kind, ts in rows:
        entry = counts.setdefault(kind, {"kind": kind, "rows": 0, "mtime": None})
        entry["rows"] += 1
        entry["mtime"] = ts.timestamp() if hasattr(ts, "timestamp") else None
    return [counts[k] for k in sorted(counts.keys())]
