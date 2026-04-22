"""create watchlists + watchlist_items tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-22 18:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_watchlists_name"),
    )
    # Partial unique index: at most one row with is_default=1.
    op.create_index(
        "ux_watchlists_default_one",
        "watchlists",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "watchlist_id",
            sa.Integer(),
            sa.ForeignKey("watchlists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "watchlist_id", "asset_id", name="uq_watchlist_items_list_asset"
        ),
    )
    op.create_index(
        "ix_watchlist_items_watchlist_id",
        "watchlist_items",
        ["watchlist_id"],
    )
    op.create_index(
        "ix_watchlist_items_asset_id",
        "watchlist_items",
        ["asset_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_watchlist_items_asset_id", table_name="watchlist_items"
    )
    op.drop_index(
        "ix_watchlist_items_watchlist_id", table_name="watchlist_items"
    )
    op.drop_table("watchlist_items")
    op.drop_index("ux_watchlists_default_one", table_name="watchlists")
    op.drop_table("watchlists")
