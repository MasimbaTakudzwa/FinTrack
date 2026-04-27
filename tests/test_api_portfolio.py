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


# ---------------------------------------------------------------------------
# performance endpoint
# ---------------------------------------------------------------------------


def test_performance_empty_returns_no_points(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/portfolio/performance/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["points"] == []
        assert body["lookback_days"] == 90


def test_performance_with_position_returns_points(isolated_db: Path) -> None:
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
        resp = client.get("/api/portfolio/performance/")
        body = resp.json()
        # At least one point with value > 0.
        assert len(body["points"]) >= 1
        assert any(Decimal(p["value"]) > 0 for p in body["points"])


def test_performance_lookback_validation(isolated_db: Path) -> None:
    with TestClient(app) as client:
        assert (
            client.get(
                "/api/portfolio/performance/", params={"lookback_days": 0}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/portfolio/performance/", params={"lookback_days": 999999}
            ).status_code
            == 422
        )


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def test_export_csv_empty_returns_header_only(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/portfolio/transactions/export.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        text = resp.text
        # Header row only.
        assert text.startswith("transaction_date,symbol,transaction_type")
        assert text.strip().count("\n") == 0  # only one line — the header


def test_export_csv_includes_transactions(isolated_db: Path) -> None:
    aid = _seed_asset()
    with TestClient(app) as client:
        client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "buy",
                "quantity": "10",
                "price_per_unit": "150",
                "transaction_date": "2026-04-01",
                "fee": "1.5",
                "notes": "broker test",
            },
        )
        client.post(
            "/api/portfolio/transactions/",
            json={
                "asset_id": aid,
                "transaction_type": "sell",
                "quantity": "5",
                "price_per_unit": "160",
                "transaction_date": "2026-04-15",
            },
        )

        resp = client.get("/api/portfolio/transactions/export.csv")
        assert resp.status_code == 200
        rows = resp.text.strip().splitlines()
        assert rows[0] == (
            "transaction_date,symbol,transaction_type,quantity,"
            "price_per_unit,fee,notes"
        )
        # 1 header + 2 rows
        assert len(rows) == 3
        # Newest-first order from list_transactions matches the CSV row order.
        assert "2026-04-15" in rows[1]
        assert "sell" in rows[1]
        assert "2026-04-01" in rows[2]
        assert "broker test" in rows[2]


def test_export_csv_filename_has_today(isolated_db: Path) -> None:
    """Content-Disposition includes a YYYYMMDD-stamped filename so users
    can download multiple snapshots without overwriting."""
    with TestClient(app) as client:
        resp = client.get("/api/portfolio/transactions/export.csv")
        # ``today`` here is the test runner's local date — just check the
        # general shape.
        cd = resp.headers["content-disposition"]
        assert "fintrack-transactions-" in cd
        assert ".csv" in cd


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------


def test_import_csv_empty_body_no_op(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/import", json={"csv": ""}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"inserted": 0, "skipped": 0, "errors": []}


def test_import_csv_round_trip(isolated_db: Path) -> None:
    """Round-trip: export a populated portfolio, wipe it, import the
    CSV back, end up with the same transactions."""
    aid = _seed_asset()
    with TestClient(app) as client:
        # Seed two transactions.
        for date_str, qty, price in [
            ("2026-04-01", "10", "150"),
            ("2026-04-15", "5", "160"),
        ]:
            client.post(
                "/api/portfolio/transactions/",
                json={
                    "asset_id": aid,
                    "transaction_type": "buy",
                    "quantity": qty,
                    "price_per_unit": price,
                    "transaction_date": date_str,
                },
            )

        export = client.get("/api/portfolio/transactions/export.csv").text

        # Wipe.
        for t in client.get("/api/portfolio/transactions/").json()[
            "transactions"
        ]:
            client.delete(f"/api/portfolio/transactions/{t['id']}/")
        assert (
            client.get("/api/portfolio/transactions/").json()["count"] == 0
        )

        # Import the export.
        resp = client.post(
            "/api/portfolio/transactions/import", json={"csv": export}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["inserted"] == 2
        assert body["skipped"] == 0
        assert body["errors"] == []

        listed = client.get("/api/portfolio/transactions/").json()
        assert listed["count"] == 2


def test_import_csv_missing_required_header_returns_error(
    isolated_db: Path,
) -> None:
    csv = "transaction_date,symbol\n2026-04-01,AAPL\n"
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/import", json={"csv": csv}
        )
        body = resp.json()
        assert body["inserted"] == 0
        assert body["errors"][0]["row"] == 1
        assert "missing required columns" in body["errors"][0]["message"]


def test_import_csv_unknown_symbol_skipped(isolated_db: Path) -> None:
    csv = (
        "transaction_date,symbol,transaction_type,quantity,price_per_unit\n"
        "2026-04-01,GHOST,buy,1,100\n"
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/import", json={"csv": csv}
        )
        body = resp.json()
        assert body["inserted"] == 0
        assert body["skipped"] == 1
        assert "GHOST" in body["errors"][0]["message"]


def test_import_csv_partial_success(isolated_db: Path) -> None:
    """One valid row + one bad row → 1 inserted, 1 skipped, 1 error."""
    aid = _seed_asset()
    csv = (
        "transaction_date,symbol,transaction_type,quantity,price_per_unit\n"
        "2026-04-01,AAPL,buy,10,150\n"
        "2026-04-02,AAPL,buy,not-a-number,160\n"
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/import", json={"csv": csv}
        )
        body = resp.json()
        assert body["inserted"] == 1
        assert body["skipped"] == 1
        assert body["errors"][0]["row"] == 3  # 1 header + 2 = third row
        # Verify the valid row actually landed.
        listed = client.get("/api/portfolio/transactions/").json()
        assert listed["count"] == 1
        assert listed["transactions"][0]["asset_id"] == aid


def test_import_csv_extra_columns_silently_ignored(isolated_db: Path) -> None:
    """Exports from other tools often include extra columns (id,
    broker, …) — we should accept them rather than failing."""
    _seed_asset()
    csv = (
        "id,transaction_date,symbol,transaction_type,quantity,"
        "price_per_unit,broker\n"
        "999,2026-04-01,AAPL,buy,10,150,Robinhood\n"
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/import", json={"csv": csv}
        )
        body = resp.json()
        assert body["inserted"] == 1
        assert body["errors"] == []


def test_import_csv_blank_lines_skipped(isolated_db: Path) -> None:
    """Blank rows in the middle of the CSV (typical when copy-pasting
    from spreadsheet apps) shouldn't count as errors."""
    _seed_asset()
    csv = (
        "transaction_date,symbol,transaction_type,quantity,price_per_unit\n"
        "2026-04-01,AAPL,buy,10,150\n"
        ",,,,\n"
        "2026-04-02,AAPL,buy,5,160\n"
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/import", json={"csv": csv}
        )
        body = resp.json()
        assert body["inserted"] == 2
        assert body["skipped"] == 0


def test_import_csv_preserves_fee_and_notes(isolated_db: Path) -> None:
    _seed_asset()
    csv = (
        "transaction_date,symbol,transaction_type,quantity,"
        "price_per_unit,fee,notes\n"
        "2026-04-01,AAPL,buy,10,150,1.5,my note\n"
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/portfolio/transactions/import", json={"csv": csv}
        )
        assert resp.json()["inserted"] == 1
        listed = client.get("/api/portfolio/transactions/").json()
        t = listed["transactions"][0]
        assert Decimal(t["fee"]) == Decimal("1.5")
        assert t["notes"] == "my note"
