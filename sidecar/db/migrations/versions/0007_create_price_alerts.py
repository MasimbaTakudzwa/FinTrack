"""create price_alerts table

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-22 19:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("threshold", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("above", "below", name="alert_direction_enum"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_price_alerts_asset_id", "price_alerts", ["asset_id"]
    )
    # Hot index for the scheduler job: active, not-yet-triggered rows.
    op.create_index(
        "ix_price_alerts_active_pending",
        "price_alerts",
        ["is_active", "triggered_at"],
    )
    # Hot index for the shell poller: triggered but not-yet-notified rows.
    op.create_index(
        "ix_price_alerts_notify_pending",
        "price_alerts",
        ["triggered_at", "notified_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_price_alerts_notify_pending", table_name="price_alerts")
    op.drop_index("ix_price_alerts_active_pending", table_name="price_alerts")
    op.drop_index("ix_price_alerts_asset_id", table_name="price_alerts")
    op.drop_table("price_alerts")
