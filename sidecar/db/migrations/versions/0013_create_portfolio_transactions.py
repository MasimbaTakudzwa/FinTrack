"""create portfolio_transactions table

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-26 00:00:00

Phase 2 expansion — portfolio tracking. Positions are derived from an
append-only transaction log rather than stored directly: every buy/sell
the user records is a single row, and the running quantity / average-
cost / realized-P&L state is computed on read. Append-only is cleaner
for accuracy + auditability (you can always trace a position to its
contributing transactions) and a single user has at most a few hundred
transactions, so the per-read computation cost is trivial.

Schema notes:
- ``transaction_type`` is a string ("buy" / "sell") rather than a
  SQLEnum so a third type ("dividend", "split") can be added without
  a migration. Application enum lives in ``sidecar.db.models``.
- ``quantity`` and ``price_per_unit`` are ``Numeric(18, 6)`` to match
  ``PricePoint`` — fractional shares (5+ decimal places) are common
  in modern brokerages.
- ``fee`` defaults to 0 so existing free-broker users don't have to
  fill it in for every row.
- Composite index on ``(asset_id, transaction_date)`` lets the
  position-computation path scan in chronological order without a
  separate sort step.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "portfolio_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transaction_type", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("price_per_unit", sa.Numeric(18, 6), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column(
            "fee", sa.Numeric(18, 6), nullable=False, server_default="0"
        ),
        sa.Column("notes", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_portfolio_transactions_asset_date",
        "portfolio_transactions",
        ["asset_id", "transaction_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_portfolio_transactions_asset_date",
        table_name="portfolio_transactions",
    )
    op.drop_table("portfolio_transactions")
