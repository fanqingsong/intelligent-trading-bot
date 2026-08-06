"""Custom Kedro datasets and catalog helpers."""
from __future__ import annotations

from typing import Any

import pandas as pd
from kedro.io import AbstractDataset
from kedro.io.core import DatasetError


_EMPTY = object()


class OptionalMemoryDataset(AbstractDataset[Any, Any]):
    """In-memory dataset that loads a default when nothing has been saved yet.

    Used for ``trained_models`` so daily-predict jobs (download→…→predict, no
    ``train`` node) can still resolve the predict input. ``predict`` loads
    models from MLflow via :class:`ModelStore`; the catalog value is only a
    DAG placeholder when train is skipped.
    """

    def __init__(self, default: Any = None) -> None:
        self._data: Any = _EMPTY
        # YAML may pass null; treat as empty dict for model-map sentinel.
        self._default: Any = {} if default is None else default

    def _describe(self) -> dict:
        return {"default": self._default, "saved": self._data is not _EMPTY}

    def _load(self) -> Any:
        if self._data is _EMPTY:
            return self._default
        return self._data

    def _save(self, data: Any) -> None:
        self._data = data

    def _exists(self) -> bool:
        return True


class PostgresTableDataSet(AbstractDataset[pd.DataFrame, pd.DataFrame]):
    """Persist a pipeline DataFrame in Postgres ``market_frames``."""

    def __init__(
        self,
        symbol: str,
        kind: str,
        time_column: str = "timestamp",
    ) -> None:
        self._symbol = str(symbol)
        self._kind = str(kind)
        self._time_column = time_column or "timestamp"

    def _describe(self) -> dict:
        return {
            "symbol": self._symbol,
            "kind": self._kind,
            "time_column": self._time_column,
        }

    def _load(self) -> pd.DataFrame:
        from shared.db.frames import load_frame

        return load_frame(self._symbol, self._kind, time_column=self._time_column)

    def _save(self, df: pd.DataFrame) -> None:
        from shared.db.frames import save_frame

        # Upsert only — avoid DELETE+full rewrite on every pipeline step.
        save_frame(self._symbol, self._kind, df, time_column=self._time_column, replace=False)

    def _exists(self) -> bool:
        from shared.db.frames import load_frame

        df = load_frame(self._symbol, self._kind, time_column=self._time_column)
        return not df.empty


# Re-export for clarity in error paths / tests.
__all__ = ["OptionalMemoryDataset", "PostgresTableDataSet", "DatasetError"]