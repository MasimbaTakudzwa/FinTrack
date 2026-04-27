"""add metric + window_days columns to price_alerts

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-26 00:00:00

Phase 2 follow-up — sentiment-aware alerts. Existing ``price_alerts``
rows fire when the latest close crosses a price threshold. The schema
extension lets the same table express "fire when 7-day rolling
sentiment dips below -0.3" by adding two columns:

- ``metric`` (String, default "price"): which signal to threshold.
  "price" preserves legacy semantics; "sentiment" routes through the
  rolling-mean computation in services.alerts.
- ``window_days`` (Integer, nullable): only set for metric="sentiment"
  — the rolling window for the sentiment mean. None for price alerts.

We deliberately stay in one table rather than splitting into a separate
SentimentAlert model because (a) the user's mental model is "alerts on
this asset", (b) the create/list/notify flow is identical, and (c) the
PriceAlert table is small (≤ tens of rows for a single user) so the
extra column cost is trivial.

The ``metric`` column carries a server-side default so existing rows
auto-fill to "price" — no backfill needed.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "price_alerts",
        sa.Column(
            "metric",
            sa.String(32),
            nullable=False,
            server_default="price",
        ),
    )
    op.add_column(
        "price_alerts",
        sa.Column("window_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("price_alerts", "window_days")
    op.drop_column("price_alerts", "metric")
