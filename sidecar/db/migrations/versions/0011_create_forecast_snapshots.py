"""create forecast_snapshots table

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-25 00:00:00

Phase 2 follow-up — accuracy tracking. The existing ``forecasts`` table
keeps the LATEST forecast per asset (single row, fast lookup). This new
``forecast_snapshots`` table is an append-only log of every forecast
ever generated, used to compute rolling accuracy metrics (MAPE / RMSE /
directional) over time and across engines.

Rationale for two tables:
- ``forecasts`` — hot path. The chart overlay fetches the latest forecast
  per asset O(1) via the ``uq_forecasts_asset_id`` constraint. We don't
  want to churn that path with a SELECT-by-MAX(generated_at).
- ``forecast_snapshots`` — analytics path. Every ``save_forecast`` call
  also appends here so accuracy can be measured *after* the forecast
  horizon has elapsed (e.g. "the SARIMAX run we did 14 days ago — how
  did it actually pan out vs. real closes?"). Append-only, no upserts.

Same JSON-encoded points payload as ``forecasts`` so the decode path is
shared. Index on ``(asset_id, generated_at DESC)`` so the accuracy job
can fetch the last N snapshots per asset cheaply.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forecast_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column(
            "horizon_days", sa.Integer(), nullable=False, server_default="14"
        ),
        sa.Column(
            "training_rows", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_close", sa.Numeric(18, 6), nullable=False),
        sa.Column("last_close_date", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("points_json", sa.Text(), nullable=False),
    )
    # Composite index on (asset_id, generated_at DESC) — accuracy job
    # filters by asset and orders by recency, so this index lets SQLite
    # serve "last N snapshots for AAPL" via a single index scan.
    op.create_index(
        "ix_forecast_snapshots_asset_time",
        "forecast_snapshots",
        ["asset_id", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_forecast_snapshots_asset_time", table_name="forecast_snapshots"
    )
    op.drop_table("forecast_snapshots")
