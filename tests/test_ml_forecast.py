"""Forecast-engine unit tests.

These cover `ml.forecast.forecast_series` in isolation — no DB, no scheduler.
We fit SARIMAX on synthetic series sized around the real-world minimum
(MIN_TRAINING_ROWS = 60) to keep runtime reasonable (~0.5 s per test).
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from ml.forecast import (
    ENGINES,
    HOLT_WINTERS_MODEL_NAME,
    MIN_TRAINING_ROWS,
    MODEL_NAME,
    SARIMAX_MODEL_NAME,
    ForecastError,
    ForecastPoint,
    ForecastResult,
    InsufficientDataError,
    forecast_series,
)


def _gen_series(n: int, *, start: date = date(2020, 1, 1)) -> list[tuple[date, float]]:
    """Synthetic trending series with mild noise — enough structure that
    SARIMAX converges without tripping warnings."""
    # Linear trend + small sinusoidal wiggle; non-stationary (the whole point
    # of SARIMAX's d=1) but smoother than a random walk.
    out: list[tuple[date, float]] = []
    for i in range(n):
        value = 100.0 + 0.3 * i + 2.0 * math.sin(i / 7.0)
        out.append((start + timedelta(days=i), value))
    return out


def test_forecast_series_returns_horizon_many_points() -> None:
    series = _gen_series(120)
    result = forecast_series(series, horizon_days=14)

    assert isinstance(result, ForecastResult)
    assert len(result.points) == 14
    assert result.horizon_days == 14
    assert result.training_rows == 120
    assert result.model == MODEL_NAME


def test_forecast_series_dates_are_calendar_consecutive() -> None:
    series = _gen_series(80)
    result = forecast_series(series, horizon_days=5)

    last_training_date = series[-1][0]
    assert result.last_close_date == last_training_date
    for i, point in enumerate(result.points):
        assert point.forecast_date == last_training_date + timedelta(days=i + 1)


def test_forecast_series_ci_bands_ordered() -> None:
    """95% band must be >= 80% band (wider = less certain)."""
    series = _gen_series(100)
    result = forecast_series(series, horizon_days=10)

    for p in result.points:
        assert p.lower_95 <= p.lower_80 <= p.yhat <= p.upper_80 <= p.upper_95


def test_forecast_series_rejects_too_few_rows() -> None:
    series = _gen_series(MIN_TRAINING_ROWS - 1)
    with pytest.raises(InsufficientDataError) as exc:
        forecast_series(series, horizon_days=7)
    assert str(MIN_TRAINING_ROWS) in str(exc.value)


def test_forecast_series_rejects_zero_horizon() -> None:
    series = _gen_series(80)
    with pytest.raises(ForecastError):
        forecast_series(series, horizon_days=0)


def test_forecast_series_rejects_overlong_horizon() -> None:
    series = _gen_series(80)
    with pytest.raises(ForecastError):
        forecast_series(series, horizon_days=91)


def test_forecast_series_rejects_unsorted_dates() -> None:
    series = _gen_series(80)
    # Swap two entries so the series is no longer ascending.
    series[10], series[11] = series[11], series[10]
    with pytest.raises(ForecastError) as exc:
        forecast_series(series, horizon_days=7)
    assert "ascending" in str(exc.value).lower()


def test_forecast_point_is_frozen_dataclass() -> None:
    """Forecasts are value objects — mutating them mid-flight would be a bug."""
    p = ForecastPoint(
        forecast_date=date(2026, 1, 1),
        yhat=1.0,
        lower_80=0.9,
        upper_80=1.1,
        lower_95=0.8,
        upper_95=1.2,
    )
    with pytest.raises(Exception):  # FrozenInstanceError subclasses AttributeError
        p.yhat = 2.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Engine selection — both dispatched implementations
# ---------------------------------------------------------------------------


def test_default_engine_is_sarimax() -> None:
    """``forecast_series`` without an explicit engine fits SARIMAX (the
    historical behaviour pre-multi-engine refactor)."""
    series = _gen_series(80)
    result = forecast_series(series, horizon_days=7)
    assert result.model == SARIMAX_MODEL_NAME == MODEL_NAME


def test_holt_winters_engine_fits_and_emits_horizon_points() -> None:
    series = _gen_series(120)
    result = forecast_series(series, horizon_days=14, engine="holt_winters")

    assert result.model == HOLT_WINTERS_MODEL_NAME
    assert len(result.points) == 14
    assert result.training_rows == 120
    # Forecast dates remain calendar-consecutive across engines.
    last = series[-1][0]
    for i, point in enumerate(result.points):
        assert point.forecast_date == last + timedelta(days=i + 1)


def test_holt_winters_engine_ci_bands_ordered() -> None:
    """ETS prediction intervals must obey 95% ⊇ 80% ⊇ point estimate, same
    invariant as SARIMAX. Catches any future regression in the column-
    indexing path inside ``_materialise_points``."""
    series = _gen_series(100)
    result = forecast_series(series, horizon_days=10, engine="holt_winters")
    for p in result.points:
        assert p.lower_95 <= p.lower_80 <= p.yhat <= p.upper_80 <= p.upper_95


def test_unknown_engine_raises_forecast_error() -> None:
    series = _gen_series(80)
    with pytest.raises(ForecastError) as exc:
        forecast_series(series, horizon_days=7, engine="prophet")  # type: ignore[arg-type]
    assert "engine" in str(exc.value).lower()


def test_engines_constant_is_stable() -> None:
    """Catches accidental drift in the literal set — must stay aligned with
    the API's `_validate_engine_param` and the Settings dropdown."""
    assert set(ENGINES) == {"sarimax", "holt_winters"}


# ---------------------------------------------------------------------------
# Volatility-aware confidence bands
# ---------------------------------------------------------------------------


def _flat_with_vol(n: int, sigma: float, *, start: date = date(2020, 1, 1)) -> list[tuple[date, float]]:
    """Series with a deterministic ± alternating return of magnitude `sigma`
    so realized volatility is controllable for band-width assertions."""
    out: list[tuple[date, float]] = []
    price = 100.0
    for i in range(n):
        out.append((start + timedelta(days=i), price))
        price *= 1.0 + (sigma if i % 2 == 0 else -sigma)
    return out


def test_volatility_bands_widen_with_horizon() -> None:
    from ml.forecast import _apply_volatility_bands

    result = forecast_series(_gen_series(120), horizon_days=14)
    widths = [p.upper_95 - p.lower_95 for p in result.points]
    # Monotonically non-decreasing (sqrt-of-horizon scaling), strictly wider end.
    assert all(widths[i + 1] >= widths[i] - 1e-9 for i in range(len(widths) - 1))
    assert widths[-1] > widths[0]
    assert _apply_volatility_bands  # symbol exists


def test_volatility_bands_symmetric_about_yhat() -> None:
    result = forecast_series(_gen_series(120), horizon_days=10)
    for p in result.points:
        assert p.upper_80 - p.yhat == pytest.approx(p.yhat - p.lower_80, rel=1e-6)
        assert p.upper_95 - p.yhat == pytest.approx(p.yhat - p.lower_95, rel=1e-6)
        # 95% strictly wider than 80%.
        assert (p.upper_95 - p.lower_95) > (p.upper_80 - p.lower_80)


def test_higher_realized_vol_gives_wider_bands() -> None:
    calm = forecast_series(_flat_with_vol(120, 0.002), horizon_days=7)
    wild = forecast_series(_flat_with_vol(120, 0.02), horizon_days=7)
    calm_w = calm.points[-1].upper_95 - calm.points[-1].lower_95
    wild_w = wild.points[-1].upper_95 - wild.points[-1].lower_95
    assert wild_w > calm_w


def test_ewma_vol_zero_for_flat_series() -> None:
    from ml.forecast import _ewma_daily_vol

    assert _ewma_daily_vol([100.0] * 50) == 0.0
