from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Article, ArticleAsset, Asset, AssetType
from sidecar.ingestion.rss_fetcher import NewsItem


def _seed_assets() -> None:
    with session_scope() as s:
        s.add(Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK))
        s.add(
            Asset(symbol="MSFT", name="Microsoft Corporation", asset_type=AssetType.STOCK)
        )


def _make_item(symbol: str, idx: int) -> NewsItem:
    return NewsItem(
        url=f"https://example.com/{symbol}/{idx}",
        headline=f"{symbol} headline {idx}",
        source="Yahoo Finance",
        published_at=datetime(2026, 4, 22, 12, idx, tzinfo=UTC),
        summary=f"Summary {idx}",
        symbol=symbol,
    )


def test_ingest_news_inserts_articles_and_links_them(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_assets()
    items = [_make_item("AAPL", i) for i in range(3)] + [
        _make_item("MSFT", i) for i in range(2)
    ]

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_news_for_many", lambda symbols: items)

    linked = jobs.ingest_news()
    assert linked == 5

    with session_scope() as s:
        articles = s.execute(select(Article)).scalars().all()
        assert len(articles) == 5
        associations = s.execute(select(ArticleAsset)).all()
        assert len(associations) == 5


def test_ingest_news_dedups_by_url(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_assets()
    items = [_make_item("AAPL", 0), _make_item("AAPL", 1)]

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_news_for_many", lambda symbols: items)

    # First run inserts 2 articles + 2 associations
    first = jobs.ingest_news()
    assert first == 2

    # Second run sees same URLs — no new articles, no new associations
    second = jobs.ingest_news()
    assert second == 0

    with session_scope() as s:
        articles = s.execute(select(Article)).scalars().all()
        assert len(articles) == 2


def test_ingest_news_links_same_article_to_multiple_assets(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An article appearing in both AAPL and MSFT feeds → one row, two links."""
    _seed_assets()
    shared_url = "https://example.com/shared-story"
    shared = NewsItem(
        url=shared_url,
        headline="Tech giants report earnings",
        source="Yahoo Finance",
        published_at=datetime(2026, 4, 22, 12, 30, tzinfo=UTC),
        summary=None,
        symbol="AAPL",
    )
    shared_msft = NewsItem(
        url=shared_url,
        headline="Tech giants report earnings",
        source="Yahoo Finance",
        published_at=datetime(2026, 4, 22, 12, 30, tzinfo=UTC),
        summary=None,
        symbol="MSFT",
    )

    from sidecar.scheduler import jobs

    monkeypatch.setattr(
        jobs, "fetch_news_for_many", lambda symbols: [shared, shared_msft]
    )

    linked = jobs.ingest_news()
    assert linked == 2

    with session_scope() as s:
        articles = s.execute(select(Article)).scalars().all()
        assert len(articles) == 1, "article should be stored once"
        associations = s.execute(select(ArticleAsset)).all()
        assert len(associations) == 2, "article should be linked to both assets"


def test_ingest_news_with_no_assets_skips(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sidecar.scheduler import jobs

    called = {"count": 0}

    def _fake_fetch(symbols: object) -> list[NewsItem]:
        called["count"] += 1
        return []

    monkeypatch.setattr(jobs, "fetch_news_for_many", _fake_fetch)

    linked = jobs.ingest_news()
    assert linked == 0
    assert called["count"] == 0  # short-circuited before fetching


def test_ingest_news_scores_new_articles_inline(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Newly inserted articles get a sentiment score on the same call."""
    _seed_assets()
    items = [
        NewsItem(
            url="https://t/positive",
            headline="Outstanding results, brilliant performance",
            source="Yahoo Finance",
            published_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
            summary=None,
            symbol="AAPL",
        ),
        NewsItem(
            url="https://t/negative",
            headline="Tragic catastrophic loss reported",
            source="Yahoo Finance",
            published_at=datetime(2026, 4, 22, 12, 1, tzinfo=UTC),
            summary=None,
            symbol="MSFT",
        ),
    ]

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_news_for_many", lambda symbols: items)

    jobs.ingest_news()

    with session_scope() as s:
        rows = s.execute(select(Article)).scalars().all()
        by_url = {a.url: a for a in rows}
        assert by_url["https://t/positive"].sentiment is not None
        assert by_url["https://t/positive"].sentiment > 0
        assert by_url["https://t/negative"].sentiment is not None
        assert by_url["https://t/negative"].sentiment < 0


def test_ingest_news_does_not_rescore_existing(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An article that already has a sentiment score is left untouched."""
    _seed_assets()

    # Pre-seed an article with a known score, then re-ingest the same URL.
    pre_url = "https://t/preseeded"
    with session_scope() as s:
        article = Article(
            url=pre_url,
            headline="Anything goes here",
            source="Yahoo Finance",
            published_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
            sentiment=0.42,
        )
        s.add(article)

    duplicate = NewsItem(
        url=pre_url,
        headline="Different headline (would score differently)",
        source="Yahoo Finance",
        published_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        summary=None,
        symbol="AAPL",
    )

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_news_for_many", lambda symbols: [duplicate])

    jobs.ingest_news()

    with session_scope() as s:
        row = s.execute(
            select(Article).where(Article.url == pre_url)
        ).scalar_one()
        # Score preserved exactly — VADER didn't re-run on this article.
        assert row.sentiment == 0.42
