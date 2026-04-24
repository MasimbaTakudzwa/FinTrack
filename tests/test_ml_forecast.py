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
    MIN_TRAINING_ROWS,
    MODEL_NAME,
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
