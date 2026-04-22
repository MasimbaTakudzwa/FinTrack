from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from sidecar.db.engine import session_scope
from sidecar.db.models import Article, ArticleAsset, Asset, AssetType
from sidecar.main import app


def _seed() -> tuple[int, int]:
    """Seed AAPL and MSFT, plus 3 articles linked variously. Returns their ids."""
    with session_scope() as s:
        aapl = Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK)
        msft = Asset(
            symbol="MSFT", name="Microsoft Corporation", asset_type=AssetType.STOCK
        )
        s.add_all([aapl, msft])
        s.flush()

        base = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
        a1 = Article(
            url="https://example.com/a1",
            headline="Apple story",
            source="Yahoo Finance",
            published_at=base,
            summary="summary 1",
        )
        a2 = Article(
            url="https://example.com/a2",
            headline="Microsoft story",
            source="Yahoo Finance",
            published_at=base + timedelta(hours=1),
            summary=None,
        )
        a3 = Article(
            url="https://example.com/shared",
            headline="Both giants report earnings",
            source="Yahoo Finance",
            published_at=base + timedelta(hours=2),
            summary="shared",
        )
        s.add_all([a1, a2, a3])
        s.flush()

        s.add(ArticleAsset(article_id=a1.id, asset_id=aapl.id))
        s.add(ArticleAsset(article_id=a2.id, asset_id=msft.id))
        s.add(ArticleAsset(article_id=a3.id, asset_id=aapl.id))
        s.add(ArticleAsset(article_id=a3.id, asset_id=msft.id))

        return aapl.id, msft.id


def test_list_news_returns_all_articles_newest_first(isolated_db: Path) -> None:
    _seed()
    with TestClient(app) as client:
        resp = client.get("/api/news/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert len(data["articles"]) == 3
        # newest first
        pub = [a["published_at"] for a in data["articles"]]
        assert pub == sorted(pub, reverse=True)


def test_list_news_hydrates_symbols(isolated_db: Path) -> None:
    _seed()
    with TestClient(app) as client:
        resp = client.get("/api/news/")
        data = resp.json()
        by_url = {a["url"]: a for a in data["articles"]}
        assert by_url["https://example.com/a1"]["symbols"] == ["AAPL"]
        assert by_url["https://example.com/a2"]["symbols"] == ["MSFT"]
        assert by_url["https://example.com/shared"]["symbols"] == ["AAPL", "MSFT"]


def test_list_news_filter_by_symbol(isolated_db: Path) -> None:
    _seed()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"symbol": "AAPL"})
        assert resp.status_code == 200
        data = resp.json()
        urls = {a["url"] for a in data["articles"]}
        assert urls == {"https://example.com/a1", "https://example.com/shared"}


def test_list_news_filter_by_symbol_is_case_insensitive(isolated_db: Path) -> None:
    _seed()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"symbol": "aapl"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2


def test_list_news_unknown_symbol_404(isolated_db: Path) -> None:
    _seed()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"symbol": "NOPE"})
        assert resp.status_code == 404


def test_list_news_filter_by_date_range(isolated_db: Path) -> None:
    _seed()
    base = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    with TestClient(app) as client:
        resp = client.get(
            "/api/news/",
            params={
                "from": (base + timedelta(minutes=30)).isoformat(),
                "to": (base + timedelta(hours=1, minutes=30)).isoformat(),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Only a2 (published base+1h) falls in the window
        urls = {a["url"] for a in data["articles"]}
        assert urls == {"https://example.com/a2"}


def test_list_news_limit_clamped(isolated_db: Path) -> None:
    _seed()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"limit": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1


def test_list_news_empty_db_returns_zero(isolated_db: Path) -> None:
    # isolated_db is empty — no seed() call
    with TestClient(app) as client:
        resp = client.get("/api/news/")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"count": 0, "articles": []}


def test_list_news_limit_validation(isolated_db: Path) -> None:
    _seed()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"limit": 0})
        assert resp.status_code == 422

        resp = client.get("/api/news/", params={"limit": 10000})
        assert resp.status_code == 422

    # Reach in to silence any "unused" noise from Article/ArticleAsset imports
    # if the test above happens to short-circuit in future edits.
    with session_scope() as s:
        assert s.execute(select(Article)).scalar() is not None
        assert s.execute(select(ArticleAsset)).first() is not None
