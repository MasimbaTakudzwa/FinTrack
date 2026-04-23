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


def _make_item(symbol: str, idx: int, image_url: str | None = None) -> NewsItem:
    return NewsItem(
        url=f"https://example.com/{symbol}/{idx}",
        headline=f"{symbol} headline {idx}",
        source="Yahoo Finance",
        published_at=datetime(2026, 4, 22, 12, idx, tzinfo=UTC),
        summary=f"Summary {idx}",
        symbol=symbol,
        image_url=image_url,
    )


def test_ingest_news_persists_image_url(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_assets()
    items = [
        _make_item("AAPL", 0, image_url="https://example.com/pic.jpg"),
        _make_item("AAPL", 1),  # None
    ]

    from sidecar.scheduler import jobs

    monkeypatch.setattr(jobs, "fetch_news_for_many", lambda symbols: items)

    jobs.ingest_news()

    with session_scope() as s:
        rows = {a.url: a for a in s.execute(select(Article)).scalars().all()}
        assert rows["https://example.com/AAPL/0"].image_url == "https://example.com/pic.jpg"
        assert rows["https://example.com/AAPL/1"].image_url is None


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
