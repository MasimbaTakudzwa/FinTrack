"""HTTP tests for ``/api/portfolio/`` endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint
from sidecar.main import app


def _seed_asset(symbol: str = "AAPL", name: str = "Apple Inc.") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=name, asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _add_close(asset_id: int, close: Decimal) -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=datetime.now(UTC),
                interval="1d",
                open=close,
                high=close,
                low=close,
                close=close,
                volume=0,
            )
        )


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------


def test_create_transaction_happy(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "buy",
                "quantity": "10",
                "price_per_unit": "150",
                "transaction_date": "2026-04-01",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["transaction_type"] == "buy"
        assert Decimal(body["quantity"]) == Decimal("10")


def test_create_transaction_unknown_asset_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": 9999,
                "transaction_type": "buy",
                "quantity": "1",
                "price_per_unit": "100",
                "transaction_date": "2026-04-01",
            },
        )
        assert resp.status_code == 404


def test_create_transaction_zero_quantity_400(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "buy",
                "quantity": "0",
                "price_per_unit": "100",
                "transaction_date": "2026-04-01",
            },
        )
        assert resp.status_code == 400


def test_create_transaction_invalid_type_422(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "dividend",  # not in literal
                "quantity": "1",
                "price_per_unit": "100",
                "transaction_date": "2026-04-01",
            },
        )
        # Pydantic literal mismatch → 422.
        assert resp.status_code == 422


def test_list_transactions_newest_first(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "buy",
                "quantity": "5",
                "price_per_unit": "100",
                "transaction_date": "2026-03-01",
            },
        )
        client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "buy",
                "quantity": "5",
                "price_per_unit": "110",
                "transaction_date": "2026-04-01",
            },
        )
        resp = client.get("/api/portfolio/transactions/")
        assert resp.status_code == 200
        dates = [t["transaction_date"] for t in resp.json()["transactions"]]
        assert dates == ["2026-04-01", "2026-03-01"]


def test_list_transactions_filter_by_asset(isolated_db: Path) -> None:
    aapl = _seed_asset("AAPL")
    msft = _seed_asset("MSFT", "Microsoft")
    with TestClient(app) as client:
        for aid, price in [(aapl, "100"), (msft, "200")]:
            client.post(
                "/api/portfolio/transactions/",
                json={
                    "asset_id": aid,
                    "transaction_type": "buy",
                    "quantity": "1",
                    "price_per_unit": price,
                    "transaction_date": "2026-04-01",
                },
            )
        resp = client.get(
            "/api/portfolio/transactions/", params={"asset_id": aapl}
        )
        body = resp.json()
        assert body["count"] == 1
        assert body["transactions"][0]["symbol"] == "AAPL"


def test_delete_transaction_removes_it(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        created = client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "buy",
                "quantity": "1",
                "price_per_unit": "100",
                "transaction_date": "2026-04-01",
            },
        ).json()
        resp = client.delete(f"/api/portfolio/transactions/{created['id']}/")
        assert resp.status_code == 204
        # Subsequent get → 404.
        get_resp = client.get(f"/api/portfolio/transactions/{created['id']}/")
        assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# positions / summary
# ---------------------------------------------------------------------------


def test_positions_unrealized_against_latest_close(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_close(aid, Decimal("120"))
    with TestClient(app) as client:
        client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "buy",
                "quantity": "10",
                "price_per_unit": "100",
                "transaction_date": "2026-04-01",
            },
        )
        resp = client.get("/api/portfolio/positions/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        p = body["positions"][0]
        assert p["symbol"] == "AAPL"
        assert Decimal(p["quantity"]) == Decimal("10")
        assert Decimal(p["unrealized_pl"]) == Decimal("200")
        assert Decimal(p["unrealized_pl_pct"]) == Decimal("20")


def test_positions_empty_portfolio_returns_empty(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/portfolio/positions/")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "positions": []}


def test_summary_rolls_up(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_close(aid, Decimal("110"))
    with TestClient(app) as client:
        client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "buy",
                "quantity": "10",
                "price_per_unit": "100",
                "transaction_date": "2026-04-01",
            },
        )
        resp = client.get("/api/portfolio/summary/")
        body = resp.json()
        assert body["open_positions"] == 1
        assert Decimal(body["total_cost_basis"]) == Decimal("1000")
        assert Decimal(body["total_unrealized_pl"]) == Decimal("100")


def test_summary_empty(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/portfolio/summary/")
        body = resp.json()
        assert body["open_positions"] == 0
        assert Decimal(body["total_cost_basis"]) == Decimal("0")
        assert body["total_unrealized_pl_pct"] is None
