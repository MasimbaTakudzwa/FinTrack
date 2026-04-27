"""create forecasts table

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-24 00:00:00

Phase 2: persists the latest SARIMAX forecast per asset. At most one row per
asset — a retrain upserts over the previous result — so we can serve the
"show me the current forecast" API in a single point-lookup. We intentionally
don't keep a history of previous forecasts here because (a) the training
data itself (`price_points` interval="1d") is the real source of truth and
can always be re-fitted, and (b) keeping a forecast log would complicate the
schema without any user-facing payoff in the Phase 2 cut.

``points_json`` is a JSON-encoded list of ``ForecastPoint`` rows (see
``ml/forecast.py``) — a 14-day horizon is <1 KB, well under SQLite's limits,
and JSON keeps us free to evolve the point shape (e.g. add 99% CIs) without
schema churn.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forecasts",
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
        sa.UniqueConstraint("asset_id", name="uq_forecasts_asset_id"),
    )
    op.create_index("ix_forecasts_asset_id", "forecasts", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_forecasts_asset_id", table_name="forecasts")
    op.drop_table("forecasts")
