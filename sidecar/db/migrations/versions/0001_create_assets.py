"""create assets table

Revision ID: 0001
Revises:
Create Date: 2026-04-21 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "asset_type",
            sa.Enum("stock", "etf", "crypto", "commodity", "index", name="asset_type_enum"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assets_symbol", "assets", ["symbol"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_assets_symbol", table_name="assets")
    op.drop_table("assets")
