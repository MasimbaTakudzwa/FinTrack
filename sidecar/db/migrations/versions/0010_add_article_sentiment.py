"""add sentiment column to articles

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-25 00:00:00

Phase 2 sentiment analysis: stores the VADER compound score per headline.
Range is [-1.0, +1.0]; null indicates "not yet scored" (used for the
backfill job to find work, and for the API to distinguish a missing score
from a neutral one).

We index on `sentiment` because the News page exposes a positive/neutral/
negative filter — without an index that's a full table scan on every tab
change once the article corpus crosses ~10K rows. The partial index could
skip nulls but SQLite's planner is fine without it for this size class.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("sentiment", sa.Float(), nullable=True),
    )
    op.create_index("ix_articles_sentiment", "articles", ["sentiment"])


def downgrade() -> None:
    op.drop_index("ix_articles_sentiment", table_name="articles")
    op.drop_column("articles", "sentiment")
