#!/usr/bin/env python3
"""Migrate existing data/{symbol}/*.csv tables into Postgres market_frames.

Also optionally seeds watchlist_items from discovered symbols.

Usage:
  DATABASE_URL=postgresql+psycopg://itb:itb@localhost:5432/itb \\
    python scripts/migrate_csv_to_postgres.py [--data-folder ./data] [--seed-watchlist]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.db import ensure_control_plane_db
from backend.db.models import WatchlistItem
from shared.db import FRAME_KINDS, save_frame
from shared.db.engine import get_session_factory

FILE_TO_KIND = {
    "klines.csv": "klines",
    "data.csv": "data",
    "features.csv": "features",
    "matrix.csv": "matrix",
    "predictions.csv": "predictions",
    "signals.csv": "signals",
}


def _guess_time_column(df: pd.DataFrame) -> str | None:
    for cand in ("timestamp", "time", "date", "datetime"):
        if cand in df.columns:
            return cand
    for col in df.columns:
        if "time" in col.lower() or "date" in col.lower():
            return col
    return None


def migrate_symbol(symbol_dir: Path, seed_watchlist: bool) -> dict:
    symbol = symbol_dir.name
    result = {"symbol": symbol, "files": {}, "errors": []}
    for fname, kind in FILE_TO_KIND.items():
        path = symbol_dir / fname
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path)
            time_column = _guess_time_column(df)
            if not time_column:
                result["errors"].append(f"{fname}: no time column")
                continue
            if time_column != "timestamp":
                df = df.rename(columns={time_column: "timestamp"})
                time_column = "timestamp"
            n = save_frame(symbol, kind, df, time_column="timestamp", replace=True)
            result["files"][kind] = n
            print(f"  {symbol}/{fname} → {kind}: {n} rows")
        except Exception as e:
            result["errors"].append(f"{fname}: {e}")
            print(f"  ERROR {symbol}/{fname}: {e}")

    if seed_watchlist and result["files"]:
        SessionLocal = get_session_factory()
        with SessionLocal() as session:
            item = session.get(WatchlistItem, symbol)
            if item is None:
                session.add(
                    WatchlistItem(
                        symbol=symbol,
                        name="",
                        exchange="",
                        train_status="ready" if "predictions" in result["files"] or "signals" in result["files"] else "untrained",
                    )
                )
                session.commit()
                print(f"  seeded watchlist: {symbol}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-folder", default=str(ROOT / "data"))
    parser.add_argument("--seed-watchlist", action="store_true")
    parser.add_argument("--symbol", default="", help="Migrate only this symbol folder")
    args = parser.parse_args()

    data_folder = Path(args.data_folder)
    if not data_folder.exists():
        print(f"Data folder not found: {data_folder}")
        return 1

    print(f"Initializing DB and migrating from {data_folder} ...")
    ensure_control_plane_db()

    dirs = sorted(
        p for p in data_folder.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in ("mlruns", "MODELS")
    )
    if args.symbol:
        dirs = [data_folder / args.symbol]
        if not dirs[0].is_dir():
            print(f"Symbol folder not found: {dirs[0]}")
            return 1

    summary = []
    for d in dirs:
        # Skip non-stock folders (must look like 6-digit codes ideally, but accept any)
        print(f"Migrating {d.name} ...")
        summary.append(migrate_symbol(d, seed_watchlist=args.seed_watchlist))

    ok = sum(1 for s in summary if s["files"] and not s["errors"])
    print(f"\nDone. Symbols with data: {ok}/{len(summary)}. Kinds: {FRAME_KINDS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
