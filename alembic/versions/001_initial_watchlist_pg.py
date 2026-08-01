"""initial watchlist and market_frames

Revision ID: 001
Revises:
Create Date: 2026-08-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchlist_items",
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("exchange", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_predicted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("train_status", sa.String(length=32), nullable=False),
        sa.Column("predict_status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("last_train_job_id", sa.String(length=64), nullable=False),
        sa.Column("last_predict_job_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_table(
        "schedule_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("predict_enabled", sa.Boolean(), nullable=False),
        sa.Column("predict_cron", sa.String(length=64), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "batch_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "symbol_run_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["batch_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "symbol", name="uq_batch_symbol"),
    )
    op.create_table(
        "market_frames",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "kind", "ts", name="uq_market_frame"),
    )
    op.create_index("ix_market_frames_symbol", "market_frames", ["symbol"])
    op.create_index("ix_market_frames_kind", "market_frames", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_market_frames_kind", table_name="market_frames")
    op.drop_index("ix_market_frames_symbol", table_name="market_frames")
    op.drop_table("market_frames")
    op.drop_table("symbol_run_links")
    op.drop_table("batch_runs")
    op.drop_table("schedule_settings")
    op.drop_table("watchlist_items")
