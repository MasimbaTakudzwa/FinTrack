"""create price_points table

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-22 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("asset_id", "timestamp", name="uq_price_points_asset_ts"),
    )
    op.create_index(
        "ix_price_points_asset_id", "price_points", ["asset_id"], unique=False
    )
    op.create_index(
        "ix_price_points_asset_ts",
        "price_points",
        ["asset_id", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_price_points_asset_ts", table_name="price_points")
    op.drop_index("ix_price_points_asset_id", table_name="price_points")
    op.drop_table("price_points")
