from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from sidecar.db.engine import session_scope
from sidecar.db.models import Article, ArticleAsset, Asset

router = APIRouter(prefix="/api/news", tags=["news"])


SentimentBucket = Literal["positive", "neutral", "negative"]

# VADER's conventional thresholds — kept in sync with ``ml.sentiment``.
# Imported here as plain constants so the API doesn't pull in the ml package
# (and transitively vaderSentiment) at module-load time. The numbers are
# stable across VADER versions and small enough that duplicating them is
# preferable to forcing the heavy import.
POSITIVE_THRESHOLD = 0.05
NEGATIVE_THRESHOLD = -0.05


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    headline: str
    source: str
    published_at: datetime
    summary: str | None
    sentiment: float | None
    symbols: list[str]


class ArticleListOut(BaseModel):
    count: int
    articles: list[ArticleOut]


class SentimentSummaryOut(BaseModel):
    """Rolling sentiment summary for a single asset over the last N days.

    `mean` is the average compound score across scored articles in the
    window (None when no scored articles exist). `positive`/`neutral`/
    `negative` count articles by VADER's conventional thresholds.
    `unscored` is the number of articles still awaiting a sentiment score —
    included so the UI can render a "scoring in progress" hint when it's
    non-zero rather than misrepresenting an under-scored period as neutral.
    """

    symbol: str
    days: int
    total: int
    scored: int
    unscored: int
    positive: int
    neutral: int
    negative: int
    mean: float | None


class SentimentTimeseriesPoint(BaseModel):
    """Per-day rollup: average compound + article count for that calendar day."""

    date: str  # YYYY-MM-DD UTC
    mean: float
    count: int


class SentimentTimeseriesOut(BaseModel):
    """Daily-bucketed sentiment series for one asset over the last N days.

    Drives the "sentiment vs price" panel on AssetDetail. The shape is
    intentionally aligned with our other lightweight-charts inputs so the
    UI can pipe ``points`` straight into a LineSeries without further
    transformation. Days with no scored articles are omitted (the chart
    interpolates across gaps rather than rendering a false "0" plateau).
    """

    symbol: str
    days: int
    points: list[SentimentTimeseriesPoint]


class ScoreNowResponse(BaseModel):
    """Result of an on-demand sentiment backfill kicked off from Settings."""

    scored: int


@router.get("/", response_model=ArticleListOut)
def list_news(
    symbol: Annotated[str | None, Query()] = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    sentiment: Annotated[SentimentBucket | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ArticleListOut:
    """List recent articles, newest first.

    Optional filters:
    - ``symbol`` — only articles linked to that asset (404 if unknown).
    - ``from`` / ``to`` — date range on ``published_at``.
    - ``sentiment`` — bucket filter (positive / neutral / negative). Articles
      that haven't been scored yet are excluded from the bucket views (so
      "neutral" doesn't accidentally include backlog rows).
    """
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

        if sentiment == "positive":
            stmt = stmt.where(Article.sentiment >= POSITIVE_THRESHOLD)
        elif sentiment == "negative":
            stmt = stmt.where(Article.sentiment <= NEGATIVE_THRESHOLD)
        elif sentiment == "neutral":
            stmt = stmt.where(
                Article.sentiment.is_not(None),
                Article.sentiment > NEGATIVE_THRESHOLD,
                Article.sentiment < POSITIVE_THRESHOLD,
            )

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
                sentiment=a.sentiment,
                symbols=sorted(symbols_by_article.get(a.id, [])),
            )
            for a in articles
        ]
        return ArticleListOut(count=len(out), articles=out)


@router.get("/sentiment-summary/{symbol}/", response_model=SentimentSummaryOut)
def sentiment_summary(
    symbol: str,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> SentimentSummaryOut:
    """Aggregate VADER scores for one asset over the last ``days`` days.

    Returned counts are computed in SQL (single COUNT/AVG aggregate query)
    rather than fetching rows and bucketing in Python — the news corpus
    grows over time and we don't want this endpoint to scan O(N) rows on
    every render of the AssetDetail sentiment panel.
    """
    symbol_upper = symbol.upper()
    cutoff = datetime.now(UTC) - timedelta(days=days)

    with session_scope() as s:
        asset = s.execute(
            select(Asset).where(Asset.symbol == symbol_upper)
        ).scalar_one_or_none()
        if asset is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown symbol: {symbol_upper}"
            )

        # One aggregate query for the whole rollup. `func.sum` over a
        # boolean-cast-to-int gives us per-bucket counts inline.
        positive_case = func.coalesce(
            func.sum(
                func.iif(Article.sentiment >= POSITIVE_THRESHOLD, 1, 0)
            ),
            0,
        )
        negative_case = func.coalesce(
            func.sum(
                func.iif(Article.sentiment <= NEGATIVE_THRESHOLD, 1, 0)
            ),
            0,
        )
        scored_case = func.coalesce(
            func.sum(func.iif(Article.sentiment.is_not(None), 1, 0)),
            0,
        )

        row = s.execute(
            select(
                func.count(Article.id),
                scored_case,
                positive_case,
                negative_case,
                func.avg(Article.sentiment),
            )
            .join(ArticleAsset, ArticleAsset.article_id == Article.id)
            .where(
                ArticleAsset.asset_id == asset.id,
                Article.published_at >= cutoff,
            )
        ).one()

        total = int(row[0] or 0)
        scored = int(row[1] or 0)
        positive = int(row[2] or 0)
        negative = int(row[3] or 0)
        mean_raw = row[4]
        mean = float(mean_raw) if mean_raw is not None else None
        # Neutrals = scored - positive - negative (saves another SUM/CASE in SQL).
        neutral = max(scored - positive - negative, 0)
        unscored = max(total - scored, 0)

        return SentimentSummaryOut(
            symbol=symbol_upper,
            days=days,
            total=total,
            scored=scored,
            unscored=unscored,
            positive=positive,
            neutral=neutral,
            negative=negative,
            mean=mean,
        )


@router.post("/score-now/", response_model=ScoreNowResponse)
def score_now() -> ScoreNowResponse:
    """Run the VADER backfill synchronously across every unscored article.

    Surfaced in Settings → ML controls so the user can drain the unscored
    queue without waiting for the next scheduler tick (60 minutes by
    default). Lazy-imports the ML jobs module so a sidecar built without
    ``requirements-ml.txt`` returns 503 cleanly instead of importing into
    a blank backend at startup.
    """
    try:
        from ml.jobs import score_articles
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Sentiment backend is not installed in this build "
                f"({exc}); run pip install -r requirements-ml.txt"
            ),
        ) from exc

    scored = score_articles()
    return ScoreNowResponse(scored=scored)


@router.get(
    "/sentiment-timeseries/{symbol}/", response_model=SentimentTimeseriesOut
)
def sentiment_timeseries(
    symbol: str,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> SentimentTimeseriesOut:
    """Daily-bucketed mean sentiment for one asset over the last ``days`` days.

    Computed via a single GROUP BY query so the cost stays O(scored articles
    in window) regardless of the asset's full history size. Days with no
    scored articles are simply absent from the output — the UI uses gap
    interpolation rather than rendering misleading zero values.
    """
    symbol_upper = symbol.upper()
    cutoff = datetime.now(UTC) - timedelta(days=days)

    with session_scope() as s:
        asset = s.execute(
            select(Asset).where(Asset.symbol == symbol_upper)
        ).scalar_one_or_none()
        if asset is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown symbol: {symbol_upper}"
            )

        # SQLite's `date(...)` returns ``YYYY-MM-DD`` directly — no strftime
        # acrobatics needed. Group on that, average sentiment, count
        # non-null rows. ORDER BY date so the UI doesn't have to sort.
        day_col = func.date(Article.published_at).label("day")
        rows = s.execute(
            select(
                day_col,
                func.avg(Article.sentiment).label("mean"),
                func.count(Article.id).label("count"),
            )
            .join(ArticleAsset, ArticleAsset.article_id == Article.id)
            .where(
                ArticleAsset.asset_id == asset.id,
                Article.published_at >= cutoff,
                Article.sentiment.is_not(None),
            )
            .group_by(day_col)
            .order_by(day_col.asc())
        ).all()

        points = [
            SentimentTimeseriesPoint(
                date=str(row[0]),
                mean=float(row[1]),
                count=int(row[2]),
            )
            for row in rows
        ]

        return SentimentTimeseriesOut(
            symbol=symbol_upper, days=days, points=points
        )
