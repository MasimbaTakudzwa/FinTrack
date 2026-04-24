from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint
from sidecar.main import app


def _seed_price_series(symbol: str, n: int = 5) -> datetime:
    base = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    with session_scope() as s:
        asset = Asset(symbol=symbol, name=symbol, asset_type=AssetType.STOCK)
        s.add(asset)
        s.flush()
        for i in range(n):
            s.add(
                PricePoint(
                    asset_id=asset.id,
                    timestamp=base + timedelta(minutes=5 * i),
                    open=Decimal("100.00"),
                    high=Decimal("101.00"),
                    low=Decimal("99.00"),
                    close=Decimal("100.50") + Decimal(i),
                    volume=1_000 * (i + 1),
                )
            )
    return base


def test_get_prices_unknown_symbol(isolated_db: Path) -> None:
    client = TestClient(app)
    r = client.get("/api/prices/XYZZY/")
    assert r.status_code == 404


def test_get_prices_returns_ordered_series(isolated_db: Path) -> None:
    _seed_price_series("AAPL", n=5)
    client = TestClient(app)
    r = client.get("/api/prices/aapl/")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert body["count"] == 5
    times = [p["timestamp"] for p in body["points"]]
    assert times == sorted(times), "points must be ascending in time"


def test_get_prices_respects_limit(isolated_db: Path) -> None:
    _seed_price_series("AAPL", n=10)
    client = TestClient(app)
    r = client.get("/api/prices/AAPL/?limit=3")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3


def test_get_prices_respects_date_range(isolated_db: Path) -> None:
    base = _seed_price_series("AAPL", n=5)
    client = TestClient(app)
    r = client.get(
        "/api/prices/AAPL/",
        params={
            "from": (base + timedelta(minutes=10)).isoformat(),
            "to": (base + timedelta(minutes=15)).isoformat(),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2


def test_get_prices_filters_by_interval(isolated_db: Path) -> None:
    """With both 5m and 1d bars in the DB for the same asset, the default
    response returns the 5m series; ``?interval=1d`` returns the daily series.
    """
    base = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    with session_scope() as s:
        asset = Asset(symbol="AAPL", name="Apple", asset_type=AssetType.STOCK)
        s.add(asset)
        s.flush()
        for i in range(3):
            s.add(
                PricePoint(
                    asset_id=asset.id,
                    timestamp=base + timedelta(minutes=5 * i),
                    interval="5m",
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100.50"),
                    volume=1_000,
                )
            )
        for i in range(7):
            s.add(
                PricePoint(
                    asset_id=asset.id,
                    timestamp=base + timedelta(days=i),
                    interval="1d",
                    open=Decimal("200"),
                    high=Decimal("205"),
                    low=Decimal("195"),
                    close=Decimal("200.50"),
                    volume=10_000_000,
                )
            )

    client = TestClient(app)

    # Default interval → only 5m bars.
    r_default = client.get("/api/prices/AAPL/")
    assert r_default.status_code == 200
    body_default = r_default.json()
    assert body_default["count"] == 3
    assert all(Decimal(p["close"]) == Decimal("100.50") for p in body_default["points"])

    # Explicit ?interval=1d → only daily bars.
    r_daily = client.get("/api/prices/AAPL/", params={"interval": "1d"})
    assert r_daily.status_code == 200
    body_daily = r_daily.json()
    assert body_daily["count"] == 7
    assert all(Decimal(p["close"]) == Decimal("200.50") for p in body_daily["points"])

    # Unknown interval → empty.
    r_empty = client.get("/api/prices/AAPL/", params={"interval": "1h"})
    assert r_empty.status_code == 200
    assert r_empty.json()["count"] == 0
