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
