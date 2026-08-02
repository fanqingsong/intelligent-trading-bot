"""Shared Postgres contract: market frames + engine helpers."""
from __future__ import annotations

from shared.db.engine import get_database_url, get_engine, get_session_factory, init_db
from shared.db.frames import (
    FRAME_KINDS,
    frame_exists,
    frame_row_count,
    load_frame,
    load_frame_tail,
    save_frame,
)
from shared.db.models import AshareStock, Base, MarketFrame

__all__ = [
    "AshareStock",
    "Base",
    "FRAME_KINDS",
    "MarketFrame",
    "frame_exists",
    "frame_row_count",
    "get_database_url",
    "get_engine",
    "get_session_factory",
    "init_db",
    "load_frame",
    "load_frame_tail",
    "save_frame",
]
