"""Custom Kedro datasets and catalog helpers."""
from __future__ import annotations

import pandas as pd
from kedro.io import AbstractDataset


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

        save_frame(self._symbol, self._kind, df, time_column=self._time_column, replace=True)

    def _exists(self) -> bool:
        from shared.db.frames import load_frame

        df = load_frame(self._symbol, self._kind, time_column=self._time_column)
        return not df.empty
