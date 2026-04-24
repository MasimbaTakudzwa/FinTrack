"""add interval column to price_points

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-24 00:00:00

Phase 2 prerequisite: the forecasting engine needs daily-resolution closes,
but the existing ingest pipeline only populates 5-minute intraday bars. Rather
than split `price_points` into two tables, we add an `interval` column that
distinguishes the granularity of each bar (`"5m"`, `"1d"`, etc.) and widen the
uniqueness invariant from `(asset_id, timestamp)` to
`(asset_id, timestamp, interval)` so a 5-min bar and a 1-day bar at the same
wallclock timestamp (rare, but possible on market open) can both exist.

Existing rows are backfilled with `interval="5m"` — the only interval the
fetcher has ever produced up to this migration.

SQLite can't drop a unique constraint in place, so we do the work inside
`batch_alter_table` which transparently recreates the table with the new
schema + copies rows across.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("price_points") as batch_op:
        # server_default='5m' backfills existing rows in one pass, matching the
        # historical reality that every bar prior to this migration was a
        # yfinance 5-min bar. We keep the server_default on the live column so
        # any legacy INSERT that forgets the field still lands somewhere sane.
        batch_op.add_column(
            sa.Column(
                "interval",
                sa.String(length=16),
                nullable=False,
                server_default="5m",
            )
        )
        batch_op.drop_constraint("uq_price_points_asset_ts", type_="unique")
        batch_op.create_unique_constraint(
            "uq_price_points_asset_ts_interval",
            ["asset_id", "timestamp", "interval"],
        )


def downgrade() -> None:
    with op.batch_alter_table("price_points") as batch_op:
        batch_op.drop_constraint(
            "uq_price_points_asset_ts_interval", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_price_points_asset_ts", ["asset_id", "timestamp"]
        )
        batch_op.drop_column("interval")
