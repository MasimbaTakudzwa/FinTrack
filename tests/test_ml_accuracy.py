"""Tests for the forecast-accuracy metric layer.

Pure-compute paths (`_compute_metrics`, `_evaluable_pairs`) get unit tests
that build small triple-lists by hand. The DB-aware `compute_accuracy`
gets integration tests that seed snapshots + price points + then assert
the numbers come out right end-to-end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ml.accuracy import (
    EngineAccuracy,
    _compute_metrics,
    _evaluable_pairs,
    compute_accuracy,
)
from ml.forecast import ForecastPoint, ForecastResult
from ml.persistence import save_forecast
from sidecar.db.engine import session_scope
from sidecar.db.models import Asset, AssetType, PricePoint

# ---------------------------------------------------------------------------
# Pure-compute helpers
# ---------------------------------------------------------------------------


def test_compute_metrics_perfect_forecast() -> None:
    """Predicted == actual for every pair → MAPE 0, RMSE 0, directional 1.0
    (when there are also direction-evaluable pairs)."""
    pairs = [
        (110.0, 110.0, 100.0),  # predicted 110, actual 110, last 100 → up + up
        (105.0, 105.0, 100.0),  # up + up
        (95.0, 95.0, 100.0),  # down + down
    ]
    mape, rmse, directional = _compute_metrics(pairs)
    assert mape == pytest.approx(0.0, abs=1e-9)
    assert rmse == pytest.approx(0.0, abs=1e-9)
    assert directional == pytest.approx(1.0, abs=1e-9)


def test_compute_metrics_all_wrong_direction() -> None:
    """Predicted leans up, actual leans down (and vice versa) → directional 0."""
    pairs = [
        (110.0, 90.0, 100.0),  # predicted up, actual down → miss
        (90.0, 110.0, 100.0),  # predicted down, actual up → miss
    ]
    _, _, directional = _compute_metrics(pairs)
    assert directional == pytest.approx(0.0, abs=1e-9)


def test_compute_metrics_known_mape_rmse() -> None:
    """Hand-computed example so the formulas don't drift silently."""
    pairs = [
        (105.0, 100.0, 100.0),  # 5% error, +5 sq err
        (110.0, 100.0, 100.0),  # 10% error, +100 sq err
    ]
    mape, rmse, _ = _compute_metrics(pairs)
    # MAPE = (5 + 10) / 2 = 7.5
    assert mape == pytest.approx(7.5, abs=1e-9)
    # RMSE = sqrt((25 + 100) / 2) = sqrt(62.5) ≈ 7.9056
    assert rmse == pytest.approx((62.5) ** 0.5, abs=1e-9)


def test_compute_metrics_empty_returns_all_none() -> None:
    """Below MIN_EVALUABLE_POINTS → no metrics."""
    mape, rmse, directional = _compute_metrics([])
    assert mape is None
    assert rmse is None
    assert directional is None


def test_compute_metrics_skips_zero_actuals_for_mape() -> None:
    """Dividing by zero would produce inf — make sure we skip those entries
    in MAPE while still using them for RMSE."""
    pairs = [
        (1.0, 0.0, 1.0),  # zero actual — skipped from MAPE
        (110.0, 100.0, 100.0),
    ]
    mape, rmse, _ = _compute_metrics(pairs)
    # MAPE only sees the second pair = 10%
    assert mape == pytest.approx(10.0, abs=1e-9)
    # RMSE sees both: sqrt((1**2 + 10**2) / 2) ≈ sqrt(50.5)
    assert rmse == pytest.approx((50.5) ** 0.5, abs=1e-9)


def test_compute_metrics_no_directional_pairs_returns_none() -> None:
    """All forecasts called "no change" (or actuals all = last_close) → directional is None."""
    pairs = [(100.0, 100.0, 100.0)]  # no direction either way
    _, _, directional = _compute_metrics(pairs)
    assert directional is None


def test_evaluable_pairs_filters_to_actuals() -> None:
    """Forecast points whose date isn't in the actuals dict are dropped."""
    snap = ForecastResult(
        model="SARIMAX(1,1,1)",
        horizon_days=3,
        training_rows=100,
        last_close=Decimal("100"),
        last_close_date=date(2026, 4, 20),
        generated_at=datetime(2026, 4, 21, 12, 0, tzinfo=UTC),
        points=[
            ForecastPoint(
                forecast_date=date(2026, 4, 21),
                yhat=101.0,
                lower_80=100.0,
                upper_80=102.0,
                lower_95=99.0,
                upper_95=103.0,
            ),
            ForecastPoint(
                forecast_date=date(2026, 4, 22),
                yhat=102.0,
                lower_80=101.0,
                upper_80=103.0,
                lower_95=100.0,
                upper_95=104.0,
            ),
            # Day 3 has no actual → filtered out.
            ForecastPoint(
                forecast_date=date(2026, 4, 23),
                yhat=103.0,
                lower_80=102.0,
                upper_80=104.0,
                lower_95=101.0,
                upper_95=105.0,
            ),
        ],
    )
    actuals = {
        date(2026, 4, 21): 100.5,
        date(2026, 4, 22): 102.5,
    }
    pairs = _evaluable_pairs(snap, actuals)
    assert len(pairs) == 2
    # last_close is shared across pairs from the same snapshot
    assert all(p[2] == 100.0 for p in pairs)


# ---------------------------------------------------------------------------
# DB-aware compute_accuracy
# ---------------------------------------------------------------------------


def _seed_asset(symbol: str = "AAPL") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=symbol, asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _seed_actuals(asset_id: int, points: list[tuple[date, float]]) -> None:
    """Insert daily-bar PricePoint rows for the asset."""
    with session_scope() as s:
        for d, price in points:
            ts = datetime(d.year, d.month, d.day, tzinfo=UTC)
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


def _snapshot(
    *,
    model: str,
    last_close: float,
    last_close_date: date,
    generated_at: datetime,
    forecasts: list[tuple[date, float]],
) -> ForecastResult:
    return ForecastResult(
        model=model,
        horizon_days=len(forecasts),
        training_rows=100,
        last_close=Decimal(str(last_close)),
        last_close_date=last_close_date,
        generated_at=generated_at,
        points=[
            ForecastPoint(
                forecast_date=d,
                yhat=p,
                lower_80=p * 0.99,
                upper_80=p * 1.01,
                lower_95=p * 0.98,
                upper_95=p * 1.02,
            )
            for d, p in forecasts
        ],
    )


def test_compute_accuracy_unknown_symbol_returns_empty(isolated_db: Path) -> None:
    report = compute_accuracy("NOPE")
    assert report.symbol == "NOPE"
    assert report.per_engine == []
    assert report.overall is None
    assert report.actuals_available == 0


def test_compute_accuracy_no_snapshots_returns_empty(isolated_db: Path) -> None:
    _seed_asset()
    report = compute_accuracy("AAPL")
    assert report.per_engine == []
    assert report.overall is None


def test_compute_accuracy_single_engine_full_horizon_evaluable(
    isolated_db: Path,
) -> None:
    """One snapshot, all forecast dates have matching actuals → fully scored."""
    asset_id = _seed_asset()
    _seed_actuals(
        asset_id,
        [
            (date(2026, 4, 21), 105.0),  # actual matches forecast exactly
            (date(2026, 4, 22), 110.0),
        ],
    )
    save_forecast(
        asset_id,
        _snapshot(
            model="SARIMAX(1,1,1)",
            last_close=100.0,
            last_close_date=date(2026, 4, 20),
            generated_at=datetime.now(UTC) - timedelta(days=5),
            forecasts=[
                (date(2026, 4, 21), 105.0),
                (date(2026, 4, 22), 110.0),
            ],
        ),
    )

    report = compute_accuracy("AAPL", days=30)
    assert len(report.per_engine) == 1
    eng = report.per_engine[0]
    assert eng.engine == "SARIMAX(1,1,1)"
    assert eng.snapshots == 1
    assert eng.evaluable_points == 2
    # Perfect forecast → MAPE 0, RMSE 0, directional 1.0
    assert eng.mape == pytest.approx(0.0, abs=1e-6)
    assert eng.rmse == pytest.approx(0.0, abs=1e-6)
    assert eng.directional == pytest.approx(1.0, abs=1e-6)


def test_compute_accuracy_per_engine_breakdown(isolated_db: Path) -> None:
    """Two engines on the same asset → both appear in per_engine, sorted by MAPE."""
    asset_id = _seed_asset()
    _seed_actuals(
        asset_id,
        [
            (date(2026, 4, 21), 100.0),
            (date(2026, 4, 22), 100.0),
        ],
    )
    # SARIMAX missed by 5 each day (mape 5%).
    save_forecast(
        asset_id,
        _snapshot(
            model="SARIMAX(1,1,1)",
            last_close=100.0,
            last_close_date=date(2026, 4, 20),
            generated_at=datetime.now(UTC) - timedelta(days=5),
            forecasts=[
                (date(2026, 4, 21), 105.0),
                (date(2026, 4, 22), 105.0),
            ],
        ),
    )
    # Holt-Winters was perfect.
    save_forecast(
        asset_id,
        _snapshot(
            model="Holt-Winters (ETS A,A,N)",
            last_close=100.0,
            last_close_date=date(2026, 4, 20),
            generated_at=datetime.now(UTC) - timedelta(days=5),
            forecasts=[
                (date(2026, 4, 21), 100.0),
                (date(2026, 4, 22), 100.0),
            ],
        ),
    )

    report = compute_accuracy("AAPL", days=30)
    assert len(report.per_engine) == 2
    # Best (lowest MAPE) is sorted first.
    assert report.per_engine[0].engine == "Holt-Winters (ETS A,A,N)"
    assert report.per_engine[0].mape == pytest.approx(0.0, abs=1e-6)
    assert report.per_engine[1].engine == "SARIMAX(1,1,1)"
    assert report.per_engine[1].mape == pytest.approx(5.0, abs=1e-6)


def test_compute_accuracy_overall_aggregates_across_engines(
    isolated_db: Path,
) -> None:
    asset_id = _seed_asset()
    _seed_actuals(
        asset_id,
        [(date(2026, 4, 21), 100.0)],
    )
    save_forecast(
        asset_id,
        _snapshot(
            model="SARIMAX(1,1,1)",
            last_close=100.0,
            last_close_date=date(2026, 4, 20),
            generated_at=datetime.now(UTC) - timedelta(days=3),
            forecasts=[(date(2026, 4, 21), 110.0)],
        ),
    )
    save_forecast(
        asset_id,
        _snapshot(
            model="Holt-Winters (ETS A,A,N)",
            last_close=100.0,
            last_close_date=date(2026, 4, 20),
            generated_at=datetime.now(UTC) - timedelta(days=2),
            forecasts=[(date(2026, 4, 21), 90.0)],
        ),
    )

    report = compute_accuracy("AAPL", days=30)
    overall = report.overall
    assert overall is not None
    assert overall.evaluable_points == 2  # one pair per engine
    # MAPE = (10 + 10) / 2 = 10
    assert overall.mape == pytest.approx(10.0, abs=1e-6)


def test_compute_accuracy_snapshot_outside_window_excluded(
    isolated_db: Path,
) -> None:
    asset_id = _seed_asset()
    _seed_actuals(
        asset_id,
        [(date(2026, 4, 21), 100.0)],
    )
    # Snapshot from 200 days ago — outside any 30-day window.
    save_forecast(
        asset_id,
        _snapshot(
            model="SARIMAX(1,1,1)",
            last_close=100.0,
            last_close_date=date(2025, 9, 1),
            generated_at=datetime.now(UTC) - timedelta(days=200),
            forecasts=[(date(2025, 9, 5), 105.0)],
        ),
    )

    report = compute_accuracy("AAPL", days=30)
    assert report.per_engine == []


def test_compute_accuracy_pending_snapshot_counted_but_not_scored(
    isolated_db: Path,
) -> None:
    """Snapshot whose forecast dates are entirely in the future → 1 snapshot,
    0 evaluable points, all metrics None."""
    asset_id = _seed_asset()
    # No actuals — the forecast is forward-looking.
    save_forecast(
        asset_id,
        _snapshot(
            model="SARIMAX(1,1,1)",
            last_close=100.0,
            last_close_date=date.today() + timedelta(days=1),
            generated_at=datetime.now(UTC),
            forecasts=[(date.today() + timedelta(days=10), 110.0)],
        ),
    )

    report = compute_accuracy("AAPL", days=30)
    assert len(report.per_engine) == 1
    eng = report.per_engine[0]
    assert eng.snapshots == 1
    assert eng.evaluable_points == 0
    assert eng.mape is None
    assert eng.rmse is None
    assert eng.directional is None


def test_engine_accuracy_is_frozen() -> None:
    """Reports are value objects — mutating them mid-flight would be a bug."""
    e = EngineAccuracy(
        engine="SARIMAX(1,1,1)",
        snapshots=1,
        evaluable_points=1,
        mape=0.5,
        rmse=0.7,
        directional=1.0,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        e.mape = 2.0  # type: ignore[misc]
