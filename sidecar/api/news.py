from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Article, ArticleAsset, Asset

router = APIRouter(prefix="/api/news", tags=["news"])


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    headline: str
    source: str
    published_at: datetime
    summary: str | None
    image_url: str | None
    symbols: list[str]


class ArticleListOut(BaseModel):
    count: int
    articles: list[ArticleOut]


@router.get("/", response_model=ArticleListOut)
def list_news(
    symbol: Annotated[str | None, Query()] = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ArticleListOut:
    with session_scope() as s:
        stmt = select(Article)
        if symbol is not None:
            symbol_upper = symbol.upper()
            asset = s.execute(
                select(Asset).where(Asset.symbol == symbol_upper)
            ).scalar_one_or_none()
            if asset is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown symbol: {symbol_upper}"
                )
            stmt = stmt.join(
                ArticleAsset, ArticleAsset.article_id == Article.id
            ).where(ArticleAsset.asset_id == asset.id)

        if start is not None:
            stmt = stmt.where(Article.published_at >= start)
        if end is not None:
            stmt = stmt.where(Article.published_at <= end)
        stmt = stmt.order_by(Article.published_at.desc()).limit(limit)

        articles = list(s.execute(stmt).scalars().unique().all())
        if not articles:
            return ArticleListOut(count=0, articles=[])

        # Hydrate associated symbols in one query (article_id -> [symbol...])
        ids = [a.id for a in articles]
        assoc = s.execute(
            select(ArticleAsset.article_id, Asset.symbol)
            .join(Asset, Asset.id == ArticleAsset.asset_id)
            .where(ArticleAsset.article_id.in_(ids))
        ).all()
        symbols_by_article: dict[int, list[str]] = {}
        for article_id, sym in assoc:
            symbols_by_article.setdefault(article_id, []).append(sym)

        out = [
            ArticleOut(
                id=a.id,
                url=a.url,
                headline=a.headline,
                source=a.source,
                published_at=a.published_at,
                summary=a.summary,
                image_url=a.image_url,
                symbols=sorted(symbols_by_article.get(a.id, [])),
            )
            for a in articles
        ]
        return ArticleListOut(count=len(out), articles=out)
