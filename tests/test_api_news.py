from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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


# ---------------------------------------------------------------------------
# Sentiment surfaces
# ---------------------------------------------------------------------------


def _seed_with_sentiment() -> None:
    """Seed AAPL with three articles spanning the sentiment buckets."""
    with session_scope() as s:
        aapl = Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK)
        s.add(aapl)
        s.flush()
        base = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
        rows = [
            ("https://t/p", "Excellent results", base, 0.7),
            ("https://t/n", "Tragic failure", base + timedelta(hours=1), -0.6),
            ("https://t/m", "Wire report published", base + timedelta(hours=2), 0.0),
            ("https://t/u", "Unscored backlog", base + timedelta(hours=3), None),
        ]
        for url, headline, ts, sentiment in rows:
            article = Article(
                url=url,
                headline=headline,
                source="Yahoo Finance",
                published_at=ts,
                sentiment=sentiment,
            )
            s.add(article)
            s.flush()
            s.add(ArticleAsset(article_id=article.id, asset_id=aapl.id))


def test_list_news_returns_sentiment_field(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        resp = client.get("/api/news/")
        assert resp.status_code == 200
        data = resp.json()
        by_url = {a["url"]: a for a in data["articles"]}
        assert by_url["https://t/p"]["sentiment"] == pytest.approx(0.7)
        assert by_url["https://t/n"]["sentiment"] == pytest.approx(-0.6)
        assert by_url["https://t/m"]["sentiment"] == 0.0
        assert by_url["https://t/u"]["sentiment"] is None


def test_list_news_filter_positive(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"sentiment": "positive"})
        assert resp.status_code == 200
        urls = {a["url"] for a in resp.json()["articles"]}
        assert urls == {"https://t/p"}


def test_list_news_filter_negative(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"sentiment": "negative"})
        urls = {a["url"] for a in resp.json()["articles"]}
        assert urls == {"https://t/n"}


def test_list_news_filter_neutral_excludes_unscored(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"sentiment": "neutral"})
        urls = {a["url"] for a in resp.json()["articles"]}
        # Only the explicitly-neutral row; unscored is excluded by design.
        assert urls == {"https://t/m"}


def test_list_news_invalid_sentiment_value_422(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        resp = client.get("/api/news/", params={"sentiment": "ecstatic"})
        assert resp.status_code == 422


def test_sentiment_summary_returns_aggregates(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        resp = client.get("/api/news/sentiment-summary/AAPL/", params={"days": 365})
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["days"] == 365
        assert body["total"] == 4
        assert body["scored"] == 3
        assert body["unscored"] == 1
        assert body["positive"] == 1
        assert body["negative"] == 1
        assert body["neutral"] == 1
        # mean over scored: (0.7 + -0.6 + 0.0) / 3 ≈ 0.0333
        assert body["mean"] is not None
        assert body["mean"] == pytest.approx(0.0333, abs=1e-3)


def test_sentiment_summary_unknown_symbol_404(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        resp = client.get("/api/news/sentiment-summary/NOPE/")
        assert resp.status_code == 404


def test_sentiment_summary_case_insensitive(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        resp = client.get("/api/news/sentiment-summary/aapl/")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "AAPL"


def test_sentiment_summary_empty_window_returns_zeros(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        # Articles seeded at 2026-04-22 — a 1-day window from "now" excludes them.
        resp = client.get("/api/news/sentiment-summary/AAPL/", params={"days": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["scored"] == 0
        assert body["mean"] is None


def test_sentiment_summary_days_validation(isolated_db: Path) -> None:
    _seed_with_sentiment()
    with TestClient(app) as client:
        assert client.get(
            "/api/news/sentiment-summary/AAPL/", params={"days": 0}
        ).status_code == 422
        assert client.get(
            "/api/news/sentiment-summary/AAPL/", params={"days": 999}
        ).status_code == 422


def _seed_for_timeseries() -> None:
    """Seed AAPL with five scored articles over three calendar days, plus
    one unscored row that must NOT contribute to the daily averages."""
    with session_scope() as s:
        aapl = Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK)
        s.add(aapl)
        s.flush()
        # Day 1: two articles, mean = (0.5 + -0.1) / 2 = 0.2
        # Day 2: one article, mean = 0.4
        # Day 3: two articles, mean = (-0.3 + -0.7) / 2 = -0.5
        # Plus an unscored article on Day 2 that should be ignored entirely.
        rows = [
            ("https://t/d1a", datetime(2026, 4, 20, 9, 0, tzinfo=UTC), 0.5),
            ("https://t/d1b", datetime(2026, 4, 20, 15, 0, tzinfo=UTC), -0.1),
            ("https://t/d2a", datetime(2026, 4, 21, 12, 0, tzinfo=UTC), 0.4),
            ("https://t/d2u", datetime(2026, 4, 21, 14, 0, tzinfo=UTC), None),
            ("https://t/d3a", datetime(2026, 4, 22, 10, 0, tzinfo=UTC), -0.3),
            ("https://t/d3b", datetime(2026, 4, 22, 16, 0, tzinfo=UTC), -0.7),
        ]
        for url, ts, sentiment in rows:
            article = Article(
                url=url,
                headline="x",
                source="Yahoo Finance",
                published_at=ts,
                sentiment=sentiment,
            )
            s.add(article)
            s.flush()
            s.add(ArticleAsset(article_id=article.id, asset_id=aapl.id))


def test_sentiment_timeseries_aggregates_per_day(isolated_db: Path) -> None:
    _seed_for_timeseries()
    with TestClient(app) as client:
        resp = client.get(
            "/api/news/sentiment-timeseries/AAPL/", params={"days": 365}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["days"] == 365
        # Three days with scored articles (the 4th had only an unscored row,
        # so it's excluded entirely from the timeseries).
        assert len(body["points"]) == 3

        # Ordered ascending by date
        dates = [p["date"] for p in body["points"]]
        assert dates == sorted(dates)
        # Spot-check the means we constructed
        by_date = {p["date"]: p for p in body["points"]}
        assert by_date["2026-04-20"]["count"] == 2
        assert by_date["2026-04-20"]["mean"] == pytest.approx(0.2, abs=1e-6)
        assert by_date["2026-04-21"]["count"] == 1
        assert by_date["2026-04-21"]["mean"] == pytest.approx(0.4, abs=1e-6)
        assert by_date["2026-04-22"]["count"] == 2
        assert by_date["2026-04-22"]["mean"] == pytest.approx(-0.5, abs=1e-6)


def test_sentiment_timeseries_empty_returns_no_points(isolated_db: Path) -> None:
    with session_scope() as s:
        s.add(Asset(symbol="AAPL", name="Apple", asset_type=AssetType.STOCK))

    with TestClient(app) as client:
        resp = client.get("/api/news/sentiment-timeseries/AAPL/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["points"] == []


def test_sentiment_timeseries_unknown_symbol_404(isolated_db: Path) -> None:
    _seed_for_timeseries()
    with TestClient(app) as client:
        resp = client.get("/api/news/sentiment-timeseries/NOPE/")
        assert resp.status_code == 404


def test_sentiment_timeseries_excludes_unscored_articles(isolated_db: Path) -> None:
    """An unscored article on a day with no other articles → that day is absent."""
    with session_scope() as s:
        aapl = Asset(symbol="AAPL", name="Apple Inc.", asset_type=AssetType.STOCK)
        s.add(aapl)
        s.flush()
        article = Article(
            url="https://t/u",
            headline="unscored",
            source="Yahoo Finance",
            published_at=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
            sentiment=None,
        )
        s.add(article)
        s.flush()
        s.add(ArticleAsset(article_id=article.id, asset_id=aapl.id))

    with TestClient(app) as client:
        resp = client.get(
            "/api/news/sentiment-timeseries/AAPL/", params={"days": 365}
        )
        assert resp.status_code == 200
        assert resp.json()["points"] == []


def test_sentiment_timeseries_days_validation(isolated_db: Path) -> None:
    _seed_for_timeseries()
    with TestClient(app) as client:
        assert client.get(
            "/api/news/sentiment-timeseries/AAPL/", params={"days": 0}
        ).status_code == 422
        assert client.get(
            "/api/news/sentiment-timeseries/AAPL/", params={"days": 999}
        ).status_code == 422
