"""Tests for the cross-asset analytics endpoints."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import (
    Asset,
    AssetType,
    PricePoint,
    Watchlist,
    WatchlistItem,
)
from sidecar.main import app


def _seed_asset(symbol: str) -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=symbol, asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _seed_daily_closes(
    asset_id: int, *, days: int = 60, base: float = 100.0, drift: float = 0.5
) -> None:
    today = date.today()
    with session_scope() as s:
        for i in range(days):
            d = today - timedelta(days=days - i)
            ts = datetime(d.year, d.month, d.day, tzinfo=UTC)
            price = base + drift * i
            s.add(
                PricePoint(
                    asset_id=asset_id,
                    timestamp=ts,
                    interval="1d",
                    open=Decimal(str(price)),
                    high=Decimal(str(price * 1.01)),
                    low=Decimal(str(price * 0.99)),
                    close=Decimal(str(price)),
                    volume=0,
                )
            )


# ---------------------------------------------------------------------------
# /api/analytics/correlations/
# ---------------------------------------------------------------------------


def test_correlations_returns_matrix_for_explicit_symbols(isolated_db: Path) -> None:
    aapl = _seed_asset("AAPL")
    msft = _seed_asset("MSFT")
    _seed_daily_closes(aapl, days=60, base=100, drift=0.5)
    _seed_daily_closes(msft, days=60, base=200, drift=1.0)

    with TestClient(app) as client:
        resp = client.get(
            "/api/analytics/correlations/", params={"symbols": "AAPL,MSFT"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbols"] == ["AAPL", "MSFT"]
        assert body["asset_count"] == 2
        # 3 cells: (A,A), (A,M), (M,M).
        assert len(body["cells"]) == 3
        # Diagonals are 1.0.
        diagonals = [c for c in body["cells"] if c["symbol_a"] == c["symbol_b"]]
        assert all(c["coefficient"] == 1.0 for c in diagonals)
        # min_overlap_days surfaced for the UI.
        assert body["min_overlap_days"] >= 1


def test_correlations_handles_whitespace_and_case(isolated_db: Path) -> None:
    _seed_asset("AAPL")
    with TestClient(app) as client:
        resp = client.get(
            "/api/analytics/correlations/", params={"symbols": " aapl, AAPL "}
        )
        assert resp.status_code == 200
        # Dedup + normalize + only known symbols survive.
        assert resp.json()["symbols"] == ["AAPL"]


def test_correlations_unknown_symbols_silently_dropped(isolated_db: Path) -> None:
    _seed_asset("AAPL")
    with TestClient(app) as client:
        resp = client.get(
            "/api/analytics/correlations/", params={"symbols": "AAPL,ZZZZ,XYZW"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbols"] == ["AAPL"]
        assert body["asset_count"] == 1


def test_correlations_empty_symbols_param_returns_422(isolated_db: Path) -> None:
    with TestClient(app) as client:
        # Empty string for symbols.
        resp = client.get("/api/analytics/correlations/", params={"symbols": ""})
        assert resp.status_code == 422


def test_correlations_only_commas_returns_422(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/analytics/correlations/", params={"symbols": ",,,"})
        assert resp.status_code == 422


def test_correlations_lookback_validation(isolated_db: Path) -> None:
    _seed_asset("AAPL")
    with TestClient(app) as client:
        # Below the minimum.
        assert (
            client.get(
                "/api/analytics/correlations/",
                params={"symbols": "AAPL", "lookback_days": 1},
            ).status_code
            == 422
        )
        # Above the maximum.
        assert (
            client.get(
                "/api/analytics/correlations/",
                params={"symbols": "AAPL", "lookback_days": 9999},
            ).status_code
            == 422
        )


# ---------------------------------------------------------------------------
# /api/analytics/correlations/default-watchlist/
# ---------------------------------------------------------------------------


def test_default_watchlist_correlations_pulls_from_default(isolated_db: Path) -> None:
    aapl = _seed_asset("AAPL")
    msft = _seed_asset("MSFT")
    _seed_daily_closes(aapl, days=60)
    _seed_daily_closes(msft, days=60)

    with session_scope() as s:
        wl = Watchlist(name="Default", is_default=True)
        s.add(wl)
        s.flush()
        s.add(WatchlistItem(watchlist_id=wl.id, asset_id=aapl, position=0))
        s.add(WatchlistItem(watchlist_id=wl.id, asset_id=msft, position=1))

    with TestClient(app) as client:
        resp = client.get("/api/analytics/correlations/default-watchlist/")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["symbols"]) == {"AAPL", "MSFT"}


def test_default_watchlist_correlations_404_when_no_default(isolated_db: Path) -> None:
    """Endpoint 404s rather than returning empty so the UI can fall back to
    the explicit-symbols path when the seed hasn't run yet."""
    with TestClient(app) as client:
        resp = client.get("/api/analytics/correlations/default-watchlist/")
        assert resp.status_code == 404


def test_default_watchlist_correlations_lookback_validation(isolated_db: Path) -> None:
    with session_scope() as s:
        s.add(Watchlist(name="Default", is_default=True))

    with TestClient(app) as client:
        assert (
            client.get(
                "/api/analytics/correlations/default-watchlist/",
                params={"lookback_days": 0},
            ).status_code
            == 422
        )
