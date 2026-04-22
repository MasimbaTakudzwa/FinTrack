"""create macro_indicators and macro_data_points tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-22 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "macro_indicators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("units", sa.String(128), nullable=True),
        sa.Column("frequency", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_macro_indicators_series_id",
        "macro_indicators",
        ["series_id"],
        unique=True,
    )

    op.create_table(
        "macro_data_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "indicator_id",
            sa.Integer(),
            sa.ForeignKey("macro_indicators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=False),
        sa.UniqueConstraint(
            "indicator_id", "date", name="uq_macro_data_points_ind_date"
        ),
    )
    op.create_index(
        "ix_macro_data_points_indicator_id",
        "macro_data_points",
        ["indicator_id"],
        unique=False,
    )
    op.create_index(
        "ix_macro_data_points_ind_date",
        "macro_data_points",
        ["indicator_id", "date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_macro_data_points_ind_date", table_name="macro_data_points")
    op.drop_index("ix_macro_data_points_indicator_id", table_name="macro_data_points")
    op.drop_table("macro_data_points")
    op.drop_index("ix_macro_indicators_series_id", table_name="macro_indicators")
    op.drop_table("macro_indicators")
