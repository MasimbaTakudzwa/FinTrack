"""add image_url to articles

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-23 12:00:00

Many Yahoo RSS entries carry a ``<media:content>`` or ``<media:thumbnail>``
element with a preview image. Capturing that URL lets the shell render a
thumbnail per news row — a cheap UX density win without touching the rest of
the article pipeline. Nullable on purpose: feeds without media still ingest.
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
    op.add_column(
        "articles",
        sa.Column("image_url", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("articles", "image_url")
