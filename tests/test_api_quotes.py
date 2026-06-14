from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint
from sidecar.main import app


def _seed(symbol: str = "AAPL") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=f"{symbol} Inc.", asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _add_daily(asset_id: int, ts: datetime, close: str) -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=ts,
                interval="1d",
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=0,
            )
        )


def test_list_quotes(isolated_db: Path) -> None:
    aid = _seed("AAPL")
    _add_daily(aid, datetime(2026, 6, 11, tzinfo=UTC), "100")
    _add_daily(aid, datetime(2026, 6, 12, tzinfo=UTC), "110")
    with TestClient(app) as client:
        resp = client.get("/api/quotes/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        q = body["quotes"][0]
        assert q["symbol"] == "AAPL"
        assert Decimal(q["previous_close"]) == Decimal("100")
        assert q["change_pct"] == 10.0


def test_single_quote(isolated_db: Path) -> None:
    aid = _seed("AAPL")
    _add_daily(aid, datetime(2026, 6, 12, tzinfo=UTC), "110")
    with TestClient(app) as client:
        resp = client.get("/api/quotes/AAPL/")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "AAPL"


def test_single_quote_unknown_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/quotes/NOPE/")
        assert resp.status_code == 404


def test_list_quotes_symbol_filter(isolated_db: Path) -> None:
    _seed("AAPL")
    _seed("MSFT")
    with TestClient(app) as client:
        resp = client.get("/api/quotes/", params={"symbols": "MSFT"})
        assert resp.status_code == 200
        body = resp.json()
        assert [q["symbol"] for q in body["quotes"]] == ["MSFT"]
