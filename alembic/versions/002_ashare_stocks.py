"""ashare_stocks reference table for suggest cache

Revision ID: 002
Revises: 001
Create Date: 2026-08-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, Sequence[str], None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ashare_stocks",
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("name_key", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=8), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index("ix_ashare_stocks_name_key", "ashare_stocks", ["name_key"])


def downgrade() -> None:
    op.drop_index("ix_ashare_stocks_name_key", table_name="ashare_stocks")
    op.drop_table("ashare_stocks")
