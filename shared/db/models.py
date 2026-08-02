"""Shared ORM: market frames (pipeline data contract)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MarketFrame(Base):
    """One timestamped row of a pipeline table, payload as JSONB."""

    __tablename__ = "market_frames"
    __table_args__ = (
        UniqueConstraint("symbol", "kind", "ts", name="uq_market_frame"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class AshareStock(Base):
    """Cached A-share code/name row for Watchlist suggest (沪深)."""

    __tablename__ = "ashare_stocks"

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    name_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
