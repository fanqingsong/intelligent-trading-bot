"""Control-plane ORM models (watchlist / schedule / batch runs)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_predicted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    train_status: Mapped[str] = mapped_column(String(32), default="untrained", nullable=False)
    predict_status: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_train_job_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_predict_job_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class ScheduleSettings(Base):
    __tablename__ = "schedule_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    predict_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    predict_cron: Mapped[str] = mapped_column(String(64), default="0 16 * * 1-5", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class BatchRun(Base):
    __tablename__ = "batch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # train | predict
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    links: Mapped[list[SymbolRunLink]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class SymbolRunLink(Base):
    __tablename__ = "symbol_run_links"
    __table_args__ = (UniqueConstraint("batch_id", "symbol", name="uq_batch_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batch_runs.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    batch: Mapped[BatchRun] = relationship(back_populates="links")
