"""create articles + article_assets tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-22 16:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("headline", sa.String(512), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("url", name="uq_articles_url"),
    )
    op.create_index("ix_articles_url", "articles", ["url"], unique=True)
    op.create_index("ix_articles_published_at", "articles", ["published_at"])

    op.create_table(
        "article_assets",
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_article_assets_asset_id", "article_assets", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_article_assets_asset_id", table_name="article_assets")
    op.drop_table("article_assets")
    op.drop_index("ix_articles_published_at", table_name="articles")
    op.drop_index("ix_articles_url", table_name="articles")
    op.drop_table("articles")
