"""HTTP tests for ``/api/forecast/`` endpoints.

Mirrors the test_api_alerts.py layout: isolated SQLite, TestClient against
the real FastAPI app, synthetic daily closes seeded into ``price_points``.
Each full-stack retrain test exercises SARIMAX end-to-end (~0.5 s) so we
keep the count modest; finer-grained ML mechanics live in test_ml_jobs.py.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from ml.jobs import train_one
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint
from sidecar.main import app


def _seed_asset_with_daily_closes(
    symbol: str,
    *,
    n_rows: int,
    is_active: bool = True,
    start: date = date(2024, 1, 1),
) -> int:
    """Seed an asset plus ``n_rows`` of synthetic daily OHLCV bars (interval='1d')."""
    with session_scope() as s:
        asset = Asset(
            symbol=symbol,
            name=symbol,
            asset_type=AssetType.STOCK,
            is_active=is_active,
        )
        s.add(asset)
        s.flush()
        bars = []
        for i in range(n_rows):
            price = 100.0 + 0.2 * i + 1.5 * math.sin(i / 5.0)
            ts = datetime(
                start.year, start.month, start.day, tzinfo=UTC
            ) + timedelta(days=i)
            bars.append(
                PricePoint(
                    asset_id=asset.id,
                    timestamp=ts,
                    interval="1d",
                    open=Decimal(str(price)),
                    high=Decimal(str(price * 1.01)),
                    low=Decimal(str(price * 0.99)),
                    close=Decimal(str(price)),
                    volume=1_000_000,
                )
            )
        s.add_all(bars)
        return asset.id


def _resolve_asset_id(symbol: str) -> int:
    with session_scope() as s:
        return int(
            s.execute(select(Asset.id).where(Asset.symbol == symbol)).scalar_one()
        )


# ---------------------------------------------------------------------------
# GET /api/forecast/
# ---------------------------------------------------------------------------


def test_list_availability_empty_when_no_assets(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/forecast/")
        assert resp.status_code == 200
        assert resp.json() == {
            "eligible": [],
            "persisted": [],
            "engines": ["sarimax", "holt_winters"],
        }


def test_list_availability_eligible_without_persisted(isolated_db: Path) -> None:
    """Asset has daily bars but no forecast row yet — it appears in ``eligible``
    only. UI uses this to decide whether to show "Train now" vs. "Show forecast"."""
    _seed_asset_with_daily_closes("AAPL", n_rows=10)

    with TestClient(app) as client:
        resp = client.get("/api/forecast/")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["eligible"] == ["AAPL"]
        assert payload["persisted"] == []


def test_list_availability_persisted_after_training(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    train_one("AAPL")

    with TestClient(app) as client:
        resp = client.get("/api/forecast/")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["eligible"] == ["AAPL"]
        assert payload["persisted"] == ["AAPL"]


def test_list_availability_distinguishes_eligible_from_persisted(
    isolated_db: Path,
) -> None:
    """Two assets, only one trained — both in eligible, one in persisted."""
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    _seed_asset_with_daily_closes("MSFT", n_rows=10)  # has bars but under threshold
    train_one("AAPL")

    with TestClient(app) as client:
        resp = client.get("/api/forecast/")
        assert resp.status_code == 200
        payload = resp.json()
        assert set(payload["eligible"]) == {"AAPL", "MSFT"}
        assert payload["persisted"] == ["AAPL"]


# ---------------------------------------------------------------------------
# GET /api/forecast/{symbol}/
# ---------------------------------------------------------------------------


def test_get_forecast_unknown_symbol_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.get("/api/forecast/NOPE/")
        assert resp.status_code == 404


def test_get_forecast_known_symbol_no_forecast_404(isolated_db: Path) -> None:
    """Symbol is tracked but no row in ``forecasts`` yet → 404.

    UI disambiguates via ``/api/forecast/`` (the symbol will be in ``eligible``
    but not in ``persisted``).
    """
    _seed_asset_with_daily_closes("AAPL", n_rows=80)

    with TestClient(app) as client:
        resp = client.get("/api/forecast/AAPL/")
        assert resp.status_code == 404


def test_get_forecast_happy_path(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    train_one("AAPL")
    asset_id = _resolve_asset_id("AAPL")

    with TestClient(app) as client:
        resp = client.get("/api/forecast/AAPL/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["asset_id"] == asset_id
        assert body["horizon_days"] == 14
        assert body["training_rows"] == 80
        assert len(body["points"]) == 14
        # Every point carries all six fields.
        p = body["points"][0]
        for key in (
            "forecast_date",
            "yhat",
            "lower_80",
            "upper_80",
            "lower_95",
            "upper_95",
        ):
            assert key in p
        # 95% band strictly wraps the 80% band (or equal at the limit).
        assert p["lower_95"] <= p["lower_80"]
        assert p["upper_95"] >= p["upper_80"]


def test_get_forecast_case_insensitive_symbol(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    train_one("AAPL")

    with TestClient(app) as client:
        resp = client.get("/api/forecast/aapl/")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# POST /api/forecast/{symbol}/retrain/
# ---------------------------------------------------------------------------


def test_retrain_happy_path(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)

    with TestClient(app) as client:
        resp = client.post("/api/forecast/AAPL/retrain/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["training_rows"] == 80
        assert len(body["points"]) == 14
        # Persisted — a second GET succeeds.
        resp2 = client.get("/api/forecast/AAPL/")
        assert resp2.status_code == 200


def test_retrain_unknown_symbol_404(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.post("/api/forecast/NOPE/retrain/")
        assert resp.status_code == 404


def test_retrain_insufficient_data_422(isolated_db: Path) -> None:
    """Known asset but fewer than ``MIN_TRAINING_ROWS`` daily closes."""
    _seed_asset_with_daily_closes("AAPL", n_rows=30)

    with TestClient(app) as client:
        resp = client.post("/api/forecast/AAPL/retrain/")
        assert resp.status_code == 422
        assert "closes" in resp.json()["detail"].lower()


def test_retrain_case_insensitive_symbol(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)

    with TestClient(app) as client:
        resp = client.post("/api/forecast/aapl/retrain/")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "AAPL"


def test_retrain_overwrites_previous_forecast(isolated_db: Path) -> None:
    """Re-training replaces the persisted row wholesale (upsert semantics)."""
    _seed_asset_with_daily_closes("AAPL", n_rows=80)

    with TestClient(app) as client:
        resp1 = client.post("/api/forecast/AAPL/retrain/")
        assert resp1.status_code == 200
        gen1 = resp1.json()["generated_at"]

        # Second retrain — ``generated_at`` advances, only one row in ``forecasts``.
        resp2 = client.post("/api/forecast/AAPL/retrain/")
        assert resp2.status_code == 200
        gen2 = resp2.json()["generated_at"]
        assert gen2 >= gen1  # monotonic non-decreasing within a session

        avail = client.get("/api/forecast/").json()
        assert avail["persisted"] == ["AAPL"]  # still exactly one


# ---------------------------------------------------------------------------
# Engine selection on retrain
# ---------------------------------------------------------------------------


def test_retrain_default_engine_is_sarimax(isolated_db: Path) -> None:
    """No ``engine=`` param → SARIMAX (the historical default)."""
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    with TestClient(app) as client:
        resp = client.post("/api/forecast/AAPL/retrain/")
        assert resp.status_code == 200
        assert "SARIMAX" in resp.json()["model"]


def test_retrain_with_explicit_engine_uses_holt_winters(
    isolated_db: Path,
) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    with TestClient(app) as client:
        resp = client.post(
            "/api/forecast/AAPL/retrain/", params={"engine": "holt_winters"}
        )
        assert resp.status_code == 200
        assert "Holt-Winters" in resp.json()["model"]


def test_retrain_unknown_engine_returns_422(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    with TestClient(app) as client:
        resp = client.post(
            "/api/forecast/AAPL/retrain/", params={"engine": "prophet"}
        )
        assert resp.status_code == 422
        assert "prophet" in resp.json()["detail"].lower()


def test_availability_returns_engine_list(isolated_db: Path) -> None:
    """The availability endpoint exposes the canonical engine literals so the
    UI selector doesn't have to hard-code them."""
    with TestClient(app) as client:
        resp = client.get("/api/forecast/")
        body = resp.json()
        assert body["engines"] == ["sarimax", "holt_winters"]


# ---------------------------------------------------------------------------
# Bulk admin endpoints (Settings → ML controls)
# ---------------------------------------------------------------------------


def test_retrain_all_returns_counts(isolated_db: Path) -> None:
    """Two eligible assets, one with enough rows to actually fit, one not."""
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    _seed_asset_with_daily_closes("MSFT", n_rows=10)

    with TestClient(app) as client:
        resp = client.post("/api/forecast/retrain-all/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["requested"] == 2
        assert body["trained"] == 1
        assert body["skipped"] == 1
        assert body["engine"] in {"sarimax", "holt_winters"}


def test_retrain_all_with_explicit_engine(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    with TestClient(app) as client:
        resp = client.post(
            "/api/forecast/retrain-all/", params={"engine": "holt_winters"}
        )
        assert resp.status_code == 200
        assert resp.json()["engine"] == "holt_winters"

        # The persisted forecast is from the requested engine.
        forecast = client.get("/api/forecast/AAPL/").json()
        assert "Holt-Winters" in forecast["model"]


def test_retrain_all_no_eligible_assets_returns_zeros(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.post("/api/forecast/retrain-all/")
        body = resp.json()
        assert body["requested"] == 0
        assert body["trained"] == 0
        assert body["skipped"] == 0


def test_clear_all_forecasts_wipes_persisted(isolated_db: Path) -> None:
    _seed_asset_with_daily_closes("AAPL", n_rows=80)
    with TestClient(app) as client:
        # Seed one persisted forecast first.
        client.post("/api/forecast/AAPL/retrain/")
        avail_before = client.get("/api/forecast/").json()
        assert avail_before["persisted"] == ["AAPL"]

        resp = client.delete("/api/forecast/")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

        avail_after = client.get("/api/forecast/").json()
        assert avail_after["persisted"] == []
        # Eligibility is unchanged — clearing forecasts doesn't touch
        # price_points.
        assert avail_after["eligible"] == ["AAPL"]


def test_clear_all_forecasts_with_no_rows_returns_zero(isolated_db: Path) -> None:
    with TestClient(app) as client:
        resp = client.delete("/api/forecast/")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0
